"""Tests for the opt-in BizRouter Decisions adapter (google/gemini-decision-1 and Jev)."""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "agentic-vault" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import jev_ask  # noqa: E402
import vault_judge  # noqa: E402


def _load_bizrouter():
    import bizrouter_client
    return bizrouter_client


class BizRouterClientAndCliTests(unittest.TestCase):
    def test_module_uses_stdlib_only_and_fixed_origin(self) -> None:
        br = _load_bizrouter()
        self.assertEqual(br.ENDPOINT, "https://api.bizrouter.ai/v1/decisions")
        self.assertEqual(br.HOST, "api.bizrouter.ai")
        self.assertEqual(br.PORT, 443)
        self.assertEqual(br.PATH, "/v1/decisions")
        self.assertEqual(br.DEFAULT_MODEL, "google/gemini-decision-1")
        tree = ast.parse((SCRIPTS / "bizrouter_client.py").read_text(encoding="utf-8"))
        forbidden = {"requests", "httpx", "aiohttp", "openai", "google", "urllib3"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertFalse({n.name.split(".")[0] for n in node.names} & forbidden)
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0], forbidden)

    def test_default_jev_ask_is_unchanged_without_provider_flag(self) -> None:
        req = {
            "schema_version": 1,
            "context": {"kind": "user_input", "text": "1+1=2?"},
            "questions": [{"id": "q1", "type": "noul", "instructions": "Is 1+1=2?"}],
            "min_confidence": 0.8,
        }
        raw = json.dumps(req).encode("utf-8")
        prep = jev_ask.prepare(raw, api_key="test-key")
        self.assertEqual(prep["status"], "prepared")
        self.assertEqual(prep["model"], "jev-1.13.0")
        self.assertNotIn("provider", prep)

    def test_bizrouter_gemini_supports_large_question_batches_and_15_level_scores(self) -> None:
        questions = [
            {"id": f"q_{i:02d}", "type": "noul", "instructions": f"Item {i} positive?"}
            for i in range(1, 20)
        ]
        questions.append({
            "id": "q_20_score15",
            "type": "score",
            "instructions": "Rate value 7 on 0..14.",
            "criteria": [f"Level {k}" for k in range(15)],
        })
        req = {
            "schema_version": 1,
            "context": {"kind": "selected_text", "text": "Observed value is 7."},
            "questions": questions,
            "min_confidence": 0.8,
        }
        raw = json.dumps(req).encode("utf-8")
        # Default Jev rejects >12 questions and >10 score levels.
        self.assertEqual(jev_ask.prepare(raw, api_key="nonmatching-secret-xyz")["error_code"], "invalid_input")

        prep = jev_ask.prepare(raw, api_key="br-key", provider="bizrouter", model="google/gemini-decision-1")
        self.assertEqual(prep["status"], "prepared")
        self.assertEqual(prep["provider"], "bizrouter")
        self.assertEqual(prep["model"], "google/gemini-decision-1")
        self.assertEqual(prep["question_count"], 20)

        probs = {str(k): (1.0 if k == 7 else 0.0) for k in range(15)}
        legend = {str(k): f"Level {k}" for k in range(15)}
        fake_answers = {
            f"q_{i:02d}": {"type": "noul", "noul": 0.96, "confidence": 0.96}
            for i in range(1, 20)
        }
        fake_answers["q_20_score15"] = {
            "type": "score",
            "score": 7.0,
            "confidence": 1.0,
            "legend": legend,
            "probabilities": probs,
        }
        fake_reply = json.dumps({
            "id": "req-123",
            "provider": "Gemini",
            "model": "google/gemini-decision-1",
            "answers": fake_answers,
            "usage": {
                "input_tokens": 1223,
                "output_tokens": 0,
                "total_tokens": 1223,
                "cost": 0.176234,
                "input_tokens_by_modality": [{"modality": "text", "tokens": 1223}],
            },
        }).encode("utf-8")

        res = jev_ask.run(
            raw,
            allow_network=True,
            api_key="br-key-safe",
            provider="bizrouter",
            model="google/gemini-decision-1",
            transport=lambda payload, key, timeout: fake_reply,
        )
        self.assertEqual(res["status"], "reviewed")
        self.assertEqual(res["provider"], "bizrouter")
        self.assertEqual(res["model"], "google/gemini-decision-1")
        self.assertEqual(res["review_reasons"], [])
        self.assertEqual(len(res["results"]), 20)
        self.assertAlmostEqual(res["usage"]["cost"], 0.176234)

    def test_bizrouter_rejects_sensitive_input_and_caller_key_echo(self) -> None:
        caller_key = "my_custom_bizrouter_token_value_99"
        req = {
            "schema_version": 1,
            "context": {"kind": "user_input", "text": f" accidental leak {caller_key} "},
            "questions": [{"id": "q1", "type": "noul", "instructions": "Check?"}],
        }
        res = jev_ask.prepare(json.dumps(req).encode("utf-8"), api_key=caller_key, provider="bizrouter")
        self.assertEqual(res["status"], "unverified")
        self.assertEqual(res["error_code"], "sensitive_input")

    def test_escalate_bizrouter_rejudges_only_low_confidence_items_not_abstentions(self) -> None:
        req = {
            "schema_version": 1,
            "context": {"kind": "selected_text", "text": "Deploy log: 2026-11-15T23:30:00Z. Server region omitted."},
            "questions": [
                {"id": "tz_check", "type": "noul", "instructions": "Inside 08:00-09:00 KST on 2026-11-16?"},
                {
                    "id": "region",
                    "type": "choice",
                    "instructions": "Server country?",
                    "criteria": {"kr": "Korea", "jp": "Japan", "unknown": "Insufficient evidence"},
                    "abstain": "unknown",
                },
            ],
            "min_confidence": 0.8,
        }
        raw = json.dumps(req).encode("utf-8")
        jev_reply = json.dumps({
            "model": "jev-1.13.0",
            "answers": {
                "tz_check": {"type": "noul", "noul": 0.27},  # strength 0.73 < 0.8 -> low_decisiveness!
                "region": {
                    "type": "choice",
                    "choice": "unknown",
                    "confidence": 1.0,
                    "probabilities": {"kr": 0.0, "jp": 0.0, "unknown": 1.0},
                },
            },
            "usage": {"input_tokens": 200, "output_tokens": 20},
        }).encode("utf-8")

        captured_br = []

        def fake_br_transport(payload: bytes, key: str, timeout: float) -> bytes:
            body = json.loads(payload.decode("utf-8"))
            captured_br.append(body)
            return json.dumps({
                "id": "br-esc-1",
                "provider": "Gemini",
                "model": "google/gemini-decision-1",
                "answers": {
                    "tz_check": {"type": "noul", "noul": 0.97, "confidence": 0.97},
                },
                "usage": {"input_tokens": 140, "output_tokens": 0, "total_tokens": 140, "cost": 0.02},
            }).encode("utf-8")

        res = jev_ask.run(
            raw,
            allow_network=True,
            api_key="ts-key",
            escalate_bizrouter=True,
            bizrouter_api_key="br-key",
            transport=lambda p, k, t: jev_reply,
            bizrouter_transport=fake_br_transport,
        )
        # Only tz_check was escalated; region ("abstained") was NOT sent to BizRouter.
        self.assertEqual(len(captured_br), 1)
        self.assertEqual(list(captured_br[0]["questions"].keys()), ["tz_check"])
        by_id = {r["id"]: r for r in res["results"]}
        self.assertAlmostEqual(by_id["tz_check"]["noul"], 0.97)
        self.assertEqual(by_id["region"]["choice"], "unknown")
        # tz_check is now resolved (>= 0.8), while region remains abstained -> needs_review for region only.
        self.assertEqual(res["review_reasons"], [{"id": "region", "reason": "abstained"}])
        self.assertEqual(res["escalation"]["status"], "applied")
        self.assertEqual(res["escalation"]["escalated_ids"], ["tz_check"])

    def test_vault_judge_bizrouter_enforces_source_binding_and_deny_zones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "00-meta").mkdir()
            (vault / "20-knowledge").mkdir()
            (vault / "90-assets").mkdir()
            (vault / "00-meta" / "vault-config.json").write_text(
                json.dumps({"deny_zones": ["90-assets"], "exclude_dirs": [".git"]}),
                encoding="utf-8",
            )
            (vault / "20-knowledge" / "spec.md").write_text(
                "---\ntitle: Spec\n---\nRated current is 1,600A (1.6kA).\n",
                encoding="utf-8",
            )
            (vault / "90-assets" / "secret.md").write_text("Denied content\n", encoding="utf-8")

            denied_req = {
                "schema_version": 1,
                "task": "support",
                "sources": [{"path": "90-assets/secret.md", "excerpt": "Denied content"}],
                "questions": [{
                    "id": "q1",
                    "instructions": "Supported?",
                    "choices": {"yes": "Yes", "no": "No", "unknown": "Unknown"},
                    "abstain": "unknown",
                }],
            }
            denied_res = vault_judge.prepare(vault, denied_req, provider="bizrouter")
            self.assertEqual(denied_res["status"], "unverified")
            self.assertEqual(denied_res["error"], "unsafe_source")

            # Valid 15-choice question (>12 choices allowed on BizRouter Gemini!)
            choices = {f"opt_{k}": f"Option {k} meaning" for k in range(14)}
            choices["unknown"] = "Insufficient evidence"
            valid_req = {
                "schema_version": 1,
                "task": "classification",
                "sources": [{"path": "20-knowledge/spec.md", "excerpt": "Rated current is 1,600A (1.6kA)."}],
                "questions": [{
                    "id": "q_class",
                    "instructions": "Classify rated current.",
                    "choices": choices,
                    "abstain": "unknown",
                }],
            }
            prep = vault_judge.prepare(vault, valid_req, provider="bizrouter")
            self.assertEqual(prep["status"], "prepared")
            self.assertEqual(prep["provider"], "bizrouter")
            self.assertEqual(prep["model"], "google/gemini-decision-1")

            probs = {k: (0.95 if k == "opt_1" else (0.05 if k == "unknown" else 0.0)) for k in choices}
            fake_reply = json.dumps({
                "id": "vj-1",
                "provider": "Gemini",
                "model": "google/gemini-decision-1",
                "answers": {
                    "q_class": {
                        "type": "choice",
                        "choice": "opt_1",
                        "confidence": 0.95,
                        "probabilities": probs,
                    }
                },
                "usage": {"input_tokens": 150, "output_tokens": 0, "total_tokens": 150, "cost": 0.021},
            }).encode("utf-8")

            ran = vault_judge.run(
                vault,
                valid_req,
                allow_network=True,
                api_key="br-judge-key",
                provider="bizrouter",
                transport=lambda payload, key, timeout: fake_reply,
            )
            self.assertEqual(ran["status"], "reviewed")
            self.assertEqual(ran["provider"], "bizrouter")
            self.assertEqual(ran["answers"]["q_class"]["choice"], "opt_1")


if __name__ == "__main__":
    unittest.main()
