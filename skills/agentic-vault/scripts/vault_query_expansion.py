#!/usr/bin/env python3
"""Opt-in bounded, deterministic query synonyms. Aliases never resolve links.

Plain mappings contain one ``trigger => replacement`` per line, with optional
blank/comment lines. They are data only. Conflicting triggers and shared aliases
are diagnosed and omitted. No cache, dependency, model or network is involved.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
from pathlib import Path

MAX_MAPPING_BYTES = 64 * 1024
MAX_MAPPING_ENTRIES = 128
MAX_ALIAS_LINES = 128
MAX_ALIASES_PER_NOTE = 16
MAX_VALUE_CHARS = 256
MAX_EXPANSIONS = 16
MAX_ADDED_TERMS = 16
_PARTICLES = ('으로부터', '에게서', '에서는', '으로는', '에서', '에게', '으로', '까지', '부터', '은', '는', '이', '가', '을', '를', '와', '과', '의', '도')


def _base(query):
    import vault_recall as recall
    terms = list(recall._terms(query[:recall.MAX_QUERY_CHARS]))[:recall.MAX_QUERY_TERMS] if isinstance(query, str) else []
    return {'enabled': False, 'status': 'disabled', 'terms': terms, 'expansions': [],
            'ambiguities': [], 'errors': [], 'mapping_bytes_read': 0,
            'alias_documents_considered': 0, 'omitted_expansions': 0,
            'limits': {'mapping_bytes': MAX_MAPPING_BYTES, 'mapping_entries': MAX_MAPPING_ENTRIES,
                       'alias_lines': MAX_ALIAS_LINES, 'aliases_per_note': MAX_ALIASES_PER_NOTE,
                       'value_chars': MAX_VALUE_CHARS, 'expansions': MAX_EXPANSIONS,
                       'added_terms': MAX_ADDED_TERMS, 'query_terms': recall.MAX_QUERY_TERMS}}


def inventory_mark(path, *, directory=False):
    """Bind an existing file/directory generation without reading its contents."""
    metadata = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(metadata.st_mode) or (not directory and metadata.st_nlink != 1) or getattr(metadata, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
        raise ValueError('unsafe inventory member')
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)


def verify_inventory(vault, documents, directories, *, deny=(), exclude=()):
    """Check every already-scanned potential alias destination and directory.

    No second walk or payload reread occurs. Directory marks detect membership
    changes; note marks include notes without aliases and temporal rejects.
    These are metadata point checks, not an atomic snapshot or a lock on
    writers. A rewrite preserving the checked marks can evade this detector.
    Source SHA values bind the initial read; this sweep does not freshly hash
    every possible destination or certify its current bytes cryptographically.
    """
    import vault_recall as recall
    from vault_paths import relative_parts, resolve_note_path
    result = {'status': 'ok', 'documents_checked': 0, 'directories_checked': 0,
              'method': 'bounded marks from the original scan; no second scan or payload reads',
              'assurance': 'metadata_generation_only; preserved marks can evade checks; initial source hashes do not certify current inventory bytes'}
    try:
        for rel, doc in sorted(documents.items()):
            parts = relative_parts(rel, 'alias inventory member')
            path = resolve_note_path(vault, rel, deny)
            if recall._classified_skip(parts, deny, exclude) or inventory_mark(path) != doc['stamp']:
                raise ValueError('alias inventory changed')
            result['documents_checked'] += 1
        for rel, expected in sorted(directories.items()):
            if rel:
                parts = relative_parts(rel, 'alias inventory directory')
                path = resolve_note_path(vault, rel, deny)
                if recall._classified_skip(parts, deny, exclude):
                    raise ValueError('alias inventory directory excluded')
            else:
                # The root was bootstrapped through resolve_note_path; validate
                # again without inventing an empty relative-note path.
                resolve_note_path(vault, recall.CONFIG_RELPATH)
                path = vault
            if inventory_mark(path, directory=True) != expected:
                raise ValueError('alias inventory directory changed')
            result['directories_checked'] += 1
    except (OSError, ValueError, KeyError):
        result['status'] = 'source_changed'
    return result


def invalidate_expansion(result, query, *, reason='alias_inventory_changed'):
    result.update(status='source_changed', terms=_base(query)['terms'], expansions=[])
    if len(result['errors']) < MAX_EXPANSIONS:
        result['errors'].append(reason)


def _value(value):
    value = value.strip()
    if value.startswith('"'):
        value = json.loads(value)
    elif value.startswith("'"):
        if not value.endswith("'") or len(value) < 2:
            raise ValueError('unclosed alias')
        value = value[1:-1].replace("''", "'")
    if not isinstance(value, str) or not value or len(value) > MAX_VALUE_CHARS or any(ord(c) < 32 for c in value) or value[:1] in '[{&*!>|' or ' #' in value:
        raise ValueError('unsupported or overlong alias')
    return value


def _aliases(text):
    """Small YAML scalar/list subset; reject ambiguous/unclosed alias metadata."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != '---':
        return []
    closing = next((i for i, line in enumerate(lines[1:MAX_ALIAS_LINES+1], 1) if line.strip() == '---'), None)
    if closing is None:
        raise ValueError('unclosed or overlong frontmatter')
    aliases, active, seen = [], False, False
    for line in lines[1:closing]:
        if line.startswith('aliases:'):
            if seen:
                raise ValueError('duplicate aliases')
            seen, active = True, True
            rest = line[len('aliases:'):].strip()
            if rest.startswith('['):
                # JSON arrays preserve escaped commas/quotes deterministically;
                # plain YAML arrays permit only the same scalar subset.
                try:
                    values = json.loads(rest)
                except json.JSONDecodeError:
                    if not rest.endswith(']') or any(c in rest for c in '\"\'{}'):
                        raise ValueError('ambiguous aliases list')
                    values = [v.strip() for v in rest[1:-1].split(',')] if rest[1:-1].strip() else []
                if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                    raise ValueError('aliases must be strings')
                aliases.extend(_value(v) for v in values)
                active = False
            elif rest:
                aliases.append(_value(rest))
                active = False
        elif active and re.match(r'^\s+-\s+', line):
            aliases.append(_value(re.sub(r'^\s+-\s+', '', line)))
        elif line and not line.startswith((' ', '\t', '#')):
            active = False
        elif active and line.strip() and not line.lstrip().startswith('#'):
            raise ValueError('unsupported aliases structure')
        if len(aliases) > MAX_ALIASES_PER_NOTE:
            raise ValueError('alias count limit')
    return sorted(set(aliases), key=lambda s: (s.casefold(), s))


def _variants(query):
    import vault_recall as recall
    terms = recall._terms(query)
    stripped = []
    for term in terms:
        candidate = term
        if re.fullmatch('[가-힣]+', term):
            for particle in _PARTICLES:
                if term.endswith(particle) and len(term) - len(particle) >= 2:
                    candidate = term[:-len(particle)]
                    break
        stripped.append(candidate)
    return (recall._normalize(query), ' '.join(terms), ' '.join(stripped))


def _trigger_hits(trigger, variants):
    import vault_recall as recall
    key = recall._normalize(trigger)
    compact = key.replace(' ', '')
    for variant in variants:
        if ' ' + key + ' ' in ' ' + variant + ' ':
            return True
        if compact == variant.replace(' ', ''):
            return True
        # Korean spacing/particles are matched only for an explicit, bounded
        # alias or mapping trigger; this is not automatic stemming of corpus.
        if len(compact) >= 3 and re.fullmatch('[가-힣]+', compact) and compact in variant.replace(' ', ''):
            return True
    return False


def _read_mapping(vault, mapping, deny, exclude, result, byte_limit=MAX_MAPPING_BYTES):
    import vault_recall as recall
    from vault_paths import relative_parts, resolve_note_path
    if not isinstance(mapping, str) or len(mapping) > 1024:
        raise ValueError('mapping must be a vault-relative plain file')
    parts = relative_parts(mapping, 'query mapping')
    if recall._classified_skip(parts, deny, exclude):
        raise ValueError('mapping denied or excluded')
    path = resolve_note_path(vault, mapping, deny)
    raw, stable = recall._read_regular_bytes(path, min(MAX_MAPPING_BYTES, byte_limit))
    result['mapping_bytes_read'] += len(raw)
    if not stable:
        raise ValueError('mapping changed')
    text = raw.decode('utf-8-sig')
    entries = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if line.count('=>') != 1:
            raise ValueError('mapping syntax')
        trigger, replacement = (_value(v) for v in line.split('=>'))
        entries.append((trigger, replacement, number))
        if len(entries) > MAX_MAPPING_ENTRIES:
            raise ValueError('mapping entry limit')
    return entries, hashlib.sha256(raw).hexdigest()


def _expand_documents(vault, query, documents, *, mapping=None, deny=(), exclude=(), inventory_complete=True, mapping_byte_limit=MAX_MAPPING_BYTES):
    """Internal integration: reuse stable, eligible, already policy-read notes."""
    import vault_recall as recall
    result = _base(query)
    result.update(enabled=True, status='ok')
    variants = _variants(query[:recall.MAX_QUERY_CHARS])
    groups = {}
    if mapping is not None:
        try:
            entries, digest = _read_mapping(vault, mapping, deny, exclude, result, mapping_byte_limit)
            result['mapping_source'] = {'path': mapping, 'sha256': digest}
            for trigger, replacement, line in entries:
                key = recall._normalize(trigger)
                groups.setdefault(key.replace(' ', ''), []).append({'trigger': key, 'replacement': replacement,
                    'source_kind': 'plain_mapping', 'source_path': mapping, 'source_line': line, 'source_sha256': digest})
        except (ValueError, OSError, UnicodeError, OverflowError, RecursionError):
            result.update(status='invalid_mapping', errors=['mapping_invalid_or_unavailable'])
            return result
    alias_groups = {}
    for rel, doc in sorted(documents.items(), key=lambda item: (item[0].casefold(), item[0])):
        result['alias_documents_considered'] += 1
        try:
            aliases = _aliases(doc['text'])
            title = recall._title(doc['text'].splitlines(), Path(rel).stem)[0]
            replacement = (Path(rel).stem + ' ' + title)[:MAX_VALUE_CHARS]
            for alias in aliases:
                key = recall._normalize(alias)
                alias_groups.setdefault(key.replace(' ', ''), []).append({'trigger': key, 'replacement': replacement,
                    'source_kind': 'frontmatter_alias', 'source_path': rel,
                    'source_sha256': doc['sha256']})
        except (ValueError, json.JSONDecodeError, RecursionError):
            if len(result['errors']) < MAX_EXPANSIONS:
                result['errors'].append({'path': rel, 'reason': 'invalid_alias_metadata'})
    # Mapping and aliases have separate ambiguity domains. Explicit mapping
    # duplicates conflict on normalized replacement; aliases conflict on paths.
    candidates = []
    for source_groups in (groups, alias_groups):
        for trigger, entries in sorted(source_groups.items()):
            if not any(_trigger_hits(entry['trigger'], variants) for entry in entries):
                continue
            identities = {entry['source_path'] if entry['source_kind'] == 'frontmatter_alias'
                          else recall._normalize(entry['replacement']) for entry in entries}
            if len(identities) > 1 or (source_groups is alias_groups and not inventory_complete):
                if len(result['ambiguities']) < MAX_EXPANSIONS:
                    result['ambiguities'].append({'trigger': trigger, 'source_kind': entries[0]['source_kind'],
                        'source_paths': sorted({entry['source_path'] for entry in entries}),
                        'reason': 'multiple_destinations' if len(identities) > 1 else 'incomplete_inventory'})
                continue
            candidates.append(entries[0])
    added = 0
    for entry in candidates:
        new = [term for term in recall._terms(entry['replacement']) if term not in result['terms']]
        capacity = min(MAX_ADDED_TERMS-added, recall.MAX_QUERY_TERMS-len(result['terms']))
        if len(result['expansions']) >= MAX_EXPANSIONS or capacity <= 0:
            result['omitted_expansions'] += 1
            continue
        new = new[:capacity]
        if new:
            result['terms'].extend(new)
            added += len(new)
            result['expansions'].append({**entry, 'added_terms': new})
    return result


def expand_query(vault: Path, query: str, *, enabled=False, mapping=None) -> dict:
    """Return deterministic original-first terms, provenance, diagnostics/limits.

    Disabled calls perform no I/O. Enabled standalone calls use recall's exact
    safe inventory and byte budgets. Retrieval integrates the internal helper
    with its existing inventory rather than scanning the corpus twice.
    """
    import vault_recall as recall
    result = _base(query)
    if enabled is False:
        return result
    if enabled is not True or not isinstance(query, str) or not result['terms'] or not isinstance(vault, Path):
        result.update(enabled=True, status='invalid_options')
        return result
    diagnostics = recall._diagnostics()
    try:
        from vault_paths import resolve_note_path
        resolve_note_path(vault, recall.CONFIG_RELPATH)
        safe_vault = vault.resolve(strict=True)
        config_path = resolve_note_path(safe_vault, recall.CONFIG_RELPATH)
        policy_mark = inventory_mark(config_path)
        config = recall._read_config(safe_vault, diagnostics)
        if config is None:
            result.update(enabled=True, status=diagnostics['status'])
            return result
        deny, exclude = tuple(config.get('deny_zones') or ()), (*tuple(config.get('exclude_dirs') or ()), '.git')
        directories = {}
        def bind_directory(rel, path):
            directories[rel] = inventory_mark(path, directory=True)
        paths = recall._markdown_paths(safe_vault, deny, exclude, diagnostics, directory_sink=bind_directory)
        if len(paths) > recall.MAX_FILES:
            recall._omit(diagnostics, 'file_count_limit')
        documents = {}
        for rel, path in paths[:recall.MAX_FILES]:
            raw = []
            before = inventory_mark(path)
            text = recall._read_markdown(path, diagnostics, rel, raw_sink=raw.append)
            if text is not None:
                if inventory_mark(path) != before:
                    recall._omit(diagnostics, 'source_changed')
                    continue
                documents[rel] = {'text': text, 'sha256': hashlib.sha256(raw[0]).hexdigest(), 'stamp': before}
        result = _expand_documents(safe_vault, query, documents, mapping=mapping, deny=deny, exclude=exclude,
                                   inventory_complete=diagnostics['search_complete'],
                                   mapping_byte_limit=max(0, recall.MAX_TOTAL_READ_BYTES-diagnostics['bytes_read']))
        diagnostics['bytes_read'] += result['mapping_bytes_read']
        alias_binding_used = any(entry['source_kind'] == 'frontmatter_alias' for entry in result['expansions'])
        if recall._read_config(safe_vault, recall._diagnostics()) != config:
            result.update(status='policy_changed', terms=_base(query)['terms'], expansions=[])
        if result['status'] == 'ok':
            sources = {entry['source_path']: entry['source_sha256'] for entry in result['expansions']
                       if entry['source_kind'] == 'frontmatter_alias'}
            for rel, digest in sorted(sources.items()):
                try:
                    path = resolve_note_path(safe_vault, rel, deny)
                    remaining = max(0, recall.MAX_TOTAL_READ_BYTES-diagnostics['bytes_read'])
                    raw, stable = recall._read_regular_bytes(path, min(recall.MAX_FILE_BYTES, remaining))
                    diagnostics['bytes_read'] += len(raw)
                    if not stable or hashlib.sha256(raw).hexdigest() != digest:
                        raise ValueError('alias source changed')
                except (ValueError, OSError, OverflowError):
                    result.update(status='source_changed', terms=_base(query)['terms'], expansions=[])
                    break
            checked, fresh = verify_mapping(safe_vault, result, deny=deny, exclude=exclude,
                byte_limit=max(0, recall.MAX_TOTAL_READ_BYTES-diagnostics['bytes_read']))
            result['mapping_bytes_read'] += checked
            diagnostics['bytes_read'] += checked
            if not fresh:
                result.update(status='source_changed', terms=_base(query)['terms'], expansions=[])
            if recall._read_config(safe_vault, recall._diagnostics()) != config:
                result.update(status='policy_changed', terms=_base(query)['terms'], expansions=[])
        if alias_binding_used and result['status'] == 'ok':
            # This follows mapping/config/source payload reads. Existing notes
            # without aliases can become competitors, so verify the whole
            # original inventory rather than only chosen provenance sources.
            checked = verify_inventory(safe_vault, documents, directories, deny=deny, exclude=exclude)
            result['inventory_verification'] = checked
            if checked['status'] != 'ok':
                invalidate_expansion(result, query)
        # A parsed config can be replaced by a later callback before it returns.
        # The final mark follows all reads and inventory checks without rereading
        # a policy that may now deny itself.
        try:
            fresh_policy = inventory_mark(resolve_note_path(safe_vault, recall.CONFIG_RELPATH)) == policy_mark
        except (OSError, ValueError):
            fresh_policy = False
        if not fresh_policy:
            result.update(status='policy_changed', terms=_base(query)['terms'], expansions=[])
        result['inventory_diagnostics'] = diagnostics
        return result
    except (OSError, ValueError):
        result.update(enabled=True, status='invalid_vault')
        return result


def verify_mapping(vault, expansion, *, deny=(), exclude=(), byte_limit=MAX_MAPPING_BYTES):
    """Reread the exact mapping under policy; return counted bytes and freshness."""
    import vault_recall as recall
    from vault_paths import relative_parts, resolve_note_path
    source = expansion.get('mapping_source')
    if source is None:
        return 0, True
    try:
        parts = relative_parts(source['path'], 'mapping recheck')
        if recall._classified_skip(parts, deny, exclude):
            return 0, False
        path = resolve_note_path(vault, source['path'], deny)
        raw, stable = recall._read_regular_bytes(path, min(MAX_MAPPING_BYTES, byte_limit))
        return len(raw), stable and hashlib.sha256(raw).hexdigest() == source['sha256']
    except (ValueError, OSError, OverflowError):
        return 0, False


def rank_expanded(rel_path, text, phrase, terms):
    """Keep lexical scoring; prefer actual body passages over alias/title ties."""
    import vault_recall as recall
    match = recall._rank_document(rel_path, text, phrase, terms)
    if match is None:
        return None
    lines = text.splitlines()
    frontmatter = bool(lines and lines[0].strip() == '---')
    best = None
    fenced = False
    for number, line in enumerate(lines, 1):
        if frontmatter:
            if number > 1 and line.strip() == '---':
                frontmatter = False
            continue
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
            continue
        if fenced or not line.strip() or recall._HEADING_RE.match(line):
            continue
        quality = recall._line_quality(recall._normalize(line), phrase, terms)
        if quality[1] and (best is None or quality > best[0]):
            best = (quality, number, line)
    if best:
        match.update(line=best[1], snippet=recall._snippet(best[2], phrase, terms))
    return match
