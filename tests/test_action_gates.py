"""행동 게이트(gates) 검증 테스트.

행동별 정책 선언의 형식과 정규화 충돌을 검증한다.
실행 시 승인·금지·기록을 강제하는 테스트는 아니다.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "skills" / "agentic-vault" / "scripts" / "vault_healthcheck.py"
CONFIG_TEMPLATE = REPO_ROOT / "assets" / "templates" / "vault-config.json"

SPEC = importlib.util.spec_from_file_location("agentic_vault_healthcheck_gates", MODULE_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import machinery guard
    raise RuntimeError("healthcheck module could not be loaded")
healthcheck = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = healthcheck   # frozen dataclass 가 모듈 네임스페이스를 찾는다
SPEC.loader.exec_module(healthcheck)


class GateValidationTests(unittest.TestCase):
    def _validate(self, gates):
        return healthcheck.validate_config({"gates": gates})["gates"]

    # --- 기본 동작: 설정 없으면 무동작 ---------------------------------
    def test_absent_gates_default_to_empty(self):
        cfg = healthcheck.validate_config({})
        self.assertEqual(cfg["gates"], {})

    def test_empty_gates_allowed(self):
        self.assertEqual(self._validate({}), {})
        self.assertEqual(self._validate(None), {})

    # --- 정상 케이스 ---------------------------------------------------
    def test_accepts_full_spec(self):
        gates = self._validate({
            "note_delete": {"confirm": True, "log": True,
                            "note": "삭제는 되돌릴 수 없다"},
            "lesson_promote": {"repeat": 3, "confirm": True, "probation_days": 14},
            "git_push_network": {"deny": True},
        })
        self.assertTrue(gates["note_delete"]["confirm"])
        self.assertEqual(gates["lesson_promote"]["repeat"], 3)
        self.assertTrue(gates["git_push_network"]["deny"])

    def test_zero_is_a_valid_integer(self):
        gates = self._validate({"x": {"repeat": 0}})
        self.assertEqual(gates["x"]["repeat"], 0)

    def test_result_is_independent_copy(self):
        raw = {"note_delete": {"confirm": True}}
        gates = self._validate(raw)
        gates["note_delete"]["confirm"] = False
        self.assertTrue(raw["note_delete"]["confirm"])

    def test_normalizes_unique_action_names(self):
        self.assertEqual(
            self._validate({" note_delete\t": {"deny": True}}),
            {"note_delete": {"deny": True}},
        )

    def test_rejects_action_names_that_collide_after_normalization(self):
        for alias in (" note_delete", "note_delete ", "\tnote_delete\n"):
            entries = [("note_delete", {"deny": True}), (alias, {"confirm": False})]
            for ordered in (entries, list(reversed(entries))):
                with self.subTest(names=[name for name, _ in ordered]):
                    with self.assertRaises(healthcheck.HealthcheckError):
                        self._validate(dict(ordered))

    # --- fail-closed: 잘못된 입력은 거부 -------------------------------
    def test_rejects_unknown_field(self):
        with self.assertRaises(healthcheck.HealthcheckError) as ctx:
            self._validate({"note_delete": {"confrim": True}})
        self.assertIn("unknown field", str(ctx.exception))

    def test_rejects_non_object_top_level(self):
        with self.assertRaises(healthcheck.HealthcheckError):
            self._validate(["note_delete"])

    def test_rejects_non_object_spec(self):
        with self.assertRaises(healthcheck.HealthcheckError):
            self._validate({"note_delete": True})

    def test_rejects_empty_spec(self):
        with self.assertRaises(healthcheck.HealthcheckError):
            self._validate({"note_delete": {}})

    def test_rejects_non_bool_for_bool_field(self):
        for value in ("true", 1, None):
            with self.subTest(value=value):
                with self.assertRaises(healthcheck.HealthcheckError):
                    self._validate({"note_delete": {"confirm": value}})

    def test_rejects_bad_integer(self):
        for value in (-1, "3", True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(healthcheck.HealthcheckError):
                    self._validate({"lesson_promote": {"repeat": value}})

    def test_rejects_blank_action_name(self):
        with self.assertRaises(healthcheck.HealthcheckError):
            self._validate({"   ": {"confirm": True}})


class TemplateContractTests(unittest.TestCase):
    """템플릿이 실제로 검증을 통과해야 한다 — 문서와 코드가 어긋나면 안 된다."""

    def test_template_config_validates(self):
        raw = json.loads(CONFIG_TEMPLATE.read_text(encoding="utf-8"))
        cfg = healthcheck.validate_config(raw)
        self.assertIsInstance(cfg["gates"], dict)

    def test_template_gates_are_well_formed(self):
        raw = json.loads(CONFIG_TEMPLATE.read_text(encoding="utf-8"))
        gates = healthcheck.validate_config(raw)["gates"]
        if not gates:
            self.skipTest("템플릿에 gates 예시가 아직 없다")
        for action, spec in gates.items():
            self.assertTrue(action.strip(), "행동 이름은 비어 있을 수 없다")
            self.assertTrue(spec, f"{action} 은 최소 한 필드를 가져야 한다")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
