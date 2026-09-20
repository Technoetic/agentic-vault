"""Direct typed judgments: synthetic fixtures, no real credentials or network."""
from __future__ import annotations

import copy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/agentic-vault/scripts"
KEY = "synthetic-direct-test-key"


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def request():
    return {
        "schema_version": 1,
        "context": {"kind": "user_input", "text": "1+1=2; Hello, welcome!"},
        "questions": [
            {"id": "arithmetic", "type": "noul", "instructions": "Is 1+1=2 true?"},
            {"id": "selection", "type": "choice", "instructions": "Choose the sum.",
             "criteria": {"two": "Two", "unknown": "Insufficient evidence"}, "abstain": "unknown"},
            {"id": "rating", "type": "score", "instructions": "Rate greeting friendliness.",
             "criteria": ["Hostile", "Neutral", "Friendly"]},
        ],
    }


def response():
    return {
        "model": "jev-1.13.0",
        "answers": {
            "arithmetic": {"type": "noul", "noul": 0.98},
            "selection": {"type": "choice", "choice": "two",
                          "probabilities": {"two": 1, "unknown": 0}, "confidence": 1},
            "rating": {"type": "score", "score": 1.99,
                       "legend": {"0": "Hostile", "1": "Neutral", "2": "Friendly"},
                       "probabilities": {"0": 0, "1": 0.01, "2": 0.99}, "confidence": 0.98},
        },
        "usage": {"input_tokens": 30, "output_tokens": 12},
    }


class JevAskTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((SCRIPTS / "jev_ask.py").is_file(), "Direct Jev adapter is not implemented")
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        self.ask = importlib.import_module("jev_ask")
        self.environment = mock.patch.dict("os.environ", {"TYPESAFE_API_KEY": KEY})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def run_request(self, value=None, reply=None, **options):
        options.setdefault("allow_network", True)
        options.setdefault("api_key", KEY)
        options.setdefault("transport", lambda *_: encode(response() if reply is None else reply))
        return self.ask.run(encode(request() if value is None else value), **options)

    def assert_error(self, result, code, attempted=False):
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result.get("error_code"), code)
        self.assertIs(result["network_attempted"], attempted)
        self.assertEqual(result["role"], "advisory")
        self.assertEqual(result["evidence_binding"], "inline_not_file_verified")
        self.assertNotIn(KEY, json.dumps(result))
        self.assertNotIn("results", result)

    def test_native_three_type_batch_with_no_file_provenance(self):
        calls = []

        def transport(raw, key, timeout):
            calls.append((json.loads(raw), key, timeout))
            return encode(response())

        result = self.run_request(transport=transport)
        self.assertEqual(result["status"], "reviewed")
        self.assertTrue(result["network_attempted"])
        self.assertIn("results", result, "Shared direct output must use a typed results array")
        self.assertEqual(result["results"], [{"id": identifier, **answer} for identifier, answer in response()["answers"].items()])
        self.assertNotIn("confidence", result["results"][0])
        self.assertNotIn("sources", result)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0]["state"], {"context": request()["context"]})
        self.assertEqual(calls[0][0]["model"], "jev-1.13.0")
        self.assertNotIn("abstain", calls[0][0]["questions"]["selection"])
        self.assertNotIn("id", calls[0][0]["questions"]["selection"])
        self.assertEqual(calls[0][1:], (KEY, 10.0))

    def test_prepare_is_offline_metadata_and_hashes_bind_policy_and_wire(self):
        data = request()
        with mock.patch.object(self.ask.jev_client, "_send", side_effect=AssertionError("network")):
            prepared = self.ask.prepare(encode(data))
        self.assertEqual(prepared["status"], "prepared")
        self.assertFalse(prepared["network_attempted"])
        for field in ("input_hash", "request_hash", "policy_hash"):
            self.assertRegex(prepared[field], r"^[a-f0-9]{64}$")
        self.assertNotIn("context", prepared)
        self.assertNotIn("instructions", json.dumps(prepared))
        self.assertNotIn("Hello", json.dumps(prepared))
        self.assertEqual(prepared["question_count"], 3)
        same = self.ask.prepare(json.dumps(data, indent=2).encode())
        self.assertEqual(same["input_hash"], prepared["input_hash"])
        data["min_confidence"] = 0.9
        changed = self.ask.prepare(encode(data))
        self.assertNotEqual(changed["input_hash"], prepared["input_hash"])
        self.assertNotEqual(changed["policy_hash"], prepared["policy_hash"])
        self.assertEqual(changed["request_hash"], prepared["request_hash"])
        data["context"]["text"] += " Changed."
        changed = self.ask.prepare(encode(data))
        self.assertNotEqual(changed["request_hash"], prepared["request_hash"])

    def test_selected_text_unicode_criteria_and_optional_noul_criteria(self):
        data = request()
        data["context"] = {"kind": "selected_text", "text": "안녕하세요"}
        data["questions"][0]["criteria"] = {"true": "참", "false": "거짓"}
        data["questions"][1].update(criteria={"둘": "2", "모름": "근거 없음"}, abstain="모름")
        reply = response()
        reply["answers"]["selection"].update(choice="둘", probabilities={"둘": 1, "모름": 0})
        self.assertEqual(self.run_request(data, reply)["status"], "reviewed")

    def test_native_noul_decisiveness_low_and_tie_never_fabricate_confidence(self):
        for value, threshold, expected in ((0.98, 0.8, "reviewed"), (0.02, 0.8, "reviewed"),
                                            (0.7, 0.8, "needs_review"), (0.5, 0, "needs_review")):
            data, reply = request(), response()
            data["min_confidence"] = threshold
            reply["answers"]["arithmetic"]["noul"] = value
            result = self.run_request(data, reply)
            with self.subTest(probability=value):
                self.assertEqual(result["status"], expected)
                self.assertIn("results", result)
                self.assertNotIn("confidence", result["results"][0])
                if expected == "needs_review":
                    self.assertEqual(result["review_reasons"], [{"id": "arithmetic", "reason": "low_decisiveness"}])

    def test_choice_abstention_and_native_low_confidence_require_review(self):
        reply = response()
        reply["answers"]["selection"].update(choice="unknown", probabilities={"two": 0, "unknown": 1})
        result = self.run_request(reply=reply)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["review_reasons"], [{"id": "selection", "reason": "abstained"}])
        for identifier in ("selection", "rating"):
            reply = response()
            reply["answers"][identifier]["confidence"] = 0.79
            result = self.run_request(reply=reply)
            self.assertEqual(result["review_reasons"], [{"id": identifier, "reason": "low_confidence"}])

    def test_no_authorized_network_and_no_key_never_send(self):
        def fail(*_):
            self.fail("Unauthorized network request")
        self.assert_error(self.run_request(allow_network=False, transport=fail), "network_disabled")
        self.assert_error(self.run_request(api_key="", transport=fail), "missing_api_key")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assert_error(self.run_request(api_key=None, transport=fail), "missing_api_key")
        for key in (42, True, "bad\r\nheader", " leading", "k" * 4097):
            self.assert_error(self.run_request(api_key=key, transport=fail), "invalid_input")

    def test_actual_key_and_recognizable_credentials_are_rejected_before_send(self):
        for secret in (KEY, 'sk-' + 'a' * 24, 'ghp_' + 'a' * 30,
                       'github_pat_' + 'a' * 24, 'AKIA' + 'A' * 16,
                       'ts_' + 'A' * 24, 'tsk_' + 'A' * 24, 'typesafe_' + 'A' * 24,
                       'eyJ' + 'a' * 10 + '.' + 'b' * 10 + '.' + 'c' * 10,
                       'apikey_' + 'a' * 32 + '_' + 'b' * 64,
                       '-----BEGIN PRIVATE KEY-----', 'password=longpassword',
                       'access_token=abcd1234', 'refresh_token: abcd1234', 'client_secret=abcd1234',
                       'Authorization: Basic ZmFrZTpmYWtl', 'MY_API_KEY="abcd1234"'):
            data = request()
            data["context"]["text"] = secret
            result = self.run_request(data, transport=lambda *_: self.fail("secret sent"))
            self.assert_error(result, "sensitive_input")
            self.assertNotIn(secret, json.dumps(result))
        data = request()
        data["questions"][2]["criteria"][1] = KEY
        self.assert_error(self.run_request(data, transport=lambda *_: self.fail("secret sent")), "sensitive_input")

    def test_sensitive_choice_labels_and_descriptions_are_not_returnable(self):
        for secret in (KEY, 'github_pat_' + 'a' * 24, 'AKIA' + 'A' * 16):
            for as_label in (True, False):
                data = request()
                data["questions"][1]["criteria"] = ({secret: "prohibited", "unknown": "abstain"}
                                                         if as_label else {"yes": secret, "unknown": "abstain"})
                self.assert_error(self.run_request(data, transport=lambda *_: self.fail("secret sent")), "sensitive_input")

    def test_choice_labels_reject_confusable_controls_prototype_names_and_oversize(self):
        for label in ('constructor', 'prototype', '__proto__', 'a\u202eb', 'a\u2066b', 'a' * 65, '😀' * 65):
            data = request()
            data["questions"][1]["criteria"][label] = "Disallowed label"
            self.assert_error(self.ask.prepare(encode(data)), "invalid_input")
        data = request()
        data["questions"][1]["criteria"]['😀' * 64] = "Valid label"
        self.assertEqual(self.ask.prepare(encode(data))["status"], "prepared")

    def test_strict_input_schema_rejects_extras_missing_fields_and_bad_types(self):
        mutations = [
            lambda d: d.update(extra=True), lambda d: d.update(schema_version=True),
            lambda d: d.update(context={"kind": "file", "text": "data"}),
            lambda d: d["context"].update(path="source.md"), lambda d: d["context"].update(text=" "),
            lambda d: d.update(questions=[]), lambda d: d["questions"].append(d["questions"][0]),
            lambda d: d["questions"][0].update(id="__proto__"), lambda d: d["questions"][0].update(type="text"),
            lambda d: d["questions"][0].update(instructions="\x00"),
            lambda d: d["questions"][0].update(criteria={"true": "yes"}),
            lambda d: d["questions"][0].update(abstain="no"),
            lambda d: d["questions"][1].pop("abstain"), lambda d: d["questions"][1].update(abstain="absent"),
            lambda d: d["questions"][1].update(criteria={"only": "one"}),
            lambda d: d["questions"][1]["criteria"].update({" bad ": "spaces"}),
            lambda d: d["questions"][2].update(criteria=["only"]),
            lambda d: d["questions"][2].update(criteria=["a", " "]),
            lambda d: d.update(min_confidence=True), lambda d: d.update(min_confidence=1.01),
        ]
        for i, mutate in enumerate(mutations):
            data = request()
            mutate(data)
            with self.subTest(mutation=i):
                self.assert_error(self.ask.prepare(encode(data)), "invalid_input")

    def test_native_maxima_and_wire_size_are_independently_bounded(self):
        data = request()
        data["questions"] = [{"id": f"q{i}", "type": "noul", "instructions": "True?"} for i in range(12)]
        self.assertEqual(self.ask.prepare(encode(data))["status"], "prepared")
        data["questions"].append({"id": "q12", "type": "noul", "instructions": "True?"})
        self.assert_error(self.ask.prepare(encode(data)), "invalid_input")
        data = request()
        data["questions"][1].update(criteria={f"c{i}": str(i) for i in range(255)}, abstain="c254")
        data["questions"][2]["criteria"] = [str(i) for i in range(10)]
        self.assertEqual(self.ask.prepare(encode(data))["status"], "prepared")
        too_many = copy.deepcopy(data)
        too_many["questions"][1]["criteria"]["c255"] = "extra"
        self.assert_error(self.ask.prepare(encode(too_many)), "invalid_input")
        data["questions"][2]["criteria"].append("extra")
        self.assert_error(self.ask.prepare(encode(data)), "invalid_input")
        self.assert_error(self.ask.prepare(b" " * 65537), "input_too_large")

    def test_wire_expansion_is_bounded_even_when_stdin_fits(self):
        data = request()
        data["context"]["text"] = "x" * (65536 - len(encode(data)) + len(data["context"]["text"]))
        raw = encode(data)
        self.assertEqual(len(raw), 65536)
        self.assert_error(self.ask.prepare(raw), "input_too_large")

    def test_invalid_timeouts_and_transport_cannot_reach_network(self):
        for timeout in (0, -1, 30.01, float("nan"), float("inf"), True, "10", 10**400):
            result = self.run_request(timeout=timeout, transport=lambda *_: self.fail("network reached"))
            self.assert_error(result, "invalid_input")
        self.assert_error(self.run_request(transport=42), "invalid_input")

    def test_actual_api_key_is_rejected_during_offline_preparation_too(self):
        data = request()
        data["questions"][0]["instructions"] = KEY
        self.assert_error(self.ask.prepare(encode(data)), "sensitive_input")

    def test_score_tolerates_documented_rounding_but_not_an_unrelated_number(self):
        reply = response()
        reply["answers"]["rating"].update(score=1.98)
        self.assertEqual(self.run_request(reply=reply)["status"], "reviewed")
        reply["answers"]["rating"].update(score=1.979)
        self.assert_error(self.run_request(reply=reply), "malformed_response", True)

    def test_request_hash_matches_exact_transmitted_bytes(self):
        import hashlib
        hashes = []
        def transport(raw, *_):
            hashes.append(hashlib.sha256(raw).hexdigest())
            return encode(response())
        result = self.run_request(transport=transport)
        self.assertEqual(hashes, [result["request_hash"]])

    def test_transport_cannot_change_the_expected_request_after_serialization(self):
        data = request()
        def transport(*_):
            data["questions"][2]["criteria"][0] = "Mutated after send"
            return encode(response())
        result = self.run_request(data, transport=transport)
        self.assertEqual(result["status"], "reviewed")
        self.assertEqual(result["results"][2]["legend"]["0"], "Hostile")

    def test_json_rejects_duplicate_keys_nonfinite_huge_numbers_and_invalid_utf8(self):
        cases = [b'{"schema_version":1,"schema_version":1}', b'\xff', b'{"a":NaN}',
                 b'{"a":1e9999}', b'{"a":' + b'9' * 5000 + b'}', b'[]', b'{"a":"\\ud800"}',
                 b'{"a":{"x":1,"x":2}}', b'[' * 1100 + b']' * 1100]
        for raw in cases:
            self.assert_error(self.ask.prepare(raw), "invalid_input")

    def test_strict_response_types_ids_numeric_bounds_and_native_fields(self):
        mutations = [
            lambda r: r.update(model="unasked"), lambda r: r["answers"].pop("rating"),
            lambda r: r.update(explanation=KEY),
            lambda r: r["answers"].update(extra={"type": "noul", "noul": 1}),
            lambda r: r["answers"]["arithmetic"].update(confidence=1),
            lambda r: r["answers"]["arithmetic"].update(noul=True),
            lambda r: r["answers"]["arithmetic"].update(noul=1.1),
            lambda r: r["answers"]["arithmetic"].update(type="score"),
            lambda r: r["answers"]["selection"].update(choice="unasked"),
            lambda r: r["answers"]["selection"].update(confidence=-1),
            lambda r: r["answers"]["selection"].update(probabilities={"two": 0.2, "unknown": 0.8}),
            lambda r: r["answers"]["selection"].update(probabilities={"two": 0.6, "unknown": 0.1}),
            lambda r: r["answers"]["rating"].update(score=1),
            lambda r: r["answers"]["rating"].update(score=True),
            lambda r: r["answers"]["rating"]["legend"].update({"2": "provider injected text"}),
            lambda r: r["answers"]["rating"].update(legend=["Hostile", "Neutral", "Friendly"]),
            lambda r: r["answers"]["rating"]["probabilities"].update({"3": 0}),
            lambda r: r["answers"]["rating"].update(explanation=KEY),
            lambda r: r["usage"].update(input_tokens=True),
        ]
        for i, mutate in enumerate(mutations):
            reply = response()
            mutate(reply)
            with self.subTest(mutation=i):
                self.assert_error(self.run_request(reply=reply), "malformed_response", True)
        for raw in (b'\xff', b'{"model":1,"model":2}', b" " * 65537, b'{"x":1e999}'):
            self.assert_error(self.run_request(transport=lambda *_: raw), "malformed_response", True)

    def test_nonfinite_boolean_or_huge_numbers_are_rejected_for_all_native_numeric_fields(self):
        for value in (float("nan"), float("inf"), True, "0.9", None, 10**400):
            for identifier, field in (("arithmetic", "noul"), ("selection", "confidence"), ("rating", "score")):
                reply = response()
                reply["answers"][identifier][field] = value
                self.assert_error(self.run_request(reply=reply), "malformed_response", True)

    def test_provider_error_details_never_escape_and_no_automatic_retry(self):
        for error, expected in ((TimeoutError(KEY), "timeout"), (RuntimeError(KEY), "transport"),
                                (self.ask.jev_client.JevError("authentication"), "authentication")):
            calls = []
            def transport(*_):
                calls.append(True)
                raise error
            result = self.run_request(transport=transport)
            self.assert_error(result, expected, True)
            self.assertEqual(len(calls), 1)

    def test_cli_only_stdin_json_no_file_reads_and_sanitized_argument_errors(self):
        # Windows Python 3.10 needs SystemRoot before the CLI can start. Keep only
        # runtime roots and the fake key, without inheriting unrelated secrets.
        env = {**{name: os.environ[name] for name in ("SystemRoot", "WINDIR") if name in os.environ},
               "TYPESAFE_API_KEY": KEY}
        for argv, raw, expected, code in ((["prepare", "--input", "-"], encode(request()), "prepared", 0),
                                           (["run", "--input", "-"], encode(request()), "unverified", 2),
                                           (["prepare", "--input", KEY], b"", "unverified", 2),
                                           (["prepare", "--input", "-", "--input", "-"], b"", "unverified", 2),
                                           (["prepare", "--bogus", KEY], b"", "unverified", 2)):
            completed = subprocess.run([sys.executable, str(SCRIPTS / "jev_ask.py"), *argv],
                                       input=raw, capture_output=True, timeout=10, env=env)
            self.assertEqual(completed.returncode, code, completed.stderr)
            self.assertEqual(completed.stderr, b"")
            result = json.loads(completed.stdout)
            self.assertEqual(result["status"], expected)
            self.assertNotIn(KEY.encode(), completed.stdout)


if __name__ == "__main__":
    unittest.main()
