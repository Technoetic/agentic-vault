#!/usr/bin/env python3
"""Bounded data-only existence, substitution and occurrence rules.

Literal matching is the default. Optional regex executes the fixed worker below
in a fresh standard-library spawn process, with a real wall-clock timeout. No
rule can supply Python, commands, network targets, or a worker executable.
Results describe matching/transformation, never approval or factual authority.
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import re
import sys

MAX_TEXT_BYTES = 64 * 1024
MAX_PATTERN_BYTES = 512
MAX_RULES = 32
MAX_MATCHES = 1000
MAX_INPUT_BYTES = 128 * 1024
AUTHORITY = 'data_rule_evaluation_only'


class RuleError(Exception):
    """A content-free input error."""


def _bytes(value):
    try:
        return len(value.encode('utf-8'))
    except UnicodeError as exc:
        raise RuleError('invalid_utf8') from exc


def _validate(text, rules, allow_regex, timeout_seconds):
    if not isinstance(text, str) or _bytes(text) > MAX_TEXT_BYTES:
        raise RuleError('text_limit')
    if not isinstance(rules, list) or len(rules) > MAX_RULES:
        raise RuleError('rule_limit')
    if type(allow_regex) is not bool:
        raise RuleError('invalid_opt_in')
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or not .05 <= timeout_seconds <= 3):
        raise RuleError('invalid_timeout')
    result, identifiers = [], set()
    for index, value in enumerate(rules):
        if not isinstance(value, dict) or value.get('type') not in ('existence', 'substitution', 'occurrence'):
            raise RuleError('invalid_rule_type')
        allowed = {'id', 'type', 'pattern', 'mode'} | ({'replacement'} if value['type'] == 'substitution'
                                                     else {'min', 'max'} if value['type'] == 'occurrence' else set())
        if set(value) - allowed:
            raise RuleError('invalid_rule_fields')
        pattern = value.get('pattern')
        if not isinstance(pattern, str) or not pattern or _bytes(pattern) > MAX_PATTERN_BYTES:
            raise RuleError('pattern_limit')
        mode = value.get('mode', 'literal')
        if mode not in ('literal', 'regex'):
            raise RuleError('invalid_mode')
        identifier = value.get('id', 'rule-' + str(index + 1))
        if (not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', identifier)
                or identifier in identifiers):
            raise RuleError('invalid_or_duplicate_id')
        identifiers.add(identifier)
        normalized = dict(value, id=identifier, mode=mode)
        if value['type'] == 'substitution':
            replacement = value.get('replacement')
            if not isinstance(replacement, str) or _bytes(replacement) > 4096:
                raise RuleError('replacement_limit')
        elif value['type'] == 'occurrence':
            minimum, maximum = value.get('min', 0), value.get('max', MAX_MATCHES)
            if (type(minimum) is not int or type(maximum) is not int
                    or not 0 <= minimum <= maximum <= MAX_MATCHES):
                raise RuleError('invalid_count_bounds')
            normalized.update(min=minimum, max=maximum)
        result.append(normalized)
    return result


def _expand(match, template, remaining):
    r"""Expand supported re-style group references without large intermediates.

    Numbered (1..99) and named \g<...> references and ordinary escaped controls
    are supported; octal replacement escapes deliberately abstain. Each group
    is checked against the remaining encoded-output budget before concatenation.
    """
    pieces, size, index = [], 0, 0
    escapes = {'n': '\n', 'r': '\r', 't': '\t', 'f': '\f', 'v': '\v',
               'a': '\a', 'b': '\b', '\\': '\\'}
    while index < len(template):
        part = template[index]
        index += 1
        if part == '\\':
            if index >= len(template):
                raise re.error('invalid replacement')
            token = template[index]
            index += 1
            if token in '123456789':
                digits = token
                if index < len(template) and template[index].isdigit() and template[index].isascii():
                    digits += template[index]
                    index += 1
                part = match.group(int(digits)) or ''
            elif token == 'g':
                if index >= len(template) or template[index] != '<':
                    raise re.error('invalid group')
                end = template.find('>', index + 1)
                if end < 0:
                    raise re.error('invalid group')
                name = template[index + 1:end]
                if not name or (name.isdecimal() and not name.isascii()):
                    raise re.error('invalid group')
                part = match.group(int(name) if name.isdecimal() else name) or ''
                index = end + 1
            elif token in escapes:
                part = escapes[token]
            elif token.isalpha() or token.isdigit():
                raise re.error('unsupported replacement escape')
            else:
                part = '\\' + token
        size += _bytes(part)
        if size > remaining:
            return None
        pieces.append(part)
    return ''.join(pieces)


def _bounded_regex_substitute(text, compiled, replacement):
    pieces, used, cursor, count = [], 0, 0, 0
    for match in compiled.finditer(text):
        count += 1
        if count > MAX_MATCHES:
            return dict(status='match_limit', passed=None), text
        prefix = text[cursor:match.start()]
        used += _bytes(prefix)
        if used > MAX_TEXT_BYTES:
            return dict(status='output_limit', passed=None), text
        expanded = _expand(match, replacement, MAX_TEXT_BYTES - used)
        if expanded is None:
            return dict(status='output_limit', passed=None), text
        used += _bytes(expanded)
        pieces.extend((prefix, expanded))
        cursor = match.end()
    tail = text[cursor:]
    if used + _bytes(tail) > MAX_TEXT_BYTES:
        return dict(status='output_limit', passed=None), text
    pieces.append(tail)
    return dict(status='complete', passed=True, count=count), ''.join(pieces)


def _evaluate_one(text, rule, regex=False):
    kind, pattern = rule['type'], rule['pattern']
    if regex:
        try:
            compiled = re.compile(pattern)
        except re.error:
            return dict(status='invalid_pattern', passed=None), text
        if kind == 'existence':
            return dict(status='complete', passed=compiled.search(text) is not None), text
        if kind == 'substitution':
            try:
                return _bounded_regex_substitute(text, compiled, rule['replacement'])
            except (re.error, IndexError, KeyError):
                return dict(status='invalid_pattern', passed=None), text
        count = 0
        for _ in compiled.finditer(text):
            count += 1
            if count > MAX_MATCHES:
                return dict(status='match_limit', passed=None), text
        if kind == 'occurrence':
            return dict(status='complete', passed=rule['min'] <= count <= rule['max'], count=count), text
    else:
        count = text.count(pattern)
        if kind == 'existence':
            return dict(status='complete', passed=count > 0), text
        if kind == 'occurrence':
            return dict(status='complete', passed=rule['min'] <= count <= rule['max'], count=count), text
        # Compute a conservative encoded-output upper bound before allocation.
        if _bytes(text) + count * max(0, _bytes(rule['replacement']) - _bytes(pattern)) > MAX_TEXT_BYTES:
            return dict(status='output_limit', passed=None), text
        output, substitutions = text.replace(pattern, rule['replacement']), count
    if _bytes(output) > MAX_TEXT_BYTES:
        return dict(status='output_limit', passed=None), text
    return dict(status='complete', passed=True, count=substitutions), output


def _worker(connection, text, rule):
    try:
        result, output = _evaluate_one(text, rule, regex=True)
        connection.send((result, output))
    except BaseException:
        try:
            connection.send((dict(status='unavailable', passed=None), text))
        except (OSError, EOFError):
            pass
    finally:
        connection.close()


def _regex_process(text, rule, timeout_seconds):
    context = multiprocessing.get_context('spawn')
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(sender, text, rule), daemon=True)
    started = False
    try:
        process.start()
        started = True
        sender.close()
        if receiver.poll(timeout_seconds):
            result = receiver.recv()
            return result
        return dict(status='timeout', passed=None), text
    except (OSError, EOFError, RuntimeError):
        return dict(status='unavailable', passed=None), text
    finally:
        receiver.close()
        sender.close()
        if started:
            if process.is_alive():
                process.terminate()
            process.join(timeout=.5)
            if process.is_alive():
                process.kill()
                process.join(timeout=.5)
            process.close()


def evaluate_rules(text, rules, *, allow_regex=False, timeout_seconds=1):
    """Apply rules in order; occurrences/existence see earlier substitutions.

    Matching failures have passed=False. A disabled, unavailable, invalid or
    interrupted matcher has passed=None and stops evaluation without a fake pass.
    Each regex rule is isolated with its own wall-clock bound (maximum 32 rules).
    """
    rules = _validate(text, rules, allow_regex, timeout_seconds)
    output, results, status = text, [], 'complete'
    for rule in rules:
        if rule['mode'] == 'regex' and not allow_regex:
            result = dict(status='regex_disabled', passed=None)
        elif rule['mode'] == 'regex':
            try:
                result, output = _regex_process(output, rule, timeout_seconds)
            except (OSError, RuntimeError):
                result = dict(status='unavailable', passed=None)
        else:
            result, output = _evaluate_one(output, rule)
        results.append(dict(result, id=rule['id'], type=rule['type'], mode=rule['mode']))
        if result['status'] != 'complete':
            status = 'unavailable' if result['status'] == 'regex_disabled' else result['status']
            break
    return dict(status=status, passed=all(r['passed'] for r in results) if status == 'complete' else None,
                output=output, results=results, evaluated=len(results), requested=len(rules), authority=AUTHORITY)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-regex', action='store_true')
    parser.add_argument('--timeout-seconds', type=float, default=1)
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise RuleError('input_limit')
        # Duplicate keys do not silently overwrite a host-selected rule.
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise RuleError('duplicate_json_key')
                result[key] = value
            return result
        payload = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
        if not isinstance(payload, dict) or set(payload) != {'text', 'rules'}:
            raise RuleError('invalid_input')
        result = evaluate_rules(payload['text'], payload['rules'], allow_regex=args.allow_regex,
                                timeout_seconds=args.timeout_seconds)
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 0 if result['status'] == 'complete' and result['passed'] else 1
    except (RuleError, ValueError, TypeError, OSError, UnicodeError, RecursionError, OverflowError) as exc:
        code = str(exc) if isinstance(exc, RuleError) else 'invalid_or_unavailable_input'
        print(json.dumps({'status': 'unavailable', 'passed': None, 'error': code}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
