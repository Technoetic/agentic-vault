"""Jev API boundary checks, using synthetic data and no live network calls."""
from __future__ import annotations

import importlib
import http.client
import io
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/agentic-vault/scripts"
KEY = "synthetic-test-key"


def questions():
    return {"claim_1": {
        "type": "choice",
        "instructions": "Assess this claim using only the provided evidence.",
        "criteria": {"supported": "Direct evidence", "unknown": "Insufficient evidence"},
    }}


def response():
    return {
        "model": "jev-1.13.0",
        "answers": {"claim_1": {
            "type": "choice", "choice": "supported",
            "probabilities": {"supported": 0.9, "unknown": 0.1}, "confidence": 0.85,
        }},
        "usage": {"input_tokens": 18, "output_tokens": 4},
    }


def encode(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class ClientTestBase:
    def setUp(self):
        self.assertTrue((SCRIPTS / "jev_client.py").is_file(), "Jev HTTP client is not implemented")
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        self.client = importlib.import_module("jev_client")

    def call(self, *, data=None, state=None, selected=None, **kwargs):
        kwargs.setdefault("api_key", KEY)
        kwargs.setdefault("transport", lambda *_: encode(response()) if data is None else data)
        return self.client.request_judgments(
            {"evidence": ["Public example"]} if state is None else state,
            questions() if selected is None else selected, **kwargs,
        )

    def assert_code(self, code, **kwargs):
        with self.assertRaises(self.client.JevError) as caught:
            self.call(**kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)
        return caught.exception


class JevClientTests(ClientTestBase, unittest.TestCase):

    def test_offline_payload_builder_matches_wire_bytes_and_rejects_invalid_text(self):
        builder = getattr(self.client, "build_payload", None)
        self.assertTrue(callable(builder), "Offline request builder is not exposed")
        expected = builder({"evidence": ["공개 근거"]}, questions())
        calls = []

        def transport(payload, *_):
            calls.append(payload)
            return encode(response())

        self.call(state={"evidence": ["공개 근거"]}, transport=transport)
        self.assertEqual(calls, [expected])
        self.assertEqual(json.loads(expected)["state"], {"evidence": ["공개 근거"]})
        for invalid in ("\x7f", "\x00", " "):
            selected = questions()
            selected["claim_1"]["instructions"] = invalid
            with self.subTest(invalid=repr(invalid)), self.assertRaises(self.client.JevError) as caught:
                builder({"evidence": ["Public evidence"]}, selected)
            self.assertEqual(caught.exception.code, "invalid_input")

    def test_valid_judgment_sends_only_api_payload_and_returns_validated_answer(self):
        calls = []

        def transport(payload, key, timeout):
            calls.append((json.loads(payload), key, timeout))
            return encode(response())

        result = self.client.request_judgments(
            {"evidence": ["선택한 공개 근거"]}, questions(), api_key=KEY, transport=transport,
        )
        self.assertEqual(result, response())
        self.assertEqual(calls, [({
            "state": {"evidence": ["선택한 공개 근거"]}, "model": "jev-1.13.0",
            "questions": {"claim_1": {
                "type": "choice",
                "instructions": "Assess this claim using only the provided evidence.",
                "criteria": {"supported": "Direct evidence", "unknown": "Insufficient evidence"},
            }},
        }, KEY, 10.0)])

    def test_unicode_choice_labels_survive_validation(self):
        selected = questions()
        selected["claim_1"]["criteria"] = {"긍정": "Positive", "판단불가": "Insufficient"}
        result = response()
        result["answers"]["claim_1"].update(
            choice="긍정", probabilities={"긍정": 0.9, "판단불가": 0.1},
        )
        self.assertEqual(self.call(data=encode(result), selected=selected), result)

    def test_multiple_questions_have_independent_choice_sets(self):
        selected = questions()
        selected["claim_2"] = {"type": "choice", "instructions": "Pick a color.",
                               "criteria": {"blue": "Blue", "unknown": "Insufficient"}}
        result = response()
        result["answers"]["claim_2"] = {
            "type": "choice", "choice": "blue", "probabilities": {"blue": 1, "unknown": 0},
            "confidence": 1,
        }
        self.assertEqual(self.call(data=encode(result), selected=selected), result)

    def test_rejects_malformed_credentials_without_network_or_output(self):
        cases = [(None, "missing_api_key"), ("", "missing_api_key")]
        cases.extend((value, "invalid_input") for value in (
            7, True, b"key", " leading", "trailing ", "bad\r\nheader", "내부키", "k" * 4097,
        ))
        output = io.StringIO()
        for key, code in cases:
            with self.subTest(key_type=type(key).__name__):
                with redirect_stdout(output), redirect_stderr(output):
                    self.assert_code(code, api_key=key, transport=lambda *_: self.fail("network called"))
        self.assertEqual(output.getvalue(), "")

    def test_rejects_credential_embedded_in_payload(self):
        self.assert_code("invalid_input", state={"evidence": [KEY]},
                         transport=lambda *_: self.fail("network called"))
        escaped_key = 'synthetic-quote-"-key'
        self.assert_code("invalid_input", state={"evidence": [escaped_key]}, api_key=escaped_key,
                         transport=lambda *_: self.fail("network called"))

    def test_rejects_invalid_timeout_and_transport_before_network(self):
        for index, timeout in enumerate((0, -1, 30.01, float("nan"), float("inf"), "10", True, None, 10**400)):
            with self.subTest(case=index):
                self.assert_code("invalid_input", timeout=timeout,
                                 transport=lambda *_: self.fail("network called"))
        self.assert_code("invalid_input", transport=42)

    def test_custom_timeout_is_forwarded_with_one_call(self):
        calls = []

        def transport(payload, key, timeout):
            calls.append(timeout)
            return encode(response())

        self.call(transport=transport, timeout=30)
        self.assertEqual(calls, [30])

    def test_rejects_invalid_questions_before_network(self):
        cases = [[], {}, {"bad id": questions()["claim_1"]}]
        cases.append({f"q{i}": questions()["claim_1"] for i in range(13)})
        for field, value in (("type", "text"), ("instructions", ""), ("instructions", "x" * 4001),
                             ("criteria", {"one": "Only one"}), ("extra", "untrusted")):
            selected = questions()
            selected["claim_1"][field] = value
            cases.append(selected)
        for label, definition in (("", "Empty"), (" space ", "Bad"), ("x" * 81, "Long"),
                                  ("valid", ""), ("valid", "x" * 2001)):
            selected = questions()
            selected["claim_1"]["criteria"][label] = definition
            cases.append(selected)
        for index, selected in enumerate(cases):
            with self.subTest(case=index):
                self.assert_code("invalid_input", selected=selected,
                                 transport=lambda *_: self.fail("network called"))

    def test_rejects_unserializable_nonfinite_and_oversized_request(self):
        for state in ([], {"x": object()}, {"x": float("nan")}, {"x": "\ud800"},
                      {"evidence": ["x" * 65536]}):
            with self.subTest(state_type=type(state).__name__):
                self.assert_code("invalid_input", state=state,
                                 transport=lambda *_: self.fail("network called"))

    def test_model_answer_ids_and_answer_fields_must_match_exactly(self):
        mutations = [
            lambda r: r.update(model="jev-next"),
            lambda r: r.pop("model"),
            lambda r: r["answers"].pop("claim_1"),
            lambda r: r["answers"].update(unasked=r["answers"]["claim_1"]),
            lambda r: r["answers"]["claim_1"].update(type="text"),
            lambda r: r["answers"]["claim_1"].pop("confidence"),
            lambda r: r["answers"]["claim_1"].update(reasoning=KEY),
            lambda r: r["answers"]["claim_1"].update(choice="unasked"),
            lambda r: r["answers"]["claim_1"].update(choice=["supported"]),
            lambda r: r["answers"]["claim_1"].update(probabilities={"supported": 1}),
            lambda r: r["answers"]["claim_1"]["probabilities"].update(unasked=0),
            lambda r: r.update(answers=[]),
            lambda r: r["answers"].update(claim_1=[]),
        ]
        for index, mutate in enumerate(mutations):
            result = response()
            mutate(result)
            with self.subTest(mutation=index):
                self.assert_code("malformed_response", data=encode(result))

    def test_probability_and_confidence_reject_nonfinite_boolean_out_of_range_values(self):
        for index, value in enumerate((float("nan"), float("inf"), -0.1, 1.1, True, "0.9", None, 10**400)):
            for field in ("confidence", "probabilities"):
                result = response()
                if field == "probabilities":
                    result["answers"]["claim_1"][field]["supported"] = value
                else:
                    result["answers"]["claim_1"][field] = value
                with self.subTest(case=index, field=field):
                    self.assert_code("malformed_response", data=encode(result))

    def test_response_options_are_bound_to_serialized_request_before_transport(self):
        selected = questions()

        def transport(*_):
            selected["claim_1"]["criteria"] = {"invented": "Changed after sending", "unknown": "Unknown"}
            result = response()
            result["answers"]["claim_1"].update(choice="invented", probabilities={"invented": 1, "unknown": 0})
            return encode(result)

        self.assert_code("malformed_response", selected=selected, transport=transport)

    def test_probability_sum_tolerance_and_choice_argmax(self):
        result = response()
        for distribution, choice, allowed in (
            ({"supported": 0.8, "unknown": 0.1}, "supported", False),
            ({"supported": 0.1, "unknown": 0.9}, "supported", False),
            ({"supported": 0.9000005, "unknown": 0.1}, "supported", True),
            ({"supported": 0.900002, "unknown": 0.1}, "supported", False),
            ({"supported": 0.5, "unknown": 0.5}, "unknown", True),
        ):
            result["answers"]["claim_1"].update(probabilities=distribution, choice=choice)
            with self.subTest(distribution=distribution, choice=choice):
                if allowed:
                    self.assertEqual(self.call(data=encode(result))["answers"], result["answers"])
                else:
                    self.assert_code("malformed_response", data=encode(result))

    def test_usage_requires_nonnegative_safe_integers(self):
        for value in (None, {}, {"input_tokens": True, "output_tokens": 1},
                      {"input_tokens": -1, "output_tokens": 1},
                      {"input_tokens": 1.5, "output_tokens": 1},
                      {"input_tokens": 2**53, "output_tokens": 1}):
            result = response()
            result["usage"] = value
            with self.subTest(usage=value):
                self.assert_code("malformed_response", data=encode(result))

    def test_unrecognized_envelope_metadata_is_not_returned(self):
        result = response()
        result["debug"] = KEY
        result["usage"]["debug"] = KEY
        self.assertEqual(self.call(data=encode(result)), response())

    def test_rejects_duplicate_keys_invalid_json_utf8_and_deep_nesting(self):
        valid = encode(response())
        cases = [b"not-json", b"\xff", b"[]", b"null", b"{}", b"[" * 1100 + b"]" * 1100,
                 valid.replace(b'"model":', b'"model":"jev-other","model":'),
                 valid.replace(b'"confidence":', b'"confidence":0.1,"confidence":'),
                 valid.replace(b'"supported": 0.9', b'"supported":1e999'),
                 valid.replace(b'"confidence": 0.85', b'"confidence":-Infinity')]
        for index, data in enumerate(cases):
            with self.subTest(case=index):
                self.assert_code("malformed_response", data=data)

    def test_rejects_oversized_and_nonbyte_transport_response(self):
        for data in (b" " * 65537, "{}", None, bytearray(b"{}")):
            with self.subTest(data_type=type(data).__name__):
                self.assert_code("malformed_response", transport=lambda *_, value=data: value)

    def test_errors_are_sanitized_and_transport_is_not_retried(self):
        for source, code in ((TimeoutError(KEY), "timeout"), (OSError(KEY), "transport"),
                             (RuntimeError(KEY), "transport")):
            calls = []

            def transport(*_):
                calls.append(1)
                raise source

            with self.subTest(error=type(source).__name__):
                error = self.assert_code(code, transport=transport)
                self.assertNotIn(KEY, repr(error))
                self.assertTrue(error.__suppress_context__)
                self.assertEqual(calls, [1])


class FakeResponse:
    def __init__(self, data, status=200):
        self.status = status
        self.body = io.BytesIO(data)
        self.read_sizes = []

    def read1(self, size):
        self.read_sizes.append(size)
        return self.body.read(size)

    def close(self):
        pass


class FakeConnection:
    def __init__(self, reply):
        self.reply = reply
        self.requests = []
        self.closed = False
        self.sock = mock.Mock()

    def request(self, method, url, body, headers):
        self.requests.append((method, url, body, headers))

    def getresponse(self):
        return self.reply

    def close(self):
        self.closed = True


class JevDefaultTransportTests(ClientTestBase, unittest.TestCase):
    # Keep network fakes at the HTTPS boundary: encoding, status handling,
    # bounded reads, parsing, and validation still execute production code.
    def default_call(self, reply):
        connection = FakeConnection(reply)
        with mock.patch.object(http.client, "HTTPSConnection", return_value=connection) as constructor:
            result = self.call(transport=None)
        return result, connection, constructor

    def test_default_transport_uses_fixed_https_endpoint_and_bearer_header(self):
        result, connection, constructor = self.default_call(FakeResponse(encode(response())))
        self.assertEqual(result, response())
        self.assertEqual(constructor.call_args.args, ("api.typesafe.ai",))
        self.assertEqual(constructor.call_args.kwargs["timeout"], 10.0)
        self.assertEqual(len(connection.requests), 1)
        method, url, body, headers = connection.requests[0]
        self.assertEqual((method, url), ("POST", "/v1/systemone"))
        self.assertEqual(headers["Authorization"], "Bearer " + KEY)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertNotIn(KEY.encode(), body)
        self.assertTrue(connection.closed)

    def test_http_errors_do_not_read_bodies_redirect_or_retry(self):
        for status, code in ((301, "transport"), (302, "transport"), (307, "transport"),
                             (308, "transport"), (401, "authentication"), (403, "authentication"),
                             (429, "rate_limit"), (500, "transport"), (204, "transport")):
            reply = FakeResponse(KEY.encode(), status)
            connection = FakeConnection(reply)
            with self.subTest(status=status), mock.patch.object(
                http.client, "HTTPSConnection", return_value=connection,
            ):
                self.assert_code(code, transport=None)
                self.assertEqual(len(connection.requests), 1)
                self.assertEqual(reply.read_sizes, [])
                self.assertTrue(connection.closed)

    def test_transport_reads_at_most_limit_plus_one_and_closes_connection(self):
        reply = FakeResponse(b" " * (1024 * 1024))
        connection = FakeConnection(reply)
        with mock.patch.object(http.client, "HTTPSConnection", return_value=connection):
            self.assert_code("malformed_response", transport=None)
        self.assertEqual(reply.body.tell(), 65537)
        self.assertTrue(all(0 < size <= 65537 for size in reply.read_sizes))
        self.assertTrue(connection.closed)

    def test_response_deadline_is_checked_between_short_reads(self):
        reply = FakeResponse(encode(response()))
        connection = FakeConnection(reply)
        with mock.patch.object(http.client, "HTTPSConnection", return_value=connection), mock.patch.object(
            self.client.time, "monotonic", side_effect=[0, 1, 2, 11],
        ):
            self.assert_code("timeout", transport=None)
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
