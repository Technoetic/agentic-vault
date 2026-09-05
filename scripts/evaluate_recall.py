#!/usr/bin/env python3
"""Evaluate lexical source retrieval on the checked-in bilingual fixture corpus."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
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
        if not isinstance(query_id, str) or not query_id or query_id in seen_ids:
            raise ValueError(f"queries[{index}].id must be unique and non-empty")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"queries[{index}].query must be non-empty")
        if language not in ("en", "ko"):
            raise ValueError(f"queries[{index}].language must be en or ko")
        if not isinstance(expected, list) or not expected or not all(
            isinstance(value, str) and value.endswith(".md") for value in expected
        ):
            raise ValueError(f"queries[{index}].expected_paths must contain literal Markdown paths")
        seen_ids.add(query_id)
        checked.append(item)
    return checked


def evaluate(fixture: Path) -> dict:
    """Return recall@3 and MRR@3 for literal expected source paths."""
    recall_module = _load_recall_module()
    queries = _load_queries(fixture)
    vault = fixture / "vault"
    source_recall_sum = 0.0
    reciprocal_rank_sum = 0.0
    failures: list[dict] = []
    evaluation_errors: list[dict] = []
    language_totals: dict[str, dict[str, float]] = {}

    for item in queries:
        result = recall_module.recall(vault, item["query"], limit=3, max_tokens=0)
        retrieved = [match["path"] for match in result["matches"]]
        expected = set(item["expected_paths"])
        source_recall = len(expected.intersection(retrieved)) / len(expected)
        rank = next(
            (index for index, path in enumerate(retrieved, start=1) if path in expected),
            None,
        )
        reciprocal_rank = 1.0 / rank if rank is not None else 0.0
        source_recall_sum += source_recall
        reciprocal_rank_sum += reciprocal_rank
        language = item["language"]
        bucket = language_totals.setdefault(
            language, {"count": 0, "recall_sum": 0.0, "rr_sum": 0.0})
        bucket["count"] += 1
        bucket["recall_sum"] += source_recall
        bucket["rr_sum"] += reciprocal_rank
        if source_recall < 1.0:
            failures.append({
                "id": item["id"],
                "expected_paths": item["expected_paths"],
                "retrieved_paths": retrieved,
                "source_recall_at_3": source_recall,
            })
        if result["diagnostics"]["status"] != "ok" or not result["diagnostics"]["search_complete"]:
            evaluation_errors.append({
                "id": item["id"],
                "status": result["diagnostics"]["status"],
                "search_complete": result["diagnostics"]["search_complete"],
                "omissions": result["diagnostics"]["omissions"],
            })

    count = len(queries)
    by_language = {
        language: {
            "query_count": int(values["count"]),
            "recall_at_3": values["recall_sum"] / values["count"],
            "mrr": values["rr_sum"] / values["count"],
        }
        for language, values in sorted(language_totals.items())
    }
    return {
        "benchmark": "bilingual lexical source retrieval (not semantic or Claude answer quality)",
        "query_count": count,
        "recall_at_3": source_recall_sum / count,
        "mrr": reciprocal_rank_sum / count,
        "by_language": by_language,
        "failed_queries": failures,
        "evaluation_errors": evaluation_errors,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--min-recall-at-3", type=float, default=0.85)
    parser.add_argument("--min-mrr", type=float, default=0.75)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 0.0 <= args.min_recall_at_3 <= 1.0 or not 0.0 <= args.min_mrr <= 1.0:
        print("evaluation thresholds must be between 0 and 1", file=sys.stderr)
        return 2
    try:
        report = evaluate(args.fixture)
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"recall evaluation unavailable: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    passed = (
        report["recall_at_3"] >= args.min_recall_at_3
        and report["mrr"] >= args.min_mrr
        and not report["evaluation_errors"]
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
