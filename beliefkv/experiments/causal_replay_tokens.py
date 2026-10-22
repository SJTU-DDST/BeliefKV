"""Bounded, exact token access for auxiliary causal GPU replay."""

from __future__ import annotations

from array import array
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from threading import Lock
from typing import Mapping

from beliefkv.runtime.replay_token_capture import decode_tokens, verify_capture


STOPPING_FIELDS = frozenset((
    "max_new_tokens", "min_new_tokens", "ignore_eos",
    "stop", "stop_regex", "stop_token_ids",
))


def _validate_stopping(record: dict) -> None:
    parameters = record.get("sampling_parameters")
    if type(parameters) is not dict or set(parameters) != STOPPING_FIELDS:
        raise ValueError("replay requires complete original stopping parameters")
    maximum, minimum = (
        parameters["max_new_tokens"], parameters["min_new_tokens"],
    )
    if (
        type(maximum) is not int or maximum < 0
        or type(minimum) is not int or not 0 <= minimum <= maximum
        or type(parameters["ignore_eos"]) is not bool
    ):
        raise ValueError("invalid original stopping parameters")
    for name in ("stop", "stop_regex", "stop_token_ids"):
        values = parameters[name]
        if values is None:
            continue
        if type(values) is not list or any(
            (type(value) is not int or not 0 <= value < 2 ** 32)
            if name == "stop_token_ids"
            else (type(value) is not str or not value)
            for value in values
        ):
            raise ValueError("invalid original stop condition")
    if record.get("event") == "request_output":
        reason = record.get("native_finish_reason")
        if type(reason) is not dict or reason.get("type") not in ("stop", "length"):
            raise ValueError("replay requires an original non-aborted finish reason")


@dataclass(frozen=True)
class CapturedReplayRequest:
    request_id: str
    identity: tuple[str, str, str, int]
    prompt_tokens: array
    output_tokens: array
    prompt_sha256: str
    output_sha256: str
    sampling_parameters: dict
    native_finish_reason: dict
    session_id: str | None
    session_generation: int | None
    extra_key: object
    cache_salt: object


@dataclass(frozen=True)
class _Location:
    offset: int
    size: int
    sha256: bytes


def _signature(stat: os.stat_result) -> tuple[int, ...]:
    return (
        stat.st_dev, stat.st_ino, stat.st_size,
        stat.st_mtime_ns, stat.st_ctime_ns,
    )


class CapturedTokenCorpus:
    """Index a completed source, then reconstruct requests in any ready order."""

    def __init__(
        self, path: Path, expected: Mapping[str, dict], *,
        cache_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        if type(cache_bytes) is not int or cache_bytes < 0:
            raise ValueError("token cache budget must be a non-negative integer")
        self.path = path.resolve()
        self._lock = Lock()
        self._file = self.path.open("rb")
        self._signature = _signature(os.fstat(self._file.fileno()))
        self._index: dict[str, dict[str, _Location]] = {}
        self._cache: OrderedDict[str, array] = OrderedDict()
        self._cache_bytes = 0
        self._cache_budget = cache_bytes
        digest = hashlib.sha256()

        def indexed_records():
            while True:
                offset = self._file.tell()
                raw = self._file.readline()
                if not raw:
                    return
                if not raw.endswith(b"\n"):
                    raise ValueError("token source has an incomplete JSONL record")
                digest.update(raw)
                record = json.loads(raw)
                _validate_stopping(record)
                request, event = record.get("request_id"), record.get("event")
                if type(request) is not str or not request:
                    raise ValueError("token source lacks its request identity")
                boundaries = self._index.setdefault(request, {})
                if event not in ("request_prompt", "request_output") or event in boundaries:
                    raise ValueError("duplicate or unknown token boundary")
                boundaries[event] = _Location(
                    offset, len(raw), hashlib.sha256(raw).digest(),
                )
                yield record

        try:
            self._evidence = verify_capture(indexed_records(), expected)
            self._check_unchanged()
        except BaseException:
            self._file.close()
            raise
        self._evidence.update(
            token_source_sha256=digest.hexdigest(),
            original_stopping_parameters_complete=True,
            original_finish_reasons_complete=True,
        )

    def _check_unchanged(self) -> None:
        if self._file.closed:
            raise ValueError("token corpus is closed")
        if (
            _signature(os.fstat(self._file.fileno())) != self._signature
            or _signature(self.path.stat()) != self._signature
        ):
            raise ValueError("completed token source changed after indexing")

    def _record(self, request: str, event: str) -> dict:
        location = self._index[request][event]
        raw = os.pread(self._file.fileno(), location.size, location.offset)
        if hashlib.sha256(raw).digest() != location.sha256:
            raise ValueError("indexed token record changed")
        return json.loads(raw)

    def _remember(self, request: str, tokens: array) -> None:
        size = len(tokens) * tokens.itemsize
        if size > self._cache_budget:
            return
        while self._cache and self._cache_bytes + size > self._cache_budget:
            _, old = self._cache.popitem(last=False)
            self._cache_bytes -= len(old) * old.itemsize
        self._cache[request] = tokens
        self._cache_bytes += size

    def _prompt(self, request: str) -> array:
        chain, tokens = [], None
        current = request
        # Recorded bases follow source completion order, not replay ready order.
        while current is not None:
            if current in self._cache:
                tokens = self._cache[current]
                self._cache.move_to_end(current)
                break
            record = self._record(current, "request_prompt")
            chain.append((current, record))
            current = record["base_request_id"]
        for current, record in reversed(chain):
            tokens = decode_tokens(record, base_tokens=tokens)
            self._remember(current, tokens)
        return tokens

    def get(self, request_id: str) -> CapturedReplayRequest:
        with self._lock:
            self._check_unchanged()
            if request_id not in self._index:
                raise ValueError("request is absent from the captured population")
            prompt = self._record(request_id, "request_prompt")
            output = self._record(request_id, "request_output")
            return CapturedReplayRequest(
                request_id=request_id,
                identity=tuple(prompt[name] for name in (
                    "workflow_id", "invocation_id", "context_id", "context_epoch",
                )),
                prompt_tokens=array("I", self._prompt(request_id)),
                output_tokens=decode_tokens(output),
                prompt_sha256=prompt["token_sha256"],
                output_sha256=output["token_sha256"],
                sampling_parameters=prompt["sampling_parameters"],
                native_finish_reason=output["native_finish_reason"],
                session_id=prompt.get("session_id"),
                session_generation=prompt.get("session_generation"),
                extra_key=prompt.get("extra_key"), cache_salt=prompt.get("cache_salt"),
            )

    @property
    def evidence(self) -> dict:
        return dict(self._evidence)

    @property
    def cache_size_bytes(self) -> int:
        with self._lock:
            return self._cache_bytes

    def close(self) -> None:
        with self._lock:
            self._cache.clear()
            self._cache_bytes = 0
            self._file.close()

    def __enter__(self) -> CapturedTokenCorpus:
        return self

    def __exit__(self, *_args) -> None:
        self.close()
