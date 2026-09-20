"""Small standard-library client for explicitly requested Jev judgments."""
from __future__ import annotations

import http.client
import json
import math
import re
import time


MODEL = "jev-1.13.0"
ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MAX_BODY_BYTES = 64 * 1024
MAX_TIMEOUT = 30.0
_CODES = frozenset({"invalid_input", "missing_api_key", "authentication", "rate_limit",
                    "timeout", "transport", "malformed_response"})
_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


class JevError(Exception):
    """A non-sensitive operational error code."""

    def __init__(self, code):
        self.code = code if isinstance(code, str) and code in _CODES else "transport"
        super().__init__(self.code)


def _text(value, limit):
    return (isinstance(value, str) and 0 < len(value) <= limit and bool(value.strip())
            and not any(ord(char) < 32 and char not in "\n\r\t" or ord(char) == 127
                        for char in value))


def build_payload(state, questions):
    """Validate and encode the exact bounded wire request without network access."""
    if not isinstance(state, dict) or not isinstance(questions, dict) or not 1 <= len(questions) <= 12:
        raise JevError("invalid_input")
    for identifier, question in questions.items():
        if (not isinstance(identifier, str) or not _ID.fullmatch(identifier)
                or identifier in {"constructor", "prototype", "__proto__"}
                or not isinstance(question, dict)
                or set(question) != {"type", "instructions", "criteria"}
                or question["type"] != "choice" or not _text(question["instructions"], 4000)):
            raise JevError("invalid_input")
        criteria = question["criteria"]
        if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 12:
            raise JevError("invalid_input")
        for choice, definition in criteria.items():
            if (not _text(choice, 80) or choice != choice.strip()
                    or any(ord(char) < 32 for char in choice) or not _text(definition, 2000)):
                raise JevError("invalid_input")
    try:
        payload = json.dumps({"state": state, "model": MODEL, "questions": questions},
                             ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise JevError("invalid_input") from None
    if len(payload) > MAX_BODY_BYTES:
        raise JevError("invalid_input")
    return payload


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise JevError("malformed_response")
        result[key] = value
    return result


def _nonfinite(_value):
    raise JevError("malformed_response")


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise JevError("malformed_response")
    return result


def _probability(value):
    return type(value) in (int, float) and 0 <= value <= 1 and math.isfinite(value)


def _validate(data, questions):
    if not isinstance(data, bytes) or len(data) > MAX_BODY_BYTES:
        raise JevError("malformed_response")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_object,
                           parse_constant=_nonfinite, parse_float=_float)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise JevError("malformed_response") from None
    if (not isinstance(value, dict) or value.get("model") != MODEL
            or not isinstance(value.get("answers"), dict) or set(value["answers"]) != set(questions)):
        raise JevError("malformed_response")
    answers = {}
    for identifier, question in questions.items():
        answer = value["answers"][identifier]
        criteria = question["criteria"]
        if (not isinstance(answer, dict) or set(answer) != {"type", "choice", "probabilities", "confidence"}
                or answer["type"] != "choice" or not isinstance(answer["choice"], str)
                or answer["choice"] not in criteria or not isinstance(answer["probabilities"], dict)
                or set(answer["probabilities"]) != set(criteria) or not _probability(answer["confidence"])):
            raise JevError("malformed_response")
        probabilities = answer["probabilities"]
        if (not all(_probability(number) for number in probabilities.values())
                or abs(math.fsum(probabilities.values()) - 1) > 1e-6
                or any(number > probabilities[answer["choice"]] for number in probabilities.values())):
            raise JevError("malformed_response")
        answers[identifier] = {
            "type": "choice", "choice": answer["choice"],
            "probabilities": {choice: probabilities[choice] for choice in criteria},
            "confidence": answer["confidence"],
        }
    usage = value.get("usage")
    fields = ("input_tokens", "output_tokens")
    if (not isinstance(usage, dict)
            or not all(type(usage.get(field)) is int and 0 <= usage[field] < 2**53 for field in fields)):
        raise JevError("malformed_response")
    return {"model": MODEL, "answers": answers, "usage": {field: usage[field] for field in fields}}


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise JevError("timeout")
    return remaining


def _send(payload, api_key, timeout):
    """Use fixed-origin HTTPS; http.client does not follow redirects or retry."""
    deadline = time.monotonic() + timeout
    connection = http.client.HTTPSConnection("api.typesafe.ai", timeout=timeout)
    response = None
    try:
        connection.request("POST", "/v1/systemone", body=payload, headers={
            "Authorization": "Bearer " + api_key, "Content-Type": "application/json",
            "Accept": "application/json",
        })
        # Retain the socket: http.client may detach it on a Connection: close reply.
        sock = connection.sock
        remaining = _remaining(deadline)
        if sock is not None:
            sock.settimeout(remaining)
        response = connection.getresponse()
        if response.status != 200:
            if response.status in (401, 403):
                raise JevError("authentication")
            if response.status == 429:
                raise JevError("rate_limit")
            raise JevError("transport")
        chunks = []
        size = 0
        while True:
            remaining = _remaining(deadline)
            if sock is not None:
                sock.settimeout(remaining)
            chunk = response.read1(min(8192, MAX_BODY_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                raise JevError("malformed_response")
            chunks.append(chunk)
        _remaining(deadline)
        return b"".join(chunks)
    finally:
        if response is not None:
            response.close()
        connection.close()


def request_judgments(state, questions, *, api_key, timeout=10.0, transport=None):
    """Return validated API fields or a JevError containing only an allowlisted code.

    The caller owns authorization and source provenance. An injected transport
    receives (payload_bytes, api_key, timeout_seconds) and returns response bytes;
    it must honor the timeout and must not retry or redirect.
    """
    if api_key is None or api_key == "":
        raise JevError("missing_api_key")
    if (not isinstance(api_key, str) or len(api_key) > 4096
            or any(not 33 <= ord(char) <= 126 for char in api_key)
            or type(timeout) not in (int, float) or not 0 < timeout <= MAX_TIMEOUT
            or not math.isfinite(timeout) or transport is not None and not callable(transport)):
        raise JevError("invalid_input")
    payload = build_payload(state, questions)
    escaped_key = json.dumps(api_key, ensure_ascii=False)[1:-1].encode("utf-8")
    if escaped_key in payload:
        raise JevError("invalid_input")
    # Validate against the questions actually sent, even if the caller mutates
    # its original mapping while the network request is in flight.
    sent_questions = json.loads(payload)["questions"]
    try:
        data = (transport if transport is not None else _send)(payload, api_key, timeout)
    except JevError as error:
        raise JevError(error.code) from None
    except TimeoutError:
        raise JevError("timeout") from None
    except Exception:
        raise JevError("transport") from None
    return _validate(data, sent_questions)
