"""Opt-in, read-only timing of sandbox command stdout without recording its body."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import os
import selectors
import subprocess
import time
from typing import Sequence


@dataclass(frozen=True)
class OutputTiming:
    output: str
    exit_code: int
    first_output_ms: float | None
    last_output_ms: float | None
    observed_bytes: int
    elapsed_ms: float
    timed_out: bool
    recent_output_chunks: tuple[tuple[float, int], ...]
    total_output_chunks: int
    recent_progress_events: tuple[tuple[float, str, int, int], ...] = ()
    total_progress_events: int = 0
    first_progress_stages: tuple[tuple[float, str, int, int], ...] = ()
    progress_collection_count: int = 0


class _ProgressFrames:
    PREFIX = b"\x1eBKVP:"
    SUFFIX = b"\x1f"

    def __init__(self) -> None:
        self.pending = bytearray()

    def feed(
        self, data: bytes, *, final: bool = False,
    ) -> tuple[bytes, list[tuple[str, int, int]]]:
        self.pending.extend(data)
        clean = bytearray()
        events = []
        while self.pending:
            start = self.pending.find(self.PREFIX)
            if start < 0:
                count = (len(self.pending) if final else
                         max(0, len(self.pending) - len(self.PREFIX) + 1))
                clean.extend(self.pending[:count])
                del self.pending[:count]
                break
            if start:
                clean.extend(self.pending[:start])
                del self.pending[:start]
                continue
            end = self.pending.find(self.SUFFIX, len(self.PREFIX))
            if end < 0:
                if not final and len(self.pending) <= 256:
                    break
                clean.append(self.pending[0])
                del self.pending[0]
                continue
            frame = bytes(self.pending[:end + 1])
            del self.pending[:end + 1]
            try:
                body = json.loads(frame[len(self.PREFIX):-1])
                phase = body["phase"]
                completed = body["completed"]
                total = body["total"]
                if (phase not in {"collection", "test_done", "session_finish"}
                    or type(completed) is not int or type(total) is not int
                    or not 0 <= completed <= total <= 1_000_000):
                    raise ValueError("invalid progress")
            except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                clean.extend(frame)
            else:
                events.append((phase, completed, total))
        return bytes(clean), events


def observe_output(
    argv: Sequence[str], *, timeout_s: float, test_progress_shadow: bool = False,
) -> OutputTiming:
    if timeout_s <= 0:
        raise ValueError("timeout must be positive")
    started = time.monotonic()
    deadline = started + timeout_s
    chunks: list[bytes] = []
    recent: deque[tuple[float, int]] = deque(maxlen=64)
    progress: deque[tuple[float, str, int, int]] = deque(maxlen=128)
    progress_count = 0
    first_stages: dict[str, tuple[float, str, int, int]] = {}
    collection_count = 0
    progress_filter = _ProgressFrames() if test_progress_shadow else None

    def record_progress(at_ms: float, phase: str, completed: int, total: int) -> None:
        nonlocal progress_count, collection_count
        event = (at_ms, phase, completed, total)
        progress.append(event)
        progress_count += 1
        if phase == "collection":
            collection_count += 1
            if total:
                first_stages.setdefault("collection", event)
        if (phase == "test_done" and 0 < completed < total
            and completed * 10 >= total * 9):
            first_stages.setdefault(
                "ninety_percent_before_last",
                (at_ms, "ninety_percent_before_last", completed, total),
            )
        if phase == "test_done" and total and completed == total:
            first_stages.setdefault(
                "all_tests_done", (at_ms, "all_tests_done", completed, total),
            )

    total_chunks = 0
    first = last = None
    timed_out = False
    with subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    ) as process:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in selector.select(remaining):
                    data = os.read(key.fd, 65536)
                    if data:
                        observed = time.monotonic()
                        if progress_filter is not None:
                            data, events = progress_filter.feed(data)
                            for phase, completed, total in events:
                                record_progress(
                                    (observed - started) * 1000.0,
                                    phase, completed, total,
                                )
                        if data:
                            first = observed if first is None else first
                            last = observed
                            chunks.append(data)
                            recent.append(((observed - started) * 1000.0, len(data)))
                            total_chunks += 1
                    else:
                        selector.unregister(key.fileobj)
        if timed_out:
            process.kill()
        exit_code = process.wait()
    elapsed_ms = (time.monotonic() - started) * 1000.0
    if progress_filter is not None:
        tail, events = progress_filter.feed(b"", final=True)
        if tail:
            chunks.append(tail)
            recent.append((elapsed_ms, len(tail)))
            total_chunks += 1
            first = first if first is not None else time.monotonic()
            last = time.monotonic()
        for phase, completed, total in events:
            record_progress(elapsed_ms, phase, completed, total)
    output = b"".join(chunks).decode("utf-8", errors="replace")
    if timed_out:
        output += f"\nCommand exceeded host timeout ({timeout_s:g}s)."
    return OutputTiming(
        output=output,
        exit_code=124 if timed_out else exit_code,
        first_output_ms=(first - started) * 1000.0 if first is not None else None,
        last_output_ms=(last - started) * 1000.0 if last is not None else None,
        observed_bytes=sum(map(len, chunks)),
        elapsed_ms=elapsed_ms,
        timed_out=timed_out,
        recent_output_chunks=tuple(recent),
        total_output_chunks=total_chunks,
        recent_progress_events=tuple(progress),
        total_progress_events=progress_count,
        first_progress_stages=tuple(first_stages.values()),
        progress_collection_count=collection_count,
    )
