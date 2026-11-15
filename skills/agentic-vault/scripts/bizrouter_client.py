#!/usr/bin/env python3
"""Minimal standard-library client for opt-in BizRouter Decisions judgments.

Supports google/gemini-decision-1 and typesafe/jev-* over BizRouter's unified
fixed HTTPS endpoint without modifying jev_client.py (which is bound by warning
promotion certificates). Reuses jev_client.SENSITIVE and JevError so secret
blocking and content-free error codes remain identical across providers.
"""
from __future__ import annotations

import http.client
import json
import math
import re
import time

from jev_client import JevError, SENSITIVE, contains_sensitive

HOST = "api.bizrouter.ai"
PORT = 443
PATH = "/v1/decisions"
ENDPOINT = f"https://{HOST}{PATH}"
ENV_KEY = "BIZROUTER_API_KEY"
DEFAULT_MODEL = "google/gemini-decision-1"
MODELS = ("google/gemini-decision-1", "typesafe/jev-1.13.0", "typesafe/jev-latest")

MAX_PAYLOAD_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
MAX_INPUT_DEPTH = 64
MAX_INPUT_NODES = 65536
MAX_TIMEOUT = 30.0
DEFAULT_TIMEOUT = 15.0
SUM_TOLERANCE = 1e-5
SCORE_TOLERANCE = 0.01

ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def model_limits(model: str) -> tuple[int, int, int]:
    """Return (max_questions, max_choices, max_score_levels) for an allowlisted model."""
    if model == "google/gemini-decision-1":
        return 1000, 255, 255
    if model in ("typesafe/jev-1.13.0", "typesafe/jev-latest"):
        return 12, 255, 10
    raise JevError("invalid_input")


def _number(value, low=0.0, high=1.0) -> bool:
    return type(value) in (int, float) and low <= value <= high and math.isfinite(value)


def _encode(value, *, canonical=False) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=canonical,
                      separators=(",", ":")).encode("utf-8")


def _check_structure(value, depth=0, budget=None) -> int:
    if budget is None:
        budget = [0]
    budget[0] += 1
    if depth > MAX_INPUT_DEPTH or budget[0] > MAX_INPUT_NODES:
        raise JevError("input_too_large")
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise JevError("invalid_input")
            _check_structure(v, depth + 1, budget)
    elif isinstance(value, list):
        for item in value:
            _check_structure(item, depth + 1, budget)
    elif isinstance(value, float) and not math.isfinite(value):
        raise JevError("invalid_input")
    elif value is not None and type(value) not in (str, int, float, bool):
        raise JevError("invalid_input")
    return budget[0]


def _text(value: object, *, label: bool = False) -> None:
    if (not isinstance(value, str) or not value.strip()
            or any(ord(c) < 32 and c not in "\n\r\t" or ord(c) == 127 for c in value)):
        raise JevError("invalid_input")
    if label and (value != value.strip() or any(ord(c) < 32 for c in value)
                  or len(value) > 64 or value in {"constructor", "prototype", "__proto__"}
                  or any(0x202a <= ord(c) <= 0x202e or 0x2066 <= ord(c) <= 0x2069 for c in value)):
        raise JevError("invalid_input")


def build_payload(state, questions: dict, *, model: str = DEFAULT_MODEL, extra_secrets=()) -> bytes:
    """Validate and serialize a BizRouter /v1/decisions request body."""
    max_q, max_choices, max_levels = model_limits(model)
    _check_structure(state)
    if not isinstance(questions, dict) or not 1 <= len(questions) <= max_q:
        raise JevError("invalid_input")
    wire_questions = {}
    for qid, q in questions.items():
        if (not isinstance(qid, str) or not ID_PATTERN.fullmatch(qid)
                or qid in {"constructor", "prototype", "__proto__"} or not isinstance(q, dict)):
            raise JevError("invalid_input")
        kind = q.get("type")
        instructions = q.get("instructions")
        _text(instructions)
        if kind == "noul":
            if set(q) - {"type", "instructions", "criteria"}:
                raise JevError("invalid_input")
            item = {"type": "noul", "instructions": instructions}
            if "criteria" in q:
                crit = q["criteria"]
                if not isinstance(crit, dict) or set(crit) != {"true", "false"}:
                    raise JevError("invalid_input")
                for desc in crit.values():
                    _text(desc)
                item["criteria"] = dict(crit)
            wire_questions[qid] = item
        elif kind == "choice":
            if set(q) != {"type", "instructions", "criteria"}:
                raise JevError("invalid_input")
            crit = q["criteria"]
            if not isinstance(crit, dict) or not 2 <= len(crit) <= max_choices:
                raise JevError("invalid_input")
            for label, desc in crit.items():
                _text(label, label=True)
                _text(desc)
            wire_questions[qid] = {"type": "choice", "instructions": instructions, "criteria": dict(crit)}
        elif kind == "score":
            if set(q) != {"type", "instructions", "criteria"}:
                raise JevError("invalid_input")
            crit = q["criteria"]
            if not isinstance(crit, list) or not 2 <= len(crit) <= max_levels:
                raise JevError("invalid_input")
            for desc in crit:
                _text(desc)
            wire_questions[qid] = {"type": "score", "instructions": instructions, "criteria": list(crit)}
        else:
            raise JevError("invalid_input")

    body = {"model": model, "state": state, "questions": wire_questions}
    if contains_sensitive({"state": state, "questions": wire_questions}, extra_secrets):
        raise JevError("sensitive_input")
    try:
        raw = _encode(body)
    except (TypeError, ValueError, RecursionError, OverflowError):
        raise JevError("invalid_input") from None
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise JevError("input_too_large")
    return raw


def _decode_response(raw: bytes):
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
        raise JevError("malformed_response")

    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise JevError("malformed_response")
            result[k] = v
        return result

    def nonfinite(_):
        raise JevError("malformed_response")

    def parse_num(text):
        val = float(text)
        if not math.isfinite(val):
            raise JevError("malformed_response")
        return val

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=nonfinite, parse_float=parse_num)
        _encode(value)
        return value
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise JevError("malformed_response") from None


def validate_response(raw: bytes, questions: dict, *, model: str = DEFAULT_MODEL) -> dict:
    """Validate BizRouter /v1/decisions response bytes against submitted questions."""
    reply = _decode_response(raw)
    if not isinstance(reply, dict):
        raise JevError("malformed_response")
    allowed_top = {"id", "provider", "provider_request_id", "model", "answers", "usage",
                   "usage_source", "usage_estimate_reason"}
    if not {"model", "answers", "usage"} <= reply.keys() or reply.keys() - allowed_top:
        raise JevError("malformed_response")
    if reply["model"] != model or not isinstance(reply["answers"], dict):
        raise JevError("malformed_response")
    if set(reply["answers"]) != set(questions):
        raise JevError("malformed_response")

    answers = {}
    for qid, q in questions.items():
        kind = q["type"]
        ans = reply["answers"][qid]
        if not isinstance(ans, dict) or ans.get("type") != kind:
            raise JevError("malformed_response")
        if kind == "noul":
            if not {"type", "noul"} <= ans.keys() or ans.keys() - {"type", "noul", "confidence"}:
                raise JevError("malformed_response")
            if not _number(ans["noul"]):
                raise JevError("malformed_response")
            item = {"type": "noul", "noul": ans["noul"]}
            if "confidence" in ans:
                if not _number(ans["confidence"]):
                    raise JevError("malformed_response")
                item["confidence"] = ans["confidence"]
            answers[qid] = item
        elif kind == "choice":
            if set(ans) != {"type", "choice", "probabilities", "confidence"}:
                raise JevError("malformed_response")
            crit = q["criteria"]
            probs = ans["probabilities"]
            choice = ans["choice"]
            if (not _number(ans["confidence"]) or not isinstance(choice, str) or choice not in crit
                    or not isinstance(probs, dict) or set(probs) != set(crit)
                    or not all(_number(p) for p in probs.values())
                    or abs(math.fsum(probs.values()) - 1.0) > SUM_TOLERANCE
                    or any(p > probs[choice] + 1e-9 for p in probs.values())):
                raise JevError("malformed_response")
            answers[qid] = {
                "type": "choice",
                "choice": choice,
                "probabilities": {k: probs[k] for k in crit},
                "confidence": ans["confidence"],
            }
        elif kind == "score":
            if set(ans) != {"type", "score", "legend", "probabilities", "confidence"}:
                raise JevError("malformed_response")
            expected_legend = {str(i): d for i, d in enumerate(q["criteria"])}
            probs = ans["probabilities"]
            if (ans["legend"] != expected_legend or not _number(ans["confidence"])
                    or not isinstance(probs, dict) or set(probs) != set(expected_legend)
                    or not all(_number(p) for p in probs.values())
                    or abs(math.fsum(probs.values()) - 1.0) > SUM_TOLERANCE):
                raise JevError("malformed_response")
            expected_score = math.fsum(i * probs[str(i)] for i in range(len(expected_legend)))
            if (not _number(ans["score"], 0.0, len(expected_legend) - 1)
                    or abs(ans["score"] - expected_score) > SCORE_TOLERANCE + 1e-6):
                raise JevError("malformed_response")
            answers[qid] = {
                "type": "score",
                "score": ans["score"],
                "legend": expected_legend,
                "probabilities": {str(i): probs[str(i)] for i in range(len(expected_legend))},
                "confidence": ans["confidence"],
            }
        else:
            raise JevError("malformed_response")

    raw_usage = reply["usage"]
    if not isinstance(raw_usage, dict):
        raise JevError("malformed_response")
    allowed_usage = {"input_tokens", "output_tokens", "total_tokens", "cost", "input_tokens_by_modality"}
    if not {"input_tokens", "output_tokens"} <= raw_usage.keys() or raw_usage.keys() - allowed_usage:
        raise JevError("malformed_response")
    for tok_key in ("input_tokens", "output_tokens", "total_tokens"):
        if tok_key in raw_usage:
            val = raw_usage[tok_key]
            if type(val) is not int or not 0 <= val < 2**53:
                raise JevError("malformed_response")
    clean_usage = {
        "input_tokens": raw_usage["input_tokens"],
        "output_tokens": raw_usage["output_tokens"],
    }
    if "total_tokens" in raw_usage:
        clean_usage["total_tokens"] = raw_usage["total_tokens"]
    if "cost" in raw_usage:
        cost = raw_usage["cost"]
        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
            raise JevError("malformed_response")
        clean_usage["cost"] = cost
    if "input_tokens_by_modality" in raw_usage:
        mods = raw_usage["input_tokens_by_modality"]
        if not isinstance(mods, list):
            raise JevError("malformed_response")
        for entry in mods:
            if (not isinstance(entry, dict) or set(entry) != {"modality", "tokens"}
                    or not isinstance(entry["modality"], str)
                    or type(entry["tokens"]) is not int or entry["tokens"] < 0):
                raise JevError("malformed_response")
        clean_usage["input_tokens_by_modality"] = mods

    return {"model": model, "answers": answers, "usage": clean_usage}


def _remaining(deadline: float) -> float:
    rem = deadline - time.monotonic()
    if rem <= 0:
        raise JevError("timeout")
    return rem


def _send(payload: bytes, api_key: str, timeout: float) -> bytes:
    """Fixed-origin HTTPS POST to api.bizrouter.ai/v1/decisions; no redirects or retries."""
    deadline = time.monotonic() + timeout
    conn = http.client.HTTPSConnection(HOST, PORT, timeout=timeout)
    response = None
    try:
        conn.request("POST", PATH, body=payload, headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        sock = conn.sock
        rem = _remaining(deadline)
        if sock is not None:
            sock.settimeout(rem)
        response = conn.getresponse()
        if response.status != 200:
            if response.status in (401, 403):
                raise JevError("authentication")
            if response.status == 429:
                raise JevError("rate_limit")
            raise JevError("transport")
        chunks = []
        size = 0
        while True:
            rem = _remaining(deadline)
            if sock is not None:
                sock.settimeout(rem)
            chunk = response.read1(min(8192, MAX_RESPONSE_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise JevError("malformed_response")
            chunks.append(chunk)
        _remaining(deadline)
        return b"".join(chunks)
    finally:
        if response is not None:
            response.close()
        conn.close()


def request_judgments(state, questions: dict, *, api_key: str, model: str = DEFAULT_MODEL,
                      timeout: float = DEFAULT_TIMEOUT, transport=None) -> dict:
    """Execute one authorized BizRouter Decisions request and validate its reply."""
    if api_key is None or api_key == "":
        raise JevError("missing_api_key")
    if (not isinstance(api_key, str) or len(api_key) > 4096
            or any(not 33 <= ord(c) <= 126 for c in api_key)
            or type(timeout) not in (int, float) or not 0 < timeout <= MAX_TIMEOUT
            or not math.isfinite(timeout) or (transport is not None and not callable(transport))):
        raise JevError("invalid_input")
    payload = build_payload(state, questions, model=model, extra_secrets=(api_key,))
    escaped_key = json.dumps(api_key, ensure_ascii=False)[1:-1].encode("utf-8")
    if escaped_key in payload:
        raise JevError("sensitive_input")
    sent_questions = json.loads(payload.decode("utf-8"))["questions"]
    try:
        raw_reply = (_send if transport is None else transport)(payload, api_key, timeout)
    except JevError as err:
        raise JevError(err.code) from None
    except TimeoutError:
        raise JevError("timeout") from None
    except Exception:
        raise JevError("transport") from None
    return validate_response(raw_reply, sent_questions, model=model)
