#!/usr/bin/env python3
"""Explicit bounded language/prompt trials using the synthetic chat-tool runner.

Default execution is an offline protocol control, never model-quality evidence.
Only --backend local-model opts into inference through the existing literal-loopback
transport. System and tool descriptions share an instruction-language condition,
independently of the request/document language. Every episode archives size-checked
responses and the synthetic outbox; grading values and trial labels stay host-side.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sys

import evaluate_tool_safety as safety


@dataclass(frozen=True)
class MatrixSpec:
    request_languages: tuple = ('ko', 'en')
    instruction_languages: tuple = ('en', 'ko')
    prompt_variants: tuple = ('canonical',)
    conditions: tuple = ('clean', 'attacked')
    repeats: int = 1
    seed: int = 17

    def __post_init__(self):
        choices = {'request_languages': ('ko', 'en'), 'instruction_languages': ('en', 'ko'),
                   'prompt_variants': ('canonical', 'literal-identifiers'),
                   'conditions': ('clean', 'attacked')}
        for name, allowed in choices.items():
            values = getattr(self, name)
            if (type(values) is not tuple or not values
                    or any(type(item) is not str or item not in allowed for item in values)
                    or len(values) != len(set(values))):
                raise ValueError('invalid_matrix_' + name)
        if type(self.repeats) is not int or not 1 <= self.repeats <= 5:
            raise ValueError('invalid_matrix_repeats')
        if type(self.seed) is not int or not 0 <= self.seed < 2**31:
            raise ValueError('invalid_matrix_seed')


def plan_trials(spec):
    if not isinstance(spec, MatrixSpec):
        raise ValueError('invalid_matrix_spec')
    plan = []
    for request in spec.request_languages:
        for instruction in spec.instruction_languages:
            for variant in spec.prompt_variants:
                for condition in spec.conditions:
                    for repeat in range(spec.repeats):
                        plan.append({'trial_id': f'{request}-{instruction}-{variant}-{condition}-r{repeat + 1}',
                                     'episode_id': request + '-' + condition,
                                     'request_language': request, 'system_language': instruction,
                                     'tool_language': instruction, 'prompt_variant': variant,
                                     'condition': condition, 'repeat': repeat,
                                     'seed': (spec.seed + repeat) % 2**31})
    # Paired cells share a repeat seed; randomized execution order uses the base seed.
    random.Random(spec.seed).shuffle(plan)
    return plan


def _proportion(numerator, denominator):
    interval = None
    rate = numerator / denominator if denominator else None
    if denominator:
        z = 1.959963984540054
        divisor = 1 + z * z / denominator
        center = (rate + z * z / (2 * denominator)) / divisor
        radius = z * math.sqrt(rate * (1 - rate) / denominator
                               + z * z / (4 * denominator * denominator)) / divisor
        interval = [max(0.0, center - radius), min(1.0, center + radius)]
    return {'numerator': numerator, 'denominator': denominator, 'rate': rate,
            'wilson_95_interval': interval}


def summarize_matrix(episodes):
    """Primary metrics retain errors; attack success uses only injected episodes."""
    clean = [row for row in episodes if row['condition'] == 'clean']
    attacked = [row for row in episodes if row['condition'] == 'attacked']
    error_free_attacked = [row for row in attacked if row['model_error'] is None]
    metrics = {}
    for name, rows, field in (
        ('benign_completion_all', episodes, 'benign_completed'),
        ('benign_completion_clean', clean, 'benign_completed'),
        ('benign_completion_attacked', attacked, 'benign_completed'),
        ('attack_success_attacked', attacked, 'attack_goal_achieved'),
        ('attack_success_attacked_error_free', error_free_attacked, 'attack_goal_achieved'),
    ):
        metrics[name] = _proportion(sum(row[field] for row in rows), len(rows))
    errors = {}
    failures = dict.fromkeys(safety.FAILURE_SUBTYPES, 0)
    for row in episodes:
        failures[row['failure_subtype']] += 1
        if row['model_error'] is not None:
            errors[row['model_error']] = errors.get(row['model_error'], 0) + 1
    metrics['model_error_all'] = _proportion(sum(errors.values()), len(episodes))
    argument_failures = {key: count for key, count in failures.items() if key.startswith('arg_')}
    metrics['argument_failure_all'] = _proportion(sum(argument_failures.values()), len(episodes))
    return {'episodes': len(episodes), 'metrics': metrics, 'model_errors': errors,
            'failure_subtypes': failures, 'argument_failure_subtypes': argument_failures}


def _cost(episodes):
    names = ('prompt_tokens', 'completion_tokens', 'total_tokens')
    complete = [row['cost']['provider_usage'] for row in episodes
                if row['cost']['provider_usage'] is not None]
    observed = [safety._usage(raw['response']) for row in episodes for raw in row['raw_responses']]
    usable = [usage for usage in observed if usage is not None]
    requests = sum(row['execution']['turns'] for row in episodes)
    coverage = ('complete' if usable and len(usable) == requests
                else 'partial' if usable else 'unavailable')
    return {'requests_attempted': requests, 'responses_captured': len(observed),
            'responses_with_provider_usage': len(usable),
            'provider_usage_coverage': coverage,
            'episodes_with_complete_provider_usage': len(complete),
            'provider_usage_complete_episodes': ({key: sum(item[key] for item in complete) for key in names}
                                                 if complete else None),
            'provider_usage_observed_responses': ({key: sum(item[key] for item in usable) for key in names}
                                                 if usable else None),
            'input_tokens_estimate': sum(row['cost']['input_tokens_estimate'] for row in episodes),
            'estimate_method': 'ceil(request JSON characters / 4), summed per request; not billed tokens',
            'output_tokens_allocated': sum(row['cost']['output_tokens_allocated'] for row in episodes),
            'elapsed_episode_seconds': sum(row['execution']['elapsed_seconds'] for row in episodes),
            'billing': None}


def _fixture_hash(trial):
    scenario = safety._scenario(trial['episode_id'])
    observations = {'system': safety._system_message(trial['system_language'], trial['prompt_variant']),
                    'task': scenario['task'], 'document': scenario['document'],
                    'tools': safety._tools(trial['tool_language'])}
    return hashlib.sha256(safety._json_bytes(observations)).hexdigest()


def _source_hashes():
    sources = (Path(__file__).resolve(), Path(safety.__file__).resolve())
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}


def evaluate_matrix(*, spec=None, backend='scripted-control', transport=None, control='safe', limits=None):
    spec = MatrixSpec() if spec is None else spec
    plan = plan_trials(spec)
    if backend not in ('scripted-control', 'local-model'):
        raise ValueError('invalid_backend')
    if backend == 'local-model' and transport is None:
        raise ValueError('explicit_local_model_transport_required')
    if control not in ('safe', 'susceptible', 'blocked'):
        raise ValueError('unknown_scripted_control')
    limits = safety.Limits() if limits is None else limits
    if not isinstance(limits, safety.Limits):
        raise ValueError('invalid_limits')
    source_start = _source_hashes()
    if backend == 'scripted-control':
        props, props_reason = None, 'scripted_control'
    elif callable(getattr(transport, 'probe_props', None)):
        props, props_reason = transport.probe_props()
    else:
        props, props_reason = None, 'props_probe_unavailable'
    rows = []
    for trial in plan:
        row = safety.run_episode(trial['episode_id'], transport=transport, backend=backend, control=control,
                                 limits=limits, diagnostics=True, capture_responses=True,
                                 system_language=trial['system_language'], tool_language=trial['tool_language'],
                                 prompt_variant=trial['prompt_variant'], seed=trial['seed'])
        row.update(trial)
        row['fixture_sha256'] = _fixture_hash(trial)
        rows.append(row)
    groups = {}
    for row in rows:
        label = (row['request_language'], row['system_language'], row['tool_language'],
                 row['prompt_variant'], row['condition'])
        groups.setdefault(label, []).append(row)
    label_names = ('request_language', 'system_language', 'tool_language', 'prompt_variant', 'condition')
    cells = [{'conditions': dict(zip(label_names, label)), 'summary': summarize_matrix(group)}
             for label, group in sorted(groups.items())]
    source_finish = _source_hashes()
    invalidity = 'source_changed_during_run' if source_start != source_finish else None
    return {'schema_version': 1, 'benchmark': 'invented-synthetic-tool-language-matrix',
            'execution_kind': backend, 'control': control if backend == 'scripted-control' else None,
            'quality_authority': ('no-current-comparison' if invalidity
                                  else 'protocol-control-only' if backend == 'scripted-control'
                                  else 'bounded-model-observations'),
            'model_configuration': getattr(transport, 'configuration', None),
            'matrix_spec': asdict(spec), 'trial_plan': plan, 'limits': asdict(limits),
            'resource_bounds': {'max_episodes': 80, 'planned_episodes': len(plan),
                                'max_requests': len(plan) * limits.max_turns,
                                'max_episode_response_bytes': limits.max_turns * limits.max_response_bytes},
            'seed_policy': 'Base seed shuffles order; each repeat requests (base + zero-based repeat) modulo 2^31. '
                           'Paired conditions share seeds. Server compliance and determinism are unverified.',
            'uncertainty': 'Two-sided 95% Wilson intervals are descriptive binomial intervals. '
                           'Repeated synthetic fixtures and shared models are dependent; these are not '
                           'population-quality guarantees or independent benchmark samples.',
            'source_sha256': source_start, 'source_sha256_at_finish': source_finish,
            'invalid_evaluation': invalidity,
            'server_props': props, 'server_props_reason': props_reason,
            'summary': summarize_matrix(rows), 'cells': cells, 'cost': _cost(rows), 'episodes': rows,
            'scope': 'Built-in invented fixtures only; no real files, messages, commands, or browsing. '
                     'Scripted controls verify protocol and graders, not model quality. '
                     'This is not an official AgentDojo or MLCL score.'}


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('scripted-control', 'local-model'), default='scripted-control')
    parser.add_argument('--control', choices=('safe', 'susceptible', 'blocked'), default='safe')
    parser.add_argument('--request-language', choices=('all', 'ko', 'en'), default='all')
    parser.add_argument('--instruction-language', choices=('all', 'ko', 'en'), default='all',
                        help='Language of both system text and tool descriptions; identifiers remain unchanged.')
    parser.add_argument('--prompt-variant', choices=('all', 'canonical', 'literal-identifiers'), default='canonical')
    parser.add_argument('--condition', choices=('all', 'clean', 'attacked'), default='all')
    parser.add_argument('--repeats', type=int, default=1, help='Explicit repeated trials, 1 through 5.')
    parser.add_argument('--seed', type=int, default=17)
    parser.add_argument('--endpoint', default='http://127.0.0.1:8080/v1/chat/completions')
    parser.add_argument('--model')
    parser.add_argument('--api-key-env', help='Explicit environment variable; never printed or read from a file.')
    parser.add_argument('--receipt-out', metavar='PATH', help='Write complete JSON to a new file in an existing directory.')
    for name, field in safety.Limits.__dataclass_fields__.items():
        parser.add_argument('--' + name.replace('_', '-'), type=float if 'seconds' in name else int,
                            default=field.default)
    args = parser.parse_args(argv)
    if args.backend == 'local-model' and not args.model:
        parser.error('--model is required for --backend local-model')
    receipt = os.path.abspath(args.receipt_out) if args.receipt_out is not None else None
    if receipt is not None and (os.path.lexists(receipt) or not os.path.isdir(os.path.dirname(receipt))):
        parser.error('--receipt-out must name a new file in an existing directory')
    try:
        def selected(value, allowed):
            return allowed if value == 'all' else (value,)
        spec = MatrixSpec(request_languages=selected(args.request_language, ('ko', 'en')),
                          instruction_languages=selected(args.instruction_language, ('en', 'ko')),
                          prompt_variants=selected(args.prompt_variant, ('canonical', 'literal-identifiers')),
                          conditions=selected(args.condition, ('clean', 'attacked')),
                          repeats=args.repeats, seed=args.seed)
        limits = safety.Limits(**{name: getattr(args, name) for name in safety.Limits.__dataclass_fields__})
        transport = None
        if args.backend == 'local-model':
            key = os.environ.get(args.api_key_env) if args.api_key_env else None
            if args.api_key_env and key is None:
                parser.error('the explicitly named API-key environment variable is absent')
            transport = safety.LocalChatTransport(args.endpoint, args.model, api_key=key)
        report = evaluate_matrix(spec=spec, backend=args.backend, transport=transport,
                                 control=args.control, limits=limits)
    except ValueError:
        parser.error('invalid loopback configuration, matrix conditions or resource limits')
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    status = 1 if report['invalid_evaluation'] or report['summary']['metrics']['model_error_all']['numerator'] else 0
    if receipt is not None:
        try:
            with open(receipt, 'xb') as handle:
                handle.write(text.encode('utf-8') + b'\n')
        except OSError:
            print('receipt_write_failed: the complete receipt is on stdout only', file=sys.stderr)
            status = 1
    print(text)
    return status


if __name__ == '__main__':
    raise SystemExit(main())
