from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass
from typing import Mapping

from beliefkv.oracle.contracts import LogicalInvocationKey


FROZEN_PHYSICAL_SIDECAR_SCHEMA_VERSION = 1


def _require_int(value: object, field: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field} must be an integer")
    if value < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


def _require_text(value: object, field: str) -> str:
    if type(value) is not str or not value:
        raise TypeError(f"{field} must be a non-empty string")
    return value


def _encode_symbols(values: tuple[int, ...]) -> str:
    if any(type(value) is not int or value < 0 or value >= 1 << 64 for value in values):
        raise ValueError("token symbols must be unsigned 64-bit integers")
    payload = struct.pack(f"<{len(values)}Q", *values) if values else b""
    return base64.b64encode(payload).decode("ascii")


def _decode_symbols(value: object, count: object, field: str) -> tuple[int, ...]:
    encoded = _require_text(value, field) if count else value
    token_count = _require_int(count, f"{field}_count")
    if token_count == 0:
        if encoded not in {"", None}:
            raise ValueError(f"{field} must be empty when count is zero")
        return ()
    if type(encoded) is not str:
        raise TypeError(f"{field} must be a base64 string")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except Exception as error:
        raise ValueError(f"{field} contains invalid base64") from error
    if len(payload) != token_count * 8:
        raise ValueError(f"{field} byte length does not match token count")
    return tuple(struct.unpack(f"<{token_count}Q", payload))


@dataclass(frozen=True)
class FrozenPhysicalCall:
    invocation: LogicalInvocationKey
    call_ordinal: int
    trace_request_ordinal: int
    runtime_context_epoch: int
    observed_cache_hit_tokens: int
    observed_unique_growth_bytes: int
    prompt_token_symbols: tuple[int, ...]
    cache_commit_token_symbols: tuple[int, ...]
    partial_cache_commit_token_symbols: tuple[tuple[int, ...], ...] = ()

    def __post_init__(self) -> None:
        for field in (
            "call_ordinal",
            "trace_request_ordinal",
            "runtime_context_epoch",
            "observed_cache_hit_tokens",
            "observed_unique_growth_bytes",
        ):
            _require_int(getattr(self, field), field)
        if self.observed_cache_hit_tokens > len(self.prompt_token_symbols):
            raise ValueError("observed cache hit exceeds prompt path")
        if self.cache_commit_token_symbols:
            shared = min(
                len(self.prompt_token_symbols), len(self.cache_commit_token_symbols)
            )
            if (
                self.prompt_token_symbols[:shared]
                != self.cache_commit_token_symbols[:shared]
            ):
                raise ValueError("cache commit path does not extend prompt path")
        if any(
            self.cache_commit_token_symbols[: len(path)] != path
            for path in self.partial_cache_commit_token_symbols
        ):
            raise ValueError("partial cache path does not extend to final path")
        for path in (
            self.prompt_token_symbols,
            self.cache_commit_token_symbols,
            *self.partial_cache_commit_token_symbols,
        ):
            _encode_symbols(path)

    def to_dict(self) -> dict[str, object]:
        return {
            "invocation": self.invocation.to_dict(),
            "call_ordinal": self.call_ordinal,
            "trace_request_ordinal": self.trace_request_ordinal,
            "runtime_context_epoch": self.runtime_context_epoch,
            "observed_cache_hit_tokens": self.observed_cache_hit_tokens,
            "observed_unique_growth_bytes": self.observed_unique_growth_bytes,
            "prompt_token_count": len(self.prompt_token_symbols),
            "prompt_token_symbols_b64": _encode_symbols(self.prompt_token_symbols),
            "cache_commit_token_count": len(self.cache_commit_token_symbols),
            "cache_commit_token_symbols_b64": _encode_symbols(
                self.cache_commit_token_symbols
            ),
            "partial_cache_commits": [
                {
                    "token_count": len(path),
                    "token_symbols_b64": _encode_symbols(path),
                }
                for path in self.partial_cache_commit_token_symbols
            ],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "FrozenPhysicalCall":
        required = {
            "invocation",
            "call_ordinal",
            "trace_request_ordinal",
            "runtime_context_epoch",
            "observed_cache_hit_tokens",
            "observed_unique_growth_bytes",
            "prompt_token_count",
            "prompt_token_symbols_b64",
            "cache_commit_token_count",
            "cache_commit_token_symbols_b64",
            "partial_cache_commits",
        }
        if raw.keys() != required:
            raise ValueError(
                "FrozenPhysicalCall fields mismatch: "
                f"missing={sorted(required - raw.keys())}, "
                f"unknown={sorted(raw.keys() - required)}"
            )
        invocation = raw["invocation"]
        partials = raw["partial_cache_commits"]
        if not isinstance(invocation, Mapping) or type(partials) is not list:
            raise TypeError("physical invocation must be an object and partials an array")
        decoded_partials = []
        for index, item in enumerate(partials):
            if not isinstance(item, Mapping) or item.keys() != {
                "token_count",
                "token_symbols_b64",
            }:
                raise ValueError(f"invalid partial cache record at index {index}")
            decoded_partials.append(
                _decode_symbols(
                    item["token_symbols_b64"],
                    item["token_count"],
                    f"partial_cache_commits[{index}]",
                )
            )
        return cls(
            invocation=LogicalInvocationKey.from_dict(invocation),
            call_ordinal=_require_int(raw["call_ordinal"], "call_ordinal"),
            trace_request_ordinal=_require_int(
                raw["trace_request_ordinal"], "trace_request_ordinal"
            ),
            runtime_context_epoch=_require_int(
                raw["runtime_context_epoch"], "runtime_context_epoch"
            ),
            observed_cache_hit_tokens=_require_int(
                raw["observed_cache_hit_tokens"], "observed_cache_hit_tokens"
            ),
            observed_unique_growth_bytes=_require_int(
                raw["observed_unique_growth_bytes"], "observed_unique_growth_bytes"
            ),
            prompt_token_symbols=_decode_symbols(
                raw["prompt_token_symbols_b64"],
                raw["prompt_token_count"],
                "prompt_token_symbols_b64",
            ),
            cache_commit_token_symbols=_decode_symbols(
                raw["cache_commit_token_symbols_b64"],
                raw["cache_commit_token_count"],
                "cache_commit_token_symbols_b64",
            ),
            partial_cache_commit_token_symbols=tuple(decoded_partials),
        )


@dataclass(frozen=True)
class FrozenPhysicalSidecar:
    truth_id: str
    truth_digest: str
    source_trace_id: str
    kv_bytes_per_token: int
    initial_radix_state: str
    calls: tuple[FrozenPhysicalCall, ...]
    schema_version: int = FROZEN_PHYSICAL_SIDECAR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_text(self.truth_id, "truth_id")
        _require_text(self.truth_digest, "truth_digest")
        _require_text(self.source_trace_id, "source_trace_id")
        _require_int(self.kv_bytes_per_token, "kv_bytes_per_token")
        if self.kv_bytes_per_token == 0:
            raise ValueError("kv_bytes_per_token must be positive")
        if self.initial_radix_state not in {
            "empty_server_boot",
            "explicit_cache_reset",
            "unknown",
        }:
            raise ValueError("invalid initial_radix_state")
        if self.schema_version != FROZEN_PHYSICAL_SIDECAR_SCHEMA_VERSION:
            raise ValueError("unsupported physical sidecar schema")
        ordered = tuple(
            sorted(self.calls, key=lambda item: (item.invocation, item.call_ordinal))
        )
        keys = {(item.invocation, item.call_ordinal) for item in ordered}
        ordinals = {item.trace_request_ordinal for item in ordered}
        if not ordered or len(keys) != len(ordered) or len(ordinals) != len(ordered):
            raise ValueError("physical sidecar calls and trace ordinals must be unique")
        object.__setattr__(self, "calls", ordered)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "truth_id": self.truth_id,
            "truth_digest": self.truth_digest,
            "source_trace_id": self.source_trace_id,
            "kv_bytes_per_token": self.kv_bytes_per_token,
            "initial_radix_state": self.initial_radix_state,
            "calls": [item.to_dict() for item in self.calls],
        }

    def canonical_bytes(self) -> bytes:
        return (
            json.dumps(
                self.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")

    @classmethod
    def from_json_bytes(cls, payload: bytes) -> "FrozenPhysicalSidecar":
        raw = json.loads(payload)
        if not isinstance(raw, Mapping):
            raise TypeError("physical sidecar must be a JSON object")
        required = {
            "schema_version",
            "truth_id",
            "truth_digest",
            "source_trace_id",
            "kv_bytes_per_token",
            "initial_radix_state",
            "calls",
        }
        if raw.keys() != required or type(raw["calls"]) is not list:
            raise ValueError("physical sidecar top-level fields mismatch")
        if any(not isinstance(item, Mapping) for item in raw["calls"]):
            raise TypeError("physical sidecar calls must contain only objects")
        return cls(
            schema_version=_require_int(raw["schema_version"], "schema_version"),
            truth_id=_require_text(raw["truth_id"], "truth_id"),
            truth_digest=_require_text(raw["truth_digest"], "truth_digest"),
            source_trace_id=_require_text(raw["source_trace_id"], "source_trace_id"),
            kv_bytes_per_token=_require_int(
                raw["kv_bytes_per_token"], "kv_bytes_per_token"
            ),
            initial_radix_state=_require_text(
                raw["initial_radix_state"], "initial_radix_state"
            ),
            calls=tuple(
                FrozenPhysicalCall.from_dict(item)
                for item in raw["calls"]
            ),
        )
