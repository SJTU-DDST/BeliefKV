"""Advisory native capacity snapshots, separate from prefetch/admission grants."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
from queue import Empty, Full, Queue
import tempfile
from threading import Event, Thread
import time
from uuid import uuid4

from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from beliefkv.runtime.sglang_v0520_observer import observe_static_full_mamba_headroom


SCHEMA_VERSION = 1
SAMPLE_INTERVAL_MS = 500.
MAX_OBSERVATION_AGE_MS = 2000.
MAX_SNAPSHOT_BYTES = 8192
CAPACITY_FIELDS = (
    "request_rows_free", "request_slots_now", "running_requests", "waiting_requests",
    "full_free_tokens", "full_evictable_tokens", "next_batch_reserve_tokens",
    "full_bytes_per_token",
)


def native_capacity(runtime, waiting_queue) -> dict:
    """Read counters at the scheduler safe point; do not allocate or reclaim."""
    try:
        cache = runtime._native_cache
        headroom = observe_static_full_mamba_headroom(cache)
        units = runtime._residency_pool_bytes
        maximum, page = runtime._native_max_running, runtime._native_page_size
        input_reserve = runtime._native_input_reserve
        if (
            not headroom.observable or units is None or units[1] != 0
            or type(maximum) is not int or maximum <= 0
            or type(page) is not int or page <= 0
            or type(input_reserve) is not int or input_reserve < 0
            or type(units[0]) is not int or units[0] <= 0
        ):
            return {"supported": False, "reason": "full_only_native_budget_unavailable"}
        rows = cache.req_to_token_pool.available_size()
        row_capacity = cache.req_to_token_pool.size
        free = headroom.device_full_free_tokens
        evictable = cache.full_evictable_size()
        pool_capacity = cache.token_to_kv_pool_allocator.size
        running = len(getattr(runtime._native_running_batch, "reqs", ()))
        waiting = len(waiting_queue)
        values = (rows, row_capacity, free, evictable, pool_capacity, running, waiting)
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("invalid native counters")
        occupied = row_capacity - rows
        if not (
            rows <= row_capacity and running <= occupied
            and free + evictable <= pool_capacity
        ):
            raise ValueError("incoherent native counters")
        # Prefetch can use a rowless restore frontier. New requests cannot.
        slots = max(0, min(rows, maximum - occupied))
        return {
            "supported": True,
            "request_rows_free": rows, "request_slots_now": slots,
            "running_requests": running, "waiting_requests": waiting,
            "full_free_tokens": free, "full_evictable_tokens": evictable,
            "next_batch_reserve_tokens": input_reserve + page * (occupied + slots),
            "full_bytes_per_token": units[0],
            "service_cost_supported": False,
            "scope": "current_full_only_counters;not_admission_or_future_parallelism",
        }
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, RuntimeError):
        return {"supported": False, "reason": "native_counters_unavailable"}


class PlanningAdvicePublisher:
    """Keep only the latest snapshot; file serialization runs off scheduler."""

    def __init__(self, path, *, run_id: str, model_path: str):
        if not run_id or not model_path:
            raise ValueError("planning advice requires run and model identity")
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id, self.model_path = run_id, str(Path(model_path).resolve())
        self.server_id = uuid4().hex
        self.domain = local_monotonic_clock_domain()
        self._revision = 0
        self._next_ms = 0.
        self._queue: Queue = Queue(maxsize=1)
        self._stop = Event()
        self.last_error = None
        self.replaced = 0
        initial = self._snapshot({"supported": False, "reason": "starting"}, time.monotonic() * 1000.)
        with self.path.open("x", encoding="utf-8") as stream:
            json.dump(initial, stream, allow_nan=False)
        os.chmod(self.path, 0o600)
        self._thread = Thread(target=self._write, name="planning-advice-writer", daemon=True)
        self._thread.start()

    def _snapshot(self, capacity, now_ms):
        return {
            "schema_version": SCHEMA_VERSION, "run_id": self.run_id,
            "model_path": self.model_path, "server_id": self.server_id,
            "revision": self._revision, "clock_domain": self.domain,
            "observed_monotonic_ms": now_ms,
            "source": "native_scheduler_safe_point",
            "capacity": capacity,
        }

    def capture(self, runtime, waiting_queue, *, now_ms):
        if self._stop.is_set() or now_ms < self._next_ms:
            return
        self._next_ms = now_ms + SAMPLE_INTERVAL_MS
        self._revision += 1
        snapshot = self._snapshot(native_capacity(runtime, waiting_queue), now_ms)
        self._submit(snapshot)

    def _submit(self, snapshot):
        try:
            self._queue.put_nowait(snapshot)
        except Full:
            try:
                self._queue.get_nowait()
                self.replaced += 1
            except Empty:
                pass
            try:
                self._queue.put_nowait(snapshot)
            except Full:
                self.replaced += 1

    def _write(self):
        while not self._stop.is_set() or not self._queue.empty():
            try:
                snapshot = self._queue.get(timeout=.1)
            except Empty:
                continue
            temporary = None
            try:
                encoded = json.dumps(snapshot, allow_nan=False, separators=(",", ":")).encode()
                if len(encoded) > MAX_SNAPSHOT_BYTES:
                    raise ValueError("planning snapshot exceeds byte limit")
                with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(encoded)
                os.replace(temporary, self.path)
                temporary = None
            except (OSError, TypeError, ValueError) as error:
                self.last_error = f"{type(error).__name__}: {error}"
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def close(self):
        if self._stop.is_set():
            return
        self._revision += 1
        self._submit(self._snapshot(
            {"supported": False, "reason": "closed"}, time.monotonic() * 1000.,
        ))
        self._stop.set()
        self._thread.join(timeout=5.)
        if self._thread.is_alive():
            raise RuntimeError("planning advice writer did not exit")


@dataclass(frozen=True)
class AdviceRead:
    snapshot: dict | None
    status: str


class PlanningAdviceReader:
    def __init__(self, path, *, run_id, model_path):
        self.path = Path(path).expanduser().resolve()
        self.run_id, self.model_path = run_id, str(Path(model_path).resolve())
        self.domain = local_monotonic_clock_domain()
        self._server_id = None
        self._revision = -1

    def read(self, *, now_ms=None):
        now_ms = time.monotonic() * 1000. if now_ms is None else now_ms
        try:
            with self.path.open("rb") as stream:
                encoded = stream.read(MAX_SNAPSHOT_BYTES + 1)
            if len(encoded) > MAX_SNAPSHOT_BYTES:
                return AdviceRead(None, "oversized")
            snapshot = json.loads(encoded)
            if (
                snapshot["schema_version"] != SCHEMA_VERSION
                or snapshot["run_id"] != self.run_id
                or snapshot["model_path"] != self.model_path
                or self.domain is None or snapshot["clock_domain"] != self.domain
                or snapshot["source"] != "native_scheduler_safe_point"
            ):
                return AdviceRead(None, "identity_mismatch")
            observed, revision = snapshot["observed_monotonic_ms"], snapshot["revision"]
            if (
                type(observed) not in (int, float) or not math.isfinite(observed)
                or type(revision) is not int or revision < 0
                or not isinstance(snapshot["server_id"], str) or not snapshot["server_id"]
            ):
                return AdviceRead(None, "invalid")
            if not 0 <= now_ms - observed <= MAX_OBSERVATION_AGE_MS:
                return AdviceRead(None, "stale_or_future")
            if (
                self._server_id is not None and snapshot["server_id"] != self._server_id
                or revision < self._revision
            ):
                return AdviceRead(None, "server_or_revision_changed")
            capacity = snapshot["capacity"]
            if type(capacity["supported"]) is not bool:
                return AdviceRead(None, "invalid")
            if capacity["supported"] and (
                any(type(capacity.get(key)) is not int or capacity[key] < 0 for key in CAPACITY_FIELDS)
                or capacity["full_bytes_per_token"] <= 0
                or capacity["request_slots_now"] > capacity["request_rows_free"]
                or capacity.get("service_cost_supported") is not False
            ):
                return AdviceRead(None, "invalid")
            self._server_id, self._revision = snapshot["server_id"], revision
            if not capacity["supported"]:
                return AdviceRead(None, "unsupported")
            return AdviceRead(snapshot, "fresh")
        except (OSError, ValueError, TypeError, KeyError):
            return AdviceRead(None, "unavailable")
