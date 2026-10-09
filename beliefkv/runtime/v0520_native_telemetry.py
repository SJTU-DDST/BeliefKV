"""Opt-in, scheduler-local evidence for Qwen3.5 native collection."""

from __future__ import annotations

from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from beliefkv.runtime.restore_wait_probe import RestoreWaitProbe

import atexit
from array import array
from collections import Counter, OrderedDict
import hashlib
import json
import math
import os
from pathlib import Path
from queue import Empty, Full, Queue
import secrets
from threading import Lock, Thread
import time
from typing import Any

from beliefkv.runtime.sglang_v0520_observer import normalize_native_creation_time
from beliefkv.runtime.eos_shadow import (
    EOS_LOW_PROB_THRESHOLDS,
    EOS_PROB_THRESHOLDS,
)


class NativeReactiveTelemetry:
    def __init__(
        self, directory: str | Path, *, scheduler_path: str | Path | None = None,
        collection_mode: str = "native_reactive",
    ) -> None:
        if collection_mode not in {
            "native_reactive", "admission_observation", "confirmed_join_canary"
        }:
            raise ValueError("unsupported native telemetry collection mode")
        self.collection_mode = collection_mode
        self.directory = Path(directory).resolve()
        self.scheduler_path = (
            Path(scheduler_path).resolve() if scheduler_path is not None else None
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self._paths = {
            "events": self.directory / "runtime_events.sglang.jsonl",
            "audit": self.directory / "runtime_audit.jsonl",
            "transfer": self.directory / "transfer_telemetry.jsonl",
            "action_ack": self.directory / "physical_action_ack.jsonl",
            "action_use": self.directory / "physical_action_use.jsonl",
            "host_pool": self.directory / "host_pool_telemetry.jsonl",
            "eviction_attribution": self.directory / "eviction_attribution.jsonl",
        }
        if any(path.exists() for path in self._paths.values()):
            raise RuntimeError("native telemetry directory already contains evidence")
        self._queue: Queue[tuple[str, dict[str, Any]] | None] = Queue(maxsize=32768)
        self._pending: dict[str, float] = {}
        self._active: set[str] = set()
        self._completed: set[str] = set()
        self._pending_prefetch_use: OrderedDict[
            str, tuple[Any, tuple[tuple[Any, ...], ...], float, dict[int, tuple[array, Any, Any]]]
        ] = OrderedDict()
        self._reported_output_tokens: dict[str, int] = {}
        self._targeted_pair_cursor: dict[str, tuple[int, int, tuple[int, int]]] = {}
        self._targeted_pair_seen: dict[str, set[float]] = {}
        self._targeted_pair_windows: dict[str, dict[str, Any]] = {}
        self._targeted_pair_invalid: set[str] = set()
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
        # The index is bounded already; probing only its newest 1024 lengths
        # hid older live-prefix losses under multi-workflow pressure.
        self._max_prefix_lengths_per_probe = self._block_eviction_index_limit
        self._context_prefix_history: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
        self._context_prefix_history_limit = 1024
        self._context_prefix_counts: Counter[str] = Counter()
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
        self._restore_wait_probe = RestoreWaitProbe()
        self._restore_wait_device: object | None = None
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
                "collection_mode": self.collection_mode,
                "scheduler_pid": os.getpid(),
                "capacity": observation,
            }, output, indent=2, sort_keys=True)
            output.write("\n")
        self._cache = cache
        if getattr(getattr(cache, "cache_controller", None), "layer_done_counter", None) is not None:
            from sglang.srt.utils import get_device_module

            self._restore_wait_device = get_device_module()
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
                "reentry_pressure_backups": dict(
                    getattr(cache, "beliefkv_reentry_backup_counts", {}) or {}
                ),
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
                else getattr(key, "token_ids", key if isinstance(key, array) else ())
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

    def _queue_context_prefix(self, req: Any, identity: dict[str, Any], *, served: bool) -> None:
        if served and not getattr(req, "output_ids", ()):
            return
        self._emit("eviction_attribution", {
            "_internal_event": "served_context_prefix" if served else "context_prefix_probe",
            "ts_ms": time.time() * 1000.,
            **identity, "request_id": str(req.rid),
            "input_ids": req.origin_input_ids,
            "extra_key": getattr(req, "extra_key", None),
            "cache_salt": getattr(req, "cache_salt", None),
            "session_id": getattr(req, "session_id", None),
            "session_generation": getattr(req, "session_generation", None),
            "cached_tokens_device": int(getattr(req, "cached_tokens_device", 0) or 0),
            "cached_tokens_host": int(getattr(req, "cached_tokens_host", 0) or 0),
            "mamba_host_hit_slots": int(getattr(req, "mamba_host_hit_length", 0) or 0),
        })

    def _write_context_prefix(self, record: dict[str, Any], output: Any) -> None:
        token_ids = array("q", (int(token) for token in record["input_ids"]))
        namespace = self._namespace_digest(record.get("extra_key"), record.get("cache_salt"))
        # A fresh native session/compaction is not an eviction-caused loss.
        scope = (
            record["workflow_id"], record["context_id"], namespace,
            record.get("session_id"), record.get("session_generation"),
        )
        if record["_internal_event"] == "served_context_prefix":
            self._context_prefix_history[scope] = {
                "tokens": token_ids, "request_id": record["request_id"],
                "context_epoch": record["context_epoch"],
            }
            self._context_prefix_history.move_to_end(scope)
            while len(self._context_prefix_history) > self._context_prefix_history_limit:
                self._context_prefix_history.popitem(last=False)
                self._context_prefix_counts["history_capacity_expirations"] += 1
            return
        self._context_prefix_counts["request_probes"] += 1
        previous = self._context_prefix_history.get(scope)
        if previous is None:
            self._context_prefix_counts["without_prior_served_prompt"] += 1
            return
        if record["context_epoch"] < previous["context_epoch"]:
            self._context_prefix_counts["epoch_regression"] += 1
            return
        common = 0
        for left, right in zip(token_ids, previous["tokens"]):
            if left != right:
                break
            common += 1
        cached = min(len(token_ids), max(0, record["cached_tokens_device"] + record["cached_tokens_host"]))
        lost = max(0, common - cached)
        new = len(token_ids) - common
        self._context_prefix_counts["with_prior_served_prompt"] += 1
        self._context_prefix_counts["previously_served_common_prefix_tokens"] += common
        self._context_prefix_counts["previously_served_prefix_recompute_proxy_tokens"] += lost
        self._context_prefix_counts["new_or_changed_prompt_tokens"] += new
        self._context_prefix_counts["requests_with_prefix_recompute_proxy"] += int(lost > 0)
        output.write(json.dumps({
            "event": "context_prefix_reuse_probe", "ts_ms": record["ts_ms"],
            **{name: record[name] for name in (
                "workflow_id", "invocation_id", "context_id", "context_epoch", "request_id",
            )},
            "prior_request_id": previous["request_id"],
            "common_previously_served_input_tokens": common,
            "native_cached_input_tokens": cached,
            "previously_served_prefix_recompute_proxy_tokens": lost,
            "new_or_changed_prompt_tokens": new,
            "mamba_host_hit_slots": record["mamba_host_hit_slots"],
            "semantics": (
                "Exact same-session input prefix served by a prior completed generation "
                "but absent from current native cache-hit counters. Not a per-layer "
                "kernel-work measurement or proof of eviction cause; excludes new input."
            ),
        }, separators=(",", ":"), allow_nan=False) + "\n")

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

    def _match_path(self, node_id: Any) -> list[list[int | float]] | None:
        tree_core = getattr(self._cache, "tree_core", None)
        try:
            node = tree_core.node_by_id(node_id)
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
        path: list[list[int | float]] = []
        seen: set[int] = set()
        while node is not None and len(path) < 256:
            if id(node) in seen or type(getattr(node, "id", None)) is not int:
                return None
            try:
                created = normalize_native_creation_time(node.creation_time)
            except (AttributeError, ValueError):
                return None
            seen.add(id(node))
            path.append([node.id, created])
            node = getattr(node, "parent", None)
        return list(reversed(path)) if node is None else None

    def on_abort_request(self, abort: Any) -> None:
        affected = [
            rid for rid in self._pending.keys() | self._active
            if abort.abort_all or rid.startswith(abort.rid)
        ]
        for rid in affected:
            self._drop_targeted_pair(rid)
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
        self._poll_restore_wait()
        controller = getattr(self._cache, "cache_controller", None)
        counter = getattr(controller, "layer_done_counter", None)
        if counter is not None:
            counter.beliefkv_wait_probe = self._restore_wait_probe.begin(
                batch, self._restore_wait_device
            )
        launch = float(batch.launch_ts)
        phase = "prefill" if mode.is_extend() else "decode"
        samples = []
        mamba_forward_candidates = []
        for req in batch.reqs:
            identity = self._identity(req)
            if identity is None:
                continue
            rid = str(req.rid)
            if rid in self._completed:
                continue
            if rid not in self._active:
                submit_ts = self._pending.pop(rid, time.time() * 1000)
                mamba_forward_candidates.extend(
                    self._record_prefetch_first_service(req, identity, batch)
                )
                prompt_tokens = len(req.origin_input_ids)
                cached_device = int(
                    getattr(req, "cached_tokens_device", 0) or 0
                )
                cached_host = int(getattr(req, "cached_tokens_host", 0) or 0)
                mamba_host_hits = int(
                    getattr(req, "mamba_host_hit_length", 0) or 0
                )
                host_hit_path = (
                    {"native_host_hit_match_path": {
                        "observed_ts_ms": time.time() * 1000.0,
                        "last_device_path": self._match_path(
                            getattr(req, "last_node", None)
                        ),
                        "last_host_path": self._match_path(
                            getattr(req, "last_host_node", None)
                        ),
                        "best_match_path": self._match_path(
                            getattr(req, "best_match_node", None)
                        ),
                        "evidence": (
                            "node_identity_at_first_gpu_service_only;"
                            "not_proof_of_component_reuse"
                        ),
                    }}
                    if cached_host or mamba_host_hits else {}
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
                        "native_full_kv_hit_length": getattr(
                            req, "beliefkv_full_kv_hit_length", None
                        ),
                        "native_mamba_branching_seqlen": getattr(
                            req, "mamba_branching_seqlen", None
                        ),
                        "reentry_checkpoint_seqlen": getattr(
                            req, "beliefkv_reentry_checkpoint_seqlen", None
                        ),
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
                        **host_hit_path,
                    },
                })
                self._queue_block_reaccess_probe(req, identity)
                self._queue_context_prefix(req, identity, served=False)
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
                "mamba_forward_candidates": mamba_forward_candidates,
            }
            if self._cache is not None:
                self.record_host_pool_usage(self._cache)

    def _drop_targeted_pair(self, rid: str) -> None:
        self._targeted_pair_cursor.pop(rid, None)
        self._targeted_pair_seen.pop(rid, None)
        self._targeted_pair_windows.pop(rid, None)
        self._targeted_pair_invalid.discard(rid)

    def _emit_targeted_pair_window(
        self, rid: str, sample: dict[str, Any], ts_ms: float, sample_id: str,
        pair_ids: tuple[int, int], *, finished: bool,
    ) -> None:
        window = self._targeted_pair_windows.pop(rid, None)
        if not window:
            return
        self._emit("audit", {
            "event": "targeted_pair_recent_window",
            "ts_ms": ts_ms,
            **{key: sample[key] for key in (
                "workflow_id", "invocation_id", "context_id", "context_epoch"
            )},
            "request_id": rid,
            "sample_id": sample_id,
            "probe_token_ids": list(pair_ids),
            "first_output_token_ordinal": window["first_ordinal"],
            "last_output_token_ordinal": window["last_ordinal"],
            "scored_tokens": window["scored_tokens"],
            "max_logprob": window["max_logprob"],
            "max_output_token_ordinal": window["max_ordinal"],
            "last_logprob": window["last_logprob"],
            "finished_at_boundary": finished,
            "timing_boundary": (
                "scheduler_batch_result_processed;recent_sampled_tokens_only;"
                "token_generation_batch_not_inferred"
            ),
        })

    def _observe_targeted_pair(
        self, req: Any, sample: dict[str, Any], ts_ms: float, sample_id: str
    ) -> None:
        rid = sample["request_id"]
        if rid in self._targeted_pair_invalid:
            return
        logprob = getattr(req, "logprob", None)
        pair = getattr(logprob, "token_ids_logprob", None)
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or any(type(token_id) is not int for token_id in pair)
        ):
            return
        scores = getattr(logprob, "output_token_ids_logprobs_val", None)
        indices = getattr(logprob, "output_token_ids_logprobs_idx", None)
        sampled_ids = getattr(logprob, "output_token_logprobs_idx", None)
        if not scores:
            return
        prior = self._targeted_pair_cursor.get(rid)
        base = len(req.output_ids) - len(scores)
        pair_ids = (pair[0], pair[1])
        invalid = (
            not isinstance(indices, list)
            or len(indices) != len(scores)
            or not isinstance(sampled_ids, list)
            or len(sampled_ids) != len(scores)
            or base < 0
            or (
                prior is not None
                and (prior[0] != base or prior[2] != pair_ids
                     or not prior[1] <= len(scores))
            )
        )
        if invalid:
            self._targeted_pair_invalid.add(rid)
            self._targeted_pair_windows.pop(rid, None)
            self._emit("audit", {
                "event": "targeted_pair_ordinal_invalid",
                "ts_ms": ts_ms,
                "request_id": rid,
                "reason": "missing_or_nonmonotonic_output_alignment",
                "output_tokens": len(req.output_ids),
                "scored_tokens": len(scores),
                "index_rows": len(indices) if isinstance(indices, list) else None,
                "sampled_id_rows": (
                    len(sampled_ids) if isinstance(sampled_ids, list) else None
                ),
                "computed_base": base,
                "prior_base": prior[0] if prior is not None else None,
                "prior_scored_tokens": prior[1] if prior is not None else None,
                "output_tokens_before_batch": sample.get("output_tokens_before"),
            })
            return
        start = prior[1] if prior is not None else 0
        if any(
            sampled_ids[position] != req.output_ids[base + position]
            for position in range(start, len(scores))
        ):
            self._targeted_pair_invalid.add(rid)
            self._targeted_pair_windows.pop(rid, None)
            self._emit("audit", {
                "event": "targeted_pair_ordinal_invalid",
                "ts_ms": ts_ms,
                "request_id": rid,
                "reason": "sampled_token_id_mismatch",
                "output_tokens": len(req.output_ids),
                "scored_tokens": len(scores),
                "output_tokens_before_batch": sample.get("output_tokens_before"),
            })
            return
        seen = self._targeted_pair_seen.setdefault(rid, set())
        thresholds = EOS_LOW_PROB_THRESHOLDS + EOS_PROB_THRESHOLDS
        for position in range(start, len(scores)):
            values = scores[position]
            if (
                not isinstance(values, (list, tuple))
                or len(values) != 2
                or indices[position] != pair
                or any(type(value) not in (int, float) for value in values)
                or any(not math.isfinite(value) for value in values)
            ):
                self._targeted_pair_invalid.add(rid)
                self._targeted_pair_windows.pop(rid, None)
                self._emit("audit", {
                    "event": "targeted_pair_ordinal_invalid",
                    "ts_ms": ts_ms,
                    "request_id": rid,
                    "reason": "invalid_target_logprob_row",
                })
                return
            if req.output_ids[base + position] in pair_ids:
                continue
            best = max(values)
            ordinal = base + position + 1
            window = self._targeted_pair_windows.setdefault(rid, {
                "first_ordinal": ordinal,
                "last_ordinal": ordinal,
                "scored_tokens": 0,
                "max_logprob": float("-inf"),
                "max_ordinal": ordinal,
                "last_logprob": best,
            })
            window["scored_tokens"] += 1
            window["last_ordinal"] = ordinal
            window["last_logprob"] = best
            if best > window["max_logprob"]:
                window["max_logprob"] = best
                window["max_ordinal"] = ordinal
            if window["scored_tokens"] == 32:
                self._emit_targeted_pair_window(
                    rid, sample, ts_ms, sample_id, pair_ids,
                    finished=bool(req.finished()),
                )
            for threshold in thresholds:
                if threshold in seen or best < math.log(threshold):
                    continue
                seen.add(threshold)
                self._emit("audit", {
                    "event": "targeted_pair_first_crossing",
                    "ts_ms": ts_ms,
                    **{key: sample[key] for key in (
                        "workflow_id", "invocation_id", "context_id", "context_epoch"
                    )},
                    "request_id": rid,
                    "sample_id": sample_id,
                    "output_token_ordinal": base + position + 1,
                    "token_already_present_before_batch": (
                        base + position < sample["output_tokens_before"]
                    ),
                    "probe_token_ids": pair,
                    "threshold": threshold,
                    "max_logprob": best,
                    "timing_boundary": (
                        "scheduler_batch_result_processed;first_seen_on_scheduler;"
                        "token_generation_batch_not_inferred"
                    ),
                })
        self._targeted_pair_cursor[rid] = (base, len(scores), pair_ids)
        if req.finished():
            self._emit_targeted_pair_window(
                rid, sample, ts_ms, sample_id, pair_ids, finished=True,
            )

    def _reentry_checkpoint_evidence(self, req: Any) -> dict[str, Any]:
        cache = self._cache
        if cache is None or getattr(req, "session_id", None) is None:
            return {}
        try:
            snapshot = getattr(
                cache.session_refs, "snapshot_latest_session_leaf_anchors",
                cache.session_refs.snapshot_session_leaf_anchors,
            )
            anchors = snapshot(
                req.session_id, req.session_generation, max_leaves=8,
            )
            if anchors is None:
                return {"status": "snapshot_unavailable"}
            states = []
            for component, leaves in anchors:
                if component != 2:
                    continue
                for node_id, created in leaves:
                    node = cache.tree_core.node_by_id(node_id)
                    path = self._node_key_path(node)
                    prefix = sum(len(key) for key in path) if path is not None else None
                    state = node.component_data[2]
                    states.append({
                        "node_id": node_id, "prefix_tokens": prefix,
                        "within_safe_input": (
                            prefix < len(req.origin_input_ids) if prefix is not None else None
                        ),
                        "device_present": state.value is not None,
                        "host_present": state.host_value is not None,
                    })
            return {"status": "observed", "mamba_states": states}
        except (AttributeError, KeyError, TypeError, ValueError):
            return {"status": "snapshot_unavailable"}

    def on_completed(self, batch: Any) -> None:
        self._poll_restore_wait()
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
            if rid in self._completed:
                continue
            reported_before = self._reported_output_tokens.get(
                rid, sample["output_tokens_before"]
            )
            output_after = len(req.output_ids)
            if descriptor["phase"] == "decode":
                sample["token_delta"] = max(0, output_after - reported_before)
            self._reported_output_tokens[rid] = max(
                reported_before, output_after
            )
            self._observe_targeted_pair(
                req, sample, complete_wall, descriptor["sample_id"]
            )
            if req.finished() and rid in self._active:
                self._queue_context_prefix(req, {
                    key: sample[key] for key in (
                        "workflow_id", "invocation_id", "context_id", "context_epoch"
                    )
                }, served=True)
                self._emit("events", {
                    "kind": "llm_result",
                    "ts_ms": complete_wall,
                    **{key: sample[key] for key in (
                        "workflow_id", "invocation_id", "context_id", "context_epoch"
                    )},
                    "attributes": {
                        "request_id": rid,
                        "output_tokens": len(req.output_ids),
                        "complete_monotonic_ms": complete_mono * 1000,
                        "monotonic_clock_domain": local_monotonic_clock_domain(),
                        "reentry_checkpoint": self._reentry_checkpoint_evidence(req),
                    },
                })
                self._active.discard(rid)
                self._completed.add(rid)
                self._drop_targeted_pair(rid)
        self._emit("audit", {
            "event": "gpu_service_sample",
            "ts_ms": complete_wall,
            "sample_id": descriptor["sample_id"],
            "phase": descriptor["phase"],
            "batch_size": descriptor["batch_size"],
            "request_samples": descriptor["request_samples"],
            "service_start_ts_ms": complete_wall - (complete_mono - start_mono) * 1000,
            "complete_ts_ms": complete_wall,
            "service_start_monotonic_ms": start_mono * 1000,
            "complete_monotonic_ms": complete_mono * 1000,
            "monotonic_clock_domain": local_monotonic_clock_domain(),
            "service_elapsed_ms": (complete_mono - start_mono) * 1000,
            "timing_semantics_version": "gpu_service_interval_v1",
            "timing_boundary": "scheduler/worker interval, not CUDA kernel time",
        })
        for candidate in descriptor["mamba_forward_candidates"]:
            self._emit("action_use", {
                "event": "beliefkv_prefetch_mamba_forward_completed",
                **candidate,
                "forward_complete_ts_ms": complete_wall,
                "mamba_reuse": (
                    "verified_per_request_cow_forward_completed"
                    if candidate.get("mamba_cow_evidence") == "native_per_request_source_destination_identity"
                    else "verified_single_request_cow_forward_completed"
                ),
                "evidence": (
                    "same_node_and_device_value_at_first_prefill;"
                    "request_attributed_mamba_cow_queued_and_forward_completed"
                ),
            })
        if self._cache is not None:
            self.record_host_pool_usage(self._cache)

    def _poll_restore_wait(self) -> None:
        for record in self._restore_wait_probe.poll():
            self._emit("audit", record)

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
            "enqueue_to_submit_ms": getattr(event, "enqueue_to_submit_ms", None),
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
            "tagged_child_commits": [
                {
                    "command_id": child.command_id,
                    "anchor_node_id": child.anchor_node_id,
                    "published_node_ids": list(child.published_node_ids),
                    "num_tokens_by_pool": dict(child.num_tokens_by_pool),
                    "num_bytes": child.num_bytes,
                }
                for child in getattr(event, "child_commits", ())
            ],
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

    def on_verified_action_ack(self, action: Any) -> None:
        """Persist only actions reconciled by the physical transaction ledger."""
        self._emit("action_ack", {
            "event": "beliefkv_physical_action_ack",
            "ts_ms": time.time() * 1000.0,
            "command_id": action.command_id,
            "action": action.action,
            "source": getattr(action, "source", None),
            "context_id": action.context_id,
            "context_epoch": action.context_epoch,
            "node_ids": list(action.node_ids),
            "pool_bytes": dict(action.pool_bytes),
            "num_bytes": action.num_bytes,
            "evidence": "native_child_commit_reconciled_with_live_context",
        })
        if action.action == "PREFETCH_GPU":
            nodes = []
            prefixes = {}
            tree_core = getattr(self._cache, "tree_core", None)
            for node_id in action.node_ids:
                try:
                    node = tree_core.node_by_id(node_id)
                    path = self._node_key_path(node)
                    prefix_len = (
                        sum(len(key) for key in path) if path is not None else None
                    )
                    full_value = node.component_data[0].value
                    mamba_value = (
                        node.component_data[2].value
                        if len(node.component_data) > 2 else None
                    )
                    nodes.append((
                        node_id, node.creation_time, node, prefix_len,
                        full_value, mamba_value,
                    ))
                    if path is not None and prefix_len <= 131072:
                        token_ids = self._key_path_tokens(path)
                        if token_ids is not None:
                            prefixes[node_id] = (
                                token_ids, getattr(path[-1], "extra_key", None),
                                getattr(path[-1], "cache_salt", None),
                            )
                except (AttributeError, KeyError, TypeError, ValueError):
                    nodes.append((node_id, None, None, None, None, None))
            while len(self._pending_prefetch_use) >= 128:
                old_id, (old_action, _, old_ts, _) = (
                    self._pending_prefetch_use.popitem(last=False)
                )
                self._emit("action_use", {
                    "event": "beliefkv_prefetch_first_service_censored",
                    "command_id": old_id,
                    "context_id": old_action.context_id,
                    "context_epoch": old_action.context_epoch,
                    "ack_ts_ms": old_ts,
                    "reason": "tracking_capacity_exceeded",
                })
            self._pending_prefetch_use[action.command_id] = (
                action, tuple(nodes), time.time() * 1000.0, prefixes,
            )

    def _record_prefetch_first_service(
        self, req: Any, identity: dict[str, Any], batch: Any | None = None,
    ) -> list[dict[str, Any]]:
        mamba_forward_candidates: list[dict[str, Any]] = []
        context_id = identity["context_id"]
        context_epoch = identity["context_epoch"]
        matches = [
            command_id for command_id, (action, _, _, _) in self._pending_prefetch_use.items()
            if action.context_id == context_id
            and action.context_epoch <= context_epoch <= action.context_epoch + 1
        ]
        expired = [
            command_id for command_id, (action, _, _, _) in self._pending_prefetch_use.items()
            if action.context_id == context_id
            and context_epoch > action.context_epoch + 1
        ]
        for command_id in expired:
            action, _, ack_ts, _ = self._pending_prefetch_use.pop(command_id)
            self._emit("action_use", {
                "event": "beliefkv_prefetch_first_service_censored",
                "command_id": command_id,
                "context_id": context_id,
                "context_epoch": action.context_epoch,
                "ack_ts_ms": ack_ts,
                "reason": "epoch_advanced_without_first_service",
            })
        if not matches:
            return mamba_forward_candidates
        tree_core = getattr(self._cache, "tree_core", None)
        try:
            last_node = tree_core.node_by_id(req.last_node)
        except (AttributeError, KeyError, TypeError, ValueError):
            last_node = None
        ancestors: set[int] = set()
        node = last_node
        while node is not None and len(ancestors) < 256:
            if id(node) in ancestors:
                break
            ancestors.add(id(node))
            node = getattr(node, "parent", None)
        cached_device = int(getattr(req, "cached_tokens_device", 0) or 0)
        prefix_indices = getattr(req, "prefix_indices", None)
        device_prefix_len = (
            len(prefix_indices) if prefix_indices is not None else None
        )
        for command_id in matches:
            action, nodes, ack_ts, prefixes = self._pending_prefetch_use.pop(command_id)
            target_prefixes = []
            for node_id, (token_ids, extra_key, cache_salt) in prefixes.items():
                common = 0
                for original_token, input_token in zip(token_ids, req.origin_input_ids):
                    if original_token != input_token:
                        break
                    common += 1
                target_prefixes.append({
                    "node_id": node_id, "prefix_tokens": len(token_ids),
                    "common_input_prefix_tokens": common,
                    "complete_input_prefix": common == len(token_ids),
                    "same_cache_namespace": (
                        extra_key == getattr(req, "extra_key", None)
                        and cache_salt == (getattr(req, "cache_salt", None) or None)
                    ),
                })
            full_bytes = dict(action.pool_bytes).get("kv", 0)
            matched_nodes = [
                node_id for node_id, creation_time, original, _, _, _ in nodes
                if original is not None
                and id(original) in ancestors
                and original.creation_time == creation_time
            ]
            reused_full_nodes = []
            for node_id, creation_time, original, prefix_len, full_value, _ in nodes:
                if (
                    tree_core is None or original is None or prefix_len is None
                    or id(original) not in ancestors
                    or original.creation_time != creation_time
                    or full_value is None
                    or cached_device < prefix_len
                    or (device_prefix_len is not None
                        and device_prefix_len < prefix_len)
                ):
                    continue
                try:
                    if (
                        tree_core.node_by_id(node_id) is original
                        and original.component_data[0].value is full_value
                    ):
                        reused_full_nodes.append(node_id)
                except (AttributeError, KeyError, TypeError, ValueError):
                    continue
            full_verifiable = (
                last_node is not None
                and all(
                    original is not None and prefix_len is not None
                    and full_value is not None
                    for _, _, original, prefix_len, full_value, _ in nodes
                )
                and full_bytes > 0
            )
            mamba_node_matches = False
            if len(nodes) == 1 and nodes[0][2] is not None:
                try:
                    mamba_node_matches = (
                        tree_core.node_by_id(nodes[0][0]) is nodes[0][2]
                        and nodes[0][2].creation_time == nodes[0][1]
                        and nodes[0][5] is not None
                        and nodes[0][2].component_data[2].value is nodes[0][5]
                    )
                except (AttributeError, IndexError, KeyError, TypeError, ValueError):
                    pass
            if (
                dict(action.pool_bytes).get("mamba", 0) > 0
                and mamba_node_matches
                and batch is not None
                and batch.forward_mode.is_extend()
                and not getattr(
                    batch.forward_mode, "is_target_verify", lambda: True
                )()
                and not getattr(
                    batch.forward_mode, "is_draft_extend_v2", lambda: True
                )()
                and int(getattr(req, "mamba_host_hit_length", 0) or 0) == 0
                and getattr(req, "best_match_node", None) == nodes[0][0]
                and getattr(batch, "mamba_cow_src_indices", None) is not None
                and getattr(batch, "mamba_cow_dst_indices", None) is not None
                and len(batch.mamba_cow_src_indices) > 0
                and len(batch.mamba_cow_dst_indices) > 0
            ):
                witnesses = getattr(batch, "beliefkv_mamba_cow_witnesses", None)
                matched = [
                    (source, destination) for rid, source, destination in witnesses or ()
                    if str(rid) == str(req.rid)
                ]
                per_request = (
                    len(matched) == 1 and matched[0][0] is nodes[0][5]
                    and matched[0][1] is getattr(getattr(req, "kv", None), "mamba_pool_idx", None)
                )
                legacy_single = (
                    witnesses is None and len(batch.reqs) == 1
                    and len(batch.mamba_cow_src_indices) == 1
                    and len(batch.mamba_cow_dst_indices) == 1
                )
                if per_request or legacy_single:
                    mamba_forward_candidates.append({
                        "command_id": command_id,
                        "context_id": action.context_id,
                        "context_epoch": action.context_epoch,
                        "service_context_epoch": context_epoch,
                        "request_id": str(req.rid),
                        "node_id": nodes[0][0],
                        "ack_ts_ms": ack_ts,
                        "first_service_ts_ms": time.time() * 1000.0,
                        "mamba_cow_evidence": (
                            "native_per_request_source_destination_identity"
                            if per_request else "legacy_single_request"
                        ),
                    })
            self._emit("action_use", {
                "event": "beliefkv_prefetch_first_service",
                "command_id": command_id,
                "context_id": action.context_id,
                "context_epoch": action.context_epoch,
                "service_context_epoch": context_epoch,
                "request_id": str(req.rid),
                "ack_ts_ms": ack_ts,
                "first_service_ts_ms": time.time() * 1000.0,
                "node_ids": list(action.node_ids),
                "matched_node_ids": matched_nodes,
                "reused_full_node_ids": reused_full_nodes,
                "target_prefixes": target_prefixes,
                "native_full_kv_hit_length": getattr(
                    req, "beliefkv_full_kv_hit_length", None
                ),
                "native_mamba_branching_seqlen": getattr(
                    req, "mamba_branching_seqlen", None
                ),
                "native_best_match_node_id": getattr(req, "best_match_node", None),
                "cached_tokens_device": cached_device,
                "device_prefix_indices_len": device_prefix_len,
                "cached_tokens_host": int(
                    getattr(req, "cached_tokens_host", 0) or 0
                ),
                "full_node_reused": (
                    bool(reused_full_nodes) if full_verifiable else None
                ),
                "mamba_reuse": "unverified",
                "evidence": "same_context_first_gpu_launch_and_verified_full_prefix",
            })
        return mamba_forward_candidates

    def _write(self) -> None:
        try:
            with (
                self._paths["events"].open("x", encoding="utf-8") as events,
                self._paths["audit"].open("x", encoding="utf-8") as audit,
                self._paths["transfer"].open("x", encoding="utf-8") as transfer,
                self._paths["action_ack"].open("x", encoding="utf-8") as action_ack,
                self._paths["action_use"].open("x", encoding="utf-8") as action_use,
                self._paths["host_pool"].open("x", encoding="utf-8") as host_pool,
                self._paths["eviction_attribution"].open(
                    "x", encoding="utf-8"
                ) as eviction_attribution,
            ):
                (self.directory / "native_telemetry_ready.json").write_text(
                    json.dumps({
                        "schema_version": 1,
                        "source": "native_sglang_v0520",
                        "collection_mode": self.collection_mode,
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
                    "action_ack": action_ack,
                    "action_use": action_use,
                    "host_pool": host_pool,
                    "eviction_attribution": eviction_attribution,
                }
                next_status = time.monotonic() + 1.0
                while True:
                    if time.monotonic() >= next_status:
                        for handle in handles.values():
                            handle.flush()
                        self._write_status()
                        next_status = time.monotonic() + 1.0
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
                            elif internal_event in ("context_prefix_probe", "served_context_prefix"):
                                self._write_context_prefix(record, eviction_attribution)
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
                    if stream in ("action_ack", "action_use"):
                        handles[stream].flush()
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
            "collection_mode": self.collection_mode,
            "record_counts": dict(self._counts),
            "pending_request_count": len(self._pending) + len(self._active),
            "pending_batch_count": len(self._launched),
            "writer_error": self._error,
            "failed_records": 0 if self._error is None else 1,
            "dropped_records": 0 if self._error is None else None,
            "host_pool_evidence": pool_evidence,
            "request_cache_evidence": request_cache_evidence,
            "context_prefix_reuse_evidence": {
                "counts": dict(self._context_prefix_counts),
                "tracked_context_count": len(self._context_prefix_history),
                "tracking_limit": self._context_prefix_history_limit,
                "semantics": (
                    "Previously served same-session input-prefix loss, not newly "
                    "appended input or a per-layer FULL/Mamba recomputation count."
                ),
            },
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
        status["snapshot_ts_ms"] = time.time() * 1000.0
        status["writer_queue_depth"] = self._queue.qsize()
        status["gpu_restore_wait_probe"] = self._restore_wait_probe.snapshot()
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(path)

    def close(self) -> None:
        if self._closed:
            return
        self._poll_restore_wait()
        counter = getattr(getattr(self._cache, "cache_controller", None), "layer_done_counter", None)
        if counter is not None:
            counter.beliefkv_wait_probe = None
        for command_id, (action, _, ack_ts, _) in self._pending_prefetch_use.items():
            self._emit("action_use", {
                "event": "beliefkv_prefetch_first_service_censored",
                "command_id": command_id,
                "context_id": action.context_id,
                "context_epoch": action.context_epoch,
                "ack_ts_ms": ack_ts,
                "reason": "no_subsequent_service_before_shutdown",
            })
        self._pending_prefetch_use.clear()
        self._closed = True
        self._queue.put(None)
        self._writer.join()
        if self._error is not None:
            self._write_status()
