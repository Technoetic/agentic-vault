"""Focused behavioral tests for Jarvis configuration and message authorization."""
from __future__ import annotations

import importlib.util
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


_BRIDGE_PATH = Path(__file__).parents[1] / "skills" / "agentic-vault" / "scripts" / "jarvis_bridge.py"
_SPEC = importlib.util.spec_from_file_location("jarvis_bridge", _BRIDGE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_BRIDGE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BRIDGE)


class JarvisConfigAuthorizationTests(unittest.TestCase):
    def test_parse_briefing_slots_prefers_multiple_and_deduplicates(self):
        block = {"briefing_time": "07:30", "briefing_times": ["19:30", "08:30", "08:30"]}
        self.assertEqual(_BRIDGE.parse_briefing_slots(block), [(8, 30), (19, 30)])

    def test_parse_briefing_slots_falls_back_only_when_key_absent(self):
        self.assertEqual(_BRIDGE.parse_briefing_slots({"briefing_time": "07:30"}), [(7, 30)])
        with self.assertRaises(_BRIDGE.JarvisConfigError):
            _BRIDGE.parse_briefing_slots({"briefing_time": "07:30", "briefing_times": []})

    def test_parse_briefing_slots_rejects_bad_values(self):
        for block in (
            {"briefing_times": "08:30"},
            {"briefing_times": ["24:00"]},
            {"briefing_times": ["08:60"]},
            {"briefing_times": [8]},
        ):
            with self.subTest(block=block), self.assertRaises(_BRIDGE.JarvisConfigError):
                _BRIDGE.parse_briefing_slots(block)

    def test_authorization_requires_private_self_chat_and_whitelist(self):
        allowed = {111}
        self.assertTrue(_BRIDGE.is_authorized_private_message(
            {"from": {"id": 111}, "chat": {"id": 111, "type": "private"}}, allowed))
        self.assertFalse(_BRIDGE.is_authorized_private_message(
            {"from": {"id": 111}, "chat": {"id": -99, "type": "group"}}, allowed))
        self.assertFalse(_BRIDGE.is_authorized_private_message(
            {"from": {"id": 111}, "chat": {"id": 222, "type": "private"}}, allowed))


class JarvisStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        original_state_root = _BRIDGE.STATE_ROOT
        _BRIDGE.STATE_ROOT = self.root
        self.addCleanup(setattr, _BRIDGE, "STATE_ROOT", original_state_root)

    def test_state_namespace_separates_vaults_and_bots_without_token_plaintext(self):
        a = _BRIDGE.state_dir_for(Path("C:/vault-a"), "12345:secret", self.root)
        b = _BRIDGE.state_dir_for(Path("C:/vault-b"), "12345:secret", self.root)
        c = _BRIDGE.state_dir_for(Path("C:/vault-a"), "67890:other", self.root)

        self.assertEqual(len({a, b, c}), 3)
        self.assertEqual(a.parent, self.root)
        self.assertTrue(a.name.endswith("-12345"))
        self.assertNotIn("secret", str(a))

    def test_state_namespace_rejects_malformed_bot_id_without_echoing_token(self):
        token = "bot-name:secret-value"
        with self.assertRaises(_BRIDGE.JarvisConfigError) as raised:
            _BRIDGE.state_dir_for(self.vault, token, self.root)

        self.assertNotIn(token, str(raised.exception))
        with self.assertRaises(_BRIDGE.JarvisConfigError):
            _BRIDGE.state_dir_for(self.vault, "12345", self.root)
        with self.assertRaises(_BRIDGE.JarvisConfigError):
            _BRIDGE.state_dir_for(self.vault, "１２３４５:secret", self.root)

    def test_atomic_write_replaces_complete_value(self):
        target = self.root / "offset"

        _BRIDGE.atomic_write_text(target, "41")
        _BRIDGE.atomic_write_text(target, "42")

        self.assertEqual(target.read_text(encoding="utf-8"), "42")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_first_owner_claims_legacy_state_and_second_owner_leaves_it_untouched(self):
        first_namespace = self.root / "vault-a-12345"
        second_namespace = self.root / "vault-b-67890"
        (self.root / "offset").write_text("41", encoding="utf-8")

        _BRIDGE.migrate_legacy_state(self.root, first_namespace, "vault-a-12345")

        self.assertEqual((first_namespace / "offset").read_text(encoding="utf-8"), "41")
        self.assertFalse((self.root / "offset").exists())
        (self.root / "last_brief").write_text("2026-08-31", encoding="utf-8")
        warning = io.StringIO()
        with redirect_stdout(warning):
            _BRIDGE.migrate_legacy_state(self.root, second_namespace, "vault-b-67890")

        self.assertTrue((self.root / "last_brief").exists())
        self.assertFalse((second_namespace / "last_brief").exists())
        self.assertIn("another owner", warning.getvalue().lower())

    def test_same_owner_resumes_partial_migration_without_overwriting_target(self):
        namespace = self.root / "vault-a-12345"
        namespace.mkdir()
        (namespace / "offset").write_text("99", encoding="utf-8")
        (self.root / "offset").write_text("41", encoding="utf-8")

        _BRIDGE.migrate_legacy_state(self.root, namespace, "vault-a-12345")

        self.assertEqual((namespace / "offset").read_text(encoding="utf-8"), "99")
        self.assertEqual((self.root / "offset").read_text(encoding="utf-8"), "41")
        (self.root / "last_butler").write_text("12.5", encoding="utf-8")
        _BRIDGE.migrate_legacy_state(self.root, namespace, "vault-a-12345")

        self.assertEqual((namespace / "last_butler").read_text(encoding="utf-8"), "12.5")
        self.assertFalse((self.root / "last_butler").exists())

    def test_migration_target_race_never_overwrites_competing_file(self):
        namespace = self.root / "vault-a-12345"
        _BRIDGE.migrate_legacy_state(self.root, namespace, "vault-a-12345")
        source = self.root / "offset"
        target = namespace / "offset"
        source.write_text("legacy", encoding="utf-8")
        original_link = os.link
        original_replace = Path.replace

        def competing_rename(path, destination):
            destination.write_text("competitor", encoding="utf-8")
            return original_replace(path, destination)

        def competing_link(path, destination):
            destination.write_text("competitor", encoding="utf-8")
            return original_link(path, destination)

        with patch.object(Path, "rename", competing_rename), \
                patch.object(_BRIDGE.os, "link", competing_link):
            _BRIDGE.migrate_legacy_state(self.root, namespace, "vault-a-12345")

        self.assertEqual(target.read_text(encoding="utf-8"), "competitor")
        self.assertEqual(source.read_text(encoding="utf-8"), "legacy")

    def test_migration_source_disappearance_race_is_handled(self):
        namespace = self.root / "vault-a-12345"
        _BRIDGE.migrate_legacy_state(self.root, namespace, "vault-a-12345")
        source = self.root / "offset"
        target = namespace / "offset"
        source.write_text("41", encoding="utf-8")

        def disappearing_source(path, destination):
            path.unlink(missing_ok=True)
            raise FileNotFoundError(path)

        with patch.object(Path, "rename", disappearing_source), \
                patch.object(_BRIDGE.os, "link", disappearing_source):
            _BRIDGE.migrate_legacy_state(self.root, namespace, "vault-a-12345")

        self.assertFalse(source.exists())
        self.assertFalse(target.exists())

    def test_same_owner_resumes_after_link_before_source_unlink(self):
        namespace = self.root / "vault-a-12345"
        _BRIDGE.migrate_legacy_state(self.root, namespace, "vault-a-12345")
        source = self.root / "offset"
        target = namespace / "offset"
        source.write_text("41", encoding="utf-8")
        os.link(source, target)

        _BRIDGE.migrate_legacy_state(self.root, namespace, "vault-a-12345")

        self.assertEqual(target.read_text(encoding="utf-8"), "41")
        self.assertFalse(source.exists())

    def test_capture_id_makes_retry_idempotent_and_same_second_distinct(self):
        received = datetime(2026, 8, 31, 9, 0, 0)

        first = _BRIDGE.do_capture(
            self.vault, "A", "telegram", capture_id="100", received_at=received)
        retry = _BRIDGE.do_capture(
            self.vault, "A", "telegram", capture_id="100", received_at=received)
        second = _BRIDGE.do_capture(
            self.vault, "B", "telegram", capture_id="101", received_at=received)

        self.assertEqual(first, retry)
        self.assertNotEqual(first, second)

    def test_capture_id_is_path_safe_and_conflicting_retry_is_rejected(self):
        received = datetime(2026, 8, 31, 9, 0, 0)
        name = _BRIDGE.do_capture(
            self.vault, "A", "telegram", capture_id="100/../unsafe!?", received_at=received)

        self.assertRegex(name, r"^2026-08-31 090000-[A-Za-z0-9_-]+\.md$")
        with self.assertRaises(RuntimeError):
            _BRIDGE.do_capture(
                self.vault, "different", "telegram", capture_id="100/../unsafe!?",
                received_at=received)
