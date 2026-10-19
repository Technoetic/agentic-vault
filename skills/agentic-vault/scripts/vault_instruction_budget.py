"""Bounded read-only inventory of potential instruction files, never host telemetry.

Counts Unicode code points in unique UTF-8 files, including their frontmatter.
No instruction text, condition expressions, exact token estimates or cost claims
leave this module. A static 'always' role does not assert runtime loading.

Walked directories are bound to (dev, ino, mtime) and a SHA-256 of their sorted
entry names, taken from the walk's own listing. finish() lists each walked
directory once more (non-recursive, names only, capped by MAX_ENTRIES in total);
a changed name set makes the report partial like a changed directory time.
Limits: a same-name delete-and-recreate leaves the name set unchanged (the
finish rehash still catches changed bytes of a measured file), and a listing is
a point-in-time check, not a lock on writers.
"""
from __future__ import annotations

import hashlib
import os
import json
from pathlib import Path
import re
import stat

from vault_evidence import EvidenceError, stable_read
from vault_healthcheck import HealthcheckError, validate_config
from vault_paths import relative_parts, resolve_note_path, zone_matches


MAX_FILES = 128
MAX_ENTRIES = 512
MAX_DEPTH = 16
MAX_FILE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
MAX_IMPORTS_PER_FILE = 128
MAX_ISSUES = 128
_ROLES = ('always', 'conditional', 'candidate')
_PRIORITY = {'always': 0, 'conditional': 1, 'candidate': 2}
_SECRET_SEGMENTS = frozenset(('.git', '.ssh', '.aws', '.azure', '.gnupg', 'secrets', 'credentials'))
_SECRET_SUFFIXES = frozenset(('.key', '.pem', '.p12', '.pfx', '.jks', '.kdbx'))
_SECRET_BASENAMES = frozenset(('.npmrc', '.pypirc', '.netrc', '.dockercfg', '.git-credentials'))
_SECRET_STEMS = frozenset(('secret', 'secrets', 'credential', 'credentials', 'token', 'tokens',
                          'service-account', 'service_account', 'api-key', 'api-keys',
                          'api_key', 'api_keys', 'auth-config', 'auth_config',
                          'secret-config', 'secret_config', 'secrets-config', 'secrets_config',
                          'credentials-config', 'credentials_config', 'token-config', 'token_config'))
_IMPORT = re.compile(r'(?<![\w@])@(?:"([^"\n]+)"|\x27([^\x27\n]+)\x27|([^\s`<>()]+))')
_FENCE = re.compile(r'^\s*(`{3,}|~{3,})')
_INLINE_CODE = re.compile(r'`+[^`]*`+')
_HEADER_KEY = re.compile(r'^("(?:[^"\\]|\\.)*"|\x27(?:[^\x27]|\x27\x27)*\x27|[A-Za-z_][\w.-]*)\s*:')


def _empty(status='partial'):
    return {'schema_version': 1, 'status': status, 'runtime_loading': 'unverified',
            'character_measure': 'unicode_code_points', 'entrypoint': None,
            'files': [], 'issues': [], 'listing_diagnostics': [],
            'totals': {'unique_files': 0, 'characters': 0, 'bytes': 0,
                       'by_classification': {role: {'files': 0, 'characters': 0} for role in _ROLES}},
            'limits': {'files': MAX_FILES, 'directory_entries': MAX_ENTRIES, 'depth': MAX_DEPTH,
                       'file_bytes': MAX_FILE_BYTES, 'total_bytes': MAX_TOTAL_BYTES,
                       'imports_per_file': MAX_IMPORTS_PER_FILE, 'issues': MAX_ISSUES}}


def _listing_digest(names):
    """SHA-256 of sorted entry names only: no inode numbers, sizes or contents."""
    digest = hashlib.sha256()
    for name in sorted(names):
        digest.update(name.encode('utf-8', 'surrogatepass') + b'\0')
    return digest.hexdigest()


def _directory_names(path, limit):
    """One non-recursive os.scandir of names; None when ``limit`` is exceeded."""
    names = []
    with os.scandir(path) as scan:
        for entry in scan:
            if len(names) >= limit:
                return None
            names.append(entry.name)
    return names


def _secret(parts):
    return any(part.casefold() in _SECRET_SEGMENTS or part.casefold().startswith('.env')
               or part.casefold() in _SECRET_BASENAMES or Path(part).stem.casefold() in _SECRET_STEMS
               or Path(part).suffix.casefold() in _SECRET_SUFFIXES for part in parts)


def _imports(text):
    fence = None
    for line in text.splitlines():
        boundary = _FENCE.match(line)
        if boundary:
            marker = boundary.group(1)
            if fence is None:
                fence = marker[0]
            elif marker[0] == fence:
                fence = None
            continue
        if fence is not None:
            continue
        for match in _IMPORT.finditer(_INLINE_CODE.sub('', line)):
            yield next(group for group in match.groups() if group is not None).rstrip(',;')


def _import_path(source, target):
    # Relative imports may traverse a parent inside the vault, but never escape
    # it. Canonical components still pass the shared portable path rule.
    value = target.replace('\\', '/')
    if value.startswith('/') or re.match(r'^[A-Za-z]:', value):
        raise ValueError('unsafe_import')
    parts = list(Path(source).parent.parts)
    for part in value.split('/'):
        if part == '.':
            continue
        if part == '..':
            if not parts:
                raise ValueError('unsafe_import')
            parts.pop()
        else:
            parts.append(part)
    canonical = '/'.join(parts)
    relative_parts(canonical, 'import')
    return canonical


def _rule_role(text):
    if not text.startswith(('---\n', '---\r\n')):
        return 'always'
    lines = text.splitlines()
    try:
        end = lines.index('---', 1)
    except ValueError:
        return 'candidate'
    scoped = False
    for line in lines[1:end]:
        if not line.strip() or line.startswith((' ', '\t', '#', '- ')):
            continue
        field = _HEADER_KEY.match(line)
        if field is None:
            return 'candidate'
        key = field.group(1)
        if key.startswith('"'):
            try:
                key = json.loads(key)
            except (ValueError, TypeError):
                # YAML-only escapes and complex/merge keys are not guessed.
                return 'candidate'
        elif key.startswith("'"):
            key = key[1:-1].replace("''", "'")
        scoped = scoped or key == 'paths'
    return 'conditional' if scoped else 'always'


class _Inventory:
    def __init__(self, vault, config):
        self.vault = vault
        self.root = vault.resolve(strict=True)
        self.config = config
        self.report = _empty('complete')
        self.records = {}
        self.references = {}
        self.rule_roles = {}
        self.directories = {}
        self.seen = {}
        self.issue_keys = set()
        self.attempts = self.entries = self.read_bytes = self.relisted = 0

    def issue(self, code, path=None, change=None):
        # ``change`` (metadata or listing) tells directory change evidence apart.
        self.report['status'] = 'partial'
        key = (code, path)
        if key in self.issue_keys:
            return
        self.issue_keys.add(key)
        if len(self.report['issues']) >= MAX_ISSUES:
            return
        self.report['issues'].append({'code': code, **({'path': path} if path is not None else {}),
                                      **({'change': change} if change is not None else {})})

    def resolve(self, relative):
        parts = relative_parts(relative, 'instruction')
        if _secret(parts) or any(zone_matches(parts, zone) for zone in
                                 (*self.config['deny_zones'], *self.config['exclude_dirs'])):
            raise ValueError('blocked_path')
        return resolve_note_path(self.vault, relative, self.config['deny_zones'])

    def visit(self, relative, role, origin, ancestors=()):
        folded = relative.casefold()
        if folded in ancestors:
            self.issue('import_cycle', relative)
            return
        if len(ancestors) > MAX_DEPTH:
            self.issue('import_depth_limit', relative)
            return
        if folded in self.seen:
            previous = self.seen[folded]
            if previous != relative:
                self.issue('ambiguous_path_case', relative)
                return
            record = self.records.get(relative)
            if record:
                if origin == 'claude_rules':
                    role = self.rule_roles[relative]
                if _PRIORITY[role] < _PRIORITY[record['classification']]:
                    record['classification'] = role
                    # Revisit descendants when a previously conditional/candidate
                    # file is also reached by an unconditional explicit import.
                    for target in self.references[relative]:
                        self.visit(target, role, 'import', (*ancestors, folded))
                if origin not in record['origins']:
                    record['origins'].append(origin)
            return
        if self.attempts >= MAX_FILES:
            self.issue('file_count_limit')
            return
        self.seen[folded] = relative
        self.attempts += 1
        try:
            if not relative.casefold().endswith('.md'):
                raise ValueError('not_markdown')
            path = self.resolve(relative)
            remaining = min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - self.read_bytes)
            if remaining <= 0:
                self.issue('total_byte_limit')
                return
            info, _mark, data = stable_read(lambda: self.resolve(relative), remaining, contents=True)
            self.read_bytes += info['size']
            text = data.decode('utf-8-sig')
        except FileNotFoundError:
            self.issue('missing_import' if origin == 'import' else 'missing_instruction', relative)
            return
        except UnicodeError:
            self.issue('invalid_encoding', relative)
            return
        except EvidenceError as exc:
            code = str(exc)
            self.issue(code if code in {'unsafe_file', 'input_too_large', 'file_changed_during_read'} else 'unreadable_instruction', relative)
            return
        except (ValueError, RuntimeError):
            self.issue('unsafe_instruction_path', relative)
            return
        except OSError:
            self.issue('unreadable_instruction', relative)
            return
        self.rule_roles[relative] = _rule_role(text)
        if origin == 'claude_rules':
            role = self.rule_roles[relative]
            if role == 'candidate':
                self.issue('unparsed_rule_frontmatter', relative)
        self.records[relative] = {'path': relative, **info, 'characters': len(text),
                                  'classification': role, 'origins': [origin]}
        self.references[relative] = []
        for index, target in enumerate(_imports(text)):
            if index >= MAX_IMPORTS_PER_FILE:
                self.issue('import_count_limit', relative)
                break
            try:
                canonical = _import_path(relative, target)
            except (ValueError, RuntimeError):
                # Never echo unsafe external paths or arbitrary inline text.
                self.issue('unsafe_import', relative)
                continue
            self.references[relative].append(canonical)
            self.visit(canonical, role, 'import', (*ancestors, folded))

    def walk(self, relative, origin, depth=0):
        if depth > MAX_DEPTH:
            self.issue('directory_depth_limit', relative)
            return
        try:
            directory = self.resolve(relative)
            before = directory.lstat()
            if not stat.S_ISDIR(before.st_mode):
                self.issue('unsafe_instruction_directory', relative)
                return
            items = []
            complete = True
            with os.scandir(directory) as scan:
                for entry in scan:
                    if self.entries >= MAX_ENTRIES:
                        self.issue('directory_entry_limit')
                        complete = False
                        break
                    self.entries += 1
                    items.append(entry.name)
            after = self.resolve(relative).lstat()
            if (before.st_dev, before.st_ino, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_mtime_ns):
                self.issue('directory_changed_during_diagnosis', relative, 'metadata')
                return
            # A truncated listing has no complete name set to compare later.
            self.directories[relative] = (after.st_dev, after.st_ino, after.st_mtime_ns,
                                          _listing_digest(items) if complete else None)
        except FileNotFoundError:
            return
        except (ValueError, RuntimeError):
            self.issue('unsafe_instruction_directory', relative)
            return
        except OSError:
            self.issue('unreadable_instruction_directory', relative)
            return
        for name in sorted(items):
            child = relative + '/' + name
            try:
                path = self.resolve(child)
                metadata = path.lstat()
            except (ValueError, RuntimeError):
                self.issue('skipped_instruction_path', child)
                continue
            except OSError:
                self.issue('unreadable_instruction_path', child)
                continue
            if stat.S_ISDIR(metadata.st_mode):
                self.walk(child, origin, depth + 1)
            elif name.casefold().endswith('.md'):
                self.visit(child, 'candidate' if origin == 'agents_candidates' else 'always', origin)

    def check_listing(self, relative, expected):
        """List an already walked directory once; record a name-set difference.

        Unreadable or over-cap listings fail closed like the walk itself.
        """
        try:
            names = _directory_names(self.resolve(relative), MAX_ENTRIES - self.relisted)
        except (ValueError, RuntimeError):
            self.issue('unsafe_instruction_directory', relative)
            return
        except OSError:
            self.issue('unreadable_instruction_directory', relative)
            return
        if names is None:
            self.relisted = MAX_ENTRIES
            self.issue('directory_entry_limit')
            return
        self.relisted += len(names)
        if _listing_digest(names) != expected:
            if len(self.report['listing_diagnostics']) < MAX_ISSUES:
                self.report['listing_diagnostics'].append({'code': 'directory_listing_changed', 'path': relative})
            # A changed name set counts as a change even behind an unchanged time.
            self.issue('directory_changed_during_diagnosis', relative, 'listing')

    def finish(self):
        for relative, expected in self.directories.items():
            try:
                metadata = self.resolve(relative).lstat()
                current = (metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns)
                if current != expected[:3]:
                    self.issue('directory_changed_during_diagnosis', relative, 'metadata')
                    continue
            except (OSError, ValueError, RuntimeError):
                self.issue('directory_changed_during_diagnosis', relative, 'metadata')
                continue
            if expected[3] is not None:
                self.check_listing(relative, expected[3])
        # A second bounded descriptor read detects earlier files changing while
        # later imports/directories were inspected, including restored mtimes.
        for relative, record in list(self.records.items()):
            try:
                now, _mark, _data = stable_read(lambda: self.resolve(relative), MAX_FILE_BYTES)
                if now != {'sha256': record['sha256'], 'size': record['size']}:
                    raise EvidenceError('file_changed_during_diagnosis')
            except (EvidenceError, OSError, ValueError, RuntimeError):
                self.issue('file_changed_during_diagnosis', relative)
                del self.records[relative]
        files = sorted(self.records.values(), key=lambda row: row['path'])
        for record in files:
            record['origins'].sort()
            totals = self.report['totals']
            totals['unique_files'] += 1
            totals['characters'] += record['characters']
            totals['bytes'] += record['size']
            category = totals['by_classification'][record['classification']]
            category['files'] += 1
            category['characters'] += record['characters']
        self.report['files'] = files
        return self.report


def diagnose_instructions(vault, config):
    """Return metadata only; no writes, execution, external API or auto-enforcement.

AGENTS.md wins when present, with CLAUDE.md only as a missing-file fallback.
Claude rules with a paths field are conditional static candidates. All .agents
Markdown is candidate-only because this diagnostic cannot establish its host's
loading semantics. Supported imports are local @file.md, including quoted names;
code examples are excluded. Limits, skips and inconsistent reads yield partial.
Each walked directory gets one bounded name listing at the end; a name-set
difference behind an unchanged directory time is reported in
listing_diagnostics and as directory_changed_during_diagnosis with
change='listing' (metadata differences carry change='metadata').
"""
    try:
        config = validate_config(config)
    except (HealthcheckError, TypeError, ValueError, RecursionError):
        report = _empty('invalid_config')
        report['issues'] = [{'code': 'invalid_config'}]
        return report
    try:
        inventory = _Inventory(Path(vault), config)
        # Resolving a harmless local path also checks the vault root itself.
        resolve_note_path(inventory.vault, 'AGENTS.md')
    except (OSError, ValueError, RuntimeError, TypeError):
        report = _empty('unsafe_vault')
        report['issues'] = [{'code': 'unsafe_vault'}]
        return report
    root = 'AGENTS.md'
    try:
        inventory.resolve(root).lstat()
    except FileNotFoundError:
        root = 'CLAUDE.md'
    except (OSError, ValueError, RuntimeError):
        inventory.issue('unsafe_entrypoint', root)
    inventory.report['entrypoint'] = root
    inventory.visit(root, 'always', 'root')
    inventory.walk('.claude/rules', 'claude_rules')
    if config['rules_dir'] and config['rules_dir'] != '.claude/rules':
        inventory.walk(config['rules_dir'], 'claude_rules')
    inventory.walk('.agents', 'agents_candidates')
    return inventory.finish()
