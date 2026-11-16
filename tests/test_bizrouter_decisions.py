"""Tests for the opt-in BizRouter Decisions adapter (google/gemini-decision-1 only) and Jev escalation."""
from __future__ import annotations

import ast
import contextlib
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


KEY = "synthetic-escalation-test-key"
BR_KEY = "synthetic-bizrouter-test-key"
# TypeSafe Jev is reached only through the direct TypeSafe API; these names must never reach BizRouter.
JEV_THROUGH_BIZROUTER = ("typesafe/jev-1.13.0", "typesafe/jev-latest")
UNKNOWN_MODEL = "google/unknown-decision"
LEGEND = {"0": "None ran", "1": "Some ran", "2": "Most ran", "3": "All ran"}
IDS = ["after_freeze", "verdict", "coverage"]
BR_USAGE = {"input_tokens": 120, "output_tokens": 0, "total_tokens": 120, "cost": 0.01}


def _encode(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _printed(result: dict) -> str:
    """The line that jev_ask.main and vault_judge.main print for a result."""
    return json.dumps(result, ensure_ascii=True, allow_nan=False)


def _escalation_request() -> dict:
    return {
        "schema_version": 1,
        "context": {"kind": "selected_text",
                    "text": "Release note: build 42 shipped after the freeze; most tests ran."},
        "questions": [
            {"id": "after_freeze", "type": "noul", "instructions": "Did build 42 ship after the freeze?"},
            {"id": "verdict", "type": "choice", "instructions": "Did the test run pass?",
             "criteria": {"pass": "All tests passed", "fail": "At least one test failed",
                          "unknown": "Insufficient evidence"}, "abstain": "unknown"},
            {"id": "coverage", "type": "score", "instructions": "Rate how completely the tests ran.",
             "criteria": ["None ran", "Some ran", "Most ran", "All ran"]},
        ],
        "min_confidence": 0.8,
    }


def _uncertain_jev_reply() -> dict:
    """Every answer needs review (low_decisiveness, low_confidence, low_confidence), so all three escalate."""
    return {
        "model": "jev-1.13.0",
        "answers": {
            "after_freeze": {"type": "noul", "noul": 0.62},
            "verdict": {"type": "choice", "choice": "pass",
                        "probabilities": {"pass": 0.6, "fail": 0.3, "unknown": 0.1}, "confidence": 0.6},
            "coverage": {"type": "score", "score": 1.7, "legend": dict(LEGEND),
                         "probabilities": {"0": 0, "1": 0.3, "2": 0.7, "3": 0}, "confidence": 0.55},
        },
        "usage": {"input_tokens": 90, "output_tokens": 18},
    }


def _confident_jev_reply() -> dict:
    return {
        "model": "jev-1.13.0",
        "answers": {
            "after_freeze": {"type": "noul", "noul": 0.95},
            "verdict": {"type": "choice", "choice": "pass",
                        "probabilities": {"pass": 0.9, "fail": 0.05, "unknown": 0.05}, "confidence": 0.9},
            "coverage": {"type": "score", "score": 2.0, "legend": dict(LEGEND),
                         "probabilities": {"0": 0, "1": 0, "2": 1, "3": 0}, "confidence": 0.92},
        },
        "usage": {"input_tokens": 90, "output_tokens": 18},
    }


def _bizrouter_reply(answers: dict) -> bytes:
    return _encode({"id": "br-synthetic", "provider": "Gemini", "model": "google/gemini-decision-1",
                    "answers": answers, "usage": dict(BR_USAGE)})


def _call_main(main, argv: list, raw: bytes, env: dict) -> tuple:
    """Run a command-line entry point in-process with these stdin bytes and only these API keys."""
    stdout = io.StringIO()
    stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
    with mock.patch.dict(os.environ, env), mock.patch.object(sys, "stdin", stdin), \
            contextlib.redirect_stdout(stdout):
        for name in ("TYPESAFE_API_KEY", "BIZROUTER_API_KEY"):
            if name not in env:
                os.environ.pop(name, None)
        code = main(argv)
    return code, stdout.getvalue()


# Lines printed by jev_ask.py v0.51.0 (22168b7) for the fixtures above. v0.52.0 changes the output only
# when --escalate-bizrouter applies an escalation, so these bytes must stay identical.
_V0510_HEAD = (
    '{"schema_version": 1, "role": "advisory", "evidence_binding": "inline_not_file_verified", '
    '"model": "jev-1.13.0", "network_attempted": true, '
    '"input_hash": "806978d5120812ef9a1f841dd0bac305f887a3b4c32a6b8c6520bc0b5cfd7e2f", '
    '"request_hash": "78a64e58b619cdc92c89d6104c7f513de345c78c42b2d1c490c180c91a173771", '
    '"policy_hash": "52e597e34c3221932eee38f8bbc7b80d61549dd2ed5a7f98bc77eb5967c41bbc", ')
_V0510_UNCERTAIN = (
    '"status": "needs_review", "question_count": 3, "min_confidence": 0.8, "results": ['
    '{"id": "after_freeze", "type": "noul", "noul": 0.62}, '
    '{"id": "verdict", "type": "choice", "choice": "pass", '
    '"probabilities": {"pass": 0.6, "fail": 0.3, "unknown": 0.1}, "confidence": 0.6}, '
    '{"id": "coverage", "type": "score", "score": 1.7, '
    '"legend": {"0": "None ran", "1": "Some ran", "2": "Most ran", "3": "All ran"}, '
    '"probabilities": {"0": 0, "1": 0.3, "2": 0.7, "3": 0}, "confidence": 0.55}], '
    '"usage": {"input_tokens": 90, "output_tokens": 18}, '
    '"review_reasons": [{"id": "after_freeze", "reason": "low_decisiveness"}, '
    '{"id": "verdict", "reason": "low_confidence"}, {"id": "coverage", "reason": "low_confidence"}]')
_V0510_CANDIDATES = '"candidate_ids": ["after_freeze", "verdict", "coverage"]}}'
V0510_PRINTED = {
    "no_flag": _V0510_HEAD + _V0510_UNCERTAIN + "}",
    "unavailable_missing_key": (
        _V0510_HEAD + _V0510_UNCERTAIN + ', "escalation": {"provider": "bizrouter", '
        '"model": "google/gemini-decision-1", "status": "unavailable", "error_code": "missing_api_key", '
        + _V0510_CANDIDATES),
    "unavailable_transport": (
        _V0510_HEAD + _V0510_UNCERTAIN + ', "escalation": {"provider": "bizrouter", '
        '"model": "google/gemini-decision-1", "status": "unavailable", "error_code": "transport", '
        + _V0510_CANDIDATES),
    "not_needed": (
        _V0510_HEAD + '"status": "reviewed", "question_count": 3, "min_confidence": 0.8, "results": ['
        '{"id": "after_freeze", "type": "noul", "noul": 0.95}, '
        '{"id": "verdict", "type": "choice", "choice": "pass", '
        '"probabilities": {"pass": 0.9, "fail": 0.05, "unknown": 0.05}, "confidence": 0.9}, '
        '{"id": "coverage", "type": "score", "score": 2.0, '
        '"legend": {"0": "None ran", "1": "Some ran", "2": "Most ran", "3": "All ran"}, '
        '"probabilities": {"0": 0, "1": 0, "2": 1, "3": 0}, "confidence": 0.92}], '
        '"usage": {"input_tokens": 90, "output_tokens": 18}, "review_reasons": [], '
        '"escalation": {"provider": "bizrouter", "model": "google/gemini-decision-1", '
        '"status": "not_needed", "escalated_ids": []}}'),
    "prepare_jev": (
        '{"schema_version": 1, "role": "advisory", "evidence_binding": "inline_not_file_verified", '
        '"model": "jev-1.13.0", "network_attempted": false, '
        '"input_hash": "806978d5120812ef9a1f841dd0bac305f887a3b4c32a6b8c6520bc0b5cfd7e2f", '
        '"request_hash": "78a64e58b619cdc92c89d6104c7f513de345c78c42b2d1c490c180c91a173771", '
        '"policy_hash": "52e597e34c3221932eee38f8bbc7b80d61549dd2ed5a7f98bc77eb5967c41bbc", '
        '"status": "prepared", "question_count": 3, "min_confidence": 0.8, "network_required": true}'),
    "prepare_bizrouter": (
        '{"schema_version": 1, "role": "advisory", "evidence_binding": "inline_not_file_verified", '
        '"model": "google/gemini-decision-1", "network_attempted": false, '
        '"input_hash": "806978d5120812ef9a1f841dd0bac305f887a3b4c32a6b8c6520bc0b5cfd7e2f", '
        '"request_hash": "529dccda886671a41302501bcde28b0160833fb1d39c4c56f78b7dbd606ba6f9", '
        '"policy_hash": "652d2e3efdffbb2921f6cfff32f17f9b0be738ef016dfffbeb8fe67bcf232eb6", '
        '"provider": "bizrouter", "status": "prepared", "question_count": 3, "min_confidence": 0.8, '
        '"network_required": true}'),
}


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
        self.assertEqual(captured_br[0]["model"], "google/gemini-decision-1")
        by_id = {r["id"]: r for r in res["results"]}
        self.assertAlmostEqual(by_id["tz_check"]["noul"], 0.97)
        self.assertEqual(by_id["region"]["choice"], "unknown")
        self.assertNotIn("primary", by_id["region"])
        # v0.52.0: the Jev answer (0.27, leaning no) is kept as primary. BizRouter's decisive 0.97 leans
        # yes, so the providers disagree and tz_check still needs review after region's abstention.
        self.assertEqual(by_id["tz_check"]["escalated_from"], "jev-1.13.0")
        self.assertEqual(by_id["tz_check"]["primary"], {"type": "noul", "noul": 0.27})
        self.assertIs(by_id["tz_check"]["agreement"], False)
        self.assertEqual(res["status"], "needs_review")
        self.assertEqual(res["review_reasons"], [{"id": "region", "reason": "abstained"},
                                                 {"id": "tz_check", "reason": "provider_disagreement"}])
        self.assertEqual(res["escalation"]["status"], "applied")
        self.assertEqual(res["escalation"]["escalated_ids"], ["tz_check"])
        self.assertEqual(res["escalation"]["disagreed_ids"], ["tz_check"])

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


class BizRouterModelScopeTests(unittest.TestCase):
    """TypeSafe Jev models are reached only through the direct TypeSafe API, never through BizRouter."""

    def setUp(self) -> None:
        self.sent = []

    def record(self, *args) -> bytes:
        self.sent.append(args)
        return b"{}"

    def assert_invalid_input(self, function, *args, **kwargs) -> None:
        with self.assertRaises(_load_bizrouter().JevError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, "invalid_input")

    def test_bizrouter_client_allows_only_gemini_decision_1(self) -> None:
        br = _load_bizrouter()
        self.assertEqual(br.MODELS, ("google/gemini-decision-1",))
        self.assertEqual(br.DEFAULT_MODEL, "google/gemini-decision-1")
        self.assertEqual(br.model_limits("google/gemini-decision-1"), (1000, 255, 255))
        self.assertIn("not reachable through this client", " ".join(br.__doc__.split()))
        questions = {"q1": {"type": "noul", "instructions": "Is the statement true?"}}
        for name in JEV_THROUGH_BIZROUTER:
            with self.subTest(model=name):
                self.assert_invalid_input(br.model_limits, name)
                self.assert_invalid_input(br.build_payload, {"context": "synthetic"}, questions, model=name)
                self.assert_invalid_input(br.request_judgments, {"context": "synthetic"}, questions,
                                          api_key=BR_KEY, model=name, transport=self.record)
        self.assertEqual(self.sent, [])

    def test_jev_ask_rejects_typesafe_jev_models_through_bizrouter(self) -> None:
        br = _load_bizrouter()
        raw = _encode(_escalation_request())
        # The existing model check: the same result, error code and exit code as any unlisted model.
        expected = {
            "prepare": jev_ask.prepare(raw, api_key=BR_KEY, provider="bizrouter", model=UNKNOWN_MODEL),
            "run": jev_ask.run(raw, allow_network=True, api_key=BR_KEY, provider="bizrouter",
                               model=UNKNOWN_MODEL, transport=self.record),
        }
        for result in expected.values():
            self.assertEqual((result["status"], result["error_code"], result["network_attempted"]),
                             ("unverified", "invalid_arguments", False))
        for name in JEV_THROUGH_BIZROUTER:
            with self.subTest(model=name):
                self.assertEqual(jev_ask.prepare(raw, api_key=BR_KEY, provider="bizrouter", model=name),
                                 expected["prepare"])
                self.assertEqual(jev_ask.run(raw, allow_network=True, api_key=BR_KEY, provider="bizrouter",
                                             model=name, transport=self.record), expected["run"])
                for command in (["prepare", "--input", "-"], ["run", "--input", "-", "--allow-network"]):
                    with mock.patch.object(br, "_send", self.record):
                        code, out = _call_main(jev_ask.main,
                                               [*command, "--provider", "bizrouter", "--model", name],
                                               raw, {"BIZROUTER_API_KEY": BR_KEY})
                    self.assertEqual((code, out), (2, _printed(expected[command[0]]) + "\n"))
        self.assertEqual(self.sent, [])

    def test_vault_judge_rejects_typesafe_jev_models_through_bizrouter(self) -> None:
        br = _load_bizrouter()
        rejected = {"schema_version": 1, "status": "unverified", "role": "advisory",
                    "error": "invalid_arguments"}
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "00-meta").mkdir()
            (vault / "20-knowledge").mkdir()
            (vault / "00-meta" / "vault-config.json").write_text(
                json.dumps({"vault_name": "Test", "exclude_dirs": [".git"]}), encoding="utf-8")
            (vault / "20-knowledge" / "public.md").write_text(
                "# Public fixture\nCSV export is supported.\n", encoding="utf-8")
            request = {
                "schema_version": 1, "task": "support",
                "sources": [{"path": "20-knowledge/public.md", "excerpt": "CSV export is supported."}],
                "questions": [{"id": "export", "instructions": "Does the evidence support CSV export?",
                               "choices": {"yes": "Supported", "no": "Contradicted",
                                           "unknown": "Insufficient evidence"}, "abstain": "unknown"}],
            }
            # The supported model still prepares; an unlisted model gets the existing rejection.
            self.assertEqual(vault_judge.prepare(vault, request, provider="bizrouter")["status"], "prepared")
            self.assertEqual(vault_judge.prepare(vault, request, provider="bizrouter", model=UNKNOWN_MODEL),
                             rejected)
            for name in JEV_THROUGH_BIZROUTER:
                with self.subTest(model=name):
                    self.assertEqual(vault_judge.prepare(vault, request, provider="bizrouter", model=name),
                                     rejected)
                    self.assertEqual(vault_judge.run(vault, request, allow_network=True, api_key=BR_KEY,
                                                     transport=self.record, provider="bizrouter", model=name),
                                     rejected)
                    for command in (["prepare"], ["run", "--allow-network"]):
                        with mock.patch.object(br, "_send", self.record):
                            code, out = _call_main(
                                vault_judge.main,
                                ["--vault", str(vault), "--provider", "bizrouter", "--model", name, *command],
                                _encode(request), {"BIZROUTER_API_KEY": BR_KEY})
                        self.assertEqual((code, out), (2, _printed(rejected) + "\n"))
        self.assertEqual(self.sent, [])


def _agreeing_answers() -> dict:
    return {
        "after_freeze": {"type": "noul", "noul": 0.91, "confidence": 0.91},
        "verdict": {"type": "choice", "choice": "pass",
                    "probabilities": {"pass": 0.9, "fail": 0.05, "unknown": 0.05}, "confidence": 0.9},
        "coverage": {"type": "score", "score": 2.0, "legend": dict(LEGEND),
                     "probabilities": {"0": 0, "1": 0, "2": 1, "3": 0}, "confidence": 0.9},
    }


def _disagreeing_answers() -> dict:
    return {
        "after_freeze": {"type": "noul", "noul": 0.08, "confidence": 0.92},
        "verdict": {"type": "choice", "choice": "fail",
                    "probabilities": {"pass": 0.05, "fail": 0.9, "unknown": 0.05}, "confidence": 0.9},
        "coverage": {"type": "score", "score": 0.0, "legend": dict(LEGEND),
                     "probabilities": {"0": 1, "1": 0, "2": 0, "3": 0}, "confidence": 0.95},
    }


def _score_answer(score: float, probabilities: dict, confidence: float) -> dict:
    return {"type": "score", "score": score, "legend": dict(LEGEND),
            "probabilities": {level: probabilities.get(level, 0) for level in LEGEND}, "confidence": confidence}


class EscalationAgreementTests(unittest.TestCase):
    """--escalate-bizrouter keeps the Jev answer, records agreement and forces review on disagreement."""

    def escalate(self, bizrouter_answers: dict, *, value=None, jev_reply=None) -> tuple:
        sent = []

        def bizrouter_transport(payload, api_key, timeout):
            sent.append(json.loads(payload.decode("utf-8")))
            return _bizrouter_reply(bizrouter_answers)

        reply = _encode(_uncertain_jev_reply() if jev_reply is None else jev_reply)
        result = jev_ask.run(_encode(_escalation_request() if value is None else value), allow_network=True,
                             api_key=KEY, transport=lambda *_: reply, escalate_bizrouter=True,
                             bizrouter_api_key=BR_KEY, bizrouter_transport=bizrouter_transport)
        return result, sent

    def assert_escalated(self, result: dict, bizrouter_answers: dict, agreement: dict) -> None:
        """Each entry: the BizRouter answer, escalated_from, primary (the unescalated Jev entry) and agreement."""
        without_flag = jev_ask.run(_encode(_escalation_request()), allow_network=True, api_key=KEY,
                                   transport=lambda *_: _encode(_uncertain_jev_reply()))
        primary = {entry["id"]: {key: value for key, value in entry.items() if key != "id"}
                   for entry in without_flag["results"]}
        self.assertEqual([entry["id"] for entry in result["results"]], IDS)
        for entry in result["results"]:
            qid = entry["id"]
            self.assertEqual(entry, {"id": qid, **bizrouter_answers[qid], "escalated_from": "jev-1.13.0",
                                     "primary": primary[qid], "agreement": agreement[qid]})
            self.assertEqual(list(entry)[-3:], ["escalated_from", "primary", "agreement"])
            self.assertEqual(list(entry["primary"]), list(primary[qid]))
            self.assertIs(entry["agreement"], agreement[qid])

    def test_agreeing_providers_keep_the_jev_answer_and_need_no_review(self) -> None:
        result, sent = self.escalate(_agreeing_answers())
        self.assertEqual([body["model"] for body in sent], ["google/gemini-decision-1"])
        self.assertEqual(list(sent[0]["questions"]), IDS)
        self.assert_escalated(result, _agreeing_answers(), dict.fromkeys(IDS, True))
        self.assertEqual(result["status"], "reviewed")
        self.assertEqual(result["review_reasons"], [])
        self.assertEqual(result["escalation"], {
            "provider": "bizrouter", "model": "google/gemini-decision-1", "status": "applied",
            "escalated_ids": IDS, "disagreed_ids": [], "usage": BR_USAGE})

    def test_disagreeing_providers_force_review_for_each_disagreement(self) -> None:
        result, sent = self.escalate(_disagreeing_answers())
        self.assertEqual([body["model"] for body in sent], ["google/gemini-decision-1"])
        self.assert_escalated(result, _disagreeing_answers(), dict.fromkeys(IDS, False))
        # Every escalated answer is decisive and confident; only the disagreement forces review.
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["review_reasons"],
                         [{"id": qid, "reason": "provider_disagreement"} for qid in IDS])
        self.assertEqual(result["escalation"], {
            "provider": "bizrouter", "model": "google/gemini-decision-1", "status": "applied",
            "escalated_ids": IDS, "disagreed_ids": IDS, "usage": BR_USAGE})

    def test_agreement_never_removes_a_reason_and_disagreements_follow_the_recomputed_reasons(self) -> None:
        mixed = {
            # The same side of 0.5 as Jev's 0.62 but not decisive: agreement keeps low_decisiveness.
            "after_freeze": {"type": "noul", "noul": 0.55, "confidence": 0.55},
            # The abstention label differs from Jev's "pass": abstained and provider_disagreement.
            "verdict": {"type": "choice", "choice": "unknown",
                        "probabilities": {"pass": 0.1, "fail": 0.1, "unknown": 0.8}, "confidence": 0.8},
            # One level above Jev's 1.7 agrees, but its low confidence stays a reason.
            "coverage": {"type": "score", "score": 2.7, "legend": dict(LEGEND),
                         "probabilities": {"0": 0, "1": 0, "2": 0.3, "3": 0.7}, "confidence": 0.7},
        }
        result, _ = self.escalate(mixed)
        self.assert_escalated(result, mixed, {"after_freeze": True, "verdict": False, "coverage": True})
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["review_reasons"], [
            {"id": "after_freeze", "reason": "low_decisiveness"},
            {"id": "verdict", "reason": "abstained"},
            {"id": "coverage", "reason": "low_confidence"},
            {"id": "verdict", "reason": "provider_disagreement"},
        ])
        self.assertEqual(result["escalation"]["escalated_ids"], IDS)
        self.assertEqual(result["escalation"]["disagreed_ids"], ["verdict"])

    def test_agreement_rules_at_their_boundaries(self) -> None:
        def single(question: dict) -> dict:
            return {"schema_version": 1, "context": {"kind": "user_input", "text": "Synthetic boundary case."},
                    "questions": [question], "min_confidence": 0.8}

        noul = single({"id": "q", "type": "noul", "instructions": "Is the statement true?"})
        score = single({"id": "q", "type": "score", "instructions": "Rate how completely the tests ran.",
                        "criteria": list(LEGEND.values())})
        cases = (
            # A noul probability equal to 0.5 never agrees; otherwise both must be on one side of 0.5.
            (noul, {"type": "noul", "noul": 0.5}, {"type": "noul", "noul": 0.9, "confidence": 0.9}, False),
            (noul, {"type": "noul", "noul": 0.45}, {"type": "noul", "noul": 0.5, "confidence": 0.5}, False),
            (noul, {"type": "noul", "noul": 0.45}, {"type": "noul", "noul": 0.1, "confidence": 0.9}, True),
            (noul, {"type": "noul", "noul": 0.62}, {"type": "noul", "noul": 0.51, "confidence": 0.51}, True),
            # Scores agree when at most one level apart (2.7 - 1.7 is 1.0000000000000002 in binary
            # floating point and still counts as one level).
            (score, _score_answer(1.0, {"1": 1}, 0.5), _score_answer(2.0, {"2": 1}, 0.9), True),
            (score, _score_answer(2.0, {"2": 1}, 0.5), _score_answer(1.0, {"1": 1}, 0.9), True),
            (score, _score_answer(1.7, {"1": 0.3, "2": 0.7}, 0.5),
             _score_answer(2.7, {"2": 0.3, "3": 0.7}, 0.9), True),
            (score, _score_answer(1.0, {"1": 1}, 0.5), _score_answer(2.02, {"2": 0.98, "3": 0.02}, 0.9), False),
            # The slack is 1e-9, for binary rounding only: 1.000001 levels apart is a disagreement,
            # while 1.0000000005 levels apart still agrees.
            (score, _score_answer(1.0, {"1": 1}, 0.5),
             _score_answer(2.000001, {"2": 0.999999, "3": 0.000001}, 0.9), False),
            (score, _score_answer(1.0, {"1": 1}, 0.5),
             _score_answer(2.0000000005, {"2": 0.9999999995, "3": 5e-10}, 0.9), True),
        )
        for value, jev_answer, bizrouter_answer, agreed in cases:
            with self.subTest(jev=jev_answer, bizrouter=bizrouter_answer):
                reply = {"model": "jev-1.13.0", "answers": {"q": jev_answer},
                         "usage": {"input_tokens": 9, "output_tokens": 3}}
                result, sent = self.escalate({"q": bizrouter_answer}, value=value, jev_reply=reply)
                self.assertEqual(len(sent), 1)
                self.assertEqual(result["escalation"]["status"], "applied")
                entry = result["results"][0]
                self.assertEqual(entry["primary"], jev_answer)
                self.assertIs(entry["agreement"], agreed)
                self.assertEqual(result["escalation"]["disagreed_ids"], [] if agreed else ["q"])
                self.assertEqual({"id": "q", "reason": "provider_disagreement"} in result["review_reasons"],
                                 not agreed)

    def test_command_line_prints_the_applied_escalation(self) -> None:
        raw = _encode(_escalation_request())
        for answers, exit_code, disagreed in ((_agreeing_answers(), 0, []), (_disagreeing_answers(), 2, IDS)):
            sent = []

            def bizrouter_send(payload, api_key, timeout, answers=answers, sent=sent):
                sent.append(json.loads(payload.decode("utf-8")))
                return _bizrouter_reply(answers)

            with self.subTest(exit_code=exit_code):
                with mock.patch.object(jev_ask.jev_client, "_send", lambda *_: _encode(_uncertain_jev_reply())), \
                        mock.patch.object(_load_bizrouter(), "_send", bizrouter_send):
                    code, out = _call_main(
                        jev_ask.main, ["run", "--input", "-", "--allow-network", "--escalate-bizrouter"],
                        raw, {"TYPESAFE_API_KEY": KEY, "BIZROUTER_API_KEY": BR_KEY})
                self.assertEqual(code, exit_code)
                self.assertEqual(out, _printed(self.escalate(answers)[0]) + "\n")
                self.assertEqual(json.loads(out)["escalation"]["disagreed_ids"], disagreed)
                self.assertEqual([body["model"] for body in sent], ["google/gemini-decision-1"])


class V0510OutputTests(unittest.TestCase):
    """Without an applied escalation, jev_ask.py prints the v0.51.0 bytes."""

    def test_without_the_flag_the_output_is_the_v0510_output(self) -> None:
        raw = _encode(_escalation_request())
        result = jev_ask.run(raw, allow_network=True, api_key=KEY,
                             transport=lambda *_: _encode(_uncertain_jev_reply()))
        self.assertEqual(_printed(result), V0510_PRINTED["no_flag"])
        with mock.patch.object(jev_ask.jev_client, "_send", lambda *_: _encode(_uncertain_jev_reply())):
            code, out = _call_main(jev_ask.main, ["run", "--input", "-", "--allow-network"], raw,
                                   {"TYPESAFE_API_KEY": KEY})
        self.assertEqual((code, out), (2, V0510_PRINTED["no_flag"] + "\n"))

    def test_not_needed_and_unavailable_escalations_keep_the_v0510_output(self) -> None:
        raw = _encode(_escalation_request())
        calls = []

        def failing(*args):
            calls.append(args)
            raise OSError("synthetic transport failure")

        cases = (("not_needed", _confident_jev_reply(), BR_KEY, 0),
                 ("unavailable_missing_key", _uncertain_jev_reply(), "", 0),
                 ("unavailable_transport", _uncertain_jev_reply(), BR_KEY, 1))
        for name, reply, bizrouter_key, attempts in cases:
            with self.subTest(name):
                calls.clear()
                result = jev_ask.run(raw, allow_network=True, api_key=KEY,
                                     transport=lambda *_, reply=reply: _encode(reply),
                                     escalate_bizrouter=True, bizrouter_api_key=bizrouter_key,
                                     bizrouter_transport=failing)
                self.assertEqual(_printed(result), V0510_PRINTED[name])
                self.assertEqual(len(calls), attempts)

    def test_prepare_keeps_the_v0510_output_for_both_providers(self) -> None:
        raw = _encode(_escalation_request())
        self.assertEqual(_printed(jev_ask.prepare(raw, api_key=KEY)), V0510_PRINTED["prepare_jev"])
        self.assertEqual(_printed(jev_ask.prepare(raw, api_key=BR_KEY, provider="bizrouter")),
                         V0510_PRINTED["prepare_bizrouter"])


if __name__ == "__main__":
    unittest.main()
