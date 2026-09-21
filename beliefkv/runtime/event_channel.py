from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from queue import Full, Queue
from typing import Callable

from beliefkv.core.events import RuntimeEvent


SCHEMA_VERSION = 1
MAX_EVENTS_PER_MESSAGE = 64
MAX_DATAGRAM_BYTES = 60_000
MAX_UNIX_PATH_BYTES = 103


def _validate_socket_path(path: Path) -> None:
    if len(os.fsencode(path)) > MAX_UNIX_PATH_BYTES:
        raise ValueError(f"Unix socket path is too long: {path}")


@dataclass(frozen=True)
class RuntimeEventDelivery:
    message_id: str
    event_count: int
    accepted: bool
    duplicate: bool = False
    error: str = ""


class JsonlRuntimeEventSink:
    """Line-buffered authoritative event log without prompt or tool bodies."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("x", encoding="utf-8", buffering=1)
        self._sequence = 0
        self._lock = threading.Lock()

    def emit_batch(self, events: tuple[RuntimeEvent, ...]) -> None:
        with self._lock:
            for event in events:
                self._sequence += 1
                payload = {
                    "schema_version": SCHEMA_VERSION,
                    "sequence": self._sequence,
                    **event.to_dict(),
                }
                self._stream.write(
                    json.dumps(payload, sort_keys=True, allow_nan=False) + "\n"
                )

    def close(self) -> None:
        with self._lock:
            if not self._stream.closed:
                self._stream.close()

    def __enter__(self) -> "JsonlRuntimeEventSink":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class RuntimeEventDatagramServer:
    """Scheduler-polled, acknowledged local runtime-event receiver."""

    def __init__(
        self,
        path: str | Path,
        handler: Callable[[tuple[RuntimeEvent, ...]], object],
        *,
        ack_cache_size: int = 1024,
    ) -> None:
        if ack_cache_size <= 0:
            raise ValueError("ack_cache_size must be positive")
        self.path = Path(path).expanduser().resolve()
        _validate_socket_path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            raise FileExistsError(f"runtime event socket already exists: {self.path}")
        self.handler = handler
        self.ack_cache_size = ack_cache_size
        self._acks: OrderedDict[str, bytes] = OrderedDict()
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self._socket.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self._socket.setblocking(False)

    def drain(self, *, max_messages: int = 256) -> tuple[RuntimeEventDelivery, ...]:
        if max_messages <= 0:
            raise ValueError("max_messages must be positive")
        deliveries: list[RuntimeEventDelivery] = []
        for _ in range(max_messages):
            try:
                payload, reply_to = self._socket.recvfrom(MAX_DATAGRAM_BYTES)
            except BlockingIOError:
                break
            delivery, ack = self._handle(payload)
            deliveries.append(delivery)
            if reply_to:
                try:
                    self._socket.sendto(ack, reply_to)
                except OSError:
                    pass
        return tuple(deliveries)

    def fileno(self) -> int:
        """Expose the receive fd so SGLang's idle poller can wake on events."""

        return self._socket.fileno()

    def _handle(self, payload: bytes) -> tuple[RuntimeEventDelivery, bytes]:
        message_id = "unknown"
        try:
            raw = json.loads(payload)
            if not isinstance(raw, dict):
                raise ValueError("event message must be an object")
            if raw.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("unsupported event channel schema")
            message_id = str(raw["message_id"])
            if not message_id:
                raise ValueError("message_id must be non-empty")
            cached = self._acks.get(message_id)
            if cached is not None:
                self._acks.move_to_end(message_id)
                ack_payload = json.loads(cached)
                return (
                    RuntimeEventDelivery(
                        message_id=message_id,
                        event_count=int(ack_payload.get("event_count", 0)),
                        accepted=ack_payload.get("status") == "accepted",
                        duplicate=True,
                        error=str(ack_payload.get("error", "")),
                    ),
                    cached,
                )
            raw_events = raw.get("events")
            if not isinstance(raw_events, list) or not raw_events:
                raise ValueError("events must be a non-empty list")
            if len(raw_events) > MAX_EVENTS_PER_MESSAGE:
                raise ValueError("event batch exceeds channel limit")
            events = tuple(RuntimeEvent.from_dict(item) for item in raw_events)
            self.handler(events)
            delivery = RuntimeEventDelivery(message_id, len(events), True)
        except Exception as error:
            delivery = RuntimeEventDelivery(
                message_id=message_id,
                event_count=0,
                accepted=False,
                error=f"{type(error).__name__}: {error}",
            )
        ack = json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "message_id": delivery.message_id,
                "status": "accepted" if delivery.accepted else "rejected",
                "event_count": delivery.event_count,
                "error": delivery.error,
            },
            sort_keys=True,
        ).encode("utf-8")
        if message_id != "unknown":
            self._acks[message_id] = ack
            self._acks.move_to_end(message_id)
            while len(self._acks) > self.ack_cache_size:
                self._acks.popitem(last=False)
        return delivery, ack

    def close(self) -> None:
        self._socket.close()
        self.path.unlink(missing_ok=True)

    def __enter__(self) -> "RuntimeEventDatagramServer":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class UnixDatagramRuntimeEventSink:
    """Reliable local client using message ACKs and idempotent retries."""

    def __init__(
        self,
        server_path: str | Path,
        *,
        ack_timeout_s: float = 1.0,
        retries: int = 3,
        client_directory: str | Path | None = None,
    ) -> None:
        if ack_timeout_s <= 0:
            raise ValueError("ack_timeout_s must be positive")
        if retries <= 0:
            raise ValueError("retries must be positive")
        self.server_path = Path(server_path).expanduser().resolve()
        _validate_socket_path(self.server_path)
        directory = Path(client_directory or tempfile.gettempdir()).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        self.client_path = directory / f"bkv-{os.getpid()}-{uuid.uuid4().hex[:8]}.sock"
        _validate_socket_path(self.client_path)
        self.ack_timeout_s = ack_timeout_s
        self.retries = retries
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self._socket.bind(str(self.client_path))
        os.chmod(self.client_path, 0o600)
        self._socket.settimeout(self.ack_timeout_s)
        self._lock = threading.Lock()

    def emit_batch(self, events: tuple[RuntimeEvent, ...]) -> None:
        if not events:
            return
        if len(events) > MAX_EVENTS_PER_MESSAGE:
            raise ValueError("event batch exceeds channel limit")
        message_id = uuid.uuid4().hex
        payload = json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "message_id": message_id,
                "events": [event.to_dict() for event in events],
            },
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        if len(payload) > MAX_DATAGRAM_BYTES:
            raise ValueError("serialized event batch exceeds datagram limit")

        with self._lock:
            last_error: OSError | None = None
            for _ in range(self.retries):
                try:
                    self._socket.sendto(payload, str(self.server_path))
                except OSError as error:
                    last_error = error
                    continue
                try:
                    while True:
                        raw_ack = self._socket.recv(MAX_DATAGRAM_BYTES)
                        ack = json.loads(raw_ack)
                        if ack.get("message_id") == message_id:
                            break
                except socket.timeout:
                    continue
                if ack.get("status") != "accepted":
                    raise RuntimeError(
                        f"runtime event batch rejected: {ack.get('error', 'unknown')}"
                    )
                if int(ack.get("event_count", -1)) != len(events):
                    raise RuntimeError("runtime event ACK count mismatch")
                return
        if last_error is not None:
            raise ConnectionError(
                f"runtime event socket is unavailable after {self.retries} attempts: "
                f"{last_error}"
            ) from last_error
        raise TimeoutError(
            f"runtime event ACK timed out after {self.retries} attempts"
        )

    def close(self) -> None:
        self._socket.close()
        self.client_path.unlink(missing_ok=True)

    def __enter__(self) -> "UnixDatagramRuntimeEventSink":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


@dataclass
class _QueuedDelivery:
    events: tuple[RuntimeEvent, ...]
    submitted_ns: int
    tool_start: bool = False
    finished: threading.Event = field(default_factory=threading.Event)
    error: Exception | None = None

    def wait(self) -> None:
        self.finished.wait()
        if self.error is not None:
            raise self.error


class QueuedRuntimeEventSink:
    """Keep tool-start ACK waits off the agent thread without reordering events."""

    def __init__(
        self, sink: UnixDatagramRuntimeEventSink, *, max_pending: int = 64
    ) -> None:
        if max_pending <= 0:
            raise ValueError("max_pending must be positive")
        self._sink = sink
        self._queue: Queue[_QueuedDelivery | None] = Queue(maxsize=max_pending)
        self._lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._closed = False
        self._failure: Exception | None = None
        self._tool_queue_ms: list[float] = []
        self._tool_ack_ms: list[float] = []
        self._worker = threading.Thread(
            target=self._deliver, name="beliefkv-runtime-events", daemon=True
        )
        self._worker.start()

    def submit_batch(
        self, events: tuple[RuntimeEvent, ...], *, tool_start: bool = False
    ) -> _QueuedDelivery:
        if not events:
            raise ValueError("event batch must not be empty")
        delivery = _QueuedDelivery(events, time.perf_counter_ns(), tool_start)
        with self._lock:
            if self._closed:
                raise RuntimeError("runtime event sink is closed")
            if self._failure is not None:
                raise self._failure
            try:
                self._queue.put_nowait(delivery)
            except Full as error:
                raise RuntimeError("runtime event delivery queue is full") from error
        return delivery

    def emit_batch(self, events: tuple[RuntimeEvent, ...]) -> None:
        self.submit_batch(events).wait()

    def emit_tool_start(self, events: tuple[RuntimeEvent, ...]) -> None:
        self.submit_batch(events, tool_start=True)

    def _deliver(self) -> None:
        while (delivery := self._queue.get()) is not None:
            started_ns = time.perf_counter_ns()
            try:
                with self._lock:
                    failure = self._failure
                if failure is not None:
                    raise failure
                self._sink.emit_batch(delivery.events)
            except Exception as error:
                delivery.error = error
                with self._lock:
                    self._failure = error
            finally:
                finished_ns = time.perf_counter_ns()
                if delivery.tool_start:
                    with self._stats_lock:
                        self._tool_queue_ms.append(
                            (started_ns - delivery.submitted_ns) / 1_000_000.0
                        )
                        self._tool_ack_ms.append(
                            (finished_ns - started_ns) / 1_000_000.0
                        )
                delivery.finished.set()

    def timing_summary(self) -> dict[str, float | int | None]:
        def percentile(samples: list[float], fraction: float) -> float | None:
            if not samples:
                return None
            ordered = sorted(samples)
            return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]

        with self._stats_lock:
            return {
                "tool_start_count": len(self._tool_ack_ms),
                "queue_p50_ms": percentile(self._tool_queue_ms, 0.50),
                "queue_p95_ms": percentile(self._tool_queue_ms, 0.95),
                "ack_p50_ms": percentile(self._tool_ack_ms, 0.50),
                "ack_p95_ms": percentile(self._tool_ack_ms, 0.95),
            }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._queue.put(None)
        self._worker.join()
        self._sink.close()
