"""Tests for pre-registered evaluation plans and post-hoc amendment dual verdicts."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verify_eval_plan as vep  # noqa: E402


def _sample_plan(*, amendments: list | None = None) -> dict:
    plan = {
        "schema": vep.SCHEMA_PLAN,
        "plan_id": "recall-holdout-2026-11-23",
        "frozen_utc": "2026-11-23T00:00:00Z",
        "claim_limit": "Checked-in recall_hard fixture only; not a general production retrieval benchmark.",
        "gates": [
            {"id": "top5_hit", "metric": "top5_hit_rate", "op": ">=", "threshold": 0.90},
            {"id": "no_answer_fp", "metric": "no_answer_fp_rate", "op": "<=", "threshold": 0.05},
            {"id": "zero_errors", "metric": "error_count", "op": "==", "threshold": 0},
        ],
    }
    if amendments is not None:
        plan["amendments"] = amendments
    return plan


class VerifyEvalPlanTests(unittest.TestCase):
    def test_seal_verify_and_evaluate_clean_pass(self) -> None:
        sealed = vep.seal_plan(_sample_plan())
        info = vep.validate_plan(sealed, require_sealed=True)
        self.assertTrue(info["valid"])
        self.assertTrue(info["sealed"])
        self.assertEqual(info["gate_count"], 3)
        self.assertEqual(info["amendment_count"], 0)

        obs = {
            "measured_utc": "2026-11-23T01:00:00Z",
            "metrics": {"top5_hit_rate": 0.95, "no_answer_fp_rate": 0.02, "error_count": 0},
        }
        report = vep.evaluate_plan(sealed, obs, require_sealed=True)
        self.assertEqual(report["original_verdict"], "passed")
        self.assertEqual(report["amended_verdict"], "passed")
        self.assertFalse(report["verdict_diverged"])
        self.assertFalse(report["has_post_hoc_amendments"])
        self.assertFalse(report["disclosure_required"])
        self.assertFalse(report["post_hoc_rescued_failure"])

    def test_post_hoc_amendment_preserves_original_failure_beside_amended_pass(self) -> None:
        base_sealed = vep.seal_plan(_sample_plan())
        original_core_digest = base_sealed["frozen_core_sha256"]

        amended = dict(base_sealed)
        amended["amendments"] = [
            {
                "id": "relax-top5-after-outlier",
                "recorded_utc": "2026-11-23T02:00:00Z",
                "timing": "post_hoc",
                "what_changed": "Lowered top5_hit threshold from 0.90 to 0.85 after single stall outlier.",
                "why": "Single outlier query pulled raw hit rate to 0.87.",
                "gate_overrides": {"top5_hit": {"op": ">=", "threshold": 0.85}},
            }
        ]
        # Re-sealing keeps frozen_core_sha256 IDENTICAL while updating plan_sha256.
        amended_sealed = vep.seal_plan(amended)
        self.assertEqual(amended_sealed["frozen_core_sha256"], original_core_digest)
        self.assertNotEqual(amended_sealed["plan_sha256"], base_sealed["plan_sha256"])

        obs = {
            "measured_utc": "2026-11-23T01:00:00Z",
            "metrics": {"top5_hit_rate": 0.87, "no_answer_fp_rate": 0.01, "error_count": 0},
        }
        report = vep.evaluate_plan(amended_sealed, obs, require_sealed=True)
        self.assertEqual(report["original_verdict"], "failed")
        self.assertEqual(report["original_failed_gates"], ["top5_hit"])
        self.assertEqual(report["amended_verdict"], "passed")
        self.assertEqual(report["amended_failed_gates"], [])
        self.assertTrue(report["has_post_hoc_amendments"])
        self.assertTrue(report["verdict_diverged"])
        self.assertTrue(report["disclosure_required"])
        self.assertTrue(report["post_hoc_rescued_failure"])

    def test_rejects_pre_measurement_amendment_dated_after_measurement(self) -> None:
        plan = vep.seal_plan(_sample_plan(amendments=[
            {
                "id": "backdated-pre",
                "recorded_utc": "2026-11-23T03:00:00Z",
                "timing": "pre_measurement",
                "what_changed": "Changed threshold before run.",
                "why": "Claimed pre-measurement despite later timestamp.",
                "gate_overrides": {"top5_hit": {"op": ">=", "threshold": 0.80}},
            }
        ]))
        obs = {
            "measured_utc": "2026-11-23T01:00:00Z",
            "metrics": {"top5_hit_rate": 0.85, "no_answer_fp_rate": 0.0, "error_count": 0},
        }
        with self.assertRaisesRegex(vep.EvalPlanError, "pre_measurement_amendment_after_measurement"):
            vep.evaluate_plan(plan, obs, require_sealed=True)

    def test_detects_tampered_frozen_core_and_plan_digests(self) -> None:
        sealed = vep.seal_plan(_sample_plan())
        tampered_core = json.loads(json.dumps(sealed))
        tampered_core["gates"][0]["threshold"] = 0.50
        with self.assertRaisesRegex(vep.EvalPlanError, "frozen_core_digest_mismatch"):
            vep.validate_plan(tampered_core, require_sealed=True)

        tampered_plan = json.loads(json.dumps(sealed))
        tampered_plan["notes"] = "quiet edit"
        with self.assertRaisesRegex(vep.EvalPlanError, "plan_digest_mismatch"):
            vep.validate_plan(tampered_plan, require_sealed=True)

    def test_rejects_bool_as_int_and_missing_claim_limit_and_duplicate_keys(self) -> None:
        bad = _sample_plan()
        bad["claim_limit"] = "   "
        with self.assertRaisesRegex(vep.EvalPlanError, "missing_claim_limit"):
            vep.validate_plan(bad)

        sealed = vep.seal_plan(_sample_plan())
        obs = {"metrics": {"top5_hit_rate": True, "no_answer_fp_rate": 0.0, "error_count": False}}
        report = vep.evaluate_plan(sealed, obs)
        self.assertEqual(report["original_verdict"], "failed")
        by_id = {g["id"]: g for g in report["original_gates"]}
        self.assertFalse(by_id["top5_hit"]["passed"])
        self.assertEqual(by_id["top5_hit"]["reason"], "type_mismatch")
        self.assertFalse(by_id["zero_errors"]["passed"])

        with self.assertRaisesRegex(vep.EvalPlanError, "duplicate_json_key"):
            vep._loads_strict('{"a": 1, "a": 2}')
        with self.assertRaisesRegex(vep.EvalPlanError, "nonfinite_json_number"):
            vep._loads_strict('{"a": NaN}')

    def test_cli_seal_verify_evaluate_and_strict_original(self) -> None:
        with tempfile.TemporaryDirectory(prefix="av-plan-") as tmp:
            tdir = Path(tmp)
            raw_path = tdir / "plan.json"
            sealed_path = tdir / "plan.sealed.json"
            obs_path = tdir / "obs.json"
            raw_path.write_text(
                json.dumps(_sample_plan(amendments=[
                    {
                        "id": "post-hoc-relax",
                        "recorded_utc": "2026-11-23T02:00:00Z",
                        "timing": "post_hoc",
                        "what_changed": "Lowered top5_hit to 0.85.",
                        "why": "Single outlier stall.",
                        "gate_overrides": {"top5_hit": {"op": ">=", "threshold": 0.85}},
                    }
                ])),
                encoding="utf-8",
            )
            obs_path.write_text(
                json.dumps({
                    "measured_utc": "2026-11-23T01:00:00Z",
                    "metrics": {"top5_hit_rate": 0.87, "no_answer_fp_rate": 0.0, "error_count": 0},
                }),
                encoding="utf-8",
            )
            p_seal = subprocess.run(
                [sys.executable, str(SCRIPTS / "verify_eval_plan.py"), "seal", "--plan", str(raw_path), "--out", str(sealed_path)],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
            self.assertEqual(p_seal.returncode, 0, p_seal.stderr)
            p_ver = subprocess.run(
                [sys.executable, str(SCRIPTS / "verify_eval_plan.py"), "verify", "--plan", str(sealed_path), "--require-sealed"],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
            self.assertEqual(p_ver.returncode, 0, p_ver.stderr)

            # Default evaluate exits 0 (amended passed) while disclosing original failure.
            p_eval = subprocess.run(
                [sys.executable, str(SCRIPTS / "verify_eval_plan.py"), "evaluate", "--plan", str(sealed_path), "--observations", str(obs_path), "--require-sealed"],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
            self.assertEqual(p_eval.returncode, 0, p_eval.stderr)
            rep = json.loads(p_eval.stdout)
            self.assertEqual(rep["original_verdict"], "failed")
            self.assertEqual(rep["amended_verdict"], "passed")
            self.assertTrue(rep["post_hoc_rescued_failure"])

            # --strict-original exits 1 because original pre-registered gate failed!
            p_strict = subprocess.run(
                [sys.executable, str(SCRIPTS / "verify_eval_plan.py"), "evaluate", "--plan", str(sealed_path), "--observations", str(obs_path), "--require-sealed", "--strict-original"],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
            self.assertEqual(p_strict.returncode, 1)


if __name__ == "__main__":
    unittest.main()
