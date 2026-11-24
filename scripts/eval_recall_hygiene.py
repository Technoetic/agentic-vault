#!/usr/bin/env python3
"""Offline recall evaluation hygiene: split near-duplicate leakage & layout invariance.

Selectively adapts two Python standard-library-only evaluation hygiene ideas from
ai3inc/opentoolsearch (v0.2.1, Apache-2.0):
  1. Cross-split near-duplicate query leakage auditing (normalized exact match +
     word-set and Hangul syllable-bigram Jaccard overlap between dev and held-out
     splits, preventing inflated held-out scores from near-copy queries).
  2. Content-preserving Markdown layout-invariance probing (reordering flat YAML
     frontmatter keys and varying heading spacing in an isolated temporary copy
     to measure retrieval rank stability without modifying the source vault).

Python 3.10+ standard library only; zero network calls, zero model downloads,
zero writes to the source vault or fixture directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import shutil
import stat
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "skills" / "agentic-vault" / "scripts"

SCHEMA_LEAKAGE = "agentic-vault/query-leakage-audit/v1"
SCHEMA_LAYOUT = "agentic-vault/layout-invariance-probe/v1"

DEFAULT_JACCARD_THRESHOLD = 0.60
MAX_REPORTED_PAIRS = 100
MAX_DRIFTED_QUERIES = 50
MAX_JSON_BYTES = 4 * 1024 * 1024
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_HANGUL_FIRST, _HANGUL_LAST = "\uac00", "\ud7a3"
_HANGUL_SEGMENT = re.compile(f"[{_HANGUL_FIRST}-{_HANGUL_LAST}]+|[^{_HANGUL_FIRST}-{_HANGUL_LAST}]+")
_FLAT_FM_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):[ \t]+(.+)$")
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_ATX_HEADING = re.compile(r"^#{1,6}[ \t]+\S")


def _normalize_query(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    tokens = _WORD_RE.findall(folded)
    return " ".join(tokens)


def _word_set(text: str) -> set[str]:
    return set(_WORD_RE.findall(unicodedata.normalize("NFKC", text).casefold()))


def _hangul2_set(text: str) -> set[str]:
    tokens: set[str] = set()
    for word in _WORD_RE.findall(unicodedata.normalize("NFKC", text).casefold()):
        for seg in _HANGUL_SEGMENT.findall(word):
            if len(seg) > 1 and _HANGUL_FIRST <= seg[0] <= _HANGUL_LAST:
                for idx in range(len(seg) - 1):
                    tokens.add(seg[idx:idx + 2])
            else:
                tokens.add(seg)
    return tokens


def _jaccard(left: set[str], right: set[str], *, empty_equal: bool = True) -> float:
    if not left and not right:
        return 1.0 if empty_equal else 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _validate_query_rows(rows: object, label: str) -> list[dict]:
    if not isinstance(rows, (list, tuple)):
        raise ValueError(f"{label} must be a list of query objects")
    out: list[dict] = []
    seen: set[str] = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"{label}[{idx}] must be an object")
        qid = row.get("id")
        query = row.get("query")
        if not isinstance(qid, str) or not qid.strip() or qid in seen:
            raise ValueError(f"{label}[{idx}].id must be a unique non-empty string")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"{label}[{idx}].query must be a non-empty string")
        seen.add(qid)
        out.append(row)
    return out


def audit_split_query_leakage(
    dev_queries: Sequence[dict],
    heldout_queries: Sequence[dict],
    *,
    jaccard_threshold: float = DEFAULT_JACCARD_THRESHOLD,
    max_pairs: int = MAX_REPORTED_PAIRS,
) -> dict:
    """Detect exact and near-duplicate query leakage across dev and held-out splits.

    Flags a (dev, heldout) query pair when normalized texts are identical or
    max(word_jaccard, hangul2_jaccard) >= jaccard_threshold. Never exposes vault
    note content and returns deterministic sorted results.
    """
    if type(jaccard_threshold) not in (int, float) or not 0.0 < float(jaccard_threshold) <= 1.0:
        raise ValueError("jaccard_threshold must be in (0, 1]")
    if type(max_pairs) is not int or not 1 <= max_pairs <= 1000:
        raise ValueError("max_pairs must be an integer between 1 and 1000")
    dev_rows = _validate_query_rows(dev_queries, "dev_queries")
    held_rows = _validate_query_rows(heldout_queries, "heldout_queries")

    dev_prep = [
        (row["id"], _normalize_query(row["query"]), _word_set(row["query"]), _hangul2_set(row["query"]))
        for row in dev_rows
    ]
    held_prep = [
        (row["id"], _normalize_query(row["query"]), _word_set(row["query"]), _hangul2_set(row["query"]))
        for row in held_rows
    ]

    flagged: list[dict] = []
    exact_pairs = 0
    near_pairs = 0
    leaked_heldout: set[str] = set()

    for hid, hnorm, hwords, hbigrams in held_prep:
        for did, dnorm, dwords, dbigrams in dev_prep:
            exact = bool(hnorm) and hnorm == dnorm
            wj = _jaccard(hwords, dwords, empty_equal=False)
            hj = _jaccard(hbigrams, dbigrams, empty_equal=False)
            score = max(wj, hj)
            if exact or score >= jaccard_threshold:
                leaked_heldout.add(hid)
                if exact:
                    exact_pairs += 1
                else:
                    near_pairs += 1
                flagged.append({
                    "heldout_id": hid,
                    "dev_id": did,
                    "match_kind": "exact_normalized" if exact else "near_duplicate_jaccard",
                    "word_jaccard": round(wj, 6),
                    "hangul2_jaccard": round(hj, 6),
                    "max_jaccard": round(score, 6),
                })

    flagged.sort(key=lambda item: (-item["max_jaccard"], item["heldout_id"], item["dev_id"]))
    omitted = max(0, len(flagged) - max_pairs)
    held_n = len(held_prep)
    return {
        "schema": SCHEMA_LEAKAGE,
        "threshold": float(jaccard_threshold),
        "dev_count": len(dev_prep),
        "heldout_count": held_n,
        "exact_match_pair_count": exact_pairs,
        "near_duplicate_pair_count": near_pairs,
        "total_flagged_pairs": len(flagged),
        "leaked_heldout_ids": sorted(leaked_heldout),
        "leaked_heldout_count": len(leaked_heldout),
        "leakage_rate": (len(leaked_heldout) / held_n) if held_n else 0.0,
        "passed": len(leaked_heldout) == 0,
        "pairs": flagged[:max_pairs],
        "omitted_pairs": omitted,
    }


def _is_safe_flat_scalar(line: str) -> bool:
    """True only for single-line top-level YAML key: scalar entries safe to reorder."""
    match = _FLAT_FM_KEY.match(line)
    if not match:
        return False
    val = match.group(2).strip()
    if not val:
        return False
    # Exclude block scalars (|, >, |-, >+) and potentially multiline flow collections/quotes.
    if val[0] in ("|", ">", "[", "{"):
        return False
    if val[0] == '"' and not (len(val) >= 2 and val.endswith('"') and not val.endswith('\\"')):
        return False
    if val[0] == "'" and not (len(val) >= 2 and val.endswith("'")):
        return False
    return True


def _perturb_frontmatter_lines(fm_lines: list[str], rng: random.Random) -> list[str]:
    """Reorder flat single-line frontmatter fields while preserving multiline blocks."""
    blocks: list[list[str]] = []
    idx = 0
    total = len(fm_lines)
    while idx < total:
        line = fm_lines[idx]
        if not line.strip() or line.lstrip().startswith("#"):
            blocks.append([line])
            idx += 1
            continue
        block = [line]
        idx += 1
        # Collect block-scalar / flow-collection / indented-list continuation lines safely,
        # including blank lines embedded inside block scalars and column-0 closing brackets.
        match = _FLAT_FM_KEY.match(line)
        val = match.group(2).strip() if match else ""
        is_block_scalar = bool(val) and val[0] in ("|", ">")
        flow_depth = (
            val.count("[") - val.count("]") + val.count("{") - val.count("}")
            if bool(val) and val[0] in ("[", "{")
            else 0
        )
        while idx < total:
            nxt = fm_lines[idx]
            if flow_depth > 0:
                block.append(nxt)
                flow_depth += nxt.count("[") - nxt.count("]") + nxt.count("{") - nxt.count("}")
                idx += 1
                continue
            if nxt.startswith((" ", "\t")):
                block.append(nxt)
                idx += 1
                continue
            if is_block_scalar and not nxt.strip():
                # Peek ahead: blank line belongs to block scalar if followed by indented content.
                look = idx + 1
                while look < total and not fm_lines[look].strip():
                    look += 1
                if look < total and fm_lines[look].startswith((" ", "\t")):
                    block.append(nxt)
                    idx += 1
                    continue
            if nxt.lstrip().startswith("- ") and not nxt.startswith((" ", "\t")):
                if not match and line.rstrip().endswith(":"):
                    block.append(nxt)
                    idx += 1
                    continue
            break
        blocks.append(block)

    keyed_indices = [
        i for i, blk in enumerate(blocks)
        if len(blk) == 1 and _is_safe_flat_scalar(blk[0])
    ]
    if len(keyed_indices) >= 2:
        reordered = [blocks[i] for i in keyed_indices]
        original = list(reordered)
        rng.shuffle(reordered)
        if reordered == original:
            reordered = reordered[1:] + reordered[:1]
        for slot, blk in zip(keyed_indices, reordered):
            blocks[slot] = blk

    return [line for blk in blocks for line in blk]


def perturb_markdown_layout(text: str, *, seed: int = 1707) -> str:
    """Return a layout-perturbed copy of Markdown text without changing semantic content.

    - Reorders flat single-line top-level YAML frontmatter entries deterministically.
    - Inserts a harmless blank line before ATX headings outside fenced code blocks
      when none is present (keeping heading text, links, and temporal scalars intact).
    """
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    rng = random.Random(seed)
    has_trailing_nl = text.endswith("\n")
    lines = text.splitlines()
    if not lines:
        return text

    body_start = 0
    fm_out: list[str] = []
    if lines[0].strip() == "---":
        close_idx = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if close_idx is not None:
            fm_inner = _perturb_frontmatter_lines(lines[1:close_idx], rng)
            fm_out = [lines[0], *fm_inner, lines[close_idx]]
            body_start = close_idx + 1

    body_out: list[str] = []
    if fm_out and body_start < len(lines) and lines[body_start].strip() != "":
        body_out.append("")
    fence: str | None = None
    for line in lines[body_start:]:
        if fence is not None:
            body_out.append(line)
            if line.strip().startswith(fence[0]) and len(line.strip()) >= len(fence) and set(line.strip()) == {fence[0]}:
                fence = None
            continue
        m_fence = _FENCE.match(line)
        if m_fence:
            fence = m_fence.group(1)
            body_out.append(line)
            continue
        if _ATX_HEADING.match(line) and body_out and body_out[-1].strip() != "":
            body_out.append("")
        elif line.strip() != "" and body_out and _ATX_HEADING.match(body_out[-1]):
            body_out.append("")
        body_out.append(line)

    joined = "\n".join([*fm_out, *body_out])
    if has_trailing_nl and not joined.endswith("\n"):
        joined += "\n"
    return joined


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE)


def _fs_path(path: Path) -> Path:
    text = str(path)
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return path
    if re.match(r"^[A-Za-z]:\\", text) and len(text) >= 240:
        return Path("\\\\?\\" + text)
    if text.startswith("\\\\") and len(text) >= 240:
        return Path("\\\\?\\UNC\\" + text[2:])
    return path


def _load_retrieval_modules():
    added = False
    script_str = str(SCRIPT_DIR)
    if script_str not in sys.path:
        sys.path.insert(0, script_str)
        added = True
    try:
        import vault_recall as legacy
        import vault_retrieval as retrieval
        return legacy, retrieval
    finally:
        if added and script_str in sys.path:
            sys.path.remove(script_str)


def probe_layout_invariance(
    vault: Path,
    queries: Sequence[dict],
    *,
    backend: str = "bm25",
    top_k: int = 5,
    seed: int = 1707,
    max_tokens: int = 0,
    lexical_ngrams: str | None = None,
) -> dict:
    """Measure retrieval rank invariance under content-preserving Markdown layout perturbations.

    Copies only policy-allowed notes and config into a disposable temporary vault,
    perturbs note layout deterministically, and compares top-k paths against the
    unmodified vault. Never writes to the source vault.
    """
    if backend not in ("lexical", "bm25", "hybrid"):
        raise ValueError("backend must be lexical, bm25, or hybrid")
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise ValueError("top_k must be between 1 and 20")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    if type(max_tokens) is not int or max_tokens < 0:
        raise ValueError("max_tokens must be a non-negative integer")

    legacy, retrieval = _load_retrieval_modules()
    raw_vault = Path(vault)
    if _is_link_or_reparse(raw_vault):
        raise ValueError("symlink or reparse vault root refused")
    vault_path = raw_vault.resolve(strict=True)
    diagnostics = legacy._diagnostics()
    config = legacy._read_config(vault_path, diagnostics)
    if config is None:
        raise ValueError("vault configuration unavailable")
    deny = tuple(config.get("deny_zones") or ())
    exclude = (*tuple(config.get("exclude_dirs") or ()), ".git")
    note_entries = legacy._markdown_paths(vault_path, deny, exclude, legacy._diagnostics())
    max_file_bytes = int(getattr(legacy, "MAX_FILE_BYTES", 512 * 1024))
    max_total_bytes = int(getattr(legacy, "MAX_TOTAL_READ_BYTES", 16 * 1024 * 1024))
    read_regular = getattr(legacy, "_read_regular_bytes", None)

    validated_queries = _validate_query_rows(queries, "queries")
    answerable = [q for q in validated_queries if q.get("expectation", "answerable") == "answerable"]
    tmp_dir = tempfile.mkdtemp(prefix="av-layout-probe-")
    try:
        perturbed_vault = Path(tmp_dir) / "vault"
        cfg_src = vault_path / legacy.CONFIG_RELPATH
        if _is_link_or_reparse(cfg_src):
            raise ValueError("vault configuration is a symlink or reparse point")
        try:
            if callable(read_regular):
                cfg_bytes, cfg_stable = read_regular(_fs_path(cfg_src), max_file_bytes)
                if not cfg_stable:
                    raise ValueError("vault configuration changed while reading")
            else:
                cfg_bytes = _fs_path(cfg_src).read_bytes()
        except (OSError, ValueError, OverflowError) as exc:
            raise ValueError("vault configuration unavailable") from exc
        cfg_dst = _fs_path(perturbed_vault / legacy.CONFIG_RELPATH)
        cfg_dst.parent.mkdir(parents=True, exist_ok=True)
        cfg_dst.write_bytes(cfg_bytes)

        notes_copied = 0
        notes_perturbed = 0
        bytes_copied = 0
        for rel, src_path in note_entries:
            if bytes_copied >= max_total_bytes:
                break
            try:
                if callable(read_regular):
                    raw, stable = read_regular(_fs_path(src_path), max_file_bytes)
                    if not stable:
                        continue
                else:
                    if _is_link_or_reparse(src_path):
                        continue
                    st = _fs_path(src_path).lstat()
                    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > max_file_bytes:
                        continue
                    raw = _fs_path(src_path).read_bytes()
            except (OSError, ValueError, OverflowError):
                continue
            if bytes_copied + len(raw) > max_total_bytes:
                continue
            bytes_copied += len(raw)
            notes_copied += 1
            text = raw.decode("utf-8-sig", errors="replace")
            note_seed = int(hashlib.sha256(f"{seed}:{rel}".encode("utf-8")).hexdigest()[:8], 16)
            mutated = perturb_markdown_layout(text, seed=note_seed)
            if mutated != text:
                notes_perturbed += 1
            dst_path = _fs_path(perturbed_vault / rel)
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            dst_path.write_text(mutated, encoding="utf-8", newline="\n")

        top1_matches = 0
        topk_set_matches = 0
        topk_order_matches = 0
        jaccards: list[float] = []
        drifted: list[dict] = []
        errors = 0

        for q in answerable:
            kw = {"limit": top_k, "max_tokens": max_tokens, "backend": backend, "lexical_ngrams": lexical_ngrams}
            orig_res = retrieval.retrieve(vault_path, q["query"], **kw)
            pert_res = retrieval.retrieve(perturbed_vault, q["query"], **kw)
            if orig_res["diagnostics"]["status"] != "ok" or pert_res["diagnostics"]["status"] != "ok":
                errors += 1
                continue
            orig_paths = [m["path"] for m in orig_res["matches"][:top_k]]
            pert_paths = [m["path"] for m in pert_res["matches"][:top_k]]
            orig_top1 = orig_paths[0] if orig_paths else None
            pert_top1 = pert_paths[0] if pert_paths else None
            if orig_top1 == pert_top1:
                top1_matches += 1
            if set(orig_paths) == set(pert_paths):
                topk_set_matches += 1
            if orig_paths == pert_paths:
                topk_order_matches += 1
            else:
                if len(drifted) < MAX_DRIFTED_QUERIES:
                    drifted.append({
                        "id": q["id"],
                        "original_paths": orig_paths,
                        "perturbed_paths": pert_paths,
                    })
            jaccards.append(_jaccard(set(orig_paths), set(pert_paths)))
    finally:
        shutil.rmtree(_fs_path(Path(tmp_dir)), ignore_errors=True)

    evaluated = len(answerable) - errors
    return {
        "schema": SCHEMA_LAYOUT,
        "backend": backend,
        "lexical_ngrams": lexical_ngrams,
        "top_k": top_k,
        "seed": seed,
        "notes_total": len(note_entries),
        "notes_perturbed": notes_perturbed,
        "query_count": len(answerable),
        "evaluated_query_count": evaluated,
        "error_count": errors,
        "top1_exact_agreement": (top1_matches / evaluated) if evaluated else None,
        "topk_set_agreement": (topk_set_matches / evaluated) if evaluated else None,
        "topk_order_agreement": (topk_order_matches / evaluated) if evaluated else None,
        "mean_topk_jaccard": (sum(jaccards) / len(jaccards)) if jaccards else None,
        "drifted_query_count": evaluated - topk_order_matches,
        "drifted_queries": drifted,
        "scope": "Content-preserving frontmatter key-order and heading-spacing perturbation on an isolated temporary copy; observational.",
    }


def _load_json(path: Path) -> object:
    if _is_link_or_reparse(path):
        raise ValueError(f"symlink or reparse JSON input refused: {path}")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_JSON_BYTES:
        raise ValueError(f"JSON input missing, non-regular, or exceeds byte limit: {path}")
    with path.open("rb") as handle:
        raw = handle.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError(f"JSON input exceeds byte limit: {path}")

    def no_dups(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                raise ValueError(f"duplicate JSON key: {k}")
            out[k] = v
        return out

    def reject_nonfinite(_val):
        raise ValueError("non-finite JSON constant")

    def parse_finite_float(text: str) -> float:
        val = float(text)
        if not math.isfinite(val):
            raise ValueError("non-finite JSON number")
        return val

    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=no_dups,
        parse_constant=reject_nonfinite,
        parse_float=parse_finite_float,
    )


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    leak = sub.add_parser("leakage", help="audit dev-vs-heldout near-duplicate query leakage")
    leak.add_argument("--queries", type=Path, required=True, help="queries.json file")
    leak.add_argument("--split", type=Path, required=True, help="split.json with dev and heldout id lists")
    leak.add_argument("--threshold", type=float, default=DEFAULT_JACCARD_THRESHOLD)

    layout = sub.add_parser("layout", help="probe retrieval rank invariance under layout perturbations")
    layout.add_argument("--vault", type=Path, required=True, help="vault directory")
    layout.add_argument("--queries", type=Path, required=True, help="queries.json file")
    layout.add_argument("--backend", choices=("lexical", "bm25", "hybrid"), default="bm25")
    layout.add_argument("--lexical-ngrams", choices=("hangul2",), default=None)
    layout.add_argument("--top-k", type=int, default=5)
    layout.add_argument("--seed", type=int, default=1707)

    args = parser.parse_args(argv)
    try:
        if args.command == "leakage":
            qdata = _load_json(args.queries)
            sdata = _load_json(args.split)
            if not isinstance(qdata, dict) or not isinstance(qdata.get("queries"), list):
                raise ValueError("queries file must contain a queries list")
            if not isinstance(sdata, dict) or not isinstance(sdata.get("dev"), list) or not isinstance(sdata.get("heldout"), list):
                raise ValueError("split file must contain dev and heldout id lists")
            by_id = {q["id"]: q for q in _validate_query_rows(qdata["queries"], "queries")}
            dev_rows: list[dict] = []
            held_rows: list[dict] = []
            for side, target_list in (("dev", dev_rows), ("heldout", held_rows)):
                for qid in sdata[side]:
                    if not isinstance(qid, str) or qid not in by_id:
                        raise ValueError(f"split.{side} contains unknown or invalid query id")
                    target_list.append(by_id[qid])
            report = audit_split_query_leakage(dev_rows, held_rows, jaccard_threshold=args.threshold)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["passed"] else 1
        qdata = _load_json(args.queries)
        if not isinstance(qdata, dict) or not isinstance(qdata.get("queries"), list):
            raise ValueError("queries file must contain a queries list")
        report = probe_layout_invariance(
            args.vault, qdata["queries"], backend=args.backend, top_k=args.top_k,
            seed=args.seed, lexical_ngrams=args.lexical_ngrams,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["error_count"] == 0 else 1
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "invalid_input", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
