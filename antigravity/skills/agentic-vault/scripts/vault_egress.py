#!/usr/bin/env python3
"""Warning-only bounded secret/Korean-identifier inspection; no network or model.

Uses the shared D1 inspection copy, never rewrites user text. Matches are omitted
from diagnostics. A clean result is best-effort pattern coverage, not a guarantee.
Optional external scanners are separate explicitly invoked tools, not prerequisites.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import re
from urllib.parse import parse_qsl, urlsplit

from jev_client import _normalized, _matches_sensitive
from vault_paths import relative_parts, zone_matches

MAX_NOTE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_NOTES = 256
MAX_WARNINGS = 128
DEFAULT_DENY = ('10-inbox/_processed', '20-knowledge/_archive', '50-projects/_completed',
                '90-assets', '.obsidian', '.git')
ID_RE = re.compile(r'(?<![0-9])([0-9]{2})([0-9]{2})([0-9]{2})[- \t]?([1-8])([0-9]{6})(?![0-9])')
_URL_RE = re.compile(r'https?://[^\s<>"\'`)\]]+', re.I)
URL_SECRET_QUERY_KEYS = frozenset({
    'token', 'access_token', 'refresh_token', 'auth_token', 'id_token',
    'api_key', 'apikey', 'client_secret', 'secret', 'password', 'passwd',
    'signature', 'sig', 'x-amz-signature', 'x-amz-credential',
    'x-amz-security-token', 'x-goog-signature', 'x-goog-credential',
    'sas', 'accountseq',
})


def _is_template_placeholder(value: str) -> bool:
    stripped = value.strip()
    return (
        (stripped.startswith('<') and stripped.endswith('>'))
        or (stripped.startswith('{') and stripped.endswith('}'))
    )


def _urls_have_secret_query(text: str) -> bool:
    for match in _URL_RE.finditer(text):
        raw_url = match.group(0).rstrip('.,;:')
        try:
            query = urlsplit(raw_url).query
        except ValueError:
            continue
        if not query:
            continue
        for key, value in parse_qsl(query, keep_blank_values=False):
            key_norm = key.strip().casefold()
            val_clean = value.strip()
            if (
                key_norm in URL_SECRET_QUERY_KEYS
                and len(val_clean) >= 4
                and not _is_template_placeholder(val_clean)
            ):
                return True
    return False


def _has_url_secret(text: str) -> bool:
    if _urls_have_secret_query(text):
        return True
    if text.isascii():
        return False
    normalized = _normalized(text)
    return normalized != text and _urls_have_secret_query(normalized)


def _warning(code, path, line, reason=None):
    facts = {'code': code, 'path': path, 'line': line, 'reason': reason}
    digest = hashlib.sha256(json.dumps(facts, sort_keys=True).encode('utf-8')).hexdigest()
    # A filename can itself contain a credential or identifier. Inspect the
    # complete label before truncation; only its opaque issue hash survives.
    safe_path = ('<redacted-sensitive-path>' if _matches_sensitive(path)
                 or _has_korean_id(_normalized(path))
                 or _has_url_secret(path) else path[:256])
    messages = {'egress-secret': 'Possible credential pattern in selected original; review before egress',
        'egress-korean-id': 'Possible Korean personal identifier; review before egress',
        'egress-url-secret': 'Possible credential or signature query parameter in URL; review before egress',
        'egress-incomplete': 'Egress pattern scan incomplete; no safety claim is available'}
    return {'issue_id': 'lint-'+digest[:20], 'code': code, 'severity': 'warning',
            'path': safe_path, 'line': line, 'message': messages[code],
            'details': {'reason': reason} if reason else {'coverage': 'best_effort_pattern_only'}}


def _has_korean_id(text):
    for match in ID_RE.finditer(text):
        year, month, day, sex = (int(match[i]) for i in (1, 2, 3, 4))
        century = 1900 if sex in (1, 2, 5, 6) else 2000
        try:
            date(century + year, month, day)
        except ValueError:
            continue
        # This intentionally warns without demanding the historical checksum:
        # the modern identifier allocation does not make that a safe exclusion.
        return True
    return False


def scan_text(text, path=''):
    """One finding per category; line 0 means a whole-original match, no values."""
    if not isinstance(path, str) or len(path) > 1024 or not isinstance(text, str):
        return [_warning('egress-incomplete', '', 0, 'invalid_input')]
    try:
        size = len(text.encode('utf-8'))
    except UnicodeError:
        return [_warning('egress-incomplete', path, 0, 'invalid_utf8')]
    if size > MAX_NOTE_BYTES:
        return [_warning('egress-incomplete', path, 0, 'note_byte_limit')]
    findings, found = [], set()
    for line, original in enumerate(text.splitlines(), 1):
        if 'egress-secret' not in found and _matches_sensitive(original):
            findings.append(_warning('egress-secret', path, line))
            found.add('egress-secret')
        if 'egress-korean-id' not in found and _has_korean_id(_normalized(original)):
            findings.append(_warning('egress-korean-id', path, line))
            found.add('egress-korean-id')
        if 'egress-url-secret' not in found and _has_url_secret(original):
            findings.append(_warning('egress-url-secret', path, line))
            found.add('egress-url-secret')
        if len(found) == 3:
            break
    # D1 assignment/authorization patterns can span line separators. Keep the
    # whole bounded original inspection instead of narrowing to split lines;
    # if no line-local match exists, avoid inventing an exact source line.
    if 'egress-secret' not in found and _matches_sensitive(text):
        findings.insert(0, _warning('egress-secret', path, 0, 'whole_original_match_line_unknown'))
    return findings


def analyze_notes(notes, config=None):
    """Inspect caller-approved originals, then independently reapply inventory policy."""
    if not isinstance(notes, dict) or not isinstance(config or {}, dict):
        return [_warning('egress-incomplete', '', 0, 'invalid_inventory')]
    config = config or {}
    denied = config.get('deny_zones', DEFAULT_DENY)
    excluded = config.get('exclude_dirs', [])
    if not isinstance(denied, (list, tuple)) or not isinstance(excluded, (list, tuple)) or not all(
            isinstance(rule, str) for rule in (*denied, *excluded)):
        return [_warning('egress-incomplete', '', 0, 'invalid_policy')]
    findings, total, count = [], 0, 0
    for path, text in sorted(notes.items(), key=lambda item: str(item[0])):
        try:
            parts = relative_parts(path, 'egress note')
        except ValueError:
            findings.append(_warning('egress-incomplete', '', 0, 'unsafe_inventory_path'))
            continue
        if tuple(part.casefold() for part in parts[:3]) == ('00-meta', '.agentic-vault', 'runtime'):
            continue
        if any(zone_matches(parts, rule) for rule in (*denied, *excluded, '.git')):
            continue
        if not isinstance(text, str):
            findings.append(_warning('egress-incomplete', path, 0, 'invalid_original'))
            continue
        count += 1
        try:
            total += len(text.encode('utf-8'))
        except UnicodeError:
            findings.extend(scan_text(text, path))
            continue
        if count > MAX_NOTES or total > MAX_TOTAL_BYTES or len(findings) >= MAX_WARNINGS:
            return findings[:MAX_WARNINGS-1] + [_warning('egress-incomplete', '', 0, 'inventory_limit')]
        findings.extend(scan_text(text, path))
    if len(findings) > MAX_WARNINGS:
        return findings[:MAX_WARNINGS-1] + [_warning('egress-incomplete', '', 0, 'warning_limit')]
    return findings


def optional_scanners():
    return {'status': 'not_invoked', 'required': False,
            'reason': 'external_scanners_require_explicit_separate_invocation'}
