#!/usr/bin/env python3
"""Optional temporal, BM25/RRF and bounded wikilink source retrieval.

This is a small deterministic adaptation of memory retrieval patterns, not a
paper reproduction. No source or rank confers authority. The caller reviews
claims and decides abstention. Default arguments use the unchanged lexical
recall path. Core executes no commands, network calls or model downloads.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import stat
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import vault_recall as _legacy
from vault_paths import relative_parts, resolve_note_path

MAX_EXTERNAL_BYTES = 64 * 1024
MAX_EXTERNAL_CANDIDATES = 50
MAX_METADATA_LINES = 128
MAX_LINKS_PER_NOTE = 128
MAX_GRAPH_PROOFS = 8
RRF_K = 60
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_ISO_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)\Z")
_FIELD = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")
_LINK = re.compile(r"\[\[([^\[\]\n]{1,512})\]\]")
_TEMPORAL_FIELDS = {"valid_from", "valid_until", "checked_at", "superseded_by"}


def _time(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("time must be an explicit ISO date or zoned datetime")
    if _ISO_DATE.fullmatch(value):
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    if not _ISO_TIME.fullmatch(value):
        raise ValueError("datetime requires seconds and an explicit timezone")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _scalar(value: str) -> str:
    # A deliberately small YAML subset. Structured/anchored/tagged scalars are
    # ambiguous here and are not interpreted as dates or trusted instructions.
    value = value.strip()
    if len(value) > 512:
        raise ValueError("metadata scalar exceeds bounded output")
    if value.startswith('"'):
        decoded = json.loads(value)
        if not isinstance(decoded, str):
            raise ValueError("scalar must be text")
        return decoded
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise ValueError("unclosed scalar")
        return value[1:-1].replace("''", "'")
    if not value or value[:1] in "[{&*!>|" or " #" in value:
        raise ValueError("unsupported scalar")
    return value


def _metadata(text: str) -> dict:
    lines = text.splitlines()
    fields: dict[str, str] = {}
    errors: list[str] = []
    if lines and lines[0].strip() == "---":
        closed = False
        seen: set[str] = set()
        for line in lines[1:MAX_METADATA_LINES + 1]:
            if line.strip() == "---":
                closed = True
                break
            if line.lstrip().startswith("<<:"):
                errors.append("inherited_temporal_metadata")
            match = _FIELD.match(line)
            if not match:
                # Indented temporal fields cannot establish a root date.
                if any(re.match(r"^\s+" + name + r"\s*:", line) for name in _TEMPORAL_FIELDS):
                    errors.append("nested_temporal_field")
                if re.match(r"^\s*['\"](?:valid_from|valid_until|checked_at|superseded_by)['\"]\s*:", line, re.IGNORECASE):
                    errors.append("quoted_temporal_key")
                if re.match(r"^\s*(?:valid_from|valid_until|checked_at|superseded_by)\s+:", line, re.IGNORECASE) or line.lstrip().startswith("{"):
                    errors.append("ambiguous_temporal_metadata")
                continue
            name, value = match.groups()
            if name != name.casefold() and name.casefold() in _TEMPORAL_FIELDS:
                errors.append("nonstandard_temporal_key")
                continue
            if name not in _TEMPORAL_FIELDS:
                continue
            if name in seen:
                errors.append("duplicate_" + name)
                continue
            seen.add(name)
            try:
                fields[name] = _scalar(value)
            except (ValueError, json.JSONDecodeError, RecursionError):
                errors.append("invalid_" + name)
        if not closed:
            errors.append("unclosed_or_overlong_frontmatter")
    dates: dict[str, datetime] = {}
    for name in ("valid_from", "valid_until", "checked_at"):
        if name in fields:
            try:
                dates[name] = _time(fields[name])
            except (ValueError, OverflowError):
                errors.append("invalid_" + name)
    if "valid_from" in dates and "valid_until" in dates and dates["valid_from"] >= dates["valid_until"]:
        errors.append("nonpositive_validity_interval")
    return {"fields": fields, "dates": dates, "errors": sorted(set(errors))}


def _filename(target: str) -> str | None:
    target = target.split("|", 1)[0].split("#", 1)[0]
    if not target or target != target.strip() or "/" in target or "\\" in target:
        return None
    name = target if target.endswith(".md") else target + ".md"
    try:
        relative_parts(name, "wikilink filename")
    except ValueError:
        return None
    return name


def _successor_name(value: str) -> str | None:
    if value.startswith("[[") and value.endswith("]]"):
        value = value[2:-2]
    return _filename(value)


def _temporal(doc: dict, documents: dict, filenames: dict, as_of: datetime | None, complete_filenames: bool = True) -> dict:
    meta = doc["metadata"]
    fields, dates = meta["fields"], meta["dates"]
    result = {"status": "unknown", "currentness": "unknown", **fields, "errors": meta["errors"]}
    if meta["errors"]:
        result["status"] = "invalid"
        return result
    if as_of is None:
        # Validity is not guessed from mtime, hash, ingestion or the clock.
        result["status"] = "not_evaluated" if dates else "unknown"
        return result
    lower, upper = dates.get("valid_from"), dates.get("valid_until")
    if lower is not None and as_of < lower:
        result["status"] = "not_yet_valid"
        return result
    if upper is not None and as_of >= upper:
        result["status"] = "expired"
        return result
    if "superseded_by" in fields:
        if not complete_filenames:
            result["status"] = "supersession_unknown"
            return result
        successors = filenames.get(_successor_name(fields["superseded_by"]), [])
        successor = documents.get(successors[0]) if len(successors) == 1 else None
        next_dates = successor["metadata"]["dates"] if successor else {}
        if not successor or successor["metadata"]["errors"] or "valid_from" not in next_dates or successor["path"] == doc["path"]:
            result["status"] = "supersession_unknown"
            return result
        result["superseded_at"] = successor["metadata"]["fields"]["valid_from"]
        result["supersession_source"] = {"path": successor["path"], "sha256": successor["sha256"]}
        if as_of >= next_dates["valid_from"]:
            result["status"] = "superseded"
            return result
    if lower is not None or upper is not None:
        result["status"] = "within_explicit_bounds"
        result["currentness"] = "within_declared_interval"
    return result


def _stamp(path: Path) -> tuple:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise ValueError("unsafe source")
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)


def _duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _candidate_list(raw) -> list[dict]:
    if isinstance(raw, dict):
        raw = raw.get("candidates")
    if not isinstance(raw, (tuple, list)) or len(raw) > MAX_EXTERNAL_CANDIDATES:
        raise ValueError("external candidates must contain at most 50 paths")
    checked = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            path, rank = item, index + 1
        elif isinstance(item, dict):
            path, rank = item.get("path"), item.get("rank", index + 1)
        else:
            raise ValueError("candidate must be path/rank data")
        if not isinstance(path, str) or len(path) > 1024 or not isinstance(rank, int) or isinstance(rank, bool) or not 1 <= rank <= 1_000_000:
            raise ValueError("invalid candidate path/rank")
        checked.append({"path": path, "rank": rank})
    return checked


def _sidecar(vault: Path, rel: str, deny, exclude, diagnostics) -> list[dict]:
    parts = relative_parts(rel, "candidate sidecar")
    if not rel.casefold().endswith(".json") or _legacy._classified_skip(parts, deny, exclude):
        raise ValueError("sidecar excluded by policy")
    path = resolve_note_path(vault, rel, deny)
    before = _stamp(path)
    content, stable = _legacy._read_regular_bytes(path, MAX_EXTERNAL_BYTES)
    diagnostics["external_bytes_read"] = len(content)
    if not stable or before != _stamp(path):
        raise ValueError("sidecar changed")
    data = json.loads(content.decode("utf-8-sig"), object_pairs_hook=_duplicate_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    return _candidate_list(data)


def _source(doc: dict, match: dict) -> dict:
    lines = doc["text"].splitlines()
    line = lines[match["line"] - 1] if 0 < match["line"] <= len(lines) else ""
    snippet = match["snippet"]
    alternatives = [snippet, snippet[3:] if snippet.startswith("...") else snippet, snippet[:-3] if snippet.endswith("...") else snippet, snippet[3:-3] if snippet.startswith("...") and snippet.endswith("...") else snippet]
    quote = max((value for value in alternatives if value and value in line), key=len, default=line[:_legacy.MAX_SNIPPET_CHARS])
    return {"path": doc["path"], "line": match["line"], "quote": quote, "sha256": doc["sha256"]}


def _fallback(doc: dict, phrase: str, terms) -> dict:
    lines = doc["text"].splitlines()
    title, line_number, title_source, has_source = _legacy._title(lines, Path(doc["path"]).stem)
    if not has_source:
        line_number = next((n for n, line in enumerate(lines, 1) if line.strip()), 0)
        title_source = lines[line_number - 1] if line_number else ""
    start = 0
    if lines and lines[0].strip() == "---":
        start = next((n + 1 for n, value in enumerate(lines[1:], 1) if value.strip() == "---"), len(lines))
    body_line = next((n for n, value in enumerate(lines[start:], start + 1) if value.strip() and not value.lstrip().startswith("#")), None)
    if body_line is not None:
        line_number = body_line
        title_source = lines[body_line - 1]
    return {"path": doc["path"], "line": line_number, "title": title[:_legacy.MAX_TITLE_CHARS], "score": 0.0, "snippet": _legacy._snippet(title_source, phrase, terms)}


def _bm25(documents: dict, query_terms) -> dict[str, float]:
    counts = {path: Counter(_legacy._WORD_RE.findall(_legacy._normalize(doc["text"]))) for path, doc in documents.items()}
    lengths = {path: sum(words.values()) for path, words in counts.items()}
    average = sum(lengths.values()) / len(lengths) if lengths else 1.0
    average = average or 1.0
    frequencies = {term: sum(term in words for words in counts.values()) for term in query_terms}
    scores = {}
    for path, words in counts.items():
        score = 0.0
        for term in query_terms:
            frequency = words.get(term, 0)
            if not frequency:
                continue
            inverse = math.log(1 + (len(counts) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
            denominator = frequency + 1.2 * (1 - 0.75 + 0.75 * lengths[path] / average)
            score += inverse * frequency * 2.2 / denominator
        if score > 0:
            scores[path] = score
    return scores


def _bm25_passage(doc: dict, match: dict, phrase: str, query_terms) -> None:
    """Attribute exact-token BM25 evidence rather than a substring title hit."""
    best_quality = (0, 0)
    for number, line in enumerate(doc["text"].splitlines(), 1):
        words = Counter(_legacy._WORD_RE.findall(_legacy._normalize(line)))
        quality = (sum(term in words for term in query_terms), sum(min(words[term], 3) for term in query_terms))
        if quality > best_quality:
            best_quality = quality
            match["line"] = number
            match["snippet"] = _legacy._snippet(line, phrase, query_terms)


def _ranked_paths(scores: dict) -> list[str]:
    return sorted(scores, key=lambda path: (-scores[path], path.casefold(), path))


def _invalid(diagnostics, status="invalid_options"):
    diagnostics["status"] = status
    diagnostics["search_complete"] = False
    return _legacy._empty_result(diagnostics)


def _policy_changed(diagnostics):
    diagnostics["temporal"] = []
    diagnostics["oversized_files"] = []
    return _invalid(diagnostics, "policy_changed")


def _same_policy_stamp(vault: Path, expected: tuple) -> bool:
    try:
        return _stamp(resolve_note_path(vault, _legacy.CONFIG_RELPATH)) == expected
    except (ValueError, OSError):
        return False


def retrieve(vault: Path, query: str, limit: int = 5, max_tokens: int = 1500, *, as_of=None, expand_links=0, backend="lexical", external_candidates=None, excluded_sources=()) -> dict:
    """Retrieve source-bound review context; explicit temporal bounds are half-open.

    as_of: ISO date (UTC midnight) or zoned ISO datetime. checked_at is an
    observation timestamp, not a validity boundary. Unknown currentness stays
    unknown. superseded_by uses a unique literal successor's valid_from.
    external_candidates: <=50 path strings/path+rank mappings, {candidates:[]},
    or a vault-relative <=64KiB JSON sidecar. Other provider fields are ignored.
    excluded_sources only adds deny scope. Links use literal unique filenames.
    """
    if as_of is None and expand_links == 0 and not isinstance(expand_links, bool) and backend == "lexical" and external_candidates is None and excluded_sources == ():
        return _legacy._legacy_recall(vault, query, limit, max_tokens)
    diagnostics = _legacy._diagnostics()
    diagnostics.update({"mode": "advanced", "backend": backend if isinstance(backend, str) else "invalid", "temporal": [], "temporal_rejected": 0, "graph_ambiguous": 0, "graph_unresolved": 0, "graph_links_considered": 0, "external_rejected": 0, "external_bytes_read": 0, "external_provider_status": "not_requested", "abstention": "host_decision", "source_authority": "unverified", "ranking": "lexical/BM25; hybrid uses RRF k=60; graph decays 0.5 per hop"})
    if not isinstance(query, str) or not _legacy._terms(query[:_legacy.MAX_QUERY_CHARS]):
        return _invalid(diagnostics, "invalid_query")
    if not isinstance(vault, Path):
        return _invalid(diagnostics, "invalid_vault")
    try:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _legacy.MAX_RESULTS or not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 0:
            raise ValueError("invalid bounds")
        if not isinstance(expand_links, int) or isinstance(expand_links, bool) or not 0 <= expand_links <= 2 or backend not in ("lexical", "bm25", "hybrid"):
            raise ValueError("invalid advanced mode")
        temporal_time = None if as_of is None else _time(as_of)
        if not isinstance(excluded_sources, (tuple, list)) or len(excluded_sources) > _legacy.MAX_RESULTS:
            raise ValueError("invalid excluded scope")
        for value in excluded_sources:
            if not isinstance(value, str) or len(value) > 1024:
                raise ValueError("excluded path exceeds bounds")
            relative_parts(value, "excluded source")
        external = [] if external_candidates is None or isinstance(external_candidates, str) else _candidate_list(external_candidates)
    except (ValueError, TypeError, OverflowError):
        return _invalid(diagnostics)
    try:
        resolve_note_path(vault, _legacy.CONFIG_RELPATH)
        safe_vault = vault.resolve(strict=True)
    except (ValueError, OSError):
        return _invalid(diagnostics, "invalid_vault")
    try:
        # Bind the bootstrap to a mark taken before reading/parsing. A mark
        # captured after the read could accidentally bless a replacement.
        policy_stamp = _stamp(resolve_note_path(safe_vault, _legacy.CONFIG_RELPATH))
    except (ValueError, OSError):
        return _invalid(diagnostics, "invalid_config")
    config = _legacy._read_config(safe_vault, diagnostics)
    if config is None:
        return _legacy._empty_result(diagnostics)
    if not _same_policy_stamp(safe_vault, policy_stamp):
        return _policy_changed(diagnostics)
    deny = tuple(config.get("deny_zones") or ())
    exclude = (*tuple(config.get("exclude_dirs") or ()), ".git", *tuple(excluded_sources))
    # One bootstrap read establishes policy. If that policy forbids its own
    # config, subsequent reads cannot lawfully establish freshness.
    if _legacy._classified_skip(relative_parts(_legacy.CONFIG_RELPATH, "config path"), deny, exclude):
        return _invalid(diagnostics, "invalid_config")
    if isinstance(external_candidates, str):
        try:
            external = _sidecar(safe_vault, external_candidates, deny, exclude, diagnostics)
        except (ValueError, OSError, UnicodeError, OverflowError, RecursionError):
            return _invalid(diagnostics, "invalid_external_candidates")
    if external_candidates is not None:
        diagnostics["external_provider_status"] = "candidates_only_unverified"
    bounded_query = query[:_legacy.MAX_QUERY_CHARS]
    query_terms = _legacy._terms(bounded_query)[:_legacy.MAX_QUERY_TERMS]
    phrase = _legacy._normalize(bounded_query)
    diagnostics["query_terms_used"] = len(query_terms)
    diagnostics["query_truncated"] = int(len(query) > _legacy.MAX_QUERY_CHARS)
    paths = _legacy._markdown_paths(safe_vault, deny, exclude, diagnostics)
    # Filename ambiguity is determined before file-count trimming. If the walk
    # itself was incomplete, graph expansion cannot prove global uniqueness.
    filenames = defaultdict(list)
    for rel, _ in paths:
        filenames[Path(rel).name].append(rel)
    complete_names = diagnostics["search_complete"]
    if len(paths) > _legacy.MAX_FILES:
        diagnostics["omitted_file_limit"] = len(paths) - _legacy.MAX_FILES
        _legacy._omit(diagnostics, "file_count_limit")
        paths = paths[:_legacy.MAX_FILES]
    documents = {}
    for index, (rel, path) in enumerate(paths):
        if not _same_policy_stamp(safe_vault, policy_stamp):
            return _policy_changed(diagnostics)
        diagnostics["files_considered"] += 1
        try:
            resolve_note_path(safe_vault, rel, deny)
            before = _stamp(path)
            if diagnostics["bytes_read"] + min(before[2], _legacy.MAX_FILE_BYTES + 1) > _legacy.MAX_TOTAL_READ_BYTES:
                diagnostics["omitted_total_byte_limit"] += len(paths) - index
                _legacy._omit(diagnostics, "total_byte_limit")
                break
            original_bytes = []
            text = _legacy._read_markdown(path, diagnostics, rel, raw_sink=original_bytes.append)
            if text is None:
                continue
            resolve_note_path(safe_vault, rel, deny)
            if before != _stamp(path):
                _legacy._omit(diagnostics, "source_changed")
                continue
            # UTF-8 notes are decoded without a BOM for ranking. Hash the raw
            # stable bytes, not normalized snippets or metadata guesses.
            raw = original_bytes[0]
            if before != _stamp(path) or raw.decode("utf-8-sig", errors="replace") != text:
                _legacy._omit(diagnostics, "source_changed")
                continue
            documents[rel] = {"path": rel, "disk_path": path, "text": text, "stamp": before, "sha256": hashlib.sha256(raw).hexdigest(), "metadata": _metadata(text)}
        except OverflowError:
            _legacy._omit(diagnostics, "total_byte_limit")
        except (ValueError, OSError):
            diagnostics["skipped_unsafe"] += 1
            _legacy._omit(diagnostics, "unsafe_path")
    eligible = {}
    for rel, doc in documents.items():
        temporal = _temporal(doc, documents, filenames, temporal_time, complete_names)
        doc["temporal"] = temporal
        if len(diagnostics["temporal"]) < _legacy.MAX_RESULTS:
            diagnostics["temporal"].append({"path": rel, **temporal})
        if temporal_time is not None and temporal["status"] in ("invalid", "expired", "not_yet_valid", "superseded", "supersession_unknown"):
            diagnostics["temporal_rejected"] += 1
            continue
        eligible[rel] = doc
    lexical_matches = {rel: match for rel, doc in eligible.items() if (match := _legacy._rank_document(rel, doc["text"], phrase, query_terms)) is not None}
    lexical_scores = {rel: match["score"] for rel, match in lexical_matches.items()}
    bm25 = _bm25(eligible, query_terms) if backend != "lexical" else {}
    external_ranks = {}
    for item in external:
        rel = item["path"]
        try:
            parts = relative_parts(rel, "external candidate")
            rel = "/".join(parts)
            if not rel.casefold().endswith(".md") or _legacy._classified_skip(parts, deny, exclude):
                raise ValueError("external path denied")
            resolve_note_path(safe_vault, rel, deny)
            if rel not in eligible or not eligible[rel]["text"].strip():
                raise ValueError("external source not safely read/temporally eligible")
            external_ranks[rel] = min(item["rank"], external_ranks.get(rel, item["rank"]))
        except (ValueError, OSError):
            diagnostics["external_rejected"] += 1
    lexical_ranks = {path: n for n, path in enumerate(_ranked_paths(lexical_scores), 1)}
    bm25_ranks = {path: n for n, path in enumerate(_ranked_paths(bm25), 1)}
    active = lexical_scores if backend == "lexical" else bm25
    candidates = set(active) | set(external_ranks)
    if backend == "hybrid":
        candidates |= set(lexical_scores)
    matches = {}
    for rel in candidates:
        doc = eligible[rel]
        match = dict(lexical_matches.get(rel) or _fallback(doc, phrase, query_terms))
        if backend == "bm25" and rel in bm25:
            _bm25_passage(doc, match, phrase, query_terms)
        contributions = {"lexical": lexical_scores.get(rel, 0), "bm25": bm25.get(rel, 0.0), "rrf_lexical": 0.0, "rrf_bm25": 0.0, "rrf_external": 0.0, "graph": 0.0}
        if backend == "hybrid" or external_candidates is not None:
            if rel in lexical_ranks and backend != "bm25":
                contributions["rrf_lexical"] = 1 / (RRF_K + lexical_ranks[rel])
            if rel in bm25_ranks and backend != "lexical":
                contributions["rrf_bm25"] = 1 / (RRF_K + bm25_ranks[rel])
            if rel in external_ranks:
                contributions["rrf_external"] = 1 / (RRF_K + external_ranks[rel])
            match["score"] = sum(contributions[key] for key in ("rrf_lexical", "rrf_bm25", "rrf_external"))
        else:
            match["score"] = active[rel]
        match.update({"scores": contributions, "ranks": {"lexical": lexical_ranks.get(rel), "bm25": bm25_ranks.get(rel), "external": external_ranks.get(rel)}, "source": _source(doc, match), "temporal": doc["temporal"], "graph": [], "review_status": "needs_review", "source_authority": "unverified"})
        matches[rel] = match
    seeds = sorted(matches, key=lambda rel: (-matches[rel]["score"], rel.casefold(), rel))[:limit]
    if expand_links and complete_names:
        frontier = [(seed, seed, 0, (seed,)) for seed in seeds]
        while frontier:
            origin, seed, depth, visited = frontier.pop(0)
            if depth >= expand_links:
                continue
            doc = eligible[origin]
            links = 0
            for line_number, line in enumerate(doc["text"].splitlines(), 1):
                for found in _LINK.finditer(line):
                    if links >= MAX_LINKS_PER_NOTE:
                        _legacy._omit(diagnostics, "graph_link_limit")
                        break
                    links += 1
                    diagnostics["graph_links_considered"] += 1
                    filename = _filename(found.group(1))
                    destinations = filenames.get(filename, []) if filename else []
                    if len(destinations) > 1:
                        diagnostics["graph_ambiguous"] += 1
                        continue
                    if not destinations or destinations[0] not in eligible:
                        diagnostics["graph_unresolved"] += 1
                        continue
                    target = destinations[0]
                    if target in visited:
                        continue
                    proof = {"origin_path": origin, "origin_line": line_number, "origin_quote": found.group(0), "origin_sha256": doc["sha256"], "target_filename": filename, "depth": depth + 1, "seed_path": seed}
                    propagation = matches[seed]["score"] * (0.5 ** (depth + 1))
                    if target not in matches:
                        target_doc = eligible[target]
                        match = _fallback(target_doc, phrase, query_terms)
                        match.update({"score": propagation, "scores": {"lexical": 0, "bm25": 0.0, "rrf_lexical": 0.0, "rrf_bm25": 0.0, "rrf_external": 0.0, "graph": propagation}, "ranks": {"lexical": None, "bm25": None, "external": None}, "source": _source(target_doc, match), "temporal": target_doc["temporal"], "graph": [], "review_status": "needs_review", "source_authority": "unverified"})
                        matches[target] = match
                    if len(matches[target]["graph"]) < MAX_GRAPH_PROOFS and proof not in matches[target]["graph"]:
                        matches[target]["graph"].append(proof)
                    if len(frontier) < _legacy.MAX_FILES and len(matches) < _legacy.MAX_FILES:
                        frontier.append((target, seed, depth + 1, (*visited, target)))
                if links >= MAX_LINKS_PER_NOTE:
                    break
    elif expand_links:
        _legacy._omit(diagnostics, "graph_filename_index_incomplete")
    # Recheck the policy and every returned source/graph origin. A changed
    # policy never yields previously read content. No old index/cache is used.
    final_config = _legacy._read_config(safe_vault, _legacy._diagnostics())
    if final_config != config:
        return _policy_changed(diagnostics)
    ranked = sorted(matches.values(), key=lambda match: (-match["score"], match["path"].casefold(), match["path"]))
    selected = []
    verified_hashes = {}
    for match in ranked:
        if not _same_policy_stamp(safe_vault, policy_stamp):
            return _policy_changed(diagnostics)
        bound_paths = {match["path"]} | {proof["origin_path"] for proof in match["graph"]} | {proof["seed_path"] for proof in match["graph"]}
        bound_paths |= {documents[rel]["temporal"]["supersession_source"]["path"] for rel in tuple(bound_paths) if "supersession_source" in documents[rel]["temporal"]}
        try:
            for rel in sorted(bound_paths):
                if not _same_policy_stamp(safe_vault, policy_stamp):
                    return _policy_changed(diagnostics)
                path = resolve_note_path(safe_vault, rel, deny)
                if _legacy._classified_skip(relative_parts(rel, "source recheck"), deny, exclude) or _stamp(path) != documents[rel]["stamp"]:
                    raise ValueError("source changed")
                if rel not in verified_hashes:
                    remaining = _legacy.MAX_TOTAL_READ_BYTES - diagnostics["bytes_read"]
                    if documents[rel]["stamp"][2] > remaining:
                        raise OverflowError("source verification exceeds total read budget")
                    raw, stable = _legacy._read_regular_bytes(path, min(_legacy.MAX_FILE_BYTES, remaining))
                    diagnostics["bytes_read"] += len(raw)
                    if not stable or _stamp(path) != documents[rel]["stamp"]:
                        raise ValueError("source changed while verifying")
                    verified_hashes[rel] = hashlib.sha256(raw).hexdigest()
                if verified_hashes[rel] != documents[rel]["sha256"]:
                    raise ValueError("source content changed")
        except OverflowError:
            _legacy._omit(diagnostics, "source_verification_byte_limit")
            continue
        except (ValueError, OSError):
            _legacy._omit(diagnostics, "source_changed")
            continue
        selected.append(match)
        if len(selected) >= limit:
            break
    if _legacy._read_config(safe_vault, _legacy._diagnostics()) != config or not _same_policy_stamp(safe_vault, policy_stamp):
        return _policy_changed(diagnostics)
    context = _legacy._render_context(selected, max_tokens, diagnostics)
    # A later hash/config read or rendering callback must not leave an earlier
    # source silently stale. Sweep every selected provenance dependency after
    # the last data read and render, and check policy on both sides of the sweep.
    if not _same_policy_stamp(safe_vault, policy_stamp):
        return _policy_changed(diagnostics)
    final_bound = {match["path"] for match in selected}
    final_bound |= {proof[key] for match in selected for proof in match["graph"] for key in ("origin_path", "seed_path")}
    final_bound |= {documents[rel]["temporal"]["supersession_source"]["path"] for rel in tuple(final_bound) if "supersession_source" in documents[rel]["temporal"]}
    try:
        for rel in sorted(final_bound):
            path = resolve_note_path(safe_vault, rel, deny)
            if _legacy._classified_skip(relative_parts(rel, "final source sweep"), deny, exclude) or _stamp(path) != documents[rel]["stamp"]:
                raise ValueError("source changed after verification")
    except (ValueError, OSError):
        diagnostics["temporal"] = []
        _legacy._omit(diagnostics, "source_changed")
        return _invalid(diagnostics, "source_changed")
    if not _same_policy_stamp(safe_vault, policy_stamp):
        return _policy_changed(diagnostics)
    return {"context": context, "matches": selected, "diagnostics": diagnostics}
