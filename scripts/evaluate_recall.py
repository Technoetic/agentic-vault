#!/usr/bin/env python3
"""Evaluate lexical source retrieval on the checked-in bilingual fixture corpus."""
from __future__ import annotations

import argparse
import importlib.util
import json
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
        checked.append({**item, "expectation": expectation, "scenario": scenario,
                        "forbidden_paths": forbidden, "stale_paths": stale})
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
             backend: str | None = None) -> dict:
    """Measure labeled source retrieval and returned exposure, never answer quality."""
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 0:
        raise ValueError("max_tokens must be a non-negative integer")
    if type(advanced) is not bool or (expand_links is not None and
            (type(expand_links) is not int or expand_links not in (0, 1, 2))):
        raise ValueError('invalid advanced options')
    if backend is not None and backend not in ('lexical', 'bm25', 'hybrid'):
        raise ValueError('invalid retrieval backend')
    advanced = advanced or as_of is not None or expand_links is not None or backend is not None
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
        started = time.perf_counter()
        reader = recall_module._advanced_retrieval.retrieve if advanced else recall_module.recall
        result = reader(vault, item["query"], limit=3, max_tokens=max_tokens, **options)
        elapsed_ms = (time.perf_counter() - started) * 1000
        retrieved = [match["path"] for match in result["matches"]]
        expected = set(item["expected_paths"])
        source_recall = _ratio(len(expected.intersection(retrieved)), len(expected))
        rank = next(
            (index for index, path in enumerate(retrieved, start=1) if path in expected),
            None,
        )
        reciprocal_rank = (1.0 / rank if rank is not None else 0.0) if expected else None
        diagnostics = result["diagnostics"]
        complete = diagnostics["status"] == "ok" and diagnostics["search_complete"]
        forbidden_retrieved = [path for path in retrieved if path in item["forbidden_paths"]]
        stale_retrieved = [path for path in retrieved if path in item["stale_paths"]]
        reasons: list[str] = []
        if expected:
            outcome = "expected_source_found" if rank is not None else "expected_source_missed"
            if source_recall < 1.0:
                reasons.append("expected_source_missed")
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
    baseline = evaluate(fixture, max_tokens=max_tokens)
    improved = evaluate(fixture, max_tokens=max_tokens, advanced=True, **options)
    return {'benchmark': 'paired retrieval comparison',
            'interpretation': 'Same queries and context budget; fixture labels supply explicit retrieval options, never expected paths to the retriever. No model or paper score is inferred.',
            'baseline': baseline, 'improved': improved}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--min-recall-at-3", type=float, default=0.85)
    parser.add_argument("--min-mrr", type=float, default=0.75)
    parser.add_argument("--max-tokens", type=int, default=1500,
                        help="returned context budget (token estimate); retrieval remains top 3")
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
        options = dict(as_of=args.as_of, expand_links=args.expand_links, backend=args.backend)
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
