#!/usr/bin/env python3
"""Evaluate lexical source retrieval on the checked-in bilingual fixture corpus.

Opt-in: --vault/--goldset evaluates a user gold set against a real vault in
place (local output only), --split evaluates one side of a committed
dev/held-out split, and --redact hashes gold-set free text in the report
(--redact-key-file and --redact-ephemeral use a secret key and also cover
vault-relative paths).
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import hmac
import importlib.util
import json
import math
import os
import random
import re
import secrets
import stat
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "recall"
RECALL_PATH = REPO_ROOT / "skills" / "agentic-vault" / "scripts" / "vault_recall.py"
EVAL_STATS_PATH = REPO_ROOT / "scripts" / "eval_stats.py"
MAX_QUERIES_BYTES = 1024 * 1024
MAX_SPLIT_BYTES = 1024 * 1024
MAX_GOLDSET_WARNINGS = 50
MAX_AMBIGUOUS_CANDIDATES = 5
SPLIT_NAMES = ("dev", "heldout")
SPLIT_SEED = 1707
SPLIT_RULE = (
    "Stratify queries by (language, scenario). Within each stratum order the query ids by the "
    "lowercase SHA-256 hex of the UTF-8 text '<seed>:<id>' (ties by id); the first floor(n/2) ids "
    "are heldout and the rest dev. With a previous split file, its listed ids keep their split and "
    "only unlisted ids are ordered and divided this way within each stratum."
)
LABEL_MATCH_KINDS = ("path", "stem", "ambiguous_stem", "missing", "denied", "excluded", "incomplete_inventory")


def _load_recall_module():
    script_dir = str(RECALL_PATH.parent)
    sys.path.insert(0, script_dir)
    try:
        spec = importlib.util.spec_from_file_location("agentic_vault_recall_evaluation", RECALL_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load recall module: {RECALL_PATH}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(script_dir)


@functools.lru_cache(maxsize=1)
def _load_eval_stats():
    """Load the small-sample statistics by path; only --exact-paired/--rate-bounds use them."""
    spec = importlib.util.spec_from_file_location("agentic_vault_eval_stats", EVAL_STATS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load statistics module: {EVAL_STATS_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_queries(fixture: Path) -> list[dict]:
    path = fixture / "queries.json"
    if path.stat().st_size > MAX_QUERIES_BYTES:
        raise ValueError("queries.json exceeds evaluation byte limit")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return _checked_queries(raw)


def _checked_queries(raw: object) -> list[dict]:
    """Validate the per-query schema shared by fixture queries.json and gold sets."""
    queries = raw.get("queries") if isinstance(raw, dict) else None
    if not isinstance(queries, list) or len(queries) < 12:
        raise ValueError("fixture must contain at least 12 labeled queries")
    checked: list[dict] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(queries):
        if not isinstance(item, dict):
            raise ValueError(f"queries[{index}] must be an object")
        query_id = item.get("id")
        query = item.get("query")
        language = item.get("language")
        expected = item.get("expected_paths")
        if not isinstance(query_id, str) or not query_id.strip() or query_id in seen_ids:
            raise ValueError(f"queries[{index}].id must be unique and non-empty")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"queries[{index}].query must be non-empty")
        if language not in ("en", "ko"):
            raise ValueError(f"queries[{index}].language must be en or ko")
        expectation = item.get("expectation", "answerable")
        if expectation not in ("answerable", "no_answer"):
            raise ValueError(f"queries[{index}].expectation must be answerable or no_answer")
        _validate_paths(expected, f"queries[{index}].expected_paths")
        if bool(expected) != (expectation == "answerable"):
            raise ValueError(f"queries[{index}].expected_paths contradicts expectation")
        scenario = item.get("scenario", "unspecified")
        if not isinstance(scenario, str) or not scenario.strip():
            raise ValueError(f"queries[{index}].scenario must be non-empty")
        forbidden = item.get("forbidden_paths", [])
        stale = item.get("stale_paths", [])
        for field, values in (("forbidden_paths", forbidden), ("stale_paths", stale)):
            _validate_paths(values, f"queries[{index}].{field}")
            if {path.casefold() for path in expected}.intersection(path.casefold() for path in values):
                raise ValueError(f"queries[{index}].{field} overlaps expected_paths")
        seen_ids.add(query_id)
        claims = item.get('required_claims', [])
        if not isinstance(claims, list) or len(claims) > 32 or any(
                not isinstance(claim, str) or not claim.strip() or len(claim) > 1000 for claim in claims) or len(set(claims)) != len(claims):
            raise ValueError(f'queries[{index}].required_claims must contain bounded unique literal claims')
        if claims and expectation == 'no_answer':
            raise ValueError('no_answer query cannot require returned claims')
        relevance = item.get('relevance', {path: 1 for path in expected})
        if not isinstance(relevance, dict) or len(relevance) > 50:
            raise ValueError('relevance must contain at most 50 path-to-grade labels')
        _validate_paths(list(relevance), f'queries[{index}].relevance')
        if any(type(grade) is not int or not 0 <= grade <= 3 for grade in relevance.values()):
            raise ValueError('relevance grades must be integers from 0 through 3')
        if expectation == 'answerable' and any(relevance.get(path, 0) == 0 for path in expected):
            raise ValueError('expected paths require positive relevance labels')
        if expectation == 'no_answer' and any(relevance.values()):
            raise ValueError('no_answer query cannot have positive relevance')
        # Optional: true marks a query edited after looking at failures. Such
        # queries leave headline metrics; the key is never added when absent.
        if 'inspected' in item and type(item['inspected']) is not bool:
            raise ValueError(f'queries[{index}].inspected must be true or false')
        checked.append({**item, "expectation": expectation, "scenario": scenario,
                        "forbidden_paths": forbidden, "stale_paths": stale,
                        'required_claims': claims, 'relevance': relevance})
    return checked


def _validate_paths(values: object, field: str) -> None:
    """Validate labels without opening labeled files (missing labels can measure misses)."""
    if not isinstance(values, list):
        raise ValueError(f"{field} must be a list of literal relative Markdown paths")
    seen: set[str] = set()
    for value in values:
        if (
            not isinstance(value, str) or not value.endswith(".md")
            or any(char in value for char in "\\:*?\"<>|")
            or any(ord(char) < 32 for char in value)
            or any(part in ("", ".", "..") or part.rstrip(" .") != part for part in value.split("/"))
            or value.casefold() in seen
        ):
            raise ValueError(f"{field} must contain unique safe literal relative Markdown paths")
        seen.add(value.casefold())


def _ratio(numerator: float, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _ndcg(retrieved: list[str], relevance: dict[str, int]) -> float | None:
    ideal = sorted(relevance.values(), reverse=True)[:5]
    idcg = sum((2**grade-1) / math.log2(rank+2) for rank, grade in enumerate(ideal))
    if not idcg:
        return None
    dcg = sum((2**relevance.get(path, 0)-1) / math.log2(rank+2) for rank, path in enumerate(retrieved[:5]))
    return dcg / idcg


def paired_randomization(baseline, improved, *, seed=1707, permutations=10000) -> dict:
    """Two-sided paired sign randomization, fixed seed, add-one Monte Carlo p.

    Inputs are aligned observed metric values, not ranks or unpaired summaries.
    No normality assumptions or optional statistics package is required.
    """
    if type(seed) is not int or type(permutations) is not int or not 1 <= permutations <= 100000:
        raise ValueError('invalid randomization bounds')
    if not isinstance(baseline, (list, tuple)) or not isinstance(improved, (list, tuple)) or len(baseline) != len(improved):
        raise ValueError('paired values must have identical lengths')
    values = (*baseline, *improved)
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ValueError('paired values must be finite numbers')
    n = len(baseline)
    delta = [b-a for a, b in zip(baseline, improved)]
    observed = sum(delta) / n if n else None
    if n:
        generator = random.Random(seed)
        threshold = abs(sum(delta))
        extreme = sum(abs(sum(value if generator.getrandbits(1) else -value for value in delta)) >= threshold-1e-12
                      for _ in range(permutations))
        p_value = (extreme+1) / (permutations+1)
    else:
        p_value = None
    return {'method': 'paired sign randomization; two-sided; add-one Monte Carlo',
            'seed': seed, 'permutations': permutations, 'paired_query_count': n,
            'mean_delta': observed, 'p_value': p_value}


def paired_test(baseline, improved, *, exact: bool = False, seed: int = 1707, permutations: int = 10000) -> dict:
    """paired_randomization() unless exact=True (the opt-in --exact-paired path).

    Exact mode enumerates all 2^k sign patterns when k (non-zero pairs) <= 20,
    with the same statistic and tie rule; above that it keeps the seeded Monte
    Carlo p-value. Both exact-mode records name their method and add
    nonzero_pair_count and minimum_attainable_p_value (2^(1-k)).
    """
    if type(exact) is not bool:
        raise ValueError('exact must be boolean')
    if not exact:
        return paired_randomization(baseline, improved, seed=seed, permutations=permutations)
    if type(seed) is not int or type(permutations) is not int or not 1 <= permutations <= 100000:
        raise ValueError('invalid randomization bounds')
    stats = _load_eval_stats()
    nonzero = sum(value != 0 for value in stats.paired_differences(baseline, improved))
    if nonzero <= stats.EXACT_PAIRED_MAX_NONZERO:
        return stats.exact_paired_sign_flip(baseline, improved)
    result = paired_randomization(baseline, improved, seed=seed, permutations=permutations)
    return {**result, 'method': f"{result['method']} (more than {stats.EXACT_PAIRED_MAX_NONZERO} non-zero pairs)",
            'nonzero_pair_count': nonzero,
            'minimum_attainable_p_value': stats.minimum_attainable_p_value(nonzero)}


def _summary(rows: list[dict]) -> dict:
    answerable = [row for row in rows if row["expectation"] == "answerable"]
    no_answer = [row for row in rows if row["expectation"] == "no_answer"]
    false_positives = sum(bool(row["retrieved_paths"]) for row in no_answer)
    complete_empty = sum(row["outcome"] == "no_sources_returned_complete_scan" for row in no_answer)
    incomplete_empty = sum(row["outcome"] == "incomplete_search_no_sources" for row in no_answer)
    summary = {
        "query_count": len(rows),
        "answerable_query_count": len(answerable),
        "no_answer_query_count": len(no_answer),
        "recall_at_3": _ratio(sum(row["source_recall_at_3"] for row in answerable), len(answerable)),
        "mrr": _ratio(sum(row["reciprocal_rank_at_3"] for row in answerable), len(answerable)),
        'recall_at_k': _ratio(sum(row['source_recall_at_k'] for row in answerable), len(answerable)),
        'mrr_at_k': _ratio(sum(row['reciprocal_rank_at_k'] for row in answerable), len(answerable)),
        'ndcg_at_5': _ratio(sum(row['ndcg_at_5'] for row in answerable), len(answerable)),
        'ndcg_query_count': len(answerable),
        'required_claim_query_count': sum(bool(row['required_claims']) for row in rows),
        'required_claim_count': sum(len(row['required_claims']) for row in rows),
        'covered_claim_count': sum(len(row['covered_claims']) for row in rows),
        'returned_context_claim_coverage': _ratio(sum(len(row['covered_claims']) for row in rows),
                                                sum(len(row['required_claims']) for row in rows)),
        "no_answer": {
            "query_count": len(no_answer),
            "false_positive_query_count": false_positives,
            "false_positive_rate": _ratio(false_positives, len(no_answer)),
            "complete_empty_query_count": complete_empty,
            "complete_empty_rate": _ratio(complete_empty, len(no_answer)),
            "incomplete_empty_query_count": incomplete_empty,
        },
    }
    for name, label, retrieved in (
        ("forbidden_sources", "forbidden_paths", "forbidden_retrieved_paths"),
        ("stale_sources", "stale_paths", "stale_retrieved_paths"),
    ):
        labeled = [row for row in rows if row[label]]
        exposed = sum(bool(row[retrieved]) for row in labeled)
        top_one = sum(row["top_1_forbidden" if name == "forbidden_sources" else "top_1_stale"] for row in labeled)
        summary[name] = {
            "labeled_query_count": len(labeled),
            "exposed_query_count": exposed,
            "exposure_rate": _ratio(exposed, len(labeled)),
            "top_1_query_count": top_one,
            "top_1_rate": _ratio(top_one, len(labeled)),
        }
    return summary


def evaluate(fixture: Path, *, max_tokens: int = 1500, advanced: bool = False,
             as_of: str | None = None, expand_links: int | None = None,
             backend: str | None = None, top_k: int = 3, expand_query: bool = False,
             query_mapping: str | None = None, lexical_ngrams: str | None = None,
             render_matched: bool = False) -> dict:
    """Measure labeled source retrieval and returned exposure, never answer quality.

    lexical_ngrams is passed to every advanced-path retrieval call of this arm.
    render_matched runs the advanced arm with temporal_filter='annotate_only':
    same options (lexical_ngrams included), fields and annotations, but no
    as-of/supersession exclusion.

    fixture is a fixture directory (queries.json + vault/) or an
    EvaluationInput from load_goldset()/select_split(); the latter's report
    sections are appended. Queries marked inspected=true leave the headline
    metrics, by_language and by_scenario and are summarized separately.
    """
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 0:
        raise ValueError("max_tokens must be a non-negative integer")
    if type(top_k) is not int or top_k not in (3, 5) or type(expand_query) is not bool:
        raise ValueError('top_k must be 3 or 5 and expand_query must be boolean')
    if query_mapping is not None and (not isinstance(query_mapping, str) or not expand_query):
        raise ValueError('query_mapping requires explicit expansion')
    if type(advanced) is not bool or (expand_links is not None and
            (type(expand_links) is not int or expand_links not in (0, 1, 2))):
        raise ValueError('invalid advanced options')
    if backend is not None and backend not in ('lexical', 'bm25', 'hybrid'):
        raise ValueError('invalid retrieval backend')
    if lexical_ngrams is not None and lexical_ngrams != 'hangul2':
        raise ValueError('invalid lexical n-gram mode')
    if type(render_matched) is not bool:
        raise ValueError('render_matched must be boolean')
    advanced = (advanced or render_matched or as_of is not None or expand_links is not None
                or backend is not None or expand_query or lexical_ngrams is not None)
    prepared = fixture if isinstance(fixture, EvaluationInput) else None
    queries = list(prepared.queries) if prepared is not None else _load_queries(fixture)
    recall_module = _load_recall_module()
    vault = prepared.vault if prepared is not None else fixture / "vault"
    failures: list[dict] = []
    evaluation_errors: list[dict] = []
    per_query: list[dict] = []

    for item in queries:
        options = {}
        if advanced:
            options = {key: item[key] for key in ('as_of', 'expand_links', 'backend',
                       'external_candidates', 'excluded_sources') if key in item}
            options.update({key: value for key, value in
                            (('as_of', as_of), ('expand_links', expand_links), ('backend', backend))
                            if value is not None})
            if expand_query:
                options.update(expand_query=True, query_mapping=query_mapping)
            if lexical_ngrams is not None:
                options['lexical_ngrams'] = lexical_ngrams
            if render_matched:
                options['temporal_filter'] = 'annotate_only'
        started = time.perf_counter()
        reader = recall_module._advanced_retrieval.retrieve if advanced else recall_module.recall
        result = reader(vault, item["query"], limit=top_k, max_tokens=max_tokens, **options)
        elapsed_ms = (time.perf_counter() - started) * 1000
        retrieved = [match["path"] for match in result["matches"]]
        expected = set(item["expected_paths"])
        source_recall = _ratio(len(expected.intersection(retrieved[:3])), len(expected))
        recall_at_k = _ratio(len(expected.intersection(retrieved)), len(expected))
        rank = next(
            (index for index, path in enumerate(retrieved, start=1) if path in expected),
            None,
        )
        reciprocal_rank = (1.0 / rank if rank is not None and rank <= 3 else 0.0) if expected else None
        reciprocal_at_k = (1.0 / rank if rank is not None else 0.0) if expected else None
        # Assess only actual rendered text. Sources that were found but omitted
        # by a context budget do not cover claims. Attribution labels are not
        # factual passages and cannot satisfy path-shaped claim labels.
        context_text = recall_module._normalize(re.sub(r'^\[Source: .*\]\s*$', '', result['context'], flags=re.MULTILINE))
        covered = [claim for claim in item['required_claims'] if recall_module._normalize(claim) in context_text]
        diagnostics = result["diagnostics"]
        complete = diagnostics["status"] == "ok" and diagnostics["search_complete"]
        forbidden_retrieved = [path for path in retrieved if path in item["forbidden_paths"]]
        stale_retrieved = [path for path in retrieved if path in item["stale_paths"]]
        reasons: list[str] = []
        if expected:
            outcome = "expected_source_found" if rank is not None else "expected_source_missed"
            if recall_at_k < 1.0:
                reasons.append("expected_source_missed")
            if len(covered) < len(item['required_claims']):
                reasons.append('required_context_claim_missed')
        elif retrieved:
            outcome = "unexpected_sources_returned"
            reasons.append("no_answer_false_positive")
        else:
            outcome = "no_sources_returned_complete_scan" if complete else "incomplete_search_no_sources"
        if forbidden_retrieved:
            reasons.append("forbidden_source_exposure")
        if stale_retrieved:
            reasons.append("stale_source_exposure")
        row = {
            **item, "retrieved_paths": retrieved,
            "retrieval_options": options,
            "source_recall_at_3": source_recall, "reciprocal_rank_at_3": reciprocal_rank,
            'source_recall_at_k': recall_at_k, 'reciprocal_rank_at_k': reciprocal_at_k,
            'ndcg_at_5': _ndcg(retrieved, item['relevance']), 'covered_claims': covered,
            'returned_context_claim_coverage': _ratio(len(covered), len(item['required_claims'])),
            "outcome": outcome, "forbidden_retrieved_paths": forbidden_retrieved,
            "stale_retrieved_paths": stale_retrieved,
            "top_1_forbidden": bool(retrieved and retrieved[0] in item["forbidden_paths"]),
            "top_1_stale": bool(retrieved and retrieved[0] in item["stale_paths"]),
            "elapsed_ms": round(elapsed_ms, 3),
            "estimated_returned_context_tokens": recall_module.estimate_tokens(result["context"]),
            "diagnostics": diagnostics,
        }
        per_query.append(row)
        if reasons:
            failures.append({
                "id": item["id"],
                "expected_paths": item["expected_paths"],
                "retrieved_paths": retrieved,
                "source_recall_at_3": source_recall,
                "reasons": reasons,
            })
        if not complete:
            evaluation_errors.append({"id": item["id"], **diagnostics})

    total_elapsed = sum(row["elapsed_ms"] for row in per_query)
    # Without inspected marks this is every row, so the default report is unchanged.
    headline = [row for row in per_query if row.get("inspected") is not True]
    report = {
        "benchmark": "bilingual source retrieval and data exposure (not LLM answer/attack quality)",
        "method": 'render_matched' if render_matched else 'advanced' if advanced else 'legacy',
        'top_k': top_k,
        'metric_definitions': {'returned_context_claim_coverage': 'covered literal claims / labeled claims, in rendered context after attribution labels are removed; no semantic answer-quality inference',
            'ndcg_at_5': 'graded gain 2^grade-1, log2(rank+1) discount; top-k=3 leaves ranks 4/5 unreturned',
            'legacy_at_3': 'legacy keys always assess first three matches, including when top_k=5'},
        "interpretation": "Empty results describe this fixture scan only; they do not prove world absence. Exposure means returned source matches, not successful instruction following.",
        **_summary(headline),
        "by_language": {
            key: _summary([row for row in headline if row["language"] == key])
            for key in sorted({row["language"] for row in headline})
        },
        "by_scenario": {
            key: _summary([row for row in headline if row["scenario"] == key])
            for key in sorted({row["scenario"] for row in headline})
        },
        "costs": {
            "max_context_tokens": max_tokens,
            'top_k': top_k,
            'total_source_bytes_read': sum(row['diagnostics']['bytes_read'] for row in per_query),
            'total_mapping_bytes_read': sum(row['diagnostics'].get('query_expansion', {}).get('mapping_bytes_read', 0) for row in per_query),
            "total_elapsed_ms": round(total_elapsed, 3),
            "mean_elapsed_ms": round(total_elapsed / len(per_query), 3),
            "estimated_returned_context_tokens": sum(row["estimated_returned_context_tokens"] for row in per_query),
            "token_measurement": "estimate using recall's Hangul/non-Hangul character coefficients; not model tokenizer or billing tokens",
            "timing_scope": "per-query recall call only; excludes fixture validation and module loading",
        },
        "per_query": per_query,
        "failed_queries": failures,
        "evaluation_errors": evaluation_errors,
    }
    inspected = [row for row in per_query if row.get("inspected") is True]
    if inspected:
        report["inspected_queries"] = {
            "query_count": len(inspected),
            "ids": [row["id"] for row in inspected],
            "summary": _summary(inspected),
            "note": "Queries marked inspected=true were edited after looking at failures. They are excluded "
                    "from the headline metrics, by_language, by_scenario, gates and paired statistics, and "
                    "reported here only; per_query, failed_queries, evaluation_errors and costs keep them. "
                    "If they leave the headline with no query, or with no answerable query while some of "
                    "them are answerable, the recall@3 and MRR gates cannot be evaluated and the command "
                    "exits 1.",
        }
    if prepared is not None:
        report.update(prepared.sections)
    return report


def compare(fixture: Path, *, max_tokens: int = 1500, exact_paired: bool = False, **options) -> dict:
    """Identical labeled queries/budget; legacy ignores advanced fixture options.

    exact_paired=True (opt-in) gives each paired_randomization entry the exact
    p-value when a metric has at most 20 non-zero paired differences.
    """
    if type(exact_paired) is not bool:
        raise ValueError('exact_paired must be boolean')
    top_k = options.get('top_k', 3)
    baseline = evaluate(fixture, max_tokens=max_tokens, top_k=top_k)
    improved = evaluate(fixture, max_tokens=max_tokens, advanced=True, **options)
    report = {'benchmark': 'paired retrieval comparison',
            'interpretation': 'Same queries and context budget; fixture labels supply explicit retrieval options, never expected paths to the retriever. No model or paper score is inferred.',
            'baseline': baseline, 'improved': improved}
    if options.get('expand_query'):
        ablation_options = {key: value for key, value in options.items() if key not in ('expand_query', 'query_mapping')}
        report['expansion_ablation'] = evaluate(fixture, max_tokens=max_tokens, advanced=True, **ablation_options)
    paired_baseline = report.get('expansion_ablation', baseline)
    report['paired_randomization'] = {}
    for metric in ('source_recall_at_k', 'ndcg_at_5', 'returned_context_claim_coverage'):
        # Inspected queries leave paired statistics as they leave headline metrics.
        paired = [(a[metric], b[metric]) for a, b in zip(paired_baseline['per_query'], improved['per_query'])
                  if a[metric] is not None and b[metric] is not None and a.get('inspected') is not True]
        report['paired_randomization'][metric] = paired_test([p[0] for p in paired], [p[1] for p in paired],
                                                             exact=exact_paired)
    report['paired_comparison'] = 'expansion_ablation versus improved' if 'expansion_ablation' in report else 'baseline versus improved'
    return report


RENDER_MATCHED_ALPHA = 0.05
# (metric, paired population, higher_is_better, used by the decision rule).
# Per-query values match _summary(). stale_exposure (any returned rank) was
# added after inspecting fixture outputs, so it is descriptive only.
DECOMPOSITION_METRICS = (
    ('ndcg_at_5', 'answerable queries', True, True),
    ('source_recall_at_k', 'answerable queries', True, True),
    ('stale_top_1', 'queries with stale_paths labels', False, True),
    ('no_answer_false_positive', 'no_answer queries', False, True),
    ('stale_exposure', 'queries with stale_paths labels', False, False),
)


def _decomposition_value(row: dict, metric: str):
    if metric == 'stale_top_1':
        return float(row['top_1_stale']) if row['stale_paths'] else None
    if metric == 'stale_exposure':
        return float(bool(row['stale_retrieved_paths'])) if row['stale_paths'] else None
    if metric == 'no_answer_false_positive':
        return float(bool(row['retrieved_paths'])) if row['expectation'] == 'no_answer' else None
    return row[metric]


def temporal_decomposition(legacy: dict, render_matched: dict, advanced: dict, *,
                           alpha: float = RENDER_MATCHED_ALPHA, seed: int = 1707,
                           permutations: int = 10000, exact_paired: bool = False) -> dict:
    """Split the apparent advanced-legacy gain on the same paired queries.

    display_effect = render_matched - legacy; mechanism_residual = advanced -
    render_matched. Their means add up to apparent_gain = advanced - legacy.
    exact_paired=True runs every paired test through paired_test(exact=True).
    """
    if type(alpha) not in (int, float) or not 0 < alpha < 1:
        raise ValueError('alpha must be between 0 and 1')
    if type(exact_paired) is not bool:
        raise ValueError('exact_paired must be boolean')
    arms = (legacy, render_matched, advanced)
    ids = [[row['id'] for row in arm['per_query']] for arm in arms]
    if ids[0] != ids[1] or ids[0] != ids[2]:
        raise ValueError('decomposition requires identical queries in identical order')
    def paired(baseline, improved):
        return paired_test(baseline, improved, exact=exact_paired, seed=seed, permutations=permutations)

    metrics = {}
    for metric, population, higher_is_better, decision_metric in DECOMPOSITION_METRICS:
        triples = [values for values in (tuple(_decomposition_value(row, metric) for row in rows)
                                         for rows in zip(*(arm['per_query'] for arm in arms))
                                         if rows[0].get('inspected') is not True)
                   if None not in values]
        base, render, improved = ([values[index] for values in triples] for index in range(3))
        apparent, display, residual = paired(base, improved), paired(base, render), paired(render, improved)
        nonzero = sum(a != b for a, b in zip(render, improved))
        # Exact two-sided sign test floor: the two all-same-sign flips always tie.
        floor = 2.0 ** (1 - nonzero) if nonzero else 1.0
        delta = residual['mean_delta']
        direction = ('none' if not delta else
                     'beneficial' if (delta > 0) == higher_is_better else 'harmful')
        metrics[metric] = {
            'population': population, 'paired_query_count': len(triples),
            'higher_is_better': higher_is_better, 'decision_metric': decision_metric,
            'means': {name: _ratio(sum(values), len(values)) for name, values in
                      (('legacy', base), ('render_matched', render), ('advanced', improved))},
            'apparent_gain': apparent, 'display_effect': display, 'mechanism_residual': residual,
            'decomposition_error': (abs(apparent['mean_delta'] - display['mean_delta'] - delta)
                                    if triples else None),
            'mechanism_nonzero_pair_count': nonzero,
            'mechanism_minimum_attainable_p_value': floor,
            'mechanism_power_limited': bool(nonzero) and floor >= alpha,
            'mechanism_residual_distinguishable': residual['p_value'] is not None and residual['p_value'] < alpha,
            'mechanism_direction': direction,
        }

    def selected(direction, *, decision=True):
        return [name for name, row in metrics.items() if row['decision_metric'] is decision
                and row['mechanism_residual_distinguishable'] and row['mechanism_direction'] == direction]

    beneficial, harmful = selected('beneficial'), selected('harmful')
    limited = [name for name, row in metrics.items() if row['decision_metric'] and row['mechanism_power_limited']]
    warnings = [f'{name}: no paired queries in this fixture' for name, row in metrics.items()
                if not row['paired_query_count']]
    warnings += [f'{name}: mechanism residual cannot reach p<{alpha} with '
                 f"{metrics[name]['mechanism_nonzero_pair_count']} nonzero paired differences"
                 for name in limited]
    warnings += [f'{name}: mechanism residual is distinguishable but harmful'
                 for name in (*harmful, *selected('harmful', decision=False))]
    warnings += [f"{label} arm: {len(arm['evaluation_errors'])} incomplete or invalid searches"
                 for label, arm in zip(('legacy', 'render_matched', 'advanced'), arms) if arm['evaluation_errors']]
    return {
        'method': 'render-matched control arm (arXiv 2607.16019): advanced retrieval with '
                  "temporal_filter='annotate_only'; paired sign randomization per metric"
                  + ('; exact enumeration when a test has at most 20 non-zero pairs' if exact_paired else ''),
        'definitions': {
            'display_effect': 'render_matched - legacy: every advanced-arm change except as-of/supersession '
                              'exclusion (backend, link/query expansion, candidate sidecar, annotation fields)',
            'mechanism_residual': 'advanced - render_matched: as-of/supersession exclusion with the same '
                                  'machinery, fields and rendering',
            'apparent_gain': 'advanced - legacy = display_effect + mechanism_residual (paired means)',
        },
        'scorer_scope': 'Scores read returned paths and rendered context only. Temporal annotations are attached '
                        'to matches but not rendered into context or read by this scorer, so a reader-side '
                        'presentation effect is zero by construction here; display_effect is not a reader effect.',
        'alpha': alpha, 'metrics': metrics,
        'decision': {
            'rule': f'If no decision-metric mechanism residual is distinguishable from zero (two-sided '
                    f'p<{alpha}) in the beneficial direction, defer richer ledger typing (contested/resolves states).',
            'decision_metrics': [name for name, row in metrics.items() if row['decision_metric']],
            'beneficial_distinguishable_metrics': beneficial,
            'harmful_distinguishable_metrics': harmful,
            'power_limited_metrics': limited,
            'descriptive_beneficial_distinguishable_metrics': selected('beneficial', decision=False),
            'outcome': 'mechanism_residual_detected' if beneficial else 'defer_richer_ledger_typing',
            'note': 'A detected residual credits the existing coarse as-of/supersession exclusion only; '
                    'richer ledger states need their own render-matched comparison before adoption. '
                    'Power-limited metrics cannot show a residual on this fixture whatever its size.',
        },
        'warnings': warnings,
    }


def with_render_matched(report: dict, fixture: Path, *, max_tokens: int = 1500,
                        exact_paired: bool = False, **options) -> dict:
    """Return a compare() report plus the render-matched arm; existing keys are unchanged."""
    if not isinstance(report, dict) or 'baseline' not in report or 'improved' not in report:
        raise ValueError('render-matched decomposition requires a compare() report')
    if type(exact_paired) is not bool:
        raise ValueError('exact_paired must be boolean')
    arm = evaluate(fixture, max_tokens=max_tokens, render_matched=True, **options)
    return {**report, 'render_matched': arm,
            'temporal_decomposition': temporal_decomposition(report['baseline'], arm, report['improved'],
                                                             exact_paired=exact_paired)}


def compare_render_matched(fixture: Path, *, max_tokens: int = 1500, exact_paired: bool = False,
                           **options) -> dict:
    """compare() plus the opt-in render-matched control arm and decomposition."""
    return with_render_matched(compare(fixture, max_tokens=max_tokens, exact_paired=exact_paired, **options),
                               fixture, max_tokens=max_tokens, exact_paired=exact_paired, **options)


RATE_BOUND_CONFIDENCE = 0.95
# (bound name = optional gate name, summary section, numerator key, denominator key)
RATE_BOUND_FIELDS = (
    ('no_answer_false_positive_rate', 'no_answer', 'false_positive_query_count', 'query_count'),
    ('forbidden_exposure_rate', 'forbidden_sources', 'exposed_query_count', 'labeled_query_count'),
    ('stale_top_1_rate', 'stale_sources', 'top_1_query_count', 'labeled_query_count'),
)
REPORT_ARMS = ('baseline', 'expansion_ablation', 'improved', 'render_matched')
RATE_BOUND_DEFINITIONS = {
    'scope': 'Every summary (overall, by_language, by_scenario; each compare arm) gains rate_upper_bounds for '
             'the no-answer false-positive, forbidden exposure and stale top-1 rates. A rate whose denominator '
             'is zero has no entry.',
    'wilson_upper': 'one-sided Wilson score upper bound; z is the one-sided normal quantile of the confidence',
    'clopper_pearson_upper': 'one-sided exact binomial bound: the p with P(X <= numerator | denominator, p) = '
                             '1 - confidence, by bisection on the binomial CDF; zero events use '
                             '1 - (1 - confidence)^(1/denominator); all events give 1',
    'interpretation': 'Descriptive inputs for human judgement, never gates or exit-code inputs. They assume '
                      'independent queries that share one rate, but fixture queries are designed cases, not a '
                      'sample of real use. Each bound holds on its own, with no multiplicity adjustment.',
}


def _with_key_after(mapping: dict, anchor: str, key: str, value) -> dict:
    """Copy mapping with key (re)placed right after anchor (appended if anchor is absent)."""
    copied = {}
    for name, item in mapping.items():
        if name == key:
            continue
        copied[name] = item
        if name == anchor:
            copied[key] = value
    if key not in copied:
        copied[key] = value
    return copied


def with_rate_bounds(report: dict, *, confidence: float = RATE_BOUND_CONFIDENCE) -> dict:
    """Return an evaluate() or compare() report whose summaries carry one-sided rate upper bounds.

    Opt-in (--rate-bounds). Existing keys and values are unchanged, the input is
    not mutated, and gates keep reading the observed rates only.
    """
    if not isinstance(report, dict):
        raise ValueError('rate bounds require an evaluate() or compare() report')
    stats = _load_eval_stats()
    stats.confidence_alpha(confidence)

    def summary(section: dict) -> dict:
        bounds = {}
        for name, group, numerator, denominator in RATE_BOUND_FIELDS:
            counts = section[group]
            if counts[denominator]:
                bounds[name] = stats.rate_upper_bounds(counts[numerator], counts[denominator],
                                                       confidence=confidence)
        return _with_key_after(section, 'stale_sources', 'rate_upper_bounds', bounds)

    def arm(result: dict) -> dict:
        extended = summary(result)
        for group in ('by_language', 'by_scenario'):
            extended[group] = {key: summary(value) for key, value in result[group].items()}
        return extended

    if 'per_query' in report:
        extended = arm(report)
    elif 'baseline' in report and 'improved' in report:
        extended = {**report, **{name: arm(report[name]) for name in REPORT_ARMS if name in report}}
    else:
        raise ValueError('rate bounds require an evaluate() or compare() report')
    return _with_key_after(extended, 'interpretation', 'rate_upper_bound_definitions',
                           {'confidence': confidence, 'side': 'one-sided upper', **RATE_BOUND_DEFINITIONS})


SUCCESS_BOUND_DEFINITIONS = {
    'scope': 'Every headline summary (overall, by_language, by_scenario; each compare arm) gains '
             'success_lower_bounds for the share of its answerable headline queries whose every expected source '
             'was retrieved within top_k; inspected_queries.summary is left as is. A summary without an answerable '
             'query has no entry.',
    'definition': 'all_expected_found_rate = answerable queries with source_recall_at_k == 1 / answerable queries. '
                  'Among headline (non-inspected) rows its complement is the expected_source_missed reason in '
                  'failed_queries[].reasons, not the per_query outcome of the same name (which means that no '
                  'expected source was found). This is not a lower bound of recall_at_k, which averages fractional '
                  'recall over multi-source queries.',
    'wilson_lower': '1 - the one-sided Wilson score upper bound of the miss rate; exactly 0 when none is found',
    'clopper_pearson_lower': '1 - the one-sided exact Clopper-Pearson upper bound of the miss rate; when every '
                             'query is found this is (1 - confidence)^(1/denominator)',
    'miss_rate_method': 'the Clopper-Pearson method of the miss-rate bound: zero_event_closed_form = every query '
                        'found, all_events = none found (lower bound 0), binomial_cdf_bisection otherwise',
    'interpretation': 'Descriptive inputs for human judgement, never gates or exit-code inputs. They assume '
                      'independent queries that share one rate, but fixture queries are designed cases, not a '
                      'sample of real use. Each bound holds on its own, with no multiplicity adjustment.',
}


def with_success_bounds(report: dict, *, confidence: float = RATE_BOUND_CONFIDENCE) -> dict:
    """Return an evaluate() or compare() report whose summaries carry one-sided success lower bounds.

    Opt-in (--success-bounds). Each count comes from the same headline per_query
    rows as its summary (inspected rows excluded, then filtered by language or
    scenario); a count that disagrees with the summary's answerable_query_count
    raises ValueError. Existing keys and values are unchanged, the input is not
    mutated, and gates never read the bounds.
    """
    if not isinstance(report, dict):
        raise ValueError('success bounds require an evaluate() or compare() report')
    stats = _load_eval_stats()
    stats.confidence_alpha(confidence)

    def summary(section: dict, rows: list) -> dict:
        answerable = [row for row in rows if row['expectation'] == 'answerable']
        if len(answerable) != section['answerable_query_count']:
            raise ValueError('success bounds need the rows of each summary')
        bounds = {}
        if answerable:
            found = sum(row['source_recall_at_k'] == 1.0 for row in answerable)
            miss = stats.rate_upper_bounds(len(answerable) - found, len(answerable), confidence=confidence)
            # With nothing found both lower bounds are exactly 0; float rounding of the Wilson
            # upper bound would otherwise leave a misleading positive residue such as 2.2e-16.
            bounds['all_expected_found_rate'] = {
                'numerator': found, 'denominator': len(answerable), 'observed_rate': found / len(answerable),
                'confidence': confidence, 'wilson_lower': 1 - miss['wilson_upper'] if found else 0.0,
                'clopper_pearson_lower': 1 - miss['clopper_pearson_upper'] if found else 0.0,
                'miss_rate_method': miss['clopper_pearson_method']}
        anchor = 'rate_upper_bounds' if 'rate_upper_bounds' in section else 'stale_sources'
        return _with_key_after(section, anchor, 'success_lower_bounds', bounds)

    def arm(result: dict) -> dict:
        rows = [row for row in result['per_query'] if row.get('inspected') is not True]
        extended = summary(result, rows)
        extended['by_language'] = {key: summary(value, [row for row in rows if row['language'] == key])
                                   for key, value in result['by_language'].items()}
        extended['by_scenario'] = {key: summary(value, [row for row in rows if row['scenario'] == key])
                                   for key, value in result['by_scenario'].items()}
        return extended

    if 'per_query' in report:
        extended = arm(report)
    elif 'baseline' in report and 'improved' in report:
        extended = {**report, **{name: arm(report[name]) for name in REPORT_ARMS if name in report}}
    else:
        raise ValueError('success bounds require an evaluate() or compare() report')
    anchor = 'rate_upper_bound_definitions' if 'rate_upper_bound_definitions' in extended else 'interpretation'
    return _with_key_after(extended, anchor, 'success_lower_bound_definitions',
                           {'confidence': confidence, 'side': 'one-sided lower', **SUCCESS_BOUND_DEFINITIONS})


def rate_bound_gate_notes(gated: dict) -> list[str]:
    """Optional gates that passed although the exact upper bound still exceeds their maximum."""
    bounds = gated.get('rate_upper_bounds', {})
    notes = []
    for name, gate in gated.get('optional_gates', {}).items():
        bound = bounds.get(name)
        if bound and gate['passed'] and bound['clopper_pearson_upper'] > gate['maximum']:
            notes.append(f"{name} passed at {bound['numerator']}/{bound['denominator']} (maximum "
                         f"{gate['maximum']:g}), but its one-sided {bound['confidence']:.0%} upper bound is "
                         f"{bound['clopper_pearson_upper']:.4f} (Clopper-Pearson; Wilson "
                         f"{bound['wilson_upper']:.4f}), so this sample cannot show the rate is at most "
                         f"{gate['maximum']:g}")
    return notes


LEXICAL_NGRAMS_IGNORED_NOTE = (
    "recall evaluation note (non-blocking): --lexical-ngrams had no effect because every query "
    "used the lexical backend, which has no BM25 rank; add --backend bm25 or hybrid")


def lexical_ngrams_ignored(report: dict) -> bool:
    """True when every advanced-arm query ran the lexical backend, which ignores the n-gram rank."""
    rows = report.get('improved', report).get('per_query', [])
    return bool(rows) and all(row['retrieval_options'].get('backend', 'lexical') == 'lexical' for row in rows)


# --- Opt-in: in-place gold set, dev/held-out split and redaction -----------
# None of this runs unless --vault/--goldset, --split, --derive-split or
# --redact is given. No network, model call or file write is involved.


class GoldSetError(ValueError):
    """Gold-set, split or vault-policy problem shown to the user.

    Messages carry only indices, counts, field names and vault-relative paths,
    never query text or other gold-set free text.
    """


class EvaluationInput:
    """Prepared queries for one vault; evaluate()/compare() accept it as fixture.

    ``sections`` are appended to every evaluated arm's report and ``notes`` are
    printed to stderr by main(). Built by load_goldset() and select_split().
    A plain class, not a dataclass: callers load this file with importlib
    without registering it in sys.modules, which dataclasses require.
    """
    __slots__ = ("vault", "queries", "sections", "notes")

    def __init__(self, vault: Path, queries: Sequence[dict], sections: dict | None = None,
                 notes: Sequence[str] = ()):
        self.vault = vault
        self.queries = tuple(queries)
        self.sections = dict(sections or {})
        self.notes = tuple(notes)


def _unique_json_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GoldSetError("JSON object repeats a key")
        result[key] = value
    return result


def _read_bounded_json(path: Path, limit: int, label: str) -> object:
    """Read one regular JSON file of at most ``limit`` bytes.

    json.loads detects UTF-8 (with or without BOM), UTF-16 and UTF-32 from the
    bytes, so a file saved by Windows PowerShell 5.1 redirection also loads.
    """
    try:
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise GoldSetError(f"{label} is not a regular file")
        if metadata.st_size > limit:
            raise GoldSetError(f"{label} exceeds {limit} bytes")
        raw = path.read_bytes()
    except OSError as exc:
        raise GoldSetError(f"{label} is not a readable file") from exc
    if len(raw) > limit:
        raise GoldSetError(f"{label} exceeds {limit} bytes")
    try:
        return json.loads(raw, object_pairs_hook=_unique_json_fields)
    except UnicodeDecodeError as exc:
        raise GoldSetError(f"{label} is not UTF-8, UTF-16 or UTF-32 text") from exc
    except json.JSONDecodeError as exc:
        raise GoldSetError(f"{label} is not valid JSON (line {exc.lineno}, column {exc.colno})") from exc
    except RecursionError as exc:
        raise GoldSetError(f"{label} is nested too deeply") from exc


def _label_key(text: str) -> str:
    # Obsidian matches link targets case-insensitively; macOS can store
    # decomposed Hangul names. Compare NFC + casefold on both sides.
    return unicodedata.normalize("NFC", text).casefold()


def _stem(relative: str) -> str:
    # Listed notes end in .md in any letter case; labels end in lowercase .md.
    return relative.rsplit("/", 1)[-1][:-3]


class _LabelResolver:
    """Resolve gold-set labels against the policy-filtered note listing.

    Order: deny/exclude rules on the label itself (no fallback), exact
    vault-relative path, a unique NFC+casefold path, then a unique filename
    stem (the wikilink target) when the listing is complete. Ambiguous stems
    are never guessed: such a label stays unresolved and can only miss.
    """

    def __init__(self, recall_module, paths: Sequence[str], deny: tuple, exclude: tuple, complete: bool):
        self._recall = recall_module
        self._deny, self._exclude, self._complete = deny, exclude, complete
        self._exact = set(paths)
        self._by_path: dict[str, list[str]] = defaultdict(list)
        self._by_stem: dict[str, list[str]] = defaultdict(list)
        for relative in paths:
            self._by_path[_label_key(relative)].append(relative)
            self._by_stem[_label_key(_stem(relative))].append(relative)

    def resolve(self, label: str) -> dict:
        skipped = self._recall._classified_skip(tuple(label.split("/")), self._deny, self._exclude)
        if skipped:
            return {"match": skipped, "path": None}
        if label in self._exact:
            return {"match": "path", "path": label}
        same_path = self._by_path.get(_label_key(label), [])
        if len(same_path) == 1:
            return {"match": "path", "path": same_path[0]}
        if not self._complete:
            return {"match": "incomplete_inventory", "path": None}
        candidates = sorted(self._by_stem.get(_label_key(_stem(label)), []))
        if len(candidates) == 1:
            return {"match": "stem", "path": candidates[0]}
        if candidates:
            return {"match": "ambiguous_stem", "path": None, "candidate_count": len(candidates),
                    "candidates": candidates[:MAX_AMBIGUOUS_CANDIDATES]}
        return {"match": "missing", "path": None}


_LABEL_WARNING_TEXTS = {
    "missing": "no eligible note has this path or filename stem; the label can only miss",
    "denied": "label is inside a deny zone; it is never read or matched",
    "excluded": "label is inside an excluded directory; retrieval never returns it",
    "incomplete_inventory": "path not found and the note listing is incomplete; stem matching is off",
}
_AMBIGUOUS_WARNING_END = "; label left unresolved, give the full vault-relative path"


def _label_warning(index: int, field_name: str, position: int, found: dict) -> str:
    kind = found["match"]
    if kind == "ambiguous_stem":
        shown = ", ".join(found["candidates"])
        more = ", ..." if found["candidate_count"] > len(found["candidates"]) else ""
        text = (f"filename stem is shared by {found['candidate_count']} eligible notes ({shown}{more})"
                + _AMBIGUOUS_WARNING_END)
    else:
        text = _LABEL_WARNING_TEXTS[kind]
    return f"queries[{index}].{field_name}[{position}]: {text}"


def _resolved_query(index: int, item: dict, resolver: _LabelResolver, counts: Counter, warnings: list) -> dict:
    """Rewrite labels to the notes they resolve to; record each label's match."""
    if "label_matches" in item:
        raise GoldSetError(f"queries[{index}].label_matches is reserved for evaluator output")
    matches: list[dict] = []
    resolved: dict[str, str] = {}

    def resolve(field_name: str, position: int, label: str) -> str:
        found = resolver.resolve(label)
        counts[found["match"]] += 1
        matches.append({"field": field_name, "label": label, **found})
        if found["path"] is None:
            warnings.append(_label_warning(index, field_name, position, found))
        resolved[label] = found["path"] or label
        return resolved[label]

    fields = {}
    for field_name in ("expected_paths", "forbidden_paths", "stale_paths"):
        values = [resolve(field_name, position, label) for position, label in enumerate(item[field_name])]
        if len({value.casefold() for value in values}) != len(values):
            raise GoldSetError(f"queries[{index}].{field_name} labels resolve to the same note")
        fields[field_name] = values
    expected = {value.casefold() for value in fields["expected_paths"]}
    for field_name in ("forbidden_paths", "stale_paths"):
        if expected.intersection(value.casefold() for value in fields[field_name]):
            raise GoldSetError(f"queries[{index}].{field_name} resolves to an expected note")
    relevance: dict[str, int] = {}
    for position, (label, grade) in enumerate(item["relevance"].items()):
        path = resolved[label] if label in resolved else resolve("relevance", position, label)
        if path.casefold() in {key.casefold() for key in relevance}:
            raise GoldSetError(f"queries[{index}].relevance labels resolve to the same note")
        relevance[path] = grade
    return {**item, **fields, "relevance": relevance, "label_matches": matches}


def load_goldset(vault: Path, goldset: Path) -> EvaluationInput:
    """Prepare a user gold set for evaluating a real vault in place.

    The vault is read only through the engine's policy: its validated
    00-meta/vault-config.json (with defaults) fixes deny zones and exclusions
    before any listing. This step lists note paths and reads no note content;
    retrieval reads notes later through the same policy. The gold set uses the
    fixture queries.json schema and may live outside the vault; inside the
    vault it must not be in a deny zone. Labels are resolved once and shared by
    every evaluated arm. Nothing is written or sent anywhere.
    """
    recall = _load_recall_module()
    diagnostics = recall._diagnostics()
    try:
        if not isinstance(vault, Path) or not vault.is_dir():
            raise OSError("vault is not a directory")
        # Same bootstrap as recall: check the given root before resolving it,
        # so a symlink or junction root is refused rather than followed.
        recall.resolve_note_path(vault, recall.CONFIG_RELPATH)
        safe_vault = vault.resolve(strict=True)
    except (OSError, ValueError, RuntimeError) as exc:
        raise GoldSetError("vault must be an existing directory that is not a symlink or junction") from exc
    config = recall._read_config(safe_vault, diagnostics)
    if config is None:
        raise GoldSetError(f"vault configuration unavailable ({diagnostics['status']})")
    deny = tuple(config.get("deny_zones") or ())
    exclude = (*tuple(config.get("exclude_dirs") or ()), ".git")
    try:
        goldset_path = goldset.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise GoldSetError("gold set file not found") from exc
    try:
        inside = goldset_path.relative_to(safe_vault).parts
    except ValueError:
        inside = None
    if inside is not None and any(recall._path_matches_rule(inside, zone) for zone in deny):
        raise GoldSetError("gold set file is inside a deny zone of this vault; it was not read")
    data = _read_bounded_json(goldset_path, MAX_QUERIES_BYTES, "gold set")
    if not isinstance(data, dict) or not isinstance(data.get("queries"), list) or len(data["queries"]) < 12:
        raise GoldSetError("gold set must be an object whose queries list has at least 12 labeled queries")
    try:
        queries = _checked_queries(data)
    except ValueError as exc:
        # Schema messages name indices and fields only, never label text.
        raise GoldSetError(str(exc)) from exc
    listing = [relative for relative, _ in recall._markdown_paths(safe_vault, deny, exclude, diagnostics)]
    if len(listing) > recall.MAX_FILES:
        # Same cut as retrieval, which considers only the first MAX_FILES notes of this
        # order: no label resolves to a note it can never return, and the incomplete
        # listing turns stem matching off.
        diagnostics["omitted_file_limit"] = len(listing) - recall.MAX_FILES
        recall._omit(diagnostics, "file_count_limit")
        listing = listing[:recall.MAX_FILES]
    complete = bool(diagnostics["search_complete"])
    warnings: list[str] = [] if complete else [
        "note listing incomplete (" + ", ".join(diagnostics["omissions"]) + "); labels whose path was "
        "not found are not matched by filename stem"]
    counts: Counter = Counter()
    resolver = _LabelResolver(recall, listing, deny, exclude, complete)
    prepared = tuple(_resolved_query(index, item, resolver, counts, warnings)
                     for index, item in enumerate(queries))
    section = {
        "mode": "in_place_vault",
        # Of the parsed JSON (sorted keys, compact): line endings and BOMs do not change it.
        "goldset_canonical_sha256": _digest(data),
        "query_count": len(prepared),
        "listed_note_count": len(listing),
        "listing_complete": complete,
        "label_match_counts": {kind: counts[kind] for kind in LABEL_MATCH_KINDS},
        "warnings": warnings[:MAX_GOLDSET_WARNINGS],
        "warnings_omitted": max(0, len(warnings) - MAX_GOLDSET_WARNINGS),
        "matching": "Labels resolve once against the policy-filtered note listing, before retrieval: exact "
                    "vault-relative path, then a unique case- and Unicode-normalization-insensitive path, then a "
                    "unique filename stem (wikilink target). Denied, excluded, ambiguous, missing or unverifiable "
                    "labels stay unresolved and can only miss; per_query[].label_matches records every label.",
        "interpretation": "Results cover only these user-supplied queries on this vault at run time (n = "
                          "query_count), not general retrieval quality. The report goes to standard output only; "
                          "nothing is written or sent over the network.",
    }
    resolved_count = counts["path"] + counts["stem"]
    notes = (f"goldset evaluation: {len(prepared)} queries, {len(listing)} listed notes; labels by path "
             f"{counts['path']}, by stem {counts['stem']}, unresolved {sum(counts.values()) - resolved_count}",
             *(f"goldset warning (non-blocking): {warning}" for warning in warnings[:MAX_GOLDSET_WARNINGS]))
    if len(warnings) > MAX_GOLDSET_WARNINGS:
        notes += (f"goldset warning (non-blocking): {len(warnings) - MAX_GOLDSET_WARNINGS} more not listed",)
    return EvaluationInput(vault=vault, queries=prepared, sections={"goldset": section}, notes=notes)


def load_split(path: Path) -> dict:
    """Read a split definition: disjoint lists of unique query ids under dev and heldout."""
    data = _read_bounded_json(path, MAX_SPLIT_BYTES, "split file")
    if not isinstance(data, dict):
        raise GoldSetError("split file must be a JSON object")
    for name in SPLIT_NAMES:
        values = data.get(name)
        if (not isinstance(values, list)
                or any(not isinstance(value, str) or not value.strip() for value in values)
                or len(set(values)) != len(values)):
            raise GoldSetError(f"split file {name} must list unique non-empty query ids")
    if set(data["dev"]).intersection(data["heldout"]):
        raise GoldSetError("split file lists a query id in both dev and heldout")
    return data


def _prepared(source) -> EvaluationInput:
    if isinstance(source, EvaluationInput):
        return source
    return EvaluationInput(vault=source / "vault", queries=tuple(_load_queries(source)))


def select_split(source, split: str, split_file: Path) -> EvaluationInput:
    """Keep only the queries the split file lists under ``split`` and record n per split."""
    if split not in SPLIT_NAMES:
        raise GoldSetError("split must be dev or heldout")
    prepared = _prepared(source)
    definition = load_split(split_file)
    members = {name: set(definition[name]) for name in SPLIT_NAMES}
    ids = [item["id"] for item in prepared.queries]
    counts = {name: sum(query_id in members[name] for query_id in ids) for name in SPLIT_NAMES}
    unassigned = sum(not any(query_id in members[name] for name in SPLIT_NAMES) for query_id in ids)
    selected = tuple(item for item in prepared.queries if item["id"] in members[split])
    if not selected:
        raise GoldSetError(f"split {split} selects no queries")
    seed = definition.get("seed")
    section = {
        "name": split,
        "definition_canonical_sha256": _digest(definition),
        "seed": seed if type(seed) is int else None,
        "query_count_by_split": {"dev": counts["dev"], "heldout": counts["heldout"], "unassigned": unassigned},
        "evaluated_query_count": len(selected),
        "evaluated_inspected_query_count": sum(item.get("inspected") is True for item in selected),
        "listed_ids_not_in_queries": sum(len(members[name].difference(ids)) for name in SPLIT_NAMES),
        "interpretation": "Every metric in this report covers only this split (n = evaluated_query_count). "
                          "Unassigned queries are not evaluated. Tune on dev; report heldout separately.",
    }
    note = (f"recall evaluation split: {split} n={len(selected)} "
            f"(dev n={counts['dev']}, heldout n={counts['heldout']}, unassigned n={unassigned})")
    return EvaluationInput(vault=prepared.vault, queries=selected,
                           sections={**prepared.sections, "split": section}, notes=(*prepared.notes, note))


def derive_split(queries: Sequence[dict], *, seed: int = SPLIT_SEED, previous: dict | None = None) -> dict:
    """Derive the dev/heldout split by SPLIT_RULE; deterministic on every platform.

    With ``previous`` (a split definition), ids it lists keep their split and
    only unlisted ids are divided, so growing a gold set never moves an
    existing dev query into heldout.
    """
    if type(seed) is not int or seed < 0:
        raise GoldSetError("split seed must be a non-negative integer")
    kept = {query_id: name for name in SPLIT_NAMES for query_id in (previous or {}).get(name, ())}
    strata: dict[tuple, list[str]] = defaultdict(list)
    for item in queries:
        strata[(item["language"], item["scenario"])].append(item["id"])

    def order(query_id: str):
        return hashlib.sha256(f"{seed}:{query_id}".encode("utf-8")).hexdigest(), query_id

    present = {item["id"] for item in queries}
    assigned = {query_id: name for query_id, name in kept.items() if query_id in present}
    rows = []
    for (language, scenario), ids in sorted(strata.items()):
        new = sorted((query_id for query_id in ids if query_id not in kept), key=order)
        for position, query_id in enumerate(new):
            assigned[query_id] = "heldout" if position < len(new) // 2 else "dev"
        rows.append({"language": language, "scenario": scenario, "query_count": len(ids),
                     "heldout_count": sum(assigned[query_id] == "heldout" for query_id in ids)})
    result = {
        "split_version": 1,
        "rule": SPLIT_RULE,
        "seed": seed,
        "heldout_fraction": "floor(n/2) per stratum",
        "strata_fields": ["language", "scenario"],
        "query_count": len(assigned),
        "strata": rows,
        "dev": sorted(query_id for query_id, name in assigned.items() if name == "dev"),
        "heldout": sorted(query_id for query_id, name in assigned.items() if name == "heldout"),
    }
    if previous is not None:
        retained = sum(query_id in present for query_id in kept)
        result["incremental"] = {"kept": retained, "added": len(assigned) - retained,
                                 "dropped": len(kept) - retained}
    return result


_ROW_RESULT_KEYS = frozenset({
    "retrieved_paths", "source_recall_at_3", "reciprocal_rank_at_3", "source_recall_at_k",
    "reciprocal_rank_at_k", "ndcg_at_5", "returned_context_claim_coverage", "outcome",
    "forbidden_retrieved_paths", "stale_retrieved_paths", "top_1_forbidden", "top_1_stale",
    "elapsed_ms", "estimated_returned_context_tokens"})
_ROW_STRUCTURED_LABEL_KEYS = frozenset({
    "language", "expectation", "expected_paths", "forbidden_paths", "stale_paths", "relevance",
    "inspected", "label_matches"})
_OPTION_ENUMS = {"backend": ("lexical", "bm25", "hybrid"), "lexical_ngrams": ("hangul2",),
                 "temporal_filter": ("exclude", "annotate_only")}
_AS_OF_SHAPE = re.compile(r"[0-9TZ:+.\-]{1,64}")
REDACTION_DESCRIPTION = {
    "method": "sha256 hex of the UTF-8 text; a non-text value is hashed as its compact sorted JSON",
    "replaced": ["id (also in failed_queries, evaluation_errors and inspected_queries)", "query",
                 "scenario (also by_scenario keys)", "required_claims and covered_claims (each claim)",
                 "every other free-text gold-set field, such as label_rationale",
                 "query-expansion terms, triggers, replacements and added terms"],
    "kept": "vault-relative paths and labels, languages, expectations, numbers, booleans, dates and option names",
    "limits": "A hash is not encryption: short or guessable text can be recovered by hashing candidates. "
              "File names stay readable in paths.",
}
REDACTION_KEY_MIN_BYTES = 32
REDACTION_KEY_MAX_BYTES = 64 * 1024
# Directory entries examined when looking for the report file next to the key.
MAX_KEY_DIRECTORY_ENTRIES = 4096
_KEYED_REPLACED = [
    *REDACTION_DESCRIPTION["replaced"],
    "vault-relative paths, each hashed whole: expected, forbidden, stale and retrieved paths, relevance keys, "
    "label_matches labels, paths and candidates, external candidates, excluded sources, the query mapping "
    "and paths in diagnostics, including the temporal superseded_by link",
    "temporal valid_from, valid_until, checked_at and superseded_at values that are not date-shaped",
    "fingerprints (goldset_canonical_sha256, definition_canonical_sha256, source and mapping sha256): "
    "the keyed hash of their sha256 hex",
    "candidate paths inside gold-set warnings; a warning of an unknown shape is hashed whole",
]
_KEYED_LIMITS = (
    "Pseudonymisation, not encryption or anonymisation. Equal values get equal tokens within this report, so "
    "repeats and frequencies stay visible. Whole values are hashed, not single words. Rows keep the gold-set "
    "order, so whoever holds the gold set, or part of it, can link rows, and the path tokens labelled in them, "
    "by position; sort or shuffle rows before sharing to avoid that. The report never records the key or a "
    "fingerprint of it.")
KEYED_REDACTION_DESCRIPTION = {
    "method": "HMAC-SHA256 hex, under a secret key of at least 32 bytes, of the UTF-8 text; a non-text value "
              "is hashed as its compact sorted JSON",
    "replaced": _KEYED_REPLACED,
    "kept": "languages, expectations, label match kinds, numbers, booleans, dates, option names and enums",
    "limits": _KEYED_LIMITS + " Without the key, tokens cannot be recomputed and a guess cannot be confirmed; "
              "whoever holds the key can. Keeping the key next to the vault or the report defeats this.",
}
EPHEMERAL_REDACTION_DESCRIPTION = {
    **KEYED_REDACTION_DESCRIPTION,
    "method": "HMAC-SHA256 hex, under a random 32-byte key drawn for this run and never stored, of the UTF-8 "
              "text; a non-text value is hashed as its compact sorted JSON",
    "limits": _KEYED_LIMITS + " The key is gone after the run: nobody, the operator included, can recompute a "
              "token or confirm a guess, and tokens from two runs cannot be compared.",
}
# Keyed modes: fields whose string values (or list items) are vault-relative
# paths, dictionaries keyed by paths, and sha256 fingerprints.
_PATH_FIELDS = frozenset({
    "path", "source_path", "label", "query_mapping", "external_candidates", "candidates", "source_paths",
    "excluded_sources", "expected_paths", "forbidden_paths", "stale_paths", "retrieved_paths",
    "forbidden_retrieved_paths", "stale_retrieved_paths",
    # Temporal diagnostics: the raw successor wikilink, which names a note and can name folders.
    "superseded_by"})
# Keyed modes: raw temporal frontmatter values stay readable only when date-shaped.
_DATE_FIELDS = frozenset({"valid_from", "valid_until", "checked_at", "superseded_at"})
_PATH_KEYED_MAPS = frozenset({"relevance"})
_FINGERPRINT_FIELDS = frozenset({"sha256", "source_sha256", "goldset_canonical_sha256",
                                 "definition_canonical_sha256"})
_WARNING_LABEL = r"queries\[\d+\]\.(?:expected_paths|forbidden_paths|stale_paths|relevance)\[\d+\]: "
_FIXED_WARNING = re.compile(_WARNING_LABEL + "(?:" + "|".join(
    re.escape(text) for text in _LABEL_WARNING_TEXTS.values()) + ")")
_AMBIGUOUS_WARNING = re.compile("(" + _WARNING_LABEL + r"filename stem is shared by \d+ eligible notes) \(.*\)"
                                + re.escape(_AMBIGUOUS_WARNING_END), re.DOTALL)
_LISTING_WARNING = re.compile(r"note listing incomplete \([a-z0-9_, ]*\); labels whose path was not found "
                              r"are not matched by filename stem")
_GOLDSET_NOTE_PREFIX = "goldset warning (non-blocking): "
_OMITTED_WARNINGS_NOTE = re.compile(r"\d+ more not listed")


def _canonical_text(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_text(value).encode("utf-8")).hexdigest()


def _keyed_digest(key: bytes):
    """HMAC-SHA256 digest function; a closure, so no repr ever shows the key."""
    if type(key) is not bytes:
        raise ValueError("redaction key must be bytes")
    if len(key) < REDACTION_KEY_MIN_BYTES:
        raise ValueError(f"redaction key must hold at least {REDACTION_KEY_MIN_BYTES} bytes")

    def digest(value: object) -> str:
        return hmac.new(key, _canonical_text(value).encode("utf-8"), hashlib.sha256).hexdigest()
    return digest


def _redacted_candidates(value: object, digest=_digest) -> object:
    """External candidates are paths and ranks; any other provider text is hashed."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return {key: (_redacted_candidates(item, digest) if key == "candidates" else digest(item))
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [item if isinstance(item, str) else
                {key: (entry if key in ("path", "rank") else digest(entry)) for key, entry in item.items()}
                if isinstance(item, dict) else digest(item) for item in value]
    return digest(value)


def _redacted_option(key: str, value: object, digest=_digest) -> object:
    if key in ("query_mapping", "excluded_sources"):
        return value  # vault-relative paths
    if key == "external_candidates":
        return _redacted_candidates(value, digest)
    if key == "as_of":
        return value if isinstance(value, str) and _AS_OF_SHAPE.fullmatch(value) else digest(value)
    if key == "expand_links":
        return value if type(value) is int else digest(value)
    if key == "expand_query":
        return value if type(value) is bool else digest(value)
    if key in _OPTION_ENUMS:
        return value if isinstance(value, str) and value in _OPTION_ENUMS[key] else digest(value)
    return digest(value)


def _redacted_diagnostics(diagnostics: object, digest=_digest) -> object:
    expansion = diagnostics.get("query_expansion") if isinstance(diagnostics, dict) else None
    if not isinstance(expansion, dict):
        return diagnostics

    def entry(value: dict) -> dict:
        changed = {key: digest(value[key]) for key in ("trigger", "replacement") if key in value}
        if "added_terms" in value:
            changed["added_terms"] = [digest(term) for term in value["added_terms"]]
        return {**value, **changed}

    expansion = {**expansion, "terms": [digest(term) for term in expansion.get("terms", ())],
                 "expansions": [entry(value) for value in expansion.get("expansions", ())],
                 "ambiguities": [entry(value) for value in expansion.get("ambiguities", ())]}
    return {**diagnostics, "query_expansion": expansion}


def _redacted_row(row: dict, digest=_digest) -> dict:
    redacted = {}
    for key, value in row.items():
        if key in ("required_claims", "covered_claims"):
            redacted[key] = [digest(claim) for claim in value]
        elif key == "diagnostics":
            redacted[key] = _redacted_diagnostics(value, digest)
        elif key == "retrieval_options":
            redacted[key] = {name: _redacted_option(name, option, digest) for name, option in value.items()}
        elif key in ("as_of", "expand_links", "backend", "external_candidates", "excluded_sources"):
            redacted[key] = _redacted_option(key, value, digest)
        elif key in _ROW_RESULT_KEYS or key in _ROW_STRUCTURED_LABEL_KEYS:
            redacted[key] = value
        else:
            # id, query, scenario, label_rationale and any unknown field.
            redacted[key] = digest(value)
    return redacted


def _redacted_evaluation(report: dict, digest=_digest) -> dict:
    redacted = {**report,
                "by_scenario": {digest(key): value for key, value in report["by_scenario"].items()},
                "per_query": [_redacted_row(row, digest) for row in report["per_query"]],
                "failed_queries": [{**row, "id": digest(row["id"])} for row in report["failed_queries"]],
                "evaluation_errors": [_redacted_diagnostics({**row, "id": digest(row["id"])}, digest)
                                      for row in report["evaluation_errors"]]}
    if "inspected_queries" in report:
        redacted["inspected_queries"] = {**report["inspected_queries"],
                                         "ids": [digest(value) for value in report["inspected_queries"]["ids"]]}
    return redacted


def _keyed_warning(warning: object, digest) -> str:
    """A gold-set warning without its candidate paths; any other shape is hashed whole."""
    if isinstance(warning, str):
        if _FIXED_WARNING.fullmatch(warning) or _LISTING_WARNING.fullmatch(warning):
            return warning
        ambiguous = _AMBIGUOUS_WARNING.fullmatch(warning)
        if ambiguous:
            return ambiguous.group(1) + " (paths pseudonymised in label_matches)" + _AMBIGUOUS_WARNING_END
    return digest(warning)


def _keyed_note(note: str, digest) -> str:
    """Keyed modes: the stderr form of a gold-set warning gets the same treatment."""
    if not note.startswith(_GOLDSET_NOTE_PREFIX):
        return note  # counts and split sizes only
    rest = note[len(_GOLDSET_NOTE_PREFIX):]
    if _OMITTED_WARNINGS_NOTE.fullmatch(rest):
        return note
    return _GOLDSET_NOTE_PREFIX + _keyed_warning(rest, digest)


def _keyed_paths(value: object, digest, field: str | None = None) -> object:
    """Replace path and fingerprint values, wherever they sit, with keyed hashes.

    Runs after the text redaction, which keeps these fields readable. Each path
    is hashed whole: a directory prefix would leave folder names (people,
    projects) readable, while whole-path tokens still keep equality within the
    report, which is what comparing labels with retrieved paths needs.
    """
    if isinstance(value, dict):
        if field in _PATH_KEYED_MAPS:
            return {digest(name): _keyed_paths(item, digest) for name, item in value.items()}
        redacted = {name: _keyed_paths(item, digest, name) for name, item in value.items()}
        if field == "goldset" and isinstance(redacted.get("warnings"), list):
            redacted["warnings"] = [_keyed_warning(warning, digest) for warning in redacted["warnings"]]
        return redacted
    if isinstance(value, list):
        return [_keyed_paths(item, digest, field) for item in value]
    if isinstance(value, str) and (field in _PATH_FIELDS or field in _FINGERPRINT_FIELDS):
        return digest(value)
    if isinstance(value, str) and field in _DATE_FIELDS and not _AS_OF_SHAPE.fullmatch(value):
        return digest(value)
    return value


def redact_report(report: dict, *, key: bytes | None = None, ephemeral: bool = False) -> dict:
    """Replace gold-set free text with sha256 hex; paths, numbers and enums remain.

    Works on one evaluate() report or on any report whose arms (baseline,
    improved, expansion_ablation, render_matched, ...) are evaluate() reports.
    With ``key`` (bytes, at least 32) the same fields, every vault-relative
    path and the sha256 fingerprints become HMAC-SHA256 hex under that key;
    ``ephemeral`` only records that the key was drawn for this run. The key
    and any fingerprint of it are never part of the result.
    """
    if key is None and ephemeral:
        raise ValueError("ephemeral redaction needs a key")
    digest = _digest if key is None else _keyed_digest(key)
    if "per_query" in report:
        redacted = _redacted_evaluation(report, digest)
    else:
        redacted = {name: (_redacted_evaluation(value, digest) if isinstance(value, dict) and "per_query" in value
                           else value) for name, value in report.items()}
    if key is None:
        redacted["redaction"] = REDACTION_DESCRIPTION
        mode, algorithm = "unkeyed", "sha256"
    else:
        redacted = _keyed_paths(redacted, digest)
        redacted["redaction"] = EPHEMERAL_REDACTION_DESCRIPTION if ephemeral else KEYED_REDACTION_DESCRIPTION
        mode, algorithm = ("ephemeral" if ephemeral else "keyed"), "hmac-sha256"
    redacted["redaction_mode"] = mode
    redacted["redaction_algorithm"] = algorithm
    return redacted


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


_EXTENDED_PREFIX = "\\\\?\\"
_EXTENDED_UNC_PREFIX = _EXTENDED_PREFIX + "UNC\\"


def _plain_spelling(path: Path) -> Path:
    """Drop a Windows extended-length prefix (plain or UNC) so two spellings compare equal."""
    text = str(path)
    if text.startswith(_EXTENDED_UNC_PREFIX):
        return Path("\\\\" + text[len(_EXTENDED_UNC_PREFIX):])
    if text.startswith(_EXTENDED_PREFIX):
        return Path(text[len(_EXTENDED_PREFIX):])
    return path


def _identity(path: Path) -> tuple | None:
    try:
        metadata = os.stat(path)
    except (OSError, ValueError):
        return None
    return (metadata.st_dev, metadata.st_ino) if metadata.st_ino else None


def _key_inside(path: Path, evaluated: Path) -> bool:
    """Is the key file, under any spelling, inside the evaluated tree?

    Compares the lexical absolute path and the resolved path, each without a
    Windows extended-length prefix, and then the file identity (st_dev, st_ino)
    of every ancestor of both key paths with that of the evaluated root,
    which also covers other spellings of the same directory (an admin share,
    an 8.3 short name). Only metadata is read; the key file is not opened.
    """
    locations, roots = [Path(os.path.abspath(path))], [Path(os.path.abspath(evaluated))]
    for candidate, target in ((path, locations), (evaluated, roots)):
        try:
            target.append(candidate.resolve())
        except (OSError, RuntimeError):
            pass
    locations = [_plain_spelling(location) for location in locations]
    roots = [_plain_spelling(root) for root in roots]
    if any(_within(location, root) for location in locations for root in roots):
        return True
    root_ids = {identity for identity in map(_identity, roots) if identity is not None}
    return bool(root_ids) and any(_identity(ancestor) in root_ids
                                  for location in locations for ancestor in (location, *location.parents))


def read_redaction_key(path: Path, evaluated: Path) -> bytes:
    """Read the --redact-key-file bytes; refuse a key inside the evaluated tree.

    ``evaluated`` is the --vault directory, or the fixture directory (its vault
    and labels). The lexical absolute path, the resolved path and the file
    identity of the resolved path's ancestors are checked before the key is
    read (see _key_inside). Messages never name the file or echo it.
    """
    if _key_inside(path, evaluated):
        raise GoldSetError("redaction key file is inside the evaluated vault; keep the key outside the vault "
                           "and away from the report")
    unreadable = (f"redaction key file is not a readable regular file of at most "
                  f"{REDACTION_KEY_MAX_BYTES} bytes")
    try:
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > REDACTION_KEY_MAX_BYTES:
            raise GoldSetError(unreadable)
        with path.open("rb") as handle:
            key = handle.read(REDACTION_KEY_MAX_BYTES + 1)
    except OSError as exc:
        raise GoldSetError(unreadable) from exc
    if len(key) > REDACTION_KEY_MAX_BYTES:
        raise GoldSetError(unreadable)
    if len(key) < REDACTION_KEY_MIN_BYTES:
        raise GoldSetError(f"redaction key file must hold at least {REDACTION_KEY_MIN_BYTES} bytes")
    return key


def key_near_report(path: Path, stream=None) -> bool:
    """Best effort: is the key in the working directory or next to a stdout file?

    Standard output that is a regular file is found by its file identity among
    at most MAX_KEY_DIRECTORY_ENTRIES entries of the key's directory. A pipe
    (including Windows PowerShell 5.1 redirection) can only be judged by the
    working directory.
    """
    try:
        directory = path.resolve().parent
        if Path(os.getcwd()).resolve() == directory:
            return True
        output = os.fstat((sys.stdout if stream is None else stream).fileno())
    except (AttributeError, OSError, ValueError, RuntimeError):
        return False
    if not stat.S_ISREG(output.st_mode) or not output.st_ino:
        return False
    try:
        with os.scandir(directory) as entries:
            for count, entry in enumerate(entries):
                if count >= MAX_KEY_DIRECTORY_ENTRIES:
                    break
                if entry.inode() == output.st_ino and os.stat(entry.path).st_dev == output.st_dev:
                    return True
    except OSError:
        return False
    return False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    # None means the checked-in default fixture; main() resolves it, so an
    # explicit --fixture can be told apart from the gold-set mode.
    parser.add_argument("--fixture", type=Path, default=None,
                        help="directory with queries.json and vault/ (default: tests/fixtures/recall)")
    parser.add_argument("--min-recall-at-3", type=float, default=0.85)
    parser.add_argument("--min-mrr", type=float, default=0.75)
    parser.add_argument("--max-tokens", type=int, default=1500,
                        help="returned context budget (token estimate)")
    parser.add_argument('--top-k', type=int, choices=(3, 5), default=3)
    parser.add_argument('--expand-query', action='store_true')
    parser.add_argument('--query-mapping', help='vault-relative plain mapping; requires --expand-query')
    parser.add_argument("--max-no-answer-false-positive-rate", type=float, default=None)
    parser.add_argument("--max-forbidden-exposure-rate", type=float, default=None)
    parser.add_argument("--max-stale-top-1-rate", type=float, default=None)
    parser.add_argument('--advanced', action='store_true', help='apply per-query advanced options')
    parser.add_argument('--compare', action='store_true', help='legacy versus advanced; gates apply to improved')
    parser.add_argument('--render-matched', action='store_true',
                        help='with --compare: add the annotate-only temporal control arm and its '
                             'display/mechanism decomposition (observational; gates still apply to improved)')
    parser.add_argument('--as-of', help='override all per-query time points')
    parser.add_argument('--expand-links', type=int, choices=(0, 1, 2))
    parser.add_argument('--backend', choices=('lexical', 'bm25', 'hybrid'))
    parser.add_argument('--lexical-ngrams', choices=('hangul2',),
                        help='opt-in Hangul bigram BM25 rank for advanced retrieval; requires --advanced or --compare')
    parser.add_argument('--exact-paired', action='store_true',
                        help='with --compare: exact sign-flip p-values when a paired test has at most 20 non-zero '
                             'differences (otherwise the seeded Monte Carlo test); records method, '
                             'nonzero_pair_count and minimum_attainable_p_value (observational)')
    parser.add_argument('--rate-bounds', action='store_true',
                        help='add one-sided 95%% upper bounds (Wilson, Clopper-Pearson) with numerator and '
                             'denominator for the no-answer false-positive, forbidden exposure and stale top-1 '
                             'rates (descriptive; gates and exit code are unchanged)')
    parser.add_argument('--success-bounds', action='store_true',
                        help='add one-sided 95%% lower bounds (Wilson, Clopper-Pearson) with numerator and '
                             'denominator for the share of answerable queries whose every expected source was '
                             'retrieved; not a bound of recall@k (descriptive; gates and exit code are unchanged)')
    goldset = parser.add_argument_group('in-place gold set (opt-in, local output only)')
    goldset.add_argument('--vault', type=Path,
                         help='real vault evaluated in place with --goldset; read through its '
                              '00-meta/vault-config.json policy (deny zones, exclusions); nothing is written or sent')
    goldset.add_argument('--goldset', type=Path,
                         help='gold-set JSON in the fixture queries.json schema; labels match by vault-relative '
                              'path, then by a unique filename stem (wikilink target); cannot be combined with --fixture')
    goldset.add_argument('--redact', action='store_true',
                         help='replace ids, queries, scenarios, claims and other gold-set free text in the report '
                              'with unkeyed sha256 hex; vault-relative paths remain. Short or guessable text can '
                              'be recovered by hashing candidates; prefer --redact-key-file or --redact-ephemeral')
    keyed = goldset.add_mutually_exclusive_group()
    keyed.add_argument('--redact-key-file', type=Path, metavar='FILE',
                       help='implies --redact: HMAC-SHA256 with the secret key in FILE (at least 32 random bytes, '
                            'outside the vault and away from the report) over the same text and over every '
                            'vault-relative path; the key and any fingerprint of it are never printed. Equal values '
                            'still get equal tokens, so repeats and frequencies inside one report stay visible; '
                            'without the key, tokens cannot be recomputed or a guess confirmed, but rows keep the '
                            'gold-set order, so whoever holds the gold set can link them by position. Whole values '
                            'are hashed, words are not hashed one by one')
    keyed.add_argument('--redact-ephemeral', action='store_true',
                       help='implies --redact: like --redact-key-file with a random 32-byte key drawn for this run '
                            'and never stored, so nobody can recompute the tokens and two runs cannot be compared; '
                            'rows keep the gold-set order, and repeats and frequencies inside one report stay visible')
    split = parser.add_argument_group('dev/held-out split (opt-in)')
    split.add_argument('--split', choices=SPLIT_NAMES,
                       help='evaluate only this side of the split definition and report n per split')
    split.add_argument('--split-file', type=Path,
                       help='split definition JSON (default: <fixture>/split.json; required with --goldset)')
    split.add_argument('--derive-split', action='store_true',
                       help=f'print the split derived by the documented stratified rule (seed {SPLIT_SEED}) and '
                            'exit without retrieval; with --split-file, keep its assignments and divide only new ids')
    return parser


def _mode_error(args: argparse.Namespace) -> str | None:
    if args.fixture is not None and (args.vault is not None or args.goldset is not None):
        return "--fixture cannot be combined with --vault or --goldset"
    if (args.vault is None) != (args.goldset is None):
        return "--vault and --goldset must be given together"
    if args.derive_split and (args.split is not None or args.redact):
        return "--derive-split cannot be combined with --split or --redact"
    if args.split_file is not None and args.split is None and not args.derive_split:
        return "--split-file requires --split or --derive-split"
    if args.split is not None and args.goldset is not None and args.split_file is None:
        return "--split with --goldset requires --split-file"
    return None


def _inspected_gate_gap(report: dict) -> str | None:
    """Name what inspected=true marks left the gated headline without, or None.

    As in v0.24.0, the recall@3 and MRR gates are skipped for a query set
    without answerable queries. They cannot pass when the marks themselves
    leave the headline with no query at all, or with no answerable query
    while inspected answerable queries exist. Always None without marks.
    """
    inspected = report.get("inspected_queries")
    if inspected is None:
        return None
    if not report["query_count"]:
        return "no query"
    if not report["answerable_query_count"] and inspected["summary"]["answerable_query_count"]:
        return "no answerable query"
    return None


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.redact_key_file is not None or args.redact_ephemeral:
        args.redact = True
    thresholds = (args.min_recall_at_3, args.min_mrr, args.max_no_answer_false_positive_rate,
                  args.max_forbidden_exposure_rate, args.max_stale_top_1_rate)
    if any(value is not None and not 0.0 <= value <= 1.0 for value in thresholds):
        print("evaluation thresholds must be between 0 and 1", file=sys.stderr)
        return 2
    if args.lexical_ngrams is not None and not (args.advanced or args.compare):
        print("--lexical-ngrams requires --advanced or --compare", file=sys.stderr)
        return 2
    if args.render_matched and not args.compare:
        print("--render-matched requires --compare", file=sys.stderr)
        return 2
    if args.exact_paired and not args.compare:
        print("--exact-paired requires --compare", file=sys.stderr)
        return 2
    mode_error = _mode_error(args)
    if mode_error is not None:
        print(mode_error, file=sys.stderr)
        return 2
    fixture = DEFAULT_FIXTURE if args.fixture is None else args.fixture
    source = fixture
    redaction_key = None
    if args.redact_key_file is not None:
        try:
            redaction_key = read_redaction_key(args.redact_key_file,
                                               fixture if args.vault is None else args.vault)
        except GoldSetError as exc:
            print(f"recall evaluation unavailable: {exc}", file=sys.stderr)
            return 2
        if key_near_report(args.redact_key_file):
            print("recall evaluation warning (non-blocking): the redaction key file is in the report's output "
                  "directory or the working directory; keep the key away from the report", file=sys.stderr)
    elif args.redact_ephemeral:
        redaction_key = secrets.token_bytes(REDACTION_KEY_MIN_BYTES)
    try:
        if args.goldset is not None:
            source = load_goldset(args.vault, args.goldset)
        if args.derive_split:
            previous = None if args.split_file is None else load_split(args.split_file)
            seed = SPLIT_SEED if previous is None else previous.get("seed", SPLIT_SEED)
            derived = derive_split(_prepared(source).queries, seed=seed, previous=previous)
            print(json.dumps(derived, ensure_ascii=False, indent=2))
            return 0
        if args.split is not None:
            split_file = fixture / "split.json" if args.split_file is None else args.split_file
            source = select_split(source, args.split, split_file)
        options = dict(as_of=args.as_of, expand_links=args.expand_links, backend=args.backend,
                       top_k=args.top_k, expand_query=args.expand_query, query_mapping=args.query_mapping)
        if args.lexical_ngrams is not None:
            options['lexical_ngrams'] = args.lexical_ngrams
        report = (compare(source, max_tokens=args.max_tokens, exact_paired=args.exact_paired, **options)
                  if args.compare else
                  evaluate(source, max_tokens=args.max_tokens, advanced=args.advanced, **options))
        if args.render_matched:
            report = with_render_matched(report, source, max_tokens=args.max_tokens,
                                         exact_paired=args.exact_paired, **options)
        if args.rate_bounds:
            report = with_rate_bounds(report)
        if args.success_bounds:
            report = with_success_bounds(report)
    except GoldSetError as exc:
        # Opt-in modes only; the message names indices, fields and paths.
        print(f"recall evaluation unavailable: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"recall evaluation unavailable: {type(exc).__name__}", file=sys.stderr)
        return 2
    gated = report['improved'] if args.compare else report
    optional_gates = {
        "no_answer_false_positive_rate": (gated["no_answer"]["false_positive_rate"], args.max_no_answer_false_positive_rate),
        "forbidden_exposure_rate": (gated["forbidden_sources"]["exposure_rate"], args.max_forbidden_exposure_rate),
        "stale_top_1_rate": (gated["stale_sources"]["top_1_rate"], args.max_stale_top_1_rate),
    }
    gated["optional_gates"] = {
        name: {"observed": observed, "maximum": maximum,
               "passed": observed is not None and observed <= maximum}
        for name, (observed, maximum) in optional_gates.items() if maximum is not None
    }
    printed = (report if not args.redact else redact_report(report) if redaction_key is None else
               redact_report(report, key=redaction_key, ephemeral=args.redact_ephemeral))
    print(json.dumps(printed, ensure_ascii=False, indent=2))
    if args.lexical_ngrams is not None and lexical_ngrams_ignored(report):
        print(LEXICAL_NGRAMS_IGNORED_NOTE, file=sys.stderr)
    for warning in report.get('temporal_decomposition', {}).get('warnings', ()):
        print(f"render-matched warning (non-blocking): {warning}", file=sys.stderr)
    # Opt-in notes (gold set, split, inspected); a fixture path has none.
    note_digest = None if redaction_key is None else _keyed_digest(redaction_key)
    for note in (source.notes if isinstance(source, EvaluationInput) else ()):
        print(note if note_digest is None else _keyed_note(note, note_digest), file=sys.stderr)
    # Inspected marks must not turn the recall@3/MRR gates into a vacuous pass.
    gate_gap = _inspected_gate_gap(gated)
    if gate_gap is not None:
        print(f"recall evaluation gate failed: {gated['inspected_queries']['query_count']} inspected=true "
              f"queries leave {gate_gap} in the headline, so --min-recall-at-3 and --min-mrr cannot be "
              "evaluated; add queries that were not edited after looking at failures", file=sys.stderr)
    elif "inspected_queries" in gated:
        print(f"recall evaluation note (non-blocking): {gated['inspected_queries']['query_count']} inspected "
              "queries are excluded from headline metrics and gates; see inspected_queries", file=sys.stderr)
    if args.rate_bounds:
        for note in rate_bound_gate_notes(gated):
            print(f"rate-bounds note (non-blocking): {note}", file=sys.stderr)
    passed = (
        gate_gap is None
        and (gated["recall_at_3"] is None or gated["recall_at_3"] >= args.min_recall_at_3)
        and (gated["mrr"] is None or gated["mrr"] >= args.min_mrr)
        and not gated["evaluation_errors"]
        and all(gate["passed"] for gate in gated["optional_gates"].values())
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
