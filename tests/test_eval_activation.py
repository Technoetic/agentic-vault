"""Tests for treatment activation and control zero-leakage silent no-op audits."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import eval_activation as ea  # noqa: E402
import evaluate_retrieval_backends as erb  # noqa: E402
import evaluate_tool_matrix as etm  # noqa: E402


class EvalActivationTests(unittest.TestCase):
    def test_core_audit_distinguishes_pass_silent_noop_control_leakage_and_both(self) -> None:
        ok = ea.audit_arm_activation(
            {
                "control": [{"active": False, "calls": 0}, {"active": False, "calls": 0}],
                "treatment": [{"active": True, "calls": 3}, {"active": True, "calls": 2}],
            },
            treatment_arms=("treatment",),
            control_arms=("control",),
        )
        self.assertTrue(ok["passed"])
        self.assertEqual(ok["status"], "passed")
        self.assertEqual(ok["exit_code"], 0)

        noop = ea.audit_arm_activation(
            {
                "control": [{"active": False, "calls": 0}],
                "treatment": [{"active": True, "calls": 2}, {"active": False, "calls": 0}],
            },
            treatment_arms=("treatment",),
            control_arms=("control",),
            require_all_runs=True,
        )
        self.assertFalse(noop["passed"])
        self.assertEqual(noop["status"], "silent_noop")
        self.assertEqual(noop["exit_code"], ea.INACTIVE_EXIT)

        leak = ea.audit_arm_activation(
            {
                "control": [{"active": True, "calls": 1}],
                "treatment": [{"active": True, "calls": 2}],
            },
            treatment_arms=("treatment",),
            control_arms=("control",),
        )
        self.assertFalse(leak["passed"])
        self.assertEqual(leak["status"], "control_leakage")
        self.assertEqual(leak["exit_code"], ea.INACTIVE_EXIT)

        both = ea.audit_arm_activation(
            {
                "control": [{"active": True, "calls": 1}],
                "treatment": [{"active": False, "calls": 0}],
            },
            treatment_arms=("treatment",),
            control_arms=("control",),
        )
        self.assertFalse(both["passed"])
        self.assertEqual(both["status"], "both_failed")
        self.assertEqual(both["exit_code"], ea.INACTIVE_EXIT)

    def test_retrieval_backends_check_activation_and_default_invariance(self) -> None:
        base = erb.evaluate("recall_hard")
        self.assertNotIn("activation_audit", base)

        audited = erb.evaluate("recall_hard", check_activation=True)
        self.assertIn("activation_audit", audited)
        self.assertTrue(audited["activation_audit"]["passed"])
        self.assertEqual(audited["activation_audit"]["exit_code"], 0)
        self.assertEqual(audited["activation_audit"]["control_arms"]["bm25"]["active_runs"], 0)
        self.assertGreater(audited["activation_audit"]["treatment_arms"]["bm25_hangul2"]["active_runs"], 0)

        # Simulated silent no-op in telemetry fails with exit 70.
        failed = ea.audit_retrieval_backend_telemetry({
            "bm25": [{"active": False, "calls": 0, "has_hangul": True}],
            "bm25_hangul2": [{"active": False, "calls": 0, "has_hangul": True}],
        })
        self.assertFalse(failed["passed"])
        self.assertEqual(failed["status"], "silent_noop")
        self.assertEqual(failed["exit_code"], 70)

    def test_tool_matrix_check_activation_with_and_without_string_patterns(self) -> None:
        spec = etm.MatrixSpec(request_languages=("ko",), instruction_languages=("en",), repeats=1)
        base = etm.evaluate_matrix(spec=spec)
        self.assertNotIn("activation_audit", base)

        audited = etm.evaluate_matrix(spec=spec, string_patterns=True, check_activation=True)
        self.assertIn("activation_audit", audited)
        audit = audited["activation_audit"]
        self.assertTrue(audit["passed"])
        self.assertEqual(audit["exit_code"], 0)
        self.assertIn("schema_B_patterns", audit["treatment_arms"])
        self.assertIn("schema_A_control", audit["control_arms"])
        self.assertEqual(audit["control_arms"]["schema_A_control"]["active_runs"], 0)

        # CLI --check-activation passes cleanly on default scripted control.
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "evaluate_tool_matrix.py"),
                "--request-language", "ko",
                "--instruction-language", "en",
                "--string-patterns",
                "--check-activation",
            ],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertTrue(data["activation_audit"]["passed"])

        # Corrupted schema_variant leakage triggers exit 70 verdict.
        corrupted = json.loads(json.dumps(audited))
        for ep in corrupted["episodes"]:
            if ep["schema_variant"] == "A":
                ep["evaluation_conditions"]["schema_variant"] = "B"
        leak_audit = ea.audit_tool_matrix_report(corrupted)
        self.assertFalse(leak_audit["passed"])
        self.assertEqual(leak_audit["status"], "control_leakage")
        self.assertEqual(leak_audit["exit_code"], 70)


if __name__ == "__main__":
    unittest.main()
