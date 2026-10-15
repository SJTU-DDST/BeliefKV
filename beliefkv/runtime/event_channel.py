from __future__ import annotations

import json
import os
import select
import socket
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Callable

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.runtime.hotpath_timing import HotpathTiming


SCHEMA_VERSION = 1
MAX_EVENTS_PER_MESSAGE = 64
MAX_DATAGRAM_BYTES = 60_000
MAX_UNIX_PATH_BYTES = 103
CONTROL_TIMING_SAMPLE_EVERY = 64


def _encode_event_message(
    message_id: str, batches: tuple[tuple[RuntimeEvent, ...], ...],
) -> bytes:
    message = {
        "schema_version": SCHEMA_VERSION,
        "message_id": message_id,
        "events": [event.to_dict() for batch in batches for event in batch],
    }
    if len(batches) > 1:
        message["batch_lengths"] = [len(batch) for batch in batches]
    return json.dumps(message, sort_keys=True, allow_nan=False).encode("utf-8")


_EMPTY_EVENT_MESSAGE_BYTES = len(_encode_event_message("0" * 32, ((),)))
_EMPTY_BATCH_LENGTHS_BYTES = len(json.dumps({"batch_lengths": []}).encode("utf-8"))


class _RuntimeEventBatchRejection(RuntimeError):
    def __init__(self, error: str, accepted_batch_count: int) -> None:
        super().__init__(f"runtime event batch rejected: {error}")
        self.accepted_batch_count = accepted_batch_count


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
        timing: HotpathTiming | None = None,
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
        self._timing = timing
        self._receive_polls = 0
        self._received_messages = 0
        self._acks: OrderedDict[str, bytes] = OrderedDict()
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self._socket.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self._socket.setblocking(False)
        # Probe empty drains cheaply; recent traffic keeps the direct receive path.
        self._receive_poller = select.poll()
        self._receive_poller.register(self._socket, select.POLLIN)
        self._receive_was_empty = True

    def drain(self, *, max_messages: int = 256) -> tuple[RuntimeEventDelivery, ...]:
        if max_messages <= 0:
            raise ValueError("max_messages must be positive")
        deliveries: list[RuntimeEventDelivery] = []
        timing = self._timing
        for index in range(max_messages):
            receive_frame = None
            if timing is not None and timing.enabled:
                if self._receive_polls % CONTROL_TIMING_SAMPLE_EVERY == 0:
                    receive_frame = timing.begin("control_socket_receive_sample")
                self._receive_polls += 1
            try:
                try:
                    if index == 0:
                        if self._receive_was_empty and not self._receive_poller.poll(0):
                            break
                        self._receive_was_empty = False
                    payload, reply_to = self._socket.recvfrom(MAX_DATAGRAM_BYTES)
                finally:
                    if receive_frame is not None:
                        timing.finish(receive_frame)
            except BlockingIOError:
                if index == 0:
                    self._receive_was_empty = True
                break
            profiled = False
            if timing is not None and timing.enabled:
                profiled = self._received_messages % CONTROL_TIMING_SAMPLE_EVERY == 0
                self._received_messages += 1
            delivery, ack = self._handle(payload, profiled=profiled)
            deliveries.append(delivery)
            if reply_to:
                try:
                    send_frame = (
                        timing.begin("control_socket_send_sample") if profiled else None
                    )
                    try:
                        self._socket.sendto(ack, reply_to)
                    finally:
                        if send_frame is not None:
                            timing.finish(send_frame)
                except OSError:
                    pass
        return tuple(deliveries)

    def fileno(self) -> int:
        """Expose the receive fd so SGLang's idle poller can wake on events."""

        return self._socket.fileno()

    def _handle(
        self, payload: bytes, *, profiled: bool = False,
    ) -> tuple[RuntimeEventDelivery, bytes]:
        message_id = "unknown"
        timing = self._timing
        accepted_batch_count: int | None = None
        accepted_event_count = 0
        try:
            decode_frame = timing.begin("control_json_decode_sample") if profiled else None
            try:
                raw = json.loads(payload)
            finally:
                if decode_frame is not None:
                    timing.finish(decode_frame)
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
            batch_lengths = raw.get("batch_lengths")
            if batch_lengths is None:
                batch_lengths = [len(raw_events)]
            else:
                if (
                    not isinstance(batch_lengths, list)
                    or len(batch_lengths) < 2
                    or any(type(length) is not int or length <= 0 for length in batch_lengths)
                    or sum(batch_lengths) != len(raw_events)
                ):
                    raise ValueError("invalid event batch lengths")
                accepted_batch_count = 0
            offset = 0
            for length in batch_lengths:
                event_frame = (
                    timing.begin("control_event_decode_sample") if profiled else None
                )
                try:
                    raw_batch = (
                        raw_events if length == len(raw_events)
                        else raw_events[offset:offset + length]
                    )
                    events = tuple(
                        RuntimeEvent.from_dict(item)
                        for item in raw_batch
                    )
                finally:
                    if event_frame is not None:
                        timing.finish(event_frame)
                self.handler(events)
                offset += length
                accepted_event_count += length
                if accepted_batch_count is not None:
                    accepted_batch_count += 1
            delivery = RuntimeEventDelivery(message_id, accepted_event_count, True)
        except Exception as error:
            delivery = RuntimeEventDelivery(
                message_id=message_id,
                event_count=accepted_event_count,
                accepted=False,
                error=f"{type(error).__name__}: {error}",
            )
        ack_payload = {
            "schema_version": SCHEMA_VERSION,
            "message_id": delivery.message_id,
            "status": "accepted" if delivery.accepted else "rejected",
            "event_count": delivery.event_count,
            "error": delivery.error,
        }
        if accepted_batch_count is not None:
            ack_payload["accepted_batch_count"] = accepted_batch_count
        ack_frame = timing.begin("control_ack_encode_sample") if profiled else None
        try:
            ack = json.dumps(ack_payload, sort_keys=True).encode("utf-8")
        finally:
            if ack_frame is not None:
                timing.finish(ack_frame)
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
        self.emit_batches((events,))

    def emit_batches(self, batches: tuple[tuple[RuntimeEvent, ...], ...]) -> None:
        if not batches:
            return
        if any(not batch for batch in batches):
            raise ValueError("event batch must not be empty")
        event_count = sum(len(batch) for batch in batches)
        if event_count > MAX_EVENTS_PER_MESSAGE:
            raise ValueError("event batch exceeds channel limit")
        message_id = uuid.uuid4().hex
        payload = _encode_event_message(message_id, batches)
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
                accepted_batches = int(ack.get(
                    "accepted_batch_count",
                    len(batches) if ack.get("status") == "accepted" else 0,
                ))
                if not 0 <= accepted_batches <= len(batches):
                    raise RuntimeError("runtime event ACK batch count mismatch")
                expected_count = sum(len(batch) for batch in batches[:accepted_batches])
                if int(ack.get("event_count", -1)) != expected_count:
                    raise RuntimeError("runtime event ACK count mismatch")
                if ack.get("status") != "accepted":
                    if len(batches) == 1:
                        raise RuntimeError(
                            f"runtime event batch rejected: {ack.get('error', 'unknown')}"
                        )
                    raise _RuntimeEventBatchRejection(
                        str(ack.get("error", "unknown")), accepted_batches,
                    )
                if accepted_batches != len(batches):
                    raise RuntimeError("runtime event ACK batch count mismatch")
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
    reentry: bool = False
    llm_result: bool = False
    finished: threading.Event = field(default_factory=threading.Event)
    error: Exception | None = None
    serialized_size: int | None = None

    def wait(self) -> None:
        self.finished.wait()
        if self.error is not None:
            raise self.error


class QueuedRuntimeEventSink:
    """Deliver ordered control events without blocking execution callbacks."""

    def __init__(
        self, sink: UnixDatagramRuntimeEventSink, *, max_pending: int = 64
    ) -> None:
        if max_pending <= 0:
            raise ValueError("max_pending must be positive")
        self._sink = sink
        self._emit_batches = getattr(sink, "emit_batches", None)
        self._queue: Queue[_QueuedDelivery | None] = Queue(maxsize=max_pending)
        self._lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._closed = False
        self._failure: Exception | None = None
        self._tool_queue_ms: list[float] = []
        self._tool_ack_ms: list[float] = []
        self._reentry_queue_ms: list[float] = []
        self._reentry_ack_ms: list[float] = []
        self._llm_result_queue_ms: list[float] = []
        self._llm_result_ack_ms: list[float] = []
        self._transport_message_count = 0
        self._coalesced_message_count = 0
        self._accepted_batch_count = 0
        self._worker = threading.Thread(
            target=self._deliver, name="beliefkv-runtime-events", daemon=True
        )
        self._worker.start()

    def submit_batch(
        self, events: tuple[RuntimeEvent, ...], *, tool_start: bool = False
    ) -> _QueuedDelivery:
        if not events:
            raise ValueError("event batch must not be empty")
        delivery = _QueuedDelivery(
            events, time.perf_counter_ns(), tool_start=tool_start,
            reentry=any(event.kind in {
                RuntimeEventKind.TOOL_END, RuntimeEventKind.RETURN,
                RuntimeEventKind.JOIN_SATISFIED, RuntimeEventKind.LLM_SUBMIT,
            } for event in events),
            llm_result=any(
                event.kind == RuntimeEventKind.LLM_RESULT for event in events
            ),
        )
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

    @staticmethod
    def _delivery_size(delivery: _QueuedDelivery) -> int:
        if delivery.serialized_size is None:
            delivery.serialized_size = len(
                _encode_event_message("0" * 32, (delivery.events,))
            )
        return delivery.serialized_size

    def _coalesce(
        self, first: _QueuedDelivery,
    ) -> tuple[list[_QueuedDelivery], _QueuedDelivery | None, bool]:
        deliveries = [first]
        event_count = len(first.events)
        body_bytes: int | None = None
        length_value_bytes = len(str(event_count))
        deferred = None
        stopping = False
        while event_count < MAX_EVENTS_PER_MESSAGE:
            try:
                following = self._queue.get_nowait()
            except Empty:
                break
            if following is None:
                self._queue.task_done()
                stopping = True
                break
            if event_count + len(following.events) > MAX_EVENTS_PER_MESSAGE:
                deferred = following
                break
            try:
                if body_bytes is None:
                    body_bytes = self._delivery_size(first) - _EMPTY_EVENT_MESSAGE_BYTES
                next_body_bytes = (
                    body_bytes + 2
                    + self._delivery_size(following) - _EMPTY_EVENT_MESSAGE_BYTES
                )
                next_length_bytes = length_value_bytes + len(str(len(following.events)))
                header_bytes = (
                    _EMPTY_BATCH_LENGTHS_BYTES + next_length_bytes + 2 * len(deliveries)
                )
            except Exception:
                # Keep a malformed delivery at its original failure boundary.
                deferred = following
                break
            if _EMPTY_EVENT_MESSAGE_BYTES + next_body_bytes + header_bytes > MAX_DATAGRAM_BYTES:
                deferred = following
                break
            deliveries.append(following)
            event_count += len(following.events)
            body_bytes = next_body_bytes
            length_value_bytes = next_length_bytes
        return deliveries, deferred, stopping

    def _deliver(self) -> None:
        deferred = None
        while True:
            delivery = deferred if deferred is not None else self._queue.get()
            deferred = None
            if delivery is None:
                self._queue.task_done()
                return
            with self._lock:
                failure = self._failure
            deliveries = [delivery]
            stopping = False
            if failure is None and self._emit_batches is not None:
                deliveries, deferred, stopping = self._coalesce(delivery)
            started_ns = time.perf_counter_ns()
            error = None
            accepted_count = 0
            try:
                if failure is not None:
                    raise failure
                with self._stats_lock:
                    self._transport_message_count += 1
                    self._coalesced_message_count += int(len(deliveries) > 1)
                if len(deliveries) > 1:
                    self._emit_batches(tuple(item.events for item in deliveries))
                else:
                    self._sink.emit_batch(delivery.events)
                accepted_count = len(deliveries)
            except Exception as caught:
                error = caught
                if isinstance(caught, _RuntimeEventBatchRejection):
                    accepted_count = caught.accepted_batch_count
                with self._lock:
                    self._failure = caught
            finally:
                finished_ns = time.perf_counter_ns()
                with self._stats_lock:
                    self._accepted_batch_count += accepted_count
                    for item in deliveries:
                        queue_ms = (started_ns - item.submitted_ns) / 1_000_000.0
                        ack_ms = (finished_ns - started_ns) / 1_000_000.0
                        if item.tool_start:
                            self._tool_queue_ms.append(queue_ms)
                            self._tool_ack_ms.append(ack_ms)
                        if item.reentry:
                            self._reentry_queue_ms.append(queue_ms)
                            self._reentry_ack_ms.append(ack_ms)
                        if item.llm_result:
                            self._llm_result_queue_ms.append(queue_ms)
                            self._llm_result_ack_ms.append(ack_ms)
                for index, item in enumerate(deliveries):
                    if index >= accepted_count:
                        item.error = error
                    item.finished.set()
                    self._queue.task_done()
            if stopping:
                return

    def flush(self) -> None:
        """Wait for accepted events at explicit shutdown/observation boundaries."""
        self._queue.join()
        with self._lock:
            if self._failure is not None:
                raise self._failure

    def timing_summary(self) -> dict[str, float | int | None]:
        def percentile(samples: list[float], fraction: float) -> float | None:
            if not samples:
                return None
            ordered = sorted(samples)
            return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]

        with self._stats_lock:
            return {
                "transport_message_count": self._transport_message_count,
                "coalesced_message_count": self._coalesced_message_count,
                "accepted_batch_count": self._accepted_batch_count,
                "tool_start_count": len(self._tool_ack_ms),
                "queue_p50_ms": percentile(self._tool_queue_ms, 0.50),
                "queue_p95_ms": percentile(self._tool_queue_ms, 0.95),
                "ack_p50_ms": percentile(self._tool_ack_ms, 0.50),
                "ack_p95_ms": percentile(self._tool_ack_ms, 0.95),
                "reentry_count": len(self._reentry_ack_ms),
                "reentry_queue_p50_ms": percentile(self._reentry_queue_ms, 0.50),
                "reentry_queue_p95_ms": percentile(self._reentry_queue_ms, 0.95),
                "reentry_ack_p50_ms": percentile(self._reentry_ack_ms, 0.50),
                "reentry_ack_p95_ms": percentile(self._reentry_ack_ms, 0.95),
                "llm_result_count": len(self._llm_result_ack_ms),
                "llm_result_queue_p50_ms": percentile(self._llm_result_queue_ms, 0.50),
                "llm_result_queue_p95_ms": percentile(self._llm_result_queue_ms, 0.95),
                "llm_result_ack_p50_ms": percentile(self._llm_result_ack_ms, 0.50),
                "llm_result_ack_p95_ms": percentile(self._llm_result_ack_ms, 0.95),
            }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._queue.put(None)
        self._worker.join()
        self._sink.close()
