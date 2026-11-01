#!/usr/bin/env python3
"""Export selected vault notes as a limited OKF 0.2 producer profile."""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import json
import os
import posixpath
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
from urllib.parse import quote, unquote, urlsplit

from vault_evidence import Store, EvidenceError, MAX_FILES, MAX_FILE_BYTES, MAX_SET_BYTES
from vault_paths import relative_parts, _is_link_or_reparse
from vault_healthcheck import HealthcheckError

SPEC = 'ad30107c31c06aec8a7d5636e0d1058118604e6f'
PROFILE = 'agentic-vault-selected-concepts-0.2'
class ExportError(Exception):
    """Safe, non-sensitive export failure code."""


def _parent_identity(path):
    if not path.is_absolute():
        raise ExportError('output_not_absolute')
    parent = path.parent
    current = Path(parent.anchor)
    for part in parent.parts[1:]:
        current /= part
        if _is_link_or_reparse(current):
            raise ExportError('unsafe_output_parent')
    info = parent.stat()
    if not parent.is_dir():
        raise ExportError('unsafe_output_parent')
    return (info.st_dev, info.st_ino, str(parent.resolve(strict=True)))


def _publish(src, dst):
    # Windows rename is no-clobber. POSIX rename is not; use Linux renameat2.
    if os.name == 'nt':
        os.rename(src, dst)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin':
        fn = getattr(libc, 'renamex_np', None)
        if fn is None:
            raise ExportError('atomic_publish_unavailable')
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        if fn(os.fsencode(src), os.fsencode(dst), 4):
            raise OSError(ctypes.get_errno(), 'atomic publish failed')
        return
    fn = getattr(libc, 'renameat2', None)
    if fn is None:
        raise ExportError('atomic_publish_unavailable')
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    if fn(-100, os.fsencode(src), -100, os.fsencode(dst), 1):
        raise OSError(ctypes.get_errno(), 'atomic publish failed')


def _frontmatter(text):
    # Store metadata verbatim as a JSON string, never interpret hostile YAML.
    lines = text.splitlines(keepends=True)
    if lines and lines[0].rstrip('\r\n') == '---':
        for i in range(1, len(lines)):
            if lines[i].rstrip('\r\n') == '---':
                return ''.join(lines[1:i]), ''.join(lines[i+1:])
        raise ExportError('unterminated_frontmatter')
    return '', text


def _project(body, source, mapping, basename, losses):
    # Inline code and fenced blocks stay literal; unsupported links are reported.
    wiki = re.compile(r'(!?)\[\[([^\]\n]+)\]\]')
    local = re.compile(r'(!?)\[([^\]\n]*)\]\(([^)\s]+)\)')
    def convert(segment):
        def wiki_replace(m):
            embed, content = m.groups()
            target, _, label = content.partition('|')
            name, sep, fragment = target.partition('#')
            candidates = basename.get(name.casefold(), []) if '/' not in name else [name + ('' if name.lower().endswith('.md') else '.md')]
            candidates = [p for p in candidates if p in mapping]
            if embed or len(candidates) != 1 or (sep and not fragment):
                losses.append({'source': source, 'kind': 'embed' if embed else 'unresolved_or_ambiguous_wikilink', 'reference': content})
                return m.group(0)
            dest = mapping[candidates[0]]
            rel = os.path.relpath(dest, str(PurePosixPath(mapping[source]).parent)).replace('\\', '/')
            url = quote(rel, safe='/') + (('#' + quote(fragment, safe='')) if sep else '')
            return '[' + (label or target).replace(']', r'\]') + '](' + url + ')'
        def local_check(m):
            embed, _, target = m.groups()
            parsed = urlsplit(target)
            selected = posixpath.normpath(posixpath.join(posixpath.dirname(source), unquote(parsed.path))) in mapping
            if embed or (parsed.scheme and parsed.scheme.lower() not in ('http', 'https', 'mailto')) or (not parsed.scheme and parsed.path and not selected):
                losses.append({'source': source, 'kind': 'unconverted_markdown_link', 'reference': target})
            return m.group(0)
        return wiki.sub(wiki_replace, local.sub(local_check, segment))
    out, fence = [], None
    for line in body.splitlines(keepends=True):
        match = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line.rstrip('\r\n'))
        if fence:
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= fence[1] and not match.group(2).strip():
                fence = None
            out.append(line)
        elif match and not (match.group(1)[0] == '`' and '`' in match.group(2)):
            fence = (match.group(1)[0], len(match.group(1)))
            out.append(line)
        else:
            pieces, cursor = [], 0
            for opening in re.finditer(r'`+', line):
                if opening.start() < cursor:
                    continue
                closing = re.search(r'(?<!`)'+re.escape(opening.group())+r'(?!`)', line[opening.end():])
                if closing:
                    end = opening.end() + closing.end()
                    pieces.extend((convert(line[cursor:opening.start()]), line[opening.start():end]))
                    cursor = end
            pieces.append(convert(line[cursor:]))
            out.append(''.join(pieces))
    return ''.join(out)


def export_bundle(vault: Path, notes: list[str], destination: Path) -> dict:
    try:
        if not isinstance(vault, Path) or not isinstance(destination, Path) or not isinstance(notes, list) or not 1 <= len(notes) <= MAX_FILES:
            raise ExportError('invalid_input')
        if destination.name in ('', '.', '..') or destination.exists() or destination.is_symlink():
            raise ExportError('destination_exists_or_invalid')
        parent_id = _parent_identity(destination)
        store = Store(vault)
        store.denied = (*store.denied, *store.config.get('exclude_dirs', ()))
        dest_resolved = destination.parent.resolve(strict=True) / destination.name
        if dest_resolved == store.root or store.root in dest_resolved.parents:
            raise ExportError('destination_inside_vault')
        records, originals, marks, mapping, identities = [], {}, {}, {}, set()
        folded, total, components = set(), 0, {}
        for note in notes:
            parts = relative_parts(note, 'note')
            for i, part in enumerate(parts):
                prefix = tuple(p.casefold() for p in parts[:i + 1])
                prior = components.setdefault(prefix, parts[:i + 1])
                if prior != parts[:i + 1]:
                    raise ExportError('output_collision')
            if len(parts) > 32 or not parts[-1].lower().endswith('.md') or parts[-1].casefold() in ('index.md', 'log.md'):
                raise ExportError('invalid_note')
            store.path(note)
            info, mark, data = store.fingerprint(note, limit=MAX_FILE_BYTES, contents=True)
            key = info['path']
            if key.casefold() in folded or mark[:2] in identities:
                raise ExportError('duplicate_note')
            folded.add(key.casefold()); identities.add(mark[:2])
            total += info['size']
            if total > MAX_SET_BYTES:
                raise ExportError('set_too_large')
            records.append(info); marks[key] = mark; originals[key] = data
            mapping[key] = 'concepts/' + key
        # Case collisions anywhere in output hierarchy are nonportable.
        paths = list(mapping.values())
        for a in paths:
            for b in paths:
                if a != b and (a.casefold() == b.casefold() or a.casefold().startswith(b.casefold() + '/')):
                    raise ExportError('output_collision')
        basename = {}
        for key in mapping:
            for name in (PurePosixPath(key).stem, PurePosixPath(key).name):
                basename.setdefault(name.casefold(), []).append(key)
        losses, outputs, files = [], [], {}
        for info in sorted(records, key=lambda x: x['path']):
            key = info['path']
            text = originals[key].decode('utf-8-sig')
            raw, body = _frontmatter(text)
            if not raw:
                losses.append({'source': key, 'kind': 'missing_frontmatter'})
            header = '---\ntype: "concept"\nx-agentic-vault-original-frontmatter: ' + json.dumps(raw, ensure_ascii=True) + '\n---\n'
            content = (header + _project(body, key, mapping, basename, losses)).encode('utf-8')
            files[mapping[key]] = content
        index = '---\nokf_version: "0.2"\n---\n\n# Selected concepts\n\n' + ''.join('- [' + PurePosixPath(k).stem.replace(']', r'\]') + '](' + quote(mapping[k], safe='/') + ')\n' for k in sorted(mapping))
        files['index.md'] = index.encode('utf-8')
        for name, data in sorted(files.items()):
            outputs.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)})
        manifest = {'profile': PROFILE, 'spec_commit': SPEC, 'sources': sorted(records, key=lambda x: x['path']), 'outputs': outputs, 'path_mapping': dict(sorted(mapping.items())), 'losses': losses}
        files['manifest.json'] = (json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2) + '\n').encode('utf-8')
        stage = Path(tempfile.mkdtemp(prefix='.okf-', dir=destination.parent))
        try:
            for name, data in files.items():
                target = stage.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            store.fresh_policy()
            for original in records:
                current, mark, _ = store.fingerprint(original['path'])
                if current != original or mark != marks[original['path']]:
                    raise ExportError('source_changed')
            store.fresh_policy()
            if _parent_identity(destination) != parent_id:
                raise ExportError('output_parent_changed')
            _publish(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        return manifest
    except ExportError:
        raise
    except (OSError, ValueError, UnicodeError, EvidenceError, HealthcheckError, RecursionError, OverflowError) as exc:
        raise ExportError('invalid_or_unreadable_input') from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', required=True, type=Path)
    parser.add_argument('--note', required=True, action='append')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(export_bundle(args.vault, args.note, args.output), ensure_ascii=True, sort_keys=True))
        return 0
    except ExportError as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 2

if __name__ == '__main__':
    sys.exit(main())
