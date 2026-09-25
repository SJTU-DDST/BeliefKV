"""Opt-in, read-only timing of sandbox command stdout without recording its body."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
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


def observe_output(argv: Sequence[str], *, timeout_s: float) -> OutputTiming:
    if timeout_s <= 0:
        raise ValueError("timeout must be positive")
    started = time.monotonic()
    deadline = started + timeout_s
    chunks: list[bytes] = []
    recent: deque[tuple[float, int]] = deque(maxlen=64)
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
    )
