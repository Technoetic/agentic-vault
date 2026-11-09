"""Request-local raw-source bindings for configured outbound context.

This guard is a point-in-time check, not a filesystem lock or a way to recall
information already sent. Keep the same instance from preparation to dispatch.
"""
from __future__ import annotations

import hashlib

from vault_evidence import EvidenceError
from vault_paths import zone_matches
from vault_revocation import MAX_BINDING_BYTES, MAX_FILE_BYTES, RevocationPolicy


class ContextGuard:
    def __init__(self, store):
        self.store = store
        self.policy = RevocationPolicy(store.vault, store.config)
        self.bindings = {}
        self._bytes = 0

    def _path(self, relative):
        path = self.store.path(relative)
        canonical = path.relative_to(self.store.root).as_posix()
        if path.suffix.casefold() != '.md':
            raise EvidenceError('unsafe_path')
        for parts in (tuple(relative.replace('\\', '/').split('/')), path.relative_to(self.store.root).parts,
                      *self.store.worktrees.views(path.relative_to(self.store.root).parts)):
            if any(zone_matches(parts, rule) for rule in self.store.config['exclude_dirs']):
                raise EvidenceError('unsafe_path')
        return canonical

    def _budget(self, size):
        self._bytes += size
        if self._bytes > MAX_BINDING_BYTES:
            raise EvidenceError('input_too_large')

    def project(self, relative, raw, info):
        """Project complete original UTF-8 bytes, retaining their original hash."""
        canonical = self._path(relative)
        self._budget(len(raw))
        if len(raw) > MAX_FILE_BYTES or hashlib.sha256(raw).hexdigest() != info['sha256']:
            raise EvidenceError('source_changed')
        previous = self.bindings.get(canonical)
        if previous is not None and previous != info:
            raise EvidenceError('source_changed')
        projected = self.policy.filter_text(canonical, raw.decode('utf-8'))
        self.bindings[canonical] = dict(info)
        return projected

    def read(self, relative):
        canonical = self._path(relative)
        info, _mark, raw = self.store.fingerprint(canonical, limit=MAX_FILE_BYTES, contents=True)
        binding = {key: info[key] for key in ('sha256', 'size')}
        return binding, self.project(canonical, raw, binding)

    def recheck(self):
        """Validate the original configuration, policy and every selected source."""
        self.store.fresh_policy()
        self.policy.recheck()
        for relative, expected in self.bindings.items():
            self._path(relative)
            current, _mark, _raw = self.store.fingerprint(relative, limit=MAX_FILE_BYTES)
            self._budget(current['size'])
            if any(current[key] != expected[key] for key in ('sha256', 'size')):
                raise EvidenceError('source_changed')
        self.policy.recheck()
        self.store.fresh_policy()
