"""Exact token records for an optional, separately qualified trace replay."""

from __future__ import annotations

from array import array
import base64
import hashlib
import json
import sys
from typing import Iterable, Sequence


TOKEN_ENCODING = "uint32_le_base64"


def _wire(tokens: Sequence[int]) -> bytes:
    if not isinstance(tokens, array) and any(type(token) is not int for token in tokens):
        raise TypeError("token IDs must be integers")
    try:
        values = array("I", tokens)
    except (OverflowError, TypeError) as error:
        raise ValueError("token IDs must be unsigned 32-bit integers") from error
    if values.itemsize != 4:
        raise RuntimeError("token capture requires four-byte unsigned integers")
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def _tokens(encoded: str, count: int) -> array:
    if type(count) is not int or count < 0:
        raise ValueError("token count must be a non-negative integer")
    if type(encoded) is not str:
        raise TypeError("encoded tokens must be a string")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise ValueError("invalid token encoding") from error
    if len(payload) != count * 4:
        raise ValueError("encoded token count mismatch")
    values = array("I")
    values.frombytes(payload)
    if values.itemsize != 4:
        raise RuntimeError("token capture requires four-byte unsigned integers")
    if sys.byteorder != "little":
        values.byteswap()
    return values


def encode_tokens(
    tokens: Sequence[int], *, base_request_id: str | None = None,
    common_prefix_tokens: int = 0,
) -> dict:
    """Encode a caller-verified prefix reference and independently hashed full path."""
    if (
        type(common_prefix_tokens) is not int
        or not 0 <= common_prefix_tokens <= len(tokens)
        or (common_prefix_tokens > 0) != (
            type(base_request_id) is str and bool(base_request_id)
        )
        or common_prefix_tokens == 0 and base_request_id is not None
    ):
        raise ValueError("invalid token prefix reference")
    payload = _wire(tokens)
    return {
        "token_encoding": TOKEN_ENCODING, "token_count": len(tokens),
        "token_sha256": hashlib.sha256(payload).hexdigest(),
        "base_request_id": base_request_id,
        "common_prefix_tokens": common_prefix_tokens,
        "token_suffix_b64": base64.b64encode(
            payload[common_prefix_tokens * 4:],
        ).decode("ascii"),
    }


def decode_tokens(record: dict, *, base_tokens: Sequence[int] | None = None) -> array:
    """Reject a missing or altered base rather than padding a plausible token path."""
    if record.get("token_encoding") != TOKEN_ENCODING:
        raise ValueError("unsupported token encoding")
    count, common = record.get("token_count"), record.get("common_prefix_tokens")
    base = record.get("base_request_id")
    if (
        type(count) is not int or count < 0
        or type(common) is not int or not 0 <= common <= count
        or (common > 0) != (type(base) is str and bool(base))
        or common == 0 and base is not None
    ):
        raise ValueError("invalid token prefix reference")
    if common:
        if base_tokens is None or len(base_tokens) < common:
            raise ValueError("token prefix base is missing or too short")
        prefix = array("I", base_tokens[:common])
    else:
        prefix = array("I")
    prefix.extend(_tokens(record.get("token_suffix_b64"), count - common))
    if hashlib.sha256(_wire(prefix)).hexdigest() != record.get("token_sha256"):
        raise ValueError("reconstructed token identity mismatch")
    return prefix


def capture_record(
    record: dict, tokens: Sequence[int], *, event: str,
    base_request_id: str | None = None, common_prefix_tokens: int = 0,
) -> dict:
    if event not in ("request_prompt", "request_output"):
        raise ValueError("unsupported token capture event")
    return {
        "schema_version": 1, "event": event, "ts_ms": record["ts_ms"],
        **{name: record.get(name) for name in (
            "workflow_id", "invocation_id", "context_id", "context_epoch",
            "request_id", "session_id", "session_generation",
            "extra_key", "cache_salt",
        )},
        **encode_tokens(
            tokens, base_request_id=base_request_id,
            common_prefix_tokens=common_prefix_tokens,
        ),
        "max_new_tokens": (
            record.get("_replay_request_parameters") or {}
        ).get("max_new_tokens"),
        "availability": (
            "first_native_gpu_service" if event == "request_prompt"
            else "native_request_completion"
        ),
        "scope": "exact token capture only; not a qualified GPU replay or task score",
    }


def native_request_expectations(events: Iterable[dict]) -> dict[str, dict]:
    calls = {}
    for event in events:
        kind = event.get("kind")
        if kind not in ("llm_submit", "llm_result"):
            continue
        attrs = event.get("attributes") or {}
        request = attrs.get("request_id")
        if type(request) is not str or not request:
            raise ValueError("native token evidence lacks request identity")
        call = calls.setdefault(request, {})
        if kind in call:
            raise ValueError("duplicate native request boundary")
        count = attrs.get("prompt_tokens" if kind == "llm_submit" else "output_tokens")
        if type(count) is not int or count < 0:
            raise ValueError("native token usage is unavailable")
        identity = tuple(event.get(name) for name in (
            "workflow_id", "invocation_id", "context_id", "context_epoch",
        ))
        if (
            any(type(value) is not str or not value for value in identity[:3])
            or type(identity[3]) is not int or identity[3] < 0
        ):
            raise ValueError("native causal identity is unavailable")
        call[kind] = identity, count
    if not calls or any(
        set(call) != {"llm_submit", "llm_result"}
        or call["llm_submit"][0] != call["llm_result"][0] for call in calls.values()
    ):
        raise ValueError("native requests are incomplete or change identity")
    return calls


def _capture_scope(record: dict) -> tuple:
    return (
        record.get("workflow_id"), record.get("context_id"),
        record.get("session_id"), record.get("session_generation"),
        json.dumps(
            [record.get("extra_key"), record.get("cache_salt")],
            sort_keys=True, separators=(",", ":"), allow_nan=False,
        ),
    )


def verify_capture(records: Iterable[dict], expected: dict[str, dict]) -> dict:
    """Verify exact inputs and outputs without retaining every long request."""
    pending, served, bases = {}, {}, {}
    seen_prompt, seen_output = set(), set()
    prompt_tokens = output_tokens = encoded_prompt_tokens = 0
    for record in records:
        request, event = record.get("request_id"), record.get("event")
        if (
            type(record.get("schema_version")) is not int
            or record["schema_version"] != 1 or request not in expected
        ):
            raise ValueError("unknown request or token capture schema")
        if event not in ("request_prompt", "request_output"):
            raise ValueError("unknown token capture boundary")
        identity = tuple(record.get(name) for name in (
            "workflow_id", "invocation_id", "context_id", "context_epoch",
        ))
        native_kind = "llm_submit" if event == "request_prompt" else "llm_result"
        native_identity, native_count = expected[request][native_kind]
        if identity != native_identity:
            raise ValueError("captured token causal identity mismatch")
        scope = _capture_scope(record)
        if event == "request_prompt":
            if request in seen_prompt:
                raise ValueError("duplicate captured prompt")
            base_id = record.get("base_request_id")
            base = bases.get(base_id)
            if base_id is not None and (
                base is None or base[0] != scope or base[1] > identity[3]
            ):
                raise ValueError("captured prefix has an unavailable or foreign base")
            tokens = decode_tokens(record, base_tokens=base[2] if base is not None else None)
            if len(tokens) != native_count:
                raise ValueError("captured prompt length differs from native usage")
            budget = record.get("max_new_tokens")
            if type(budget) is not int or budget < 0:
                raise ValueError("original output budget is unavailable")
            pending[request] = scope, identity[3], tokens, budget
            prompt_tokens += len(tokens)
            encoded_prompt_tokens += len(tokens) - record["common_prefix_tokens"]
            seen_prompt.add(request)
        else:
            if request in seen_output or request not in pending:
                raise ValueError("captured output is duplicate or lacks its prompt")
            prompt_scope, epoch, tokens, budget = pending.pop(request)
            if (
                scope != prompt_scope or record.get("base_request_id") is not None
                or record.get("max_new_tokens") != budget
            ):
                raise ValueError("captured output changes scope or references a prompt")
            output = decode_tokens(record)
            if len(output) != native_count or len(output) > budget:
                raise ValueError("captured output differs from native usage or budget")
            prior = served.get(scope)
            if prior is not None:
                bases.pop(prior, None)
            served[scope] = request
            bases[request] = scope, epoch, tokens
            seen_output.add(request)
            output_tokens += len(output)
    if pending or seen_prompt != set(expected) or seen_output != set(expected):
        raise ValueError("captured request population is incomplete")
    return {
        "token_evidence_complete": True, "requests": len(expected),
        "prompt_tokens": prompt_tokens, "output_tokens": output_tokens,
        "encoded_prompt_suffix_tokens": encoded_prompt_tokens,
        "omitted_exact_prompt_prefix_tokens": prompt_tokens - encoded_prompt_tokens,
        "dependency_replay_qualified": False, "gpu_replay_qualified": False,
        "performance_accepted": False,
        "scope": (
            "Exact native prompt/output token identity and complete request population only. "
            "Client causal dependencies, EOS equivalence, replay execution and task quality "
            "require separate verification."
        ),
    }
