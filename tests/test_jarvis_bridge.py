"""Focused behavioral tests for Jarvis configuration and message authorization."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from collections import deque
from contextlib import redirect_stdout
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch


_ROOT = Path(__file__).parents[1]
_BRIDGE_PATH = _ROOT / "skills" / "agentic-vault" / "scripts" / "jarvis_bridge.py"
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


class JarvisBriefingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.cfg = {
            "_hot_note": "00-meta/hot.md",
            "_handoff_note": "",
            "_log_note": "00-meta/log.md",
            "butler_interval_hours": 24,
        }

    def test_do_brief_uses_neutral_recurring_prompt(self):
        with patch.object(_BRIDGE, "_git", return_value="abc123 change"), \
                patch.object(_BRIDGE, "run_claude", return_value="brief") as runner:
            result = _BRIDGE.do_brief(self.vault, self.cfg)

        self.assertEqual(result, "brief")
        prompt = runner.call_args.args[2]
        self.assertIn("정기 브리핑", prompt)
        for time_of_day in ("아침 브리핑", "점심 브리핑", "저녁 브리핑"):
            self.assertNotIn(time_of_day, prompt)

    def test_briefing_label_maps_the_slot_only_for_telegram(self):
        self.assertEqual(_BRIDGE.briefing_label((8, 30)), ("🌅", "아침"))
        self.assertEqual(_BRIDGE.briefing_label((13, 30)), ("🌞", "점심"))
        self.assertEqual(_BRIDGE.briefing_label((19, 30)), ("🌆", "저녁"))

    def test_startup_consumes_only_already_passed_slots(self):
        current_date, fired, due = _BRIDGE.due_briefing_slots(
            datetime(2026, 9, 1, 12, 0),
            [(7, 30), (12, 0), (13, 30), (19, 30)],
            None,
            set(),
        )

        self.assertEqual(current_date, date(2026, 9, 1))
        self.assertEqual(fired, {(7, 30)})
        self.assertEqual(due, [])

    def test_exact_current_startup_slot_is_due_on_next_evaluation(self):
        now = datetime(2026, 9, 1, 12, 0)
        slots = [(7, 30), (12, 0), (13, 30)]

        current_date, fired, due = _BRIDGE.due_briefing_slots(
            now, slots, None, set())
        self.assertEqual((fired, due), ({(7, 30)}, []))

        current_date, fired, due = _BRIDGE.due_briefing_slots(
            now, slots, current_date, fired)

        self.assertEqual(fired, {(7, 30)})
        self.assertEqual(due, [(12, 0)])

    def test_new_day_resets_fired_slots_and_exposes_due_slots(self):
        current_date, fired, due = _BRIDGE.due_briefing_slots(
            datetime(2026, 9, 1, 12, 0),
            [(7, 30), (13, 30)],
            date(2026, 8, 31),
            {(7, 30), (13, 30)},
        )

        self.assertEqual(current_date, date(2026, 9, 1))
        self.assertEqual(fired, set())
        self.assertEqual(due, [(7, 30)])

    def test_scheduled_briefing_is_marked_only_after_successful_send(self):
        sender = Mock(side_effect=[False, True])
        now = datetime(2026, 9, 1, 7, 30)
        slots = [(7, 30)]

        with patch.object(_BRIDGE, "do_brief", return_value="body"):
            fired_date, fired = _BRIDGE.send_due_briefings(
                self.vault, self.cfg, "12345:secret", 111, now, slots,
                date(2026, 8, 31), set(), sender)
            self.assertEqual((fired_date, fired), (date(2026, 9, 1), set()))

            fired_date, fired = _BRIDGE.send_due_briefings(
                self.vault, self.cfg, "12345:secret", 111, now, slots,
                fired_date, fired, sender)

        self.assertEqual(fired, {(7, 30)})
        self.assertEqual(sender.call_count, 2)
        self.assertIn("🌅 아침 브리핑", sender.call_args.args[2])

    def test_first_send_failure_does_not_call_or_mark_later_due_slots(self):
        sender = Mock(return_value=False)

        with patch.object(_BRIDGE, "do_brief", return_value="body"):
            fired_date, fired = _BRIDGE.send_due_briefings(
                self.vault, self.cfg, "12345:secret", 111,
                datetime(2026, 9, 1, 20, 0),
                [(7, 30), (13, 30), (19, 30)], date(2026, 8, 31),
                set(), sender)

        self.assertEqual(fired_date, date(2026, 9, 1))
        self.assertEqual(fired, set())
        sender.assert_called_once()
        self.assertIn("🌅 아침 브리핑", sender.call_args.args[2])

    def test_manual_brief_wording_is_neutral(self):
        sender = Mock(return_value=True)
        update = {
            "update_id": 7,
            "message": {
                "text": "/brief",
                "date": 1_788_134_400,
                "from": {"id": 111},
                "chat": {"id": 111, "type": "private"},
            },
        }

        with patch.object(_BRIDGE, "do_brief", return_value="body"):
            handled = _BRIDGE.process_update(
                self.vault, self.cfg, "12345:secret", {111}, update,
                0.0, deque(), set(), sender)

        self.assertTrue(handled)
        message = sender.call_args.args[2]
        self.assertEqual(message, "📋 정기 브리핑\nbody")

    def test_butler_completion_is_success_only_and_atomic(self):
        sender = Mock(side_effect=[False, True])
        state_file = self.root / "state" / "last_butler"

        with patch.object(_BRIDGE, "do_butler", return_value="report"), \
                patch.object(
                    _BRIDGE, "atomic_write_text",
                    wraps=_BRIDGE.atomic_write_text) as writer:
            last_butler = _BRIDGE.send_butler_if_due(
                self.vault, self.cfg, "12345:secret", 111, 0.0,
                state_file, 100_000.0, sender)
            self.assertEqual(last_butler, 0.0)
            self.assertFalse(state_file.exists())
            writer.assert_not_called()

            last_butler = _BRIDGE.send_butler_if_due(
                self.vault, self.cfg, "12345:secret", 111, last_butler,
                state_file, 100_000.0, sender)

        self.assertEqual(last_butler, 100_000.0)
        self.assertEqual(state_file.read_text(encoding="utf-8"), "100000.0")
        writer.assert_called_once_with(state_file, "100000.0")
        self.assertEqual(list(state_file.parent.glob("*.tmp")), [])

    def test_template_and_docs_describe_multi_slot_fallback_contract(self):
        template = json.loads(
            (_ROOT / "assets" / "templates" / "vault-config.json").read_text(
                encoding="utf-8"))
        self.assertEqual(template["jarvis"]["briefing_times"], ["07:30"])
        self.assertNotIn("briefing_time", template["jarvis"])

        for relative in (
            "commands/vault-jarvis-setup.md",
            "README.md",
            "docs/superpowers/specs/2026-07-17-vault-jarvis-design.md",
        ):
            with self.subTest(path=relative):
                text = (_ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("`briefing_times`", text)
                self.assertIn("`briefing_time`", text)
                self.assertIn("없을 때만", text)

    def test_user_docs_disclose_deny_zones_are_not_an_os_security_boundary(self):
        disclosure = "deny zone 제한은 프롬프트·허용 도구 정책이며 OS 수준 보안 경계가 아니다."
        for relative in ("commands/vault-jarvis-setup.md", "README.md"):
            with self.subTest(path=relative):
                text = (_ROOT / relative).read_text(encoding="utf-8")
                self.assertIn(disclosure, text)


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


class JarvisUpdateDurabilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.cfg = {"qa_hourly_limit": 6}
        original_state_root = _BRIDGE.STATE_ROOT
        _BRIDGE.STATE_ROOT = self.root / "state"
        self.addCleanup(setattr, _BRIDGE, "STATE_ROOT", original_state_root)

    @staticmethod
    def _update(update_id: int, text: str | None = "/status", *,
                sender_id: int = 111, chat_id: int = 111,
                chat_type: str = "private") -> dict:
        message = {
            "date": 1_788_134_400,
            "from": {"id": sender_id},
            "chat": {"id": chat_id, "type": chat_type},
        }
        if text is not None:
            message["text"] = text
        return {"update_id": update_id, "message": message}

    def test_batch_commits_offset_only_after_success(self):
        saved = []

        def handler(update):
            return update["update_id"] != 11

        offset, complete = _BRIDGE.process_update_batch(
            [{"update_id": 10}, {"update_id": 11}, {"update_id": 12}],
            9, handler, saved.append)

        self.assertEqual((offset, complete), (10, False))
        self.assertEqual(saved, [10])

    def test_batch_processes_updates_in_update_id_order(self):
        handled = []
        saved = []

        offset, complete = _BRIDGE.process_update_batch(
            [{"update_id": 12}, {"update_id": 10}, {"update_id": 11}],
            9, lambda update: handled.append(update["update_id"]) or True,
            saved.append)

        self.assertEqual((offset, complete), (12, True))
        self.assertEqual(handled, [10, 11, 12])
        self.assertEqual(saved, [10, 11, 12])

    def test_group_update_is_discarded_without_response(self):
        sender = Mock(return_value=True)
        update = self._update(7, chat_id=-99, chat_type="group")

        ok = _BRIDGE.process_update(
            self.vault, self.cfg, "12345:secret", {111}, update,
            0.0, deque(), set(), sender)

        self.assertTrue(ok)
        sender.assert_not_called()

    def test_unwhitelisted_private_update_is_discarded_without_response(self):
        sender = Mock(return_value=True)
        update = self._update(9, sender_id=222, chat_id=222)

        ok = _BRIDGE.process_update(
            self.vault, self.cfg, "12345:secret", {111}, update,
            0.0, deque(), set(), sender)

        self.assertTrue(ok)
        sender.assert_not_called()

    def test_non_text_update_is_discarded_without_response(self):
        sender = Mock(return_value=True)
        update = self._update(8, text=None)

        ok = _BRIDGE.process_update(
            self.vault, self.cfg, "12345:secret", {111}, update,
            0.0, deque(), set(), sender)

        self.assertTrue(ok)
        sender.assert_not_called()

    def test_capture_send_failure_retries_same_update_without_duplicate_file(self):
        sender = Mock(side_effect=[False, True])
        update = self._update(77, "기억해: A")
        args = (
            self.vault, self.cfg, "12345:secret", {111}, update,
            0.0, deque(), set(), sender,
        )

        self.assertFalse(_BRIDGE.process_update(*args))
        self.assertTrue(_BRIDGE.process_update(*args))

        captures = list((self.vault / "10-inbox" / "jarvis").glob("*.md"))
        self.assertEqual(len(captures), 1)
        self.assertTrue(captures[0].name.endswith("-77.md"))
        self.assertIn("수신: 2026-08", captures[0].read_text(encoding="utf-8"))

    def test_corrupt_offset_aborts_startup(self):
        path = self.vault / "offset"
        path.write_text("not-an-int", encoding="utf-8")

        with self.assertRaises(_BRIDGE.JarvisConfigError):
            _BRIDGE.read_int_state(path, default=0)

    def test_missing_offset_uses_default(self):
        self.assertEqual(_BRIDGE.read_int_state(self.vault / "offset", default=7), 7)

    def test_offset_directory_aborts_instead_of_using_default(self):
        path = self.vault / "offset"
        path.mkdir()

        with self.assertRaises(_BRIDGE.JarvisConfigError):
            _BRIDGE.read_int_state(path, default=0)

    def test_negative_offset_aborts_instead_of_replaying_updates(self):
        path = self.vault / "offset"
        path.write_text("-1", encoding="utf-8")

        with self.assertRaises(_BRIDGE.JarvisConfigError):
            _BRIDGE.read_int_state(path, default=0)

    def test_same_qa_update_consumes_quota_once_across_retry(self):
        sender = Mock(side_effect=[False, True])
        qa_times, attempted = deque(), set()
        update = self._update(88, "질문")

        with patch.object(_BRIDGE, "do_qa", return_value="답"):
            for _ in range(2):
                _BRIDGE.process_update(
                    self.vault, self.cfg, "12345:secret", {111}, update,
                    0.0, qa_times, attempted, sender)

        self.assertEqual((len(qa_times), attempted), (1, {88}))

    def test_tg_send_returns_true_only_after_all_chunks_send(self):
        with patch.object(_BRIDGE, "_chunks_by_line", return_value=["one", "two"]), \
                patch.object(_BRIDGE, "tg_call", return_value={"ok": True}) as call:
            sent = _BRIDGE.tg_send("12345:secret", 111, "message")

        self.assertTrue(sent)
        self.assertEqual(call.call_count, 2)

    def test_tg_send_returns_false_and_redacts_token_when_fallback_fails(self):
        token = "12345:secret-value"
        html_error = _BRIDGE.urllib.error.HTTPError(
            f"https://api.telegram.org/bot{token}/sendMessage", 400,
            "bad html", {}, None)
        plain_error = _BRIDGE.urllib.error.URLError(
            f"https://api.telegram.org/bot{token}/sendMessage")

        with patch.object(_BRIDGE, "tg_call", side_effect=[html_error, plain_error]), \
                patch.object(_BRIDGE, "log") as logger:
            sent = _BRIDGE.tg_send(token, 111, "message")

        self.assertFalse(sent)
        logged = " ".join(call.args[0] for call in logger.call_args_list)
        self.assertNotIn(token, logged)
        self.assertIn("400", logged)

    def test_get_updates_failure_log_does_not_expose_token(self):
        token = "12345:secret-value"
        get_error = _BRIDGE.urllib.error.HTTPError(
            f"https://api.telegram.org/bot{token}/getUpdates", 502,
            "bad gateway", {}, None)
        cfg = {
            "telegram_user_ids": [],
            "_briefing_slots": [(7, 30)],
            "butler_interval_hours": 24,
        }

        with patch.dict(os.environ, {"JARVIS_TELEGRAM_TOKEN": token}), \
                patch.object(_BRIDGE, "tg_call", side_effect=get_error), \
                patch.object(_BRIDGE.time, "sleep", side_effect=KeyboardInterrupt), \
                patch.object(_BRIDGE, "log") as logger, \
                self.assertRaises(KeyboardInterrupt):
            _BRIDGE.serve(self.vault, cfg)

        logged = " ".join(call.args[0] for call in logger.call_args_list)
        self.assertNotIn(token, logged)
        self.assertNotIn("bad gateway", logged)
        self.assertIn("HTTPError", logged)
        self.assertIn("502", logged)
