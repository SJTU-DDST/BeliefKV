"""Diagnostic timing of raw HTTP response iteration without retaining content."""

from __future__ import annotations

from collections.abc import Callable, Iterator
import time

import httpx


class TimedSyncByteStream(httpx.SyncByteStream):
    def __init__(
        self,
        stream: httpx.SyncByteStream,
        request_id: str,
        record: Callable[[dict], None],
    ) -> None:
        self._stream = stream
        self._request_id = request_id
        self._record = record
        self._headers_at_ms = time.monotonic() * 1000
        self._first_raw_at_ms: float | None = None
        self._last_raw_at_ms: float | None = None
        self._raw_chunks = 0
        self._raw_bytes = 0
        self._pull_ms = 0.0
        self._max_pull_ms = 0.0
        self._consumer_pause_ms = 0.0
        self._max_consumer_pause_ms = 0.0
        self._complete = False
        self._reported = False

    def __iter__(self) -> Iterator[bytes]:
        source = iter(self._stream)
        while True:
            pull_started = time.monotonic() * 1000
            if self._last_raw_at_ms is not None:
                pause = max(0.0, pull_started - self._last_raw_at_ms)
                self._consumer_pause_ms += pause
                self._max_consumer_pause_ms = max(self._max_consumer_pause_ms, pause)
            try:
                chunk = next(source)
            except StopIteration:
                self._complete = True
                self._report()
                return
            observed = time.monotonic() * 1000
            pull = max(0.0, observed - pull_started)
            self._pull_ms += pull
            self._max_pull_ms = max(self._max_pull_ms, pull)
            if self._first_raw_at_ms is None:
                self._first_raw_at_ms = observed
            self._last_raw_at_ms = observed
            self._raw_chunks += 1
            self._raw_bytes += len(chunk)
            yield chunk

    def close(self) -> None:
        try:
            self._stream.close()
        finally:
            self._report()

    def _report(self) -> None:
        if self._reported:
            return
        self._reported = True
        self._record({
            "event": "llm_stream_http_transport",
            "request_id": self._request_id,
            "response_headers_at_ms": self._headers_at_ms,
            "first_raw_at_ms": self._first_raw_at_ms,
            "last_raw_at_ms": self._last_raw_at_ms,
            "raw_chunks": self._raw_chunks,
            "raw_bytes": self._raw_bytes,
            "raw_pull_total_ms": round(self._pull_ms, 3),
            "raw_pull_max_ms": round(self._max_pull_ms, 3),
            "consumer_pause_total_ms": round(self._consumer_pause_ms, 3),
            "consumer_pause_max_ms": round(self._max_consumer_pause_ms, 3),
            "stream_consumed": self._complete,
        })
