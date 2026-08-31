"""Focused behavioral tests for Jarvis configuration and message authorization."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from collections import deque
from contextlib import redirect_stdout
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch


_ROOT = Path(__file__).parents[1]
_BRIDGE_PATH = _ROOT / "skills" / "agentic-vault" / "scripts" / "jarvis_bridge.py"
_SPEC = importlib.util.spec_from_file_location("jarvis_bridge", _BRIDGE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_BRIDGE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BRIDGE)


class JarvisConfigAuthorizationTests(unittest.TestCase):
    def test_recipient_parser_accepts_only_positive_integers_and_ascii_decimals(self):
        self.assertEqual(_BRIDGE.parse_telegram_user_ids([17, "002"]), [17, 2])

        for invalid in (
            "17", [True], [1.0], [1.5], [0], [-1], ["0"], ["-1"],
            ["+1"], [" 1"], ["1 "], ["\u0661"], [""], [None],
        ):
            with self.subTest(value=invalid), self.assertRaises(
                    _BRIDGE.JarvisConfigError):
                _BRIDGE.parse_telegram_user_ids(invalid)

    def test_token_parser_validates_the_complete_shape_without_echoing_input(self):
        self.assertEqual(
            _BRIDGE.parse_telegram_token("12345:Abc_09-z"),
            ("12345", "Abc_09-z"),
        )

        for invalid in (
            "12345", "12345:", ":secret", "bot:secret", "123:two:parts",
            "123:bad.secret", "\uff11\uff12\uff13:secret", "123:s\u00e9cret", None,
        ):
            with self.subTest(value=invalid), self.assertRaises(
                    _BRIDGE.JarvisConfigError) as raised:
                _BRIDGE.parse_telegram_token(invalid)
            if isinstance(invalid, str):
                self.assertNotIn(invalid, str(raised.exception))

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


class JarvisConfigLoadingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.vault = Path(temporary.name)
        (self.vault / "00-meta").mkdir()
        self.path = self.vault / "00-meta" / "vault-config.json"
        self.valid = {
            "enabled": True,
            "telegram_user_ids": [111],
            "briefing_time": "07:30",
            "butler_interval_hours": 24,
            "qa_hourly_limit": 6,
            "qa_timeout_sec": 180,
            "claude_cmd": "claude",
        }

    def _write(self, root: object) -> None:
        self.path.write_text(json.dumps(root), encoding="utf-8")

    def test_only_missing_config_missing_block_and_exact_false_are_inactive(self):
        self.assertIsNone(_BRIDGE.load_jarvis_config(self.vault))
        self._write({"language": "ko"})
        self.assertIsNone(_BRIDGE.load_jarvis_config(self.vault))
        self._write({"jarvis": {"enabled": False}})
        self.assertIsNone(_BRIDGE.load_jarvis_config(self.vault))

    def test_malformed_read_root_block_and_enabled_values_are_configuration_errors(self):
        raw_cases = ("{", "\ufeff{}")
        for raw in raw_cases:
            with self.subTest(raw=repr(raw)):
                self.path.write_text(raw, encoding="utf-8")
                with self.assertRaises(_BRIDGE.JarvisConfigError):
                    _BRIDGE.load_jarvis_config(self.vault)

        for root in (
            [], {"jarvis": None}, {"jarvis": []}, {"jarvis": {}},
            {"jarvis": {"enabled": 1}}, {"jarvis": {"enabled": "false"}},
        ):
            with self.subTest(root=root):
                self._write(root)
                with self.assertRaises(_BRIDGE.JarvisConfigError):
                    _BRIDGE.load_jarvis_config(self.vault)

        self._write({"jarvis": self.valid})
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")), \
                self.assertRaises(_BRIDGE.JarvisConfigError):
            _BRIDGE.load_jarvis_config(self.vault)

    def test_enabled_settings_are_strictly_validated(self):
        invalid_settings = (
            ("telegram_user_ids", [-100]),
            ("briefing_times", []),
            ("butler_interval_hours", 0),
            ("butler_interval_hours", -1),
            ("butler_interval_hours", float("nan")),
            ("butler_interval_hours", float("inf")),
            ("butler_interval_hours", True),
            ("qa_hourly_limit", 0),
            ("qa_hourly_limit", -1),
            ("qa_hourly_limit", 1.0),
            ("qa_hourly_limit", True),
            ("qa_timeout_sec", 0),
            ("qa_timeout_sec", -1),
            ("qa_timeout_sec", float("nan")),
            ("qa_timeout_sec", float("inf")),
            ("qa_timeout_sec", True),
            ("claude_cmd", ""),
            ("claude_cmd", "   "),
            ("claude_cmd", 1),
        )
        for key, value in invalid_settings:
            with self.subTest(key=key, value=value):
                block = {**self.valid, key: value}
                self._write({"jarvis": block})
                with self.assertRaises(_BRIDGE.JarvisConfigError):
                    _BRIDGE.load_jarvis_config(self.vault)

    def test_valid_integer_settings_legacy_time_and_decimal_ids_remain_compatible(self):
        block = {**self.valid, "telegram_user_ids": [9, "002"]}
        self._write({"jarvis": block})

        loaded = _BRIDGE.load_jarvis_config(self.vault)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["telegram_user_ids"], [9, 2])
        self.assertEqual(loaded["_briefing_slots"], [(7, 30)])
        self.assertEqual(loaded["butler_interval_hours"], 24)
        self.assertEqual(loaded["qa_hourly_limit"], 6)
        self.assertEqual(loaded["qa_timeout_sec"], 180)


class JarvisTelegramBoundaryTests(unittest.TestCase):
    token = "12345:Secret_value-9"

    @staticmethod
    def _response(payload: object) -> MagicMock:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
        return response

    def test_tg_call_rejects_false_missing_and_non_object_protocol_roots(self):
        cases = (
            {"ok": False, "error_code": 400, "description": "CONTENT-SENTINEL"},
            {"result": []},
            ["not", "an", "object"],
        )
        for payload in cases:
            with self.subTest(payload=payload), patch.object(
                    _BRIDGE.urllib.request, "urlopen",
                    return_value=self._response(payload)), self.assertRaises(
                        _BRIDGE.TelegramAPIError) as raised:
                _BRIDGE.tg_call(self.token, "getUpdates")
            rendered = str(raised.exception)
            self.assertNotIn(self.token, rendered)
            self.assertNotIn("CONTENT-SENTINEL", rendered)

    def test_tg_call_normalizes_transport_url_json_and_unicode_failures(self):
        http_error = _BRIDGE.urllib.error.HTTPError(
            f"https://api.telegram.org/bot{self.token}/getUpdates", 502,
            "CONTENT-SENTINEL", {}, None)
        failures = (
            ("getUpdates", http_error, None, 502),
            ("getUpdates", _BRIDGE.urllib.error.URLError("CONTENT-SENTINEL"), None, None),
            ("getUpdates", TypeError("CONTENT-SENTINEL"), None, None),
            ("getUpdates", None, b"{", None),
            ("getUpdates", None, b"\xff", None),
            ("bad method", None, None, None),
            (None, None, None, None),
        )
        for method, transport_error, raw, expected_code in failures:
            with self.subTest(method=method, error=type(transport_error).__name__):
                response = MagicMock()
                response.__enter__.return_value.read.return_value = raw
                replacement = (
                    patch.object(_BRIDGE.urllib.request, "urlopen", side_effect=transport_error)
                    if transport_error is not None else
                    patch.object(_BRIDGE.urllib.request, "urlopen", return_value=response)
                )
                with replacement, self.assertRaises(
                        _BRIDGE.TelegramAPIError) as raised:
                    _BRIDGE.tg_call(self.token, method)
                rendered = str(raised.exception)
                self.assertNotIn(self.token, rendered)
                self.assertNotIn("CONTENT-SENTINEL", rendered)
                self.assertEqual(raised.exception.code, expected_code)
                self.assertIsNone(raised.exception.__context__)

    def test_tg_send_never_turns_ok_false_into_success_via_plain_retry(self):
        failure = _BRIDGE.TelegramAPIError("TelegramResponseError", 400)
        with patch.object(
                _BRIDGE, "tg_call", side_effect=[failure, {"ok": True}]) as caller, \
                patch.object(_BRIDGE, "log"):
            sent = _BRIDGE.tg_send(self.token, 111, "message")

        self.assertFalse(sent)
        self.assertEqual(caller.call_count, 1)

    def test_chunks_bound_long_lines_and_reconstruct_exactly(self):
        text = "x" * 23

        chunks = _BRIDGE._chunks_by_line(text, limit=10)

        self.assertEqual(chunks, ["x" * 10, "x" * 10, "x" * 3])
        self.assertTrue(all(len(chunk) <= 10 for chunk in chunks))
        self.assertEqual("".join(chunks), text)

    def test_chunks_preserve_exact_boundary_and_newlines_when_possible(self):
        self.assertEqual(_BRIDGE._chunks_by_line("x" * 10, limit=10), ["x" * 10])
        text = "ab\ncd\n"
        chunks = _BRIDGE._chunks_by_line(text, limit=3)
        self.assertEqual(chunks, ["ab\n", "cd\n"])
        self.assertEqual("".join(chunks), text)

    def test_tg_send_uses_bounded_plain_text_when_rendered_html_is_oversized(self):
        text = "&" * _BRIDGE.TG_CHUNK
        with patch.object(_BRIDGE, "tg_call", return_value={"ok": True}) as caller:
            sent = _BRIDGE.tg_send(self.token, 111, text)

        self.assertTrue(sent)
        caller.assert_called_once()
        self.assertEqual(caller.call_args.kwargs["text"], text)
        self.assertNotIn("parse_mode", caller.call_args.kwargs)
        self.assertLessEqual(len(caller.call_args.kwargs["text"]), _BRIDGE.TG_CHUNK)

    def test_html_failure_never_falls_back_to_an_oversized_plain_chunk(self):
        calls = []

        def fake_call(_token, _method, **kwargs):
            calls.append(kwargs)
            if kwargs.get("parse_mode") == "HTML":
                raise _BRIDGE.TelegramAPIError("HTTPError", 400)
            return {"ok": True}

        with patch.object(_BRIDGE, "tg_call", side_effect=fake_call), \
                patch.object(_BRIDGE, "log"):
            sent = _BRIDGE.tg_send(self.token, 111, "a" * 5000)

        self.assertTrue(sent)
        self.assertTrue(calls)
        self.assertTrue(all(len(call["text"]) <= _BRIDGE.TG_CHUNK for call in calls))


class JarvisSubprocessBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.vault = Path(temporary.name)
        self.parent_env = {
            "JARVIS_TELEGRAM_TOKEN": "UPPER-SECRET",
            "jarvis_telegram_token": "lower-secret",
            "Jarvis_Telegram_Token": "mixed-secret",
            "KEEP_ME": "safe",
        }

    def _assert_scrubbed_copy(self, child_env: dict[str, str]) -> None:
        self.assertEqual(child_env, {"KEEP_ME": "safe"})
        self.assertIsNot(child_env, self.parent_env)
        self.assertEqual(len(self.parent_env), 4)

    def test_claude_process_receives_a_case_insensitively_scrubbed_environment(self):
        cfg = {
            "claude_cmd": "claude", "_deny_zones": [],
            "_hot_note": "00-meta/hot.md", "_language": "ko",
            "qa_timeout_sec": 30,
        }
        completed = Mock(returncode=0, stdout="answer", stderr="")
        with patch.object(_BRIDGE.os, "environ", self.parent_env), \
                patch.object(_BRIDGE.shutil, "which", return_value="claude"), \
                patch.object(_BRIDGE.subprocess, "run", return_value=completed) as runner:
            self.assertEqual(_BRIDGE.run_claude(self.vault, cfg, "question"), "answer")

        self._assert_scrubbed_copy(runner.call_args.kwargs["env"])

    def test_claude_failure_log_contains_return_code_but_not_stderr(self):
        cfg = {
            "claude_cmd": "claude", "_deny_zones": [],
            "_hot_note": "00-meta/hot.md", "_language": "ko",
            "qa_timeout_sec": 30,
        }
        completed = Mock(
            returncode=9, stdout="", stderr="CONTENT-SENTINEL UPPER-SECRET")
        with patch.object(_BRIDGE.os, "environ", self.parent_env), \
                patch.object(_BRIDGE.shutil, "which", return_value="claude"), \
                patch.object(_BRIDGE.subprocess, "run", return_value=completed), \
                patch.object(_BRIDGE, "log") as logger:
            _BRIDGE.run_claude(self.vault, cfg, "question")

        rendered = " ".join(call.args[0] for call in logger.call_args_list)
        self.assertIn("9", rendered)
        self.assertNotIn("CONTENT-SENTINEL", rendered)
        self.assertNotIn("UPPER-SECRET", rendered)

    def test_healthcheck_process_receives_a_case_insensitively_scrubbed_environment(self):
        cfg = {"_health_report": "00-meta/health-report.md"}
        completed = Mock(returncode=0, stdout="", stderr="")
        with patch.object(_BRIDGE.os, "environ", self.parent_env), \
                patch.object(_BRIDGE.subprocess, "run", return_value=completed) as runner, \
                patch.object(_BRIDGE, "_git", return_value=""):
            _BRIDGE.do_butler(self.vault, cfg)

        self._assert_scrubbed_copy(runner.call_args.kwargs["env"])

    def test_git_process_receives_a_case_insensitively_scrubbed_environment(self):
        completed = Mock(returncode=0, stdout="ok\n", stderr="")
        with patch.object(_BRIDGE.os, "environ", self.parent_env), \
                patch.object(_BRIDGE.subprocess, "run", return_value=completed) as runner:
            self.assertEqual(_BRIDGE._git(self.vault, "status"), "ok")

        self._assert_scrubbed_copy(runner.call_args.kwargs["env"])


class JarvisMainTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.vault = self.root / "vault"
        (self.vault / "00-meta").mkdir(parents=True)
        self.config = self.vault / "00-meta" / "vault-config.json"
        original_state_root = _BRIDGE.STATE_ROOT
        _BRIDGE.STATE_ROOT = self.root / "state"
        self.addCleanup(setattr, _BRIDGE, "STATE_ROOT", original_state_root)

    def _run_main(self, env: dict[str, str]) -> tuple[int, str]:
        output = io.StringIO()
        argv = ["jarvis_bridge.py", "--vault", str(self.vault)]
        with patch.object(sys, "argv", argv), patch.dict(os.environ, env, clear=True), \
                redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            _BRIDGE.main()
        return int(raised.exception.code), output.getvalue()

    def _write_enabled(self) -> None:
        self.config.write_text(json.dumps({
            "jarvis": {"enabled": True, "telegram_user_ids": [111]}
        }), encoding="utf-8")

    def test_main_converts_malformed_config_to_one_nonzero_line_without_traceback(self):
        self.config.write_text("{", encoding="utf-8")

        code, output = self._run_main({"JARVIS_TELEGRAM_TOKEN": "12345:secret"})

        self.assertNotEqual(code, 0)
        self.assertEqual(len(output.splitlines()), 1)
        self.assertIn("configuration error", output.lower())
        self.assertNotIn("traceback", output.lower())

    def test_main_treats_missing_token_as_configuration_error(self):
        self._write_enabled()

        code, output = self._run_main({})

        self.assertNotEqual(code, 0)
        self.assertEqual(len(output.splitlines()), 1)
        self.assertIn("configuration error", output.lower())
        self.assertNotIn("traceback", output.lower())

    def test_main_never_echoes_a_malformed_token(self):
        self._write_enabled()
        token = "12345:SECRET_SENTINEL:extra"

        code, output = self._run_main({"JARVIS_TELEGRAM_TOKEN": token})

        self.assertNotEqual(code, 0)
        self.assertEqual(len(output.splitlines()), 1)
        self.assertNotIn(token, output)
        self.assertNotIn("SECRET_SENTINEL", output)

    def test_main_keeps_inactive_jarvis_as_success(self):
        self.config.write_text(
            json.dumps({"jarvis": {"enabled": False}}), encoding="utf-8")

        code, _output = self._run_main({})

        self.assertEqual(code, 0)


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

    def test_float_state_accepts_missing_default_and_finite_non_negative_values(self):
        path = self.root / "last_butler"
        self.assertEqual(_BRIDGE.read_float_state(path, default=7.5), 7.5)
        for raw, expected in (("0", 0.0), ("12.5", 12.5)):
            with self.subTest(raw=raw):
                path.write_text(raw, encoding="utf-8")
                self.assertEqual(_BRIDGE.read_float_state(path), expected)

    def test_float_state_rejects_objects_unreadable_and_non_finite_values(self):
        path = self.root / "last_butler"
        path.mkdir()
        with self.assertRaises(_BRIDGE.JarvisConfigError):
            _BRIDGE.read_float_state(path)
        path.rmdir()

        for raw in ("", "bad", "-0.1", "nan", "inf", "-inf"):
            with self.subTest(raw=raw):
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(_BRIDGE.JarvisConfigError):
                    _BRIDGE.read_float_state(path)

        path.write_text("1", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")), \
                self.assertRaises(_BRIDGE.JarvisConfigError):
            _BRIDGE.read_float_state(path)

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

    def test_invalid_last_butler_aborts_before_polling(self):
        token = "12345:secret"
        namespace = _BRIDGE.state_dir_for(self.vault, token, _BRIDGE.STATE_ROOT)
        namespace.mkdir(parents=True)
        (namespace / "last_butler").write_text("nan", encoding="utf-8")
        cfg = {
            "telegram_user_ids": [],
            "_briefing_slots": [(7, 30)],
            "butler_interval_hours": 24,
        }

        with patch.dict(os.environ, {"JARVIS_TELEGRAM_TOKEN": token}), \
                patch.object(_BRIDGE, "tg_call", side_effect=AssertionError("polled")), \
                patch.object(_BRIDGE, "log"), \
                self.assertRaises(_BRIDGE.JarvisConfigError):
            _BRIDGE.serve(self.vault, cfg)

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

    def test_authorized_inbound_logs_metadata_but_never_message_content(self):
        cases = (
            (91, "remember CONTENT-SENTINEL-CAPTURE", "capture"),
            (92, "CONTENT-SENTINEL-QUESTION", "qa"),
        )
        for update_id, text, route_name in cases:
            with self.subTest(route=route_name), patch.object(
                    _BRIDGE, "do_qa", return_value="answer"), patch.object(
                        _BRIDGE, "log") as logger:
                handled = _BRIDGE.process_update(
                    self.vault, self.cfg, "12345:secret", {111},
                    self._update(update_id, text), 0.0, deque(), set(),
                    Mock(return_value=True))

            self.assertTrue(handled)
            rendered = " ".join(call.args[0] for call in logger.call_args_list)
            self.assertNotIn("CONTENT-SENTINEL", rendered)
            self.assertIn(f"route={route_name}", rendered)
            self.assertIn(f"update_id={update_id}", rendered)
            self.assertIn("sender=111", rendered)
            self.assertIn(f"length={len(text)}", rendered)

    def test_qa_attempt_id_is_retained_on_failure_then_pruned_after_commit(self):
        update = self._update(93, "question")
        qa_times, attempted = deque(), set()
        sender = Mock(side_effect=[False, True])
        saved = []

        def handler(item):
            return _BRIDGE.process_update(
                self.vault, self.cfg, "12345:secret", {111}, item,
                0.0, qa_times, attempted, sender)

        with patch.object(_BRIDGE, "do_qa", return_value="answer"):
            offset, complete = _BRIDGE.process_update_batch(
                [update], 0, handler, saved.append,
                lambda item: attempted.discard(item["update_id"]))
            self.assertEqual((offset, complete), (0, False))
            self.assertEqual(attempted, {93})
            self.assertEqual(saved, [])

            offset, complete = _BRIDGE.process_update_batch(
                [update], offset, handler, saved.append,
                lambda item: attempted.discard(item["update_id"]))

        self.assertEqual((offset, complete), (93, True))
        self.assertEqual(saved, [93])
        self.assertEqual(attempted, set())
        self.assertEqual(len(qa_times), 1)

    def test_qa_attempt_id_is_not_pruned_when_offset_write_fails(self):
        update = self._update(94, "question")
        qa_times, attempted = deque(), set()

        def save_failure(_offset):
            raise OSError("disk full")

        with patch.object(_BRIDGE, "do_qa", return_value="answer"), \
                self.assertRaises(OSError):
            _BRIDGE.process_update_batch(
                [update], 0,
                lambda item: _BRIDGE.process_update(
                    self.vault, self.cfg, "12345:secret", {111}, item,
                    0.0, qa_times, attempted, Mock(return_value=True)),
                save_failure,
                lambda item: attempted.discard(item["update_id"]))

        self.assertEqual(attempted, {94})

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

    def test_protocol_poll_failure_retries_without_advancing_offset(self):
        token = "12345:secret-value"
        cfg = {
            "telegram_user_ids": [],
            "_briefing_slots": [(7, 30)],
            "butler_interval_hours": 24,
        }
        failure = _BRIDGE.TelegramAPIError("TelegramResponseError", 502)

        with patch.dict(os.environ, {"JARVIS_TELEGRAM_TOKEN": token}), \
                patch.object(_BRIDGE, "tg_call", side_effect=[failure, KeyboardInterrupt]), \
                patch.object(_BRIDGE.time, "sleep") as sleeper, \
                patch.object(_BRIDGE, "log"), \
                self.assertRaises(KeyboardInterrupt):
            _BRIDGE.serve(self.vault, cfg)

        sleeper.assert_called_once_with(5)
        namespace = _BRIDGE.state_dir_for(self.vault, token, _BRIDGE.STATE_ROOT)
        self.assertFalse((namespace / "offset").exists())

    def test_scheduled_work_uses_the_smallest_validated_positive_recipient(self):
        token = "12345:secret-value"
        cfg = {
            "telegram_user_ids": [17, 2],
            "_briefing_slots": [(7, 30)],
            "butler_interval_hours": 24,
        }

        with patch.dict(os.environ, {"JARVIS_TELEGRAM_TOKEN": token}), \
                patch.object(
                    _BRIDGE, "send_due_briefings", side_effect=KeyboardInterrupt) as sender, \
                patch.object(_BRIDGE, "log"), self.assertRaises(KeyboardInterrupt):
            _BRIDGE.serve(self.vault, cfg)

        self.assertEqual(sender.call_args.args[3], 2)
        self.assertGreater(sender.call_args.args[3], 0)

    def test_serve_rejects_a_negative_scheduled_recipient_at_its_boundary(self):
        cfg = {
            "telegram_user_ids": [-100, 111],
            "_briefing_slots": [(7, 30)],
            "butler_interval_hours": 24,
        }

        with patch.dict(os.environ, {"JARVIS_TELEGRAM_TOKEN": "12345:secret"}), \
                patch.object(
                    _BRIDGE, "send_due_briefings", side_effect=AssertionError("scheduled")), \
                patch.object(_BRIDGE, "log"), \
                self.assertRaises(_BRIDGE.JarvisConfigError):
            _BRIDGE.serve(self.vault, cfg)

    def test_protocol_send_failure_does_not_mark_scheduled_completion(self):
        failure = _BRIDGE.TelegramAPIError("TelegramResponseError", 400)
        with patch.object(_BRIDGE, "tg_call", side_effect=failure), \
                patch.object(_BRIDGE, "do_brief", return_value="body"), \
                patch.object(_BRIDGE, "log"):
            fired_date, fired = _BRIDGE.send_due_briefings(
                self.vault, self.cfg, "12345:secret", 111,
                datetime(2026, 9, 1, 7, 30), [(7, 30)],
                date(2026, 8, 31), set(), _BRIDGE.tg_send)

        self.assertEqual(fired_date, date(2026, 9, 1))
        self.assertEqual(fired, set())

    def test_protocol_send_failure_does_not_advance_inbound_offset(self):
        update = self._update(95, "/status")
        saved = []
        failure = _BRIDGE.TelegramAPIError("TelegramResponseError", 400)

        with patch.object(_BRIDGE, "tg_call", side_effect=failure), \
                patch.object(_BRIDGE, "log"):
            offset, complete = _BRIDGE.process_update_batch(
                [update], 0,
                lambda item: _BRIDGE.process_update(
                    self.vault, self.cfg, "12345:secret", {111}, item,
                    0.0, deque(), set(), _BRIDGE.tg_send),
                saved.append)

        self.assertEqual((offset, complete), (0, False))
        self.assertEqual(saved, [])
