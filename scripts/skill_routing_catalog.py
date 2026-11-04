"""Explicit bounded skill manifest collection; vocabulary overlap is advisory."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import unicodedata

from skill_routing_contract import RoutingError, validate_catalog

MAX_MANIFEST_BYTES = 256 * 1024
MAX_ENTRIES = 10000
OWNER = re.compile(r'[a-z0-9_-]{1,64}\Z')
FIELD = re.compile(r'([A-Za-z][A-Za-z0-9_-]*):[ \t]*(.*)\Z')


def _plain_string(raw):
    if raw.startswith('"'):
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            raise RoutingError('unsupported_manifest_scalar') from None
        if not isinstance(value, str):
            raise RoutingError('invalid_manifest_string')
        return value
    if raw.startswith("'"):
        if len(raw) < 2 or not raw.endswith("'"):
            raise RoutingError('unsupported_manifest_scalar')
        value = raw[1:-1]
        if "'" in value.replace("''", ''):
            raise RoutingError('unsupported_manifest_scalar')
        return value.replace("''", "'")
    if (not raw or raw[0] in '>|[{}]!&*#@`%,' or raw in {'-', '?', ':'}
            or raw.startswith(('- ', '? ', ': '))
            or ' #' in raw or ': ' in raw or raw.endswith(':')
            or re.fullmatch(r'[+-]?(?:[0-9][0-9_]*(?:\.[0-9_]*)?(?:[eE][+-]?[0-9]+)?|\.[0-9]+|0[xob][0-9a-fA-F_]+|\.(?:inf|nan))', raw, re.I)
            or re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[Tt ].*)?', raw)
            or re.fullmatch(r'[+-]?[0-9]+(?::[0-9]+)+(?:\.[0-9]+)?', raw)
            or raw.casefold() in {'true', 'false', 'null', '~', 'yes', 'no', 'on', 'off'}):
        raise RoutingError('unsupported_manifest_scalar')
    return raw


def _is_link(path):
    info = path.lstat()
    return (stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, 'st_file_attributes', 0) & 0x400))


def _check_path(path):
    absolute = Path(os.path.abspath(path))
    try:
        if any(_is_link(p) for p in (absolute, *absolute.parents)):
            raise RoutingError('symlink_manifest_path')
    except OSError:
        raise RoutingError('unreadable_manifest_path') from None
    return absolute


def _manifest(path):
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_MANIFEST_BYTES:
            raise RoutingError('invalid_manifest_file')
        with path.open('rb') as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        after = path.lstat()
        if (len(raw) > MAX_MANIFEST_BYTES or _is_link(path)
                or (before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_ino, after.st_size, after.st_mtime_ns)):
            raise RoutingError('manifest_changed_or_oversize')
        lines = raw.decode('utf-8-sig', errors='strict').split('\n')
    except (OSError, UnicodeError):
        raise RoutingError('unreadable_manifest') from None
    lines = [line.rstrip('\r') for line in lines]
    if not lines or lines[0] != '---':
        raise RoutingError('missing_manifest_frontmatter')
    fields = {}
    for line in lines[1:]:
        if line == '---':
            break
        if not line or line.startswith('#'):
            continue
        match = FIELD.fullmatch(line)
        if not match or match[1] in fields:
            raise RoutingError('unsupported_manifest_frontmatter')
        fields[match[1]] = match[2].strip()
    else:
        raise RoutingError('unterminated_manifest_frontmatter')
    if not {'name', 'description'} <= fields.keys():
        raise RoutingError('missing_manifest_field')
    # This intentionally restricted frontmatter reader must not label malformed
    # ignored metadata as valid YAML. Non-scalar/unsupported metadata is refused.
    for value in fields.values():
        _plain_string(value)
    name, description = (_plain_string(fields[k]) for k in ('name', 'description'))
    if (not OWNER.fullmatch(name) or not description.strip() or len(description) > 8000
            or any(unicodedata.category(c).startswith('C') for c in description)):
        raise RoutingError('invalid_manifest_string')
    return name, description, hashlib.sha256(raw).hexdigest()


def build_catalog(roots):
    """Read SKILL.md only under explicitly supplied (owner, directory) roots."""
    if not isinstance(roots, (list, tuple)) or not 1 <= len(roots) <= 16:
        raise RoutingError('invalid_manifest_roots')
    skills = []
    seen = set()
    inspected = 0
    for pair in roots:
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or not isinstance(pair[0], str) or not OWNER.fullmatch(pair[0])):
            raise RoutingError('invalid_manifest_owner')
        if not isinstance(pair[1], (str, os.PathLike)):
            raise RoutingError('invalid_manifest_root')
        owner, root = pair[0], _check_path(Path(pair[1]))
        if not root.is_dir():
            raise RoutingError('invalid_manifest_root')
        try:
            def fail_scan(_error):
                raise RoutingError('unreadable_manifest_root')
            for directory, dirs, files in os.walk(root, followlinks=False, onerror=fail_scan):
                dirs.sort(); files.sort()
                inspected += len(dirs) + len(files)
                if inspected > MAX_ENTRIES:
                    raise RoutingError('manifest_discovery_limit')
                for child in dirs + files:
                    if _is_link(Path(directory) / child):
                        raise RoutingError('symlink_manifest_path')
                if 'SKILL.md' not in files:
                    continue
                path = Path(directory) / 'SKILL.md'
                name, description, digest = _manifest(path)
                identifier = owner + ':' + name
                if identifier in seen:
                    raise RoutingError('duplicate_manifest_id')
                seen.add(identifier)
                skills.append({'id': identifier, 'name': name, 'description': description,
                               'source_sha256': digest,
                               'source_path': owner + '/' + path.relative_to(root).as_posix()})
                if len(skills) > 256:
                    raise RoutingError('catalog_size_limit')
        except OSError:
            raise RoutingError('unreadable_manifest_root') from None
    return validate_catalog({'schema_version': 1, 'kind': 'skill_routing_catalog',
                             'source_kind': 'manifest_scan',
                             'skills': sorted(skills, key=lambda row: row['id'])})


def analyze_catalog(catalog):
    """Return deterministic candidates; never asserts actual routing collisions."""
    catalog = validate_catalog(catalog)
    groups = defaultdict(list)
    tokens = {}
    for row in catalog['skills']:
        groups[row['name']].append(row['id'])
        tokens[row['id']] = set(re.findall(r'[^\W_]+', row['description'].casefold()))
    same = [{'name': name, 'skills': sorted(ids)} for name, ids in sorted(groups.items())
            if len(ids) > 1]
    pairs = []
    ids = sorted(tokens)
    for i, left in enumerate(ids):
        for right in ids[i + 1:]:
            shared = tokens[left] & tokens[right]
            union = tokens[left] | tokens[right]
            score = len(shared) / len(union) if union else 0
            if score >= .35:
                pairs.append({'skills': [left, right], 'jaccard': round(score, 6)})
    pairs.sort(key=lambda item: (-item['jaccard'], item['skills']))
    return {'schema_version': 1, 'kind': 'skill_routing_catalog_analysis',
            'model_run_performed': False, 'same_names': same,
            'overlap_candidates': pairs[:50],
            'scope': 'Unicode word-set Jaccard candidates, not routing evidence; '
                     'not BM25, language understanding, or a Korean quality benchmark.'}
