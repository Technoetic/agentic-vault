#!/usr/bin/env python3
"""Evaluate lexical source retrieval on the checked-in bilingual fixture corpus."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import re
import sys
import time
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "recall"
RECALL_PATH = REPO_ROOT / "skills" / "agentic-vault" / "scripts" / "vault_recall.py"
MAX_QUERIES_BYTES = 1024 * 1024


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


def _load_queries(fixture: Path) -> list[dict]:
    path = fixture / "queries.json"
    if path.stat().st_size > MAX_QUERIES_BYTES:
        raise ValueError("queries.json exceeds evaluation byte limit")
    raw = json.loads(path.read_text(encoding="utf-8"))
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
             query_mapping: str | None = None) -> dict:
    """Measure labeled source retrieval and returned exposure, never answer quality."""
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
    advanced = advanced or as_of is not None or expand_links is not None or backend is not None or expand_query
    queries = _load_queries(fixture)
    recall_module = _load_recall_module()
    vault = fixture / "vault"
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
    return {
        "benchmark": "bilingual source retrieval and data exposure (not LLM answer/attack quality)",
        "method": 'advanced' if advanced else 'legacy',
        'top_k': top_k,
        'metric_definitions': {'returned_context_claim_coverage': 'covered literal claims / labeled claims, in rendered context after attribution labels are removed; no semantic answer-quality inference',
            'ndcg_at_5': 'graded gain 2^grade-1, log2(rank+1) discount; top-k=3 leaves ranks 4/5 unreturned',
            'legacy_at_3': 'legacy keys always assess first three matches, including when top_k=5'},
        "interpretation": "Empty results describe this fixture scan only; they do not prove world absence. Exposure means returned source matches, not successful instruction following.",
        **_summary(per_query),
        "by_language": {
            key: _summary([row for row in per_query if row["language"] == key])
            for key in sorted({row["language"] for row in per_query})
        },
        "by_scenario": {
            key: _summary([row for row in per_query if row["scenario"] == key])
            for key in sorted({row["scenario"] for row in per_query})
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


def compare(fixture: Path, *, max_tokens: int = 1500, **options) -> dict:
    """Identical labeled queries/budget; legacy ignores advanced fixture options."""
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
        paired = [(a[metric], b[metric]) for a, b in zip(paired_baseline['per_query'], improved['per_query'])
                  if a[metric] is not None and b[metric] is not None]
        report['paired_randomization'][metric] = paired_randomization([p[0] for p in paired], [p[1] for p in paired])
    report['paired_comparison'] = 'expansion_ablation versus improved' if 'expansion_ablation' in report else 'baseline versus improved'
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
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
    parser.add_argument('--as-of', help='override all per-query time points')
    parser.add_argument('--expand-links', type=int, choices=(0, 1, 2))
    parser.add_argument('--backend', choices=('lexical', 'bm25', 'hybrid'))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    thresholds = (args.min_recall_at_3, args.min_mrr, args.max_no_answer_false_positive_rate,
                  args.max_forbidden_exposure_rate, args.max_stale_top_1_rate)
    if any(value is not None and not 0.0 <= value <= 1.0 for value in thresholds):
        print("evaluation thresholds must be between 0 and 1", file=sys.stderr)
        return 2
    try:
        options = dict(as_of=args.as_of, expand_links=args.expand_links, backend=args.backend,
                       top_k=args.top_k, expand_query=args.expand_query, query_mapping=args.query_mapping)
        report = (compare(args.fixture, max_tokens=args.max_tokens, **options) if args.compare else
                  evaluate(args.fixture, max_tokens=args.max_tokens, advanced=args.advanced, **options))
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
    print(json.dumps(report, ensure_ascii=False, indent=2))
    passed = (
        (gated["recall_at_3"] is None or gated["recall_at_3"] >= args.min_recall_at_3)
        and (gated["mrr"] is None or gated["mrr"] >= args.min_mrr)
        and not gated["evaluation_errors"]
        and all(gate["passed"] for gate in gated["optional_gates"].values())
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
