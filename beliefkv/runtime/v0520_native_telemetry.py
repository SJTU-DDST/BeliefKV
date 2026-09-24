"""Opt-in, scheduler-local evidence for Qwen3.5 native reactive collection."""

from __future__ import annotations

import atexit
from array import array
from collections import Counter, OrderedDict
import hashlib
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import secrets
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
            "eviction_attribution": self.directory / "eviction_attribution.jsonl",
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
        self._host_block_eviction_count = 0
        self._host_block_identity_available = False
        self._block_hash_key = secrets.token_bytes(32)
        self._next_block_eviction_id = 0
        self._pending_block_evictions: OrderedDict[
            tuple[str, str, int, str], dict[str, Any]
        ] = OrderedDict()
        self._block_eviction_index: dict[
            tuple[str, int, str], set[tuple[str, str, int, str]]
        ] = {}
        self._block_eviction_lengths: dict[str, OrderedDict[int, None]] = {}
        self._block_eviction_length_refs: Counter[tuple[str, int]] = Counter()
        self._block_eviction_index_limit = 32768
        self._max_prefix_lengths_per_probe = 1024
        self._block_probe_armed = False
        self._block_attribution_counts: Counter[str] = Counter()
        self._block_attribution_evicted_units: Counter[str] = Counter()
        self._block_attribution_reused_units: Counter[str] = Counter()
        self._block_attribution_recomputed_units: Counter[str] = Counter()
        self._block_attribution_probe_overflow = 0
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
        path = self.directory / "native_capacity_census.json"
        if path.exists():
            raise FileExistsError(path)
        from beliefkv.runtime.sglang_v0520_observer import (
            observe_static_full_mamba,
            observe_static_full_mamba_host_usage,
        )

        try:
            observation = observe_static_full_mamba(cache)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"native FULL/MAMBA capacity census failed: {exc}") from exc
        host_usage = observe_static_full_mamba_host_usage(cache)
        if not host_usage.observable:
            raise RuntimeError(
                "native FULL/MAMBA Host pool geometry failed: "
                f"{host_usage.reason}"
            )
        entries = cache.host_pool_group.entry_map
        host_pool_geometry = {
            "full": {
                "capacity_units": int(observation["host_full_tokens"]),
                "bytes_per_unit": int(entries["kv"].host_pool.size_per_token),
            },
            "mamba": {
                "capacity_units": int(observation["host_mamba_slots"]),
                "bytes_per_unit": int(entries["mamba"].host_pool.size_per_token),
            },
        }
        tree_core = getattr(cache, "tree_core", None)
        observer_setter = getattr(
            tree_core, "set_beliefkv_host_eviction_observer", None
        )
        if not callable(observer_setter):
            raise RuntimeError(
                "native block-level Host eviction attribution is unavailable "
                "for the selected TreeCore backend"
            )
        self._host_pool_geometry = host_pool_geometry
        self._host_pool_high_water = {
            pool: {"used_units": 0, "used_bytes": 0}
            for pool in self._host_pool_geometry
        }
        try:
            observer_setter(self.on_native_host_block_eviction)
        except Exception as exc:
            raise RuntimeError(
                f"native block-level Host eviction observer setup failed: {exc}"
            ) from exc
        with path.open("x", encoding="utf-8") as output:
            json.dump({
                "schema_version": 1,
                "source": "native_sglang_v0520",
                "scheduler_pid": os.getpid(),
                "capacity": observation,
            }, output, indent=2, sort_keys=True)
            output.write("\n")
        self._cache = cache
        cache.on_hicache_host_eviction = self.on_native_host_eviction
        self._host_block_identity_available = True

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
    def _namespace_bytes(extra_key: Any, cache_salt: Any) -> bytes:
        return json.dumps(
            [extra_key, cache_salt],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def _namespace_digest(self, extra_key: Any, cache_salt: Any) -> str:
        return hashlib.blake2b(
            self._namespace_bytes(extra_key, cache_salt),
            key=self._block_hash_key,
            digest_size=16,
            person=b"beliefkv-ns-v1",
        ).hexdigest()

    @staticmethod
    def _node_key_path(node: Any) -> tuple[Any, ...] | None:
        path = []
        current = node
        for _ in range(4096):
            if current is None or getattr(current, "parent", None) is None:
                break
            key = getattr(current, "key", None)
            if key is None:
                return None
            path.append(key)
            current = current.parent
        else:
            return None
        if current is None or not path:
            return None
        path.reverse()
        return tuple(path)

    @staticmethod
    def _key_path_tokens(path: tuple[Any, ...]) -> array | None:
        token_ids = array("q")
        for index, key in enumerate(path):
            raw_ids = getattr(key, "raw_token_ids", None)
            raw_ids = (
                raw_ids()
                if callable(raw_ids)
                else getattr(key, "token_ids", ())
            )
            if index and bool(getattr(key, "is_bigram", False)):
                raw_ids = raw_ids[1:]
            token_ids.extend(raw_ids)
        return token_ids or None

    def _prefix_digests(
        self,
        extra_key: Any,
        cache_salt: Any,
        token_ids: array,
        prefix_lengths: list[int],
    ) -> dict[int, str]:
        hasher = hashlib.blake2b(
            key=self._block_hash_key,
            digest_size=16,
            person=b"beliefkv-kv-v1",
        )
        hasher.update(self._namespace_bytes(extra_key, cache_salt))
        token_bytes = memoryview(token_ids).cast("B")
        previous_length = 0
        result: dict[int, str] = {}
        for prefix_length in prefix_lengths:
            if prefix_length <= previous_length or prefix_length > len(token_ids):
                continue
            hasher.update(
                token_bytes[
                    previous_length * token_ids.itemsize:
                    prefix_length * token_ids.itemsize
                ]
            )
            result[prefix_length] = hasher.copy().hexdigest()
            previous_length = prefix_length
        return result

    def on_native_host_block_eviction(
        self, node: Any, component_type: Any, units: int
    ) -> None:
        """Queue an immutable radix-key path for asynchronous matching."""
        names = {0: "full", 2: "mamba"}
        raw_component = getattr(component_type, "value", component_type)
        pool = names.get(raw_component)
        if pool is None or int(units) <= 0:
            return
        self._block_probe_armed = True
        path = self._node_key_path(node)
        if path is None:
            self._emit("eviction_attribution", {
                "event": "host_block_identity_unavailable",
                "ts_ms": time.time() * 1000.0,
                "pool": pool,
                "node_id": getattr(node, "id", None),
                "evicted_units": int(units),
                "reason": "node_path_identity_unavailable",
            })
            return
        self._emit("eviction_attribution", {
            "_internal_event": "host_block_eviction",
            "ts_ms": time.time() * 1000.0,
            "pool": pool,
            "node_id": int(node.id),
            "units": int(units),
            "extra_key": getattr(path[-1], "extra_key", None),
            "cache_salt": getattr(path[-1], "cache_salt", None),
            "key_path": path,
        })

    def _queue_block_reaccess_probe(
        self, req: Any, identity: dict[str, Any]
    ) -> None:
        if not self._block_probe_armed:
            return
        self._emit("eviction_attribution", {
            "_internal_event": "request_probe",
            "ts_ms": time.time() * 1000.0,
            **identity,
            "request_id": str(req.rid),
            "input_ids": req.origin_input_ids,
            "extra_key": getattr(req, "extra_key", None),
            "cache_salt": getattr(req, "cache_salt", None),
            "cached_tokens_device": int(
                getattr(req, "cached_tokens_device", 0) or 0
            ),
            "cached_tokens_host": int(
                getattr(req, "cached_tokens_host", 0) or 0
            ),
            "mamba_host_hit_slots": int(
                getattr(req, "mamba_host_hit_length", 0) or 0
            ),
        })

    def _remove_block_eviction_index(
        self, identity: tuple[str, str, int, str]
    ) -> None:
        _pool, namespace_digest, prefix_tokens, prefix_digest = identity
        index_key = (namespace_digest, prefix_tokens, prefix_digest)
        indexed = self._block_eviction_index.get(index_key)
        if indexed is not None:
            indexed.discard(identity)
            if not indexed:
                self._block_eviction_index.pop(index_key, None)
                length_key = (namespace_digest, prefix_tokens)
                self._block_eviction_length_refs[length_key] -= 1
                if self._block_eviction_length_refs[length_key] <= 0:
                    self._block_eviction_length_refs.pop(length_key, None)
                    lengths = self._block_eviction_lengths.get(namespace_digest)
                    if lengths is not None:
                        lengths.pop(prefix_tokens, None)
                        if not lengths:
                            self._block_eviction_lengths.pop(
                                namespace_digest, None
                            )

    def _write_block_eviction(self, record: dict[str, Any], output: Any) -> None:
        token_ids = self._key_path_tokens(record["key_path"])
        if token_ids is None:
            self._block_attribution_counts["identity_unavailable"] += 1
            output.write(json.dumps({
                "event": "host_block_identity_unavailable",
                "ts_ms": record["ts_ms"],
                "pool": record["pool"],
                "node_id": record["node_id"],
                "evicted_units": record["units"],
                "reason": "empty_or_unreadable_key_path",
            }, separators=(",", ":")) + "\n")
            return
        prefix_tokens = len(token_ids)
        namespace_digest = self._namespace_digest(
            record["extra_key"], record["cache_salt"]
        )
        prefix_digest = self._prefix_digests(
            record["extra_key"],
            record["cache_salt"],
            token_ids,
            [prefix_tokens],
        )[prefix_tokens]
        identity = (
            str(record["pool"]),
            namespace_digest,
            prefix_tokens,
            prefix_digest,
        )
        self._next_block_eviction_id += 1
        eviction_id = self._next_block_eviction_id
        units = int(record["units"])
        bytes_per_unit = int(
            self._host_pool_geometry.get(record["pool"], {}).get(
                "bytes_per_unit", 0
            )
        )
        pending = self._pending_block_evictions.pop(identity, None)
        if pending is None:
            pending = {
                "first_eviction_id": eviction_id,
                "first_eviction_ts_ms": float(record["ts_ms"]),
                "eviction_count": 0,
                "evicted_units_total": 0,
                "evicted_bytes_total": 0,
            }
        pending["last_eviction_id"] = eviction_id
        pending["last_eviction_ts_ms"] = float(record["ts_ms"])
        pending["last_evicted_units"] = units
        pending["eviction_count"] += 1
        pending["evicted_units_total"] += units
        pending["evicted_bytes_total"] += units * bytes_per_unit
        self._pending_block_evictions[identity] = pending
        self._pending_block_evictions.move_to_end(identity)
        index_key = (namespace_digest, prefix_tokens, prefix_digest)
        indexed = self._block_eviction_index.get(index_key)
        if indexed is None:
            indexed = set()
            self._block_eviction_index[index_key] = indexed
            self._block_eviction_length_refs[
                (namespace_digest, prefix_tokens)
            ] += 1
        lengths = self._block_eviction_lengths.setdefault(
            namespace_digest, OrderedDict()
        )
        lengths[prefix_tokens] = None
        lengths.move_to_end(prefix_tokens)
        indexed.add(identity)
        self._host_block_eviction_count += 1
        self._block_attribution_counts[f"{record['pool']}_evictions"] += 1
        self._block_attribution_evicted_units[record["pool"]] += units
        output.write(json.dumps({
            "event": "host_block_evicted",
            "eviction_id": eviction_id,
            "ts_ms": record["ts_ms"],
            "pool": record["pool"],
            "node_id": record["node_id"],
            "prefix_tokens": prefix_tokens,
            "prefix_hmac_blake2b128": prefix_digest,
            "cache_namespace_hmac_blake2b128": namespace_digest,
            "evicted_units": units,
            "evicted_bytes": units * bytes_per_unit,
            "identity_confidence": (
                "exact_namespaced_token_prefix_hmac_blake2b128"
            ),
        }, separators=(",", ":")) + "\n")
        while len(self._pending_block_evictions) > self._block_eviction_index_limit:
            expired_identity, expired = self._pending_block_evictions.popitem(
                last=False
            )
            self._remove_block_eviction_index(expired_identity)
            self._block_attribution_counts["expired_without_reaccess"] += 1
            self._block_attribution_evicted_units[
                f"{expired_identity[0]}_expired"
            ] += int(expired["last_evicted_units"])

    def _write_block_request_probe(
        self, record: dict[str, Any], output: Any
    ) -> None:
        self._block_attribution_counts["request_probes"] += 1
        try:
            input_ids = record["input_ids"]
            if isinstance(input_ids, array) and input_ids.typecode == "q":
                token_ids = input_ids
            else:
                token_ids = array("q", (int(token) for token in input_ids))
        except (KeyError, TypeError, ValueError, OverflowError):
            self._block_attribution_counts["invalid_request_probes"] += 1
            return
        namespace_digest = self._namespace_digest(
            record.get("extra_key"), record.get("cache_salt")
        )
        lengths = self._block_eviction_lengths.get(namespace_digest)
        if not lengths or not token_ids:
            return
        available_lengths = [
            length for length in reversed(lengths)
            if length <= len(token_ids)
        ]
        overflow = max(
            0, len(available_lengths) - self._max_prefix_lengths_per_probe
        )
        if overflow:
            available_lengths = available_lengths[
                :self._max_prefix_lengths_per_probe
            ]
            self._block_attribution_probe_overflow += overflow
            self._block_attribution_counts["probe_prefix_length_overflow"] += overflow
        prefix_lengths = sorted(available_lengths)
        prefix_digests = self._prefix_digests(
            record.get("extra_key"),
            record.get("cache_salt"),
            token_ids,
            prefix_lengths,
        )
        matches = 0
        cached_device = min(
            len(token_ids), max(0, int(record.get("cached_tokens_device", 0)))
        )
        cached_host = min(
            len(token_ids) - cached_device,
            max(0, int(record.get("cached_tokens_host", 0))),
        )
        host_end = cached_device + cached_host
        for prefix_tokens, prefix_digest in prefix_digests.items():
            index_key = (namespace_digest, prefix_tokens, prefix_digest)
            identities = tuple(self._block_eviction_index.get(index_key, ()))
            for identity in identities:
                pending = self._pending_block_evictions.pop(identity, None)
                if pending is None:
                    continue
                self._remove_block_eviction_index(identity)
                pool = identity[0]
                start = max(
                    0,
                    prefix_tokens - min(
                        prefix_tokens, int(pending["last_evicted_units"])
                    ),
                )
                end = prefix_tokens
                span = max(0, end - start)
                device_units = max(
                    0, min(end, cached_device) - min(start, cached_device)
                )
                host_units = max(
                    0, min(end, host_end) - max(start, cached_device)
                )
                recomputed_units = max(0, span - device_units - host_units)
                if pool == "full":
                    if recomputed_units == 0:
                        if device_units and host_units:
                            outcome = "full_mixed_tier_hit"
                        elif host_units:
                            outcome = "full_host_hit"
                        else:
                            outcome = "full_device_hit"
                    elif device_units + host_units:
                        outcome = "full_partial_hit_and_recompute"
                    else:
                        outcome = "full_recompute"
                    self._block_attribution_reused_units["full"] += (
                        device_units + host_units
                    )
                    self._block_attribution_recomputed_units["full"] += (
                        recomputed_units
                    )
                else:
                    outcome = "mamba_prefix_revisited_hit_location_unknown"
                self._block_attribution_counts[outcome] += 1
                matches += 1
                output.write(json.dumps({
                    "event": "host_block_reaccess_attributed",
                    "ts_ms": record["ts_ms"],
                    "pool": pool,
                    "eviction_ids": {
                        "first": pending["first_eviction_id"],
                        "last": pending["last_eviction_id"],
                    },
                    "eviction_count_since_prior_reaccess": (
                        pending["eviction_count"]
                    ),
                    "first_eviction_ts_ms": pending["first_eviction_ts_ms"],
                    "last_eviction_ts_ms": pending["last_eviction_ts_ms"],
                    "reaccess_delay_ms": (
                        float(record["ts_ms"])
                        - float(pending["last_eviction_ts_ms"])
                    ),
                    "workflow_id": record.get("workflow_id"),
                    "invocation_id": record.get("invocation_id"),
                    "context_id": record.get("context_id"),
                    "context_epoch": record.get("context_epoch"),
                    "request_id": record.get("request_id"),
                    "prefix_tokens": prefix_tokens,
                    "prefix_hmac_blake2b128": prefix_digest,
                    "cache_namespace_hmac_blake2b128": namespace_digest,
                    "evicted_units": pending["last_evicted_units"],
                    "evicted_units_since_prior_reaccess": (
                        pending["evicted_units_total"]
                    ),
                    "outcome": outcome,
                    "full_device_hit_units": (
                        device_units if pool == "full" else None
                    ),
                    "full_host_hit_units": (
                        host_units if pool == "full" else None
                    ),
                    "full_recomputed_units": (
                        recomputed_units if pool == "full" else None
                    ),
                    "mamba_host_hit_slots_at_request": (
                        record.get("mamba_host_hit_slots")
                        if pool == "mamba"
                        else None
                    ),
                    "attribution_semantics": (
                        "exact_radix_prefix_and_full_token_interval"
                        if pool == "full"
                        else "exact_prefix_revisit; mamba hit location is not "
                             "exposed by native request telemetry"
                    ),
                }, separators=(",", ":")) + "\n")
        if overflow or matches:
            output.write(json.dumps({
                "event": "eviction_probe_summary",
                "ts_ms": record["ts_ms"],
                "workflow_id": record.get("workflow_id"),
                "request_id": record.get("request_id"),
                "candidate_prefix_lengths": len(prefix_lengths),
                "omitted_prefix_lengths": overflow,
                "matched_evicted_blocks": matches,
            }, separators=(",", ":")) + "\n")
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
                self._queue_block_reaccess_probe(req, identity)
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
        stream_elapsed_ms = getattr(event, "transfer_stream_elapsed_ms", None)
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
            "actual_bytes": getattr(event, "actual_bytes", None),
            "submit_ts_ms": getattr(event, "submit_ts_ms", None),
            "submit_to_ack_ms": getattr(event, "submit_to_ack_ms", None),
            "native_unacked_bytes_at_submit": getattr(
                event, "unacked_bytes_at_submit", None
            ),
            "transfer_stream_elapsed_ms": stream_elapsed_ms,
            "start_ts_ms": None,
            "complete_ts_ms": getattr(event, "ack_ts_ms", None) or time.time() * 1000,
            "start_timestamp_semantics": (
                "device_event_no_wall_anchor"
                if stream_elapsed_ms is not None else "unobserved"
            ),
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
                self._paths["eviction_attribution"].open(
                    "x", encoding="utf-8"
                ) as eviction_attribution,
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
                    "eviction_attribution": eviction_attribution,
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
                    if stream == "eviction_attribution":
                        internal_event = record.get("_internal_event")
                        try:
                            if internal_event == "host_block_eviction":
                                self._write_block_eviction(
                                    record, eviction_attribution
                                )
                            elif internal_event == "request_probe":
                                self._write_block_request_probe(
                                    record, eviction_attribution
                                )
                            else:
                                eviction_attribution.write(
                                    json.dumps(
                                        record,
                                        separators=(",", ":"),
                                        allow_nan=False,
                                    ) + "\n"
                                )
                        except Exception as exc:
                            self._block_attribution_counts[
                                "processing_errors"
                            ] += 1
                            eviction_attribution.write(json.dumps({
                                "event": "host_block_attribution_error",
                                "ts_ms": time.time() * 1000.0,
                                "internal_event": internal_event,
                                "error_type": type(exc).__name__,
                                "reason": str(exc)[:256],
                            }, separators=(",", ":")) + "\n")
                        self._counts[stream] += 1
                        continue
                    handles[stream].write(
                        json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n"
                    )
                    self._counts[stream] += 1
                if self._pending_block_evictions:
                    pending_by_pool: Counter[str] = Counter()
                    units_by_pool: Counter[str] = Counter()
                    for identity, pending in self._pending_block_evictions.items():
                        pending_by_pool[identity[0]] += 1
                        units_by_pool[identity[0]] += int(
                            pending["last_evicted_units"]
                        )
                    eviction_attribution.write(json.dumps({
                        "event": "host_block_eviction_unmatched_summary",
                        "ts_ms": time.time() * 1000.0,
                        "pending_blocks_by_pool": dict(pending_by_pool),
                        "latest_evicted_units_by_pool": dict(units_by_pool),
                        "semantics": "no matching request observed before telemetry close",
                    }, separators=(",", ":")) + "\n")
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
        pending_by_pool: Counter[str] = Counter()
        pending_units_by_pool: Counter[str] = Counter()
        for identity, pending in self._pending_block_evictions.items():
            pending_by_pool[identity[0]] += 1
            pending_units_by_pool[identity[0]] += int(
                pending["last_evicted_units"]
            )
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
            "host_block_eviction_attribution": {
                "available": self._host_block_identity_available,
                "counts": dict(self._block_attribution_counts),
                "evicted_units_by_pool": dict(
                    self._block_attribution_evicted_units
                ),
                "reused_full_units": self._block_attribution_reused_units[
                    "full"
                ],
                "recomputed_full_units": (
                    self._block_attribution_recomputed_units["full"]
                ),
                "pending_blocks_by_pool": dict(pending_by_pool),
                "pending_latest_evicted_units_by_pool": dict(
                    pending_units_by_pool
                ),
                "probe_prefix_length_overflow": (
                    self._block_attribution_probe_overflow
                ),
                "identity_semantics": (
                    "run-keyed HMAC over exact cache namespace and radix token "
                    "prefix; raw token IDs are not written"
                ),
                "full_reaccess_semantics": (
                    "exact prefix match plus request device/host cached-token "
                    "intervals; remaining evicted FULL interval is attributed "
                    "to recomputation"
                ),
                "mamba_reaccess_semantics": (
                    "exact prefix revisit is observable, but native telemetry "
                    "does not expose the Mamba hit location per node"
                ),
            },
            "request_cache_evidence_semantics": {
                "full_host_hit": "cached_tokens_host input-token count",
                "mamba_host_hit": (
                    "mamba_host_hit_length checkpoint-slot count; not tokens"
                ),
                "after_eviction": (
                    "pool-level request hits and uncached prompt tokens after "
                    "the first eviction; use eviction_attribution.jsonl for "
                    "node-level FULL reaccess attribution"
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
