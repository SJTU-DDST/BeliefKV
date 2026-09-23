"""Opt-in, scheduler-local evidence for Qwen3.5 native reactive collection."""

from __future__ import annotations

import atexit
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Lock, Thread
import time
from typing import Any


class NativeReactiveTelemetry:
    def __init__(
        self, directory: str | Path, *, scheduler_path: str | Path | None = None
    ) -> None:
        self.directory = Path(directory).resolve()
        self.scheduler_path = (
            Path(scheduler_path).resolve() if scheduler_path is not None else None
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self._paths = {
            "events": self.directory / "runtime_events.sglang.jsonl",
            "audit": self.directory / "runtime_audit.jsonl",
            "transfer": self.directory / "transfer_telemetry.jsonl",
            "host_pool": self.directory / "host_pool_telemetry.jsonl",
        }
        if any(path.exists() for path in self._paths.values()):
            raise RuntimeError("native telemetry directory already contains evidence")
        self._queue: Queue[tuple[str, dict[str, Any]] | None] = Queue(maxsize=32768)
        self._pending: dict[str, float] = {}
        self._active: set[str] = set()
        self._completed: set[str] = set()
        self._reported_output_tokens: dict[str, int] = {}
        self._launched: dict[int, dict[str, Any]] = {}
        self._previous_completed_mono: float | None = None
        self._sequence = 0
        self._transfers = 0
        self._counts: Counter[str] = Counter()
        self._state_lock = Lock()
        self._host_pool_geometry: dict[str, dict[str, int]] = {}
        self._host_pool_high_water: dict[str, dict[str, int]] = {}
        self._host_evictions: Counter[str] = Counter()
        self._host_evicted_units: Counter[str] = Counter()
        self._host_evicted_bytes: Counter[str] = Counter()
        self._transfer_units: dict[str, Counter[str]] = {
            direction: Counter() for direction in ("d2h", "h2d")
        }
        self._cache_request_evidence = {
            "all": Counter(),
            "after_first_host_eviction": Counter(),
            "after_first_full_host_eviction": Counter(),
            "after_first_mamba_host_eviction": Counter(),
        }
        self._host_snapshot_interval_ms = 1000.0
        self._last_host_snapshot_ms = float("-inf")
        self._cache: object | None = None
        self._error: str | None = None
        self._closed = False
        self._writer = Thread(target=self._write, name="native-reactive-audit", daemon=True)
        self._writer.start()
        atexit.register(self.close)

    def record_capacity(self, cache: object) -> None:
        """Freeze the native shared FULL/MAMBA geometry before any workload."""
        from beliefkv.runtime.sglang_v0520_observer import (
            observe_static_full_mamba,
            observe_static_full_mamba_host_usage,
        )

        try:
            observation = observe_static_full_mamba(cache)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"native FULL/MAMBA capacity census failed: {exc}") from exc
        path = self.directory / "native_capacity_census.json"
        with path.open("x", encoding="utf-8") as output:
            json.dump({
                "schema_version": 1,
                "source": "native_sglang_v0520",
                "scheduler_pid": os.getpid(),
                "capacity": observation,
            }, output, indent=2, sort_keys=True)
            output.write("\n")
        host_usage = observe_static_full_mamba_host_usage(cache)
        if not host_usage.observable:
            raise RuntimeError(
                "native FULL/MAMBA Host pool geometry failed: "
                f"{host_usage.reason}"
            )
        entries = cache.host_pool_group.entry_map
        self._cache = cache
        self._host_pool_geometry = {
            "full": {
                "capacity_units": int(observation["host_full_tokens"]),
                "bytes_per_unit": int(entries["kv"].host_pool.size_per_token),
            },
            "mamba": {
                "capacity_units": int(observation["host_mamba_slots"]),
                "bytes_per_unit": int(entries["mamba"].host_pool.size_per_token),
            },
        }
        self._host_pool_high_water = {
            pool: {"used_units": 0, "used_bytes": 0}
            for pool in self._host_pool_geometry
        }
        cache.on_hicache_host_eviction = self.on_native_host_eviction

    def record_host_pool_usage(self, cache: object) -> None:
        """Persist at most one scheduler-safe-point Host-pool sample per second."""
        now_ms = time.time() * 1000.0
        if now_ms - self._last_host_snapshot_ms < self._host_snapshot_interval_ms:
            return
        from beliefkv.runtime.sglang_v0520_observer import (
            observe_static_full_mamba_host_usage,
        )

        observation = observe_static_full_mamba_host_usage(cache)
        if not observation.observable:
            self._emit("host_pool", {
                "event": "host_pool_sample_unavailable",
                "ts_ms": now_ms,
                "reason": observation.reason,
            })
            self._last_host_snapshot_ms = now_ms
            return
        used = {
            "full": (
                int(observation.host_full_used_tokens or 0),
                int(observation.host_full_used_bytes or 0),
            ),
            "mamba": (
                int(observation.host_mamba_used_slots or 0),
                int(observation.host_mamba_used_bytes or 0),
            ),
        }
        with self._state_lock:
            for pool, (units, byte_count) in used.items():
                high_water = self._host_pool_high_water[pool]
                high_water["used_units"] = max(high_water["used_units"], units)
                high_water["used_bytes"] = max(high_water["used_bytes"], byte_count)
            record = {
                "event": "host_pool_usage",
                "ts_ms": now_ms,
                "pools": {
                    pool: {
                        "capacity_units": self._host_pool_geometry[pool]["capacity_units"],
                        "used_units": units,
                        "used_bytes": byte_count,
                        "used_fraction": (
                            units / self._host_pool_geometry[pool]["capacity_units"]
                        ),
                        "high_water_units": self._host_pool_high_water[pool]["used_units"],
                        "high_water_bytes": self._host_pool_high_water[pool]["used_bytes"],
                    }
                    for pool, (units, byte_count) in used.items()
                },
                "sample_semantics": "scheduler_safe_point_sampled_high_water",
            }
        self._emit("host_pool", record)
        self._last_host_snapshot_ms = now_ms

    def on_native_host_eviction(self, component_type: Any, tracker: Any) -> None:
        """Record native per-component reclaimed units; never alter eviction."""
        names = {0: "full", 2: "mamba"}
        totals: dict[str, int] = {}
        for component, units in dict(tracker or {}).items():
            raw_value = getattr(component, "value", component)
            pool = names.get(raw_value)
            if pool is not None and int(units) > 0:
                totals[pool] = totals.get(pool, 0) + int(units)
        if not totals:
            return
        now_ms = time.time() * 1000.0
        with self._state_lock:
            for pool, units in totals.items():
                self._host_evictions[pool] += 1
                self._host_evicted_units[pool] += units
                self._host_evicted_bytes[pool] += (
                    units * self._host_pool_geometry.get(pool, {}).get(
                        "bytes_per_unit", 0
                    )
                )
        self._emit("host_pool", {
            "event": "host_pool_eviction",
            "ts_ms": now_ms,
            "trigger_component": str(component_type),
            "pools": {
                pool: {
                    "evicted_units": units,
                    "evicted_bytes": (
                        units * self._host_pool_geometry.get(pool, {}).get(
                            "bytes_per_unit", 0
                        )
                    ),
                }
                for pool, units in totals.items()
            },
        })

    @staticmethod
    def _identity(req: Any) -> dict[str, Any] | None:
        raw = getattr(req, "beliefkv_metadata", None)
        if not isinstance(raw, dict):
            return None
        fields = {
            "workflow_id": raw.get("root_workflow_id"),
            "invocation_id": raw.get("invocation_id"),
            "context_id": raw.get("context_id"),
            "context_epoch": raw.get("context_epoch"),
        }
        if (
            not all(fields[key] for key in ("workflow_id", "invocation_id", "context_id"))
            or type(fields["context_epoch"]) is not int
            or fields["context_epoch"] < 0
        ):
            return None
        return fields

    def _emit(self, stream: str, record: dict[str, Any]) -> None:
        if self._error is not None:
            raise RuntimeError(f"native telemetry writer failed: {self._error}")
        try:
            self._queue.put_nowait((stream, record))
        except Full as exc:
            self._error = "bounded telemetry queue overflow"
            raise RuntimeError(self._error) from exc

    def on_enqueue(self, req: Any) -> None:
        if self._identity(req) is not None and req.rid not in self._pending:
            self._pending[req.rid] = time.time() * 1000

    def on_abort_request(self, abort: Any) -> None:
        affected = [
            rid for rid in self._pending.keys() | self._active
            if abort.abort_all or rid.startswith(abort.rid)
        ]
        for rid in affected:
            stage = "started" if rid in self._active else "waiting"
            self._pending.pop(rid, None)
            self._active.discard(rid)
            self._completed.add(rid)
            self._emit("audit", {
                "event": "native_request_aborted",
                "ts_ms": time.time() * 1000,
                "request_id": rid,
                "stage": stage,
            })

    def on_launch(self, batch: Any) -> None:
        mode = batch.forward_mode
        if not (mode.is_extend() or mode.is_decode()):
            return
        launch = float(batch.launch_ts)
        phase = "prefill" if mode.is_extend() else "decode"
        samples = []
        for req in batch.reqs:
            identity = self._identity(req)
            if identity is None:
                continue
            rid = str(req.rid)
            if rid in self._completed:
                continue
            if rid not in self._active:
                submit_ts = self._pending.pop(rid, time.time() * 1000)
                prompt_tokens = len(req.origin_input_ids)
                cached_device = int(
                    getattr(req, "cached_tokens_device", 0) or 0
                )
                cached_host = int(getattr(req, "cached_tokens_host", 0) or 0)
                mamba_host_hits = int(
                    getattr(req, "mamba_host_hit_length", 0) or 0
                )
                cache_evidence = {
                    "request_count": 1,
                    "prompt_tokens": prompt_tokens,
                    "cached_tokens_device": cached_device,
                    "cached_tokens_host": cached_host,
                    "mamba_host_hit_slots": mamba_host_hits,
                    "requests_with_full_host_hit": int(cached_host > 0),
                    "requests_with_mamba_host_hit": int(mamba_host_hits > 0),
                    "uncached_prompt_tokens": max(
                        0, prompt_tokens - cached_device - cached_host
                    ),
                }
                with self._state_lock:
                    self._cache_request_evidence["all"].update(cache_evidence)
                    full_evicted = self._host_evictions["full"] > 0
                    mamba_evicted = self._host_evictions["mamba"] > 0
                    if full_evicted or mamba_evicted:
                        self._cache_request_evidence[
                            "after_first_host_eviction"
                        ].update(cache_evidence)
                    if full_evicted:
                        self._cache_request_evidence[
                            "after_first_full_host_eviction"
                        ].update(cache_evidence)
                    if mamba_evicted:
                        self._cache_request_evidence[
                            "after_first_mamba_host_eviction"
                        ].update(cache_evidence)
                self._emit("events", {
                    "kind": "llm_submit",
                    "ts_ms": submit_ts,
                    **identity,
                    "attributes": {
                        "request_id": rid,
                        "prompt_tokens": len(req.origin_input_ids),
                        "cached_tokens_device": int(
                            getattr(req, "cached_tokens_device", 0) or 0
                        ),
                        "cached_tokens_host": int(
                            getattr(req, "cached_tokens_host", 0) or 0
                        ),
                        "mamba_host_hit_slots": mamba_host_hits,
                        "cache_hit_tokens": (
                            int(getattr(req, "cached_tokens_device", 0) or 0)
                            + int(getattr(req, "cached_tokens_host", 0) or 0)
                        ),
                        "uncached_prompt_tokens": max(
                            0,
                            len(req.origin_input_ids)
                            - int(getattr(req, "cached_tokens_device", 0) or 0)
                            - int(getattr(req, "cached_tokens_host", 0) or 0),
                        ),
                        "context_tokens": len(req.origin_input_ids),
                        "expected_output_tokens": getattr(
                            req.sampling_params, "max_new_tokens", None
                        ),
                    },
                })
                self._active.add(rid)
            output_before = len(req.output_ids)
            self._reported_output_tokens.setdefault(rid, output_before)
            extend = max(0, int(getattr(req, "extend_input_len", 0) or 0))
            samples.append({
                "request_id": rid,
                **identity,
                "phase": phase,
                "sequence_tokens_before": (
                    len(req.origin_input_ids) + output_before - extend
                    if phase == "prefill"
                    else len(req.origin_input_ids) + output_before
                ),
                "output_tokens_before": output_before,
                "token_delta": extend if phase == "prefill" else 0,
                "token_delta_semantics": (
                    "prefill_extend_input_len" if phase == "prefill"
                    else "observed_output_ids_delta"
                ),
            })
        if samples:
            self._sequence += 1
            self._launched[batch.forward_iter] = {
                "sample_id": f"native-service-{self._sequence:09d}",
                "launch_mono": launch,
                "phase": phase,
                "batch_size": len(batch.reqs),
                "request_samples": samples,
            }
            if self._cache is not None:
                self.record_host_pool_usage(self._cache)

    def on_completed(self, batch: Any) -> None:
        descriptor = self._launched.pop(batch.forward_iter, None)
        if descriptor is None:
            return
        complete_mono = time.monotonic()
        complete_wall = time.time() * 1000
        start_mono = max(
            descriptor["launch_mono"],
            self._previous_completed_mono or descriptor["launch_mono"],
        )
        self._previous_completed_mono = complete_mono
        for sample in descriptor["request_samples"]:
            rid = sample["request_id"]
            req = next((item for item in batch.reqs if item.rid == rid), None)
            if req is None:
                continue
            if descriptor["phase"] == "decode":
                reported_before = self._reported_output_tokens.get(
                    rid, sample["output_tokens_before"]
                )
                output_after = len(req.output_ids)
                sample["token_delta"] = max(0, output_after - reported_before)
                self._reported_output_tokens[rid] = max(
                    reported_before, output_after
                )
            if req.finished() and rid in self._active:
                self._emit("events", {
                    "kind": "llm_result",
                    "ts_ms": complete_wall,
                    **{key: sample[key] for key in (
                        "workflow_id", "invocation_id", "context_id", "context_epoch"
                    )},
                    "attributes": {
                        "request_id": rid,
                        "output_tokens": len(req.output_ids),
                    },
                })
                self._active.discard(rid)
                self._completed.add(rid)
        self._emit("audit", {
            "event": "gpu_service_sample",
            "ts_ms": complete_wall,
            "sample_id": descriptor["sample_id"],
            "phase": descriptor["phase"],
            "batch_size": descriptor["batch_size"],
            "request_samples": descriptor["request_samples"],
            "service_start_ts_ms": complete_wall - (complete_mono - start_mono) * 1000,
            "complete_ts_ms": complete_wall,
            "service_elapsed_ms": (complete_mono - start_mono) * 1000,
            "timing_semantics_version": "gpu_service_interval_v1",
            "timing_boundary": "scheduler/worker interval, not CUDA kernel time",
        })
        if self._cache is not None:
            self.record_host_pool_usage(self._cache)

    def on_native_transfer_commit(self, event: Any) -> None:
        self._transfers += 1
        pool_units = dict(event.num_tokens_by_pool)
        direction = str(event.direction).lower()
        if direction in self._transfer_units:
            with self._state_lock:
                for pool, units in pool_units.items():
                    normalized = str(pool).lower()
                    if normalized in ("kv", "full"):
                        normalized = "full"
                    elif normalized == "mamba":
                        normalized = "mamba"
                    else:
                        continue
                    self._transfer_units[direction][normalized] += int(units)
        self._emit("transfer", {
            "event": "transfer_telemetry",
            "command_id": f"native-hicache-{self._transfers:09d}",
            "command_kind": "native_hicache_ack",
            "telemetry_origin": "native_hicache_ack_v0520",
            "direction": event.direction,
            "status": event.status,
            "node_ids": list(event.node_ids),
            "num_tokens_by_pool": pool_units,
            "actual_bytes": None,
            "start_ts_ms": None,
            "complete_ts_ms": time.time() * 1000,
            "start_timestamp_semantics": "unobserved",
            "host_copy_state": "unknown",
            "training_eligible_service_curve": False,
        })
        if direction in self._transfer_units:
            self._emit("host_pool", {
                "event": "host_pool_transfer_ack",
                "ts_ms": time.time() * 1000.0,
                "direction": direction,
                "pool_units": pool_units,
                "association_semantics": (
                    "aggregate_ack_units_after_any_host_eviction; "
                    "not exact node-level eviction-to-reload attribution"
                    if direction == "h2d"
                    else "aggregate_native_ack_units"
                ),
            })
            if self._cache is not None:
                self.record_host_pool_usage(self._cache)

    def _write(self) -> None:
        try:
            with (
                self._paths["events"].open("x", encoding="utf-8") as events,
                self._paths["audit"].open("x", encoding="utf-8") as audit,
                self._paths["transfer"].open("x", encoding="utf-8") as transfer,
                self._paths["host_pool"].open("x", encoding="utf-8") as host_pool,
            ):
                (self.directory / "native_telemetry_ready.json").write_text(
                    json.dumps({
                        "schema_version": 1,
                        "source": "native_sglang_v0520",
                        "scheduler_pid": os.getpid(),
                        "scheduler_path": (
                            str(self.scheduler_path) if self.scheduler_path else None
                        ),
                        "scheduler_sha256": (
                            hashlib.sha256(self.scheduler_path.read_bytes()).hexdigest()
                            if self.scheduler_path else None
                        ),
                    }) + "\n",
                    encoding="utf-8",
                )
                handles = {
                    "events": events, "audit": audit, "transfer": transfer,
                    "host_pool": host_pool,
                }
                while True:
                    try:
                        item = self._queue.get(timeout=0.5)
                    except Empty:
                        for handle in handles.values():
                            handle.flush()
                        self._write_status()
                        continue
                    if item is None:
                        break
                    stream, record = item
                    handles[stream].write(
                        json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n"
                    )
                    self._counts[stream] += 1
                for handle in handles.values():
                    handle.flush()
                self._write_status()
        except Exception as exc:
            self._error = repr(exc)

    def _write_status(self) -> None:
        with self._state_lock:
            pool_evidence = {
                pool: {
                    "capacity_units": self._host_pool_geometry.get(pool, {}).get(
                        "capacity_units"
                    ),
                    "bytes_per_unit": self._host_pool_geometry.get(pool, {}).get(
                        "bytes_per_unit"
                    ),
                    "high_water": dict(self._host_pool_high_water.get(pool, {})),
                    "eviction_calls": self._host_evictions[pool],
                    "evicted_units": self._host_evicted_units[pool],
                    "evicted_bytes": self._host_evicted_bytes[pool],
                    "d2h_ack_units": self._transfer_units["d2h"][pool],
                    "h2d_ack_units": self._transfer_units["h2d"][pool],
                    "h2d_units_after_first_host_eviction": (
                        self._transfer_units["h2d"][pool]
                        if self._host_evictions[pool] > 0
                        else 0
                    ),
                }
                for pool in ("full", "mamba")
            }
            request_cache_evidence = {
                key: dict(values)
                for key, values in self._cache_request_evidence.items()
            }
        status = {
            "schema_version": 1,
            "source": "native_sglang_v0520",
            "record_counts": dict(self._counts),
            "pending_request_count": len(self._pending) + len(self._active),
            "pending_batch_count": len(self._launched),
            "writer_error": self._error,
            "failed_records": 0 if self._error is None else 1,
            "dropped_records": 0 if self._error is None else None,
            "host_pool_evidence": pool_evidence,
            "request_cache_evidence": request_cache_evidence,
            "request_cache_evidence_semantics": {
                "full_host_hit": "cached_tokens_host input-token count",
                "mamba_host_hit": (
                    "mamba_host_hit_length checkpoint-slot count; not tokens"
                ),
                "after_eviction": (
                    "pool-level request hits and uncached prompt tokens after "
                    "the first eviction; eviction callback exposes aggregate "
                    "component counts, so this is not node-level identity matching"
                ),
            },
        }
        path = self.directory / "native_telemetry_status.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(path)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._writer.join()
        if self._error is not None:
            self._write_status()
