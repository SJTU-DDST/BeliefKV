from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import heapq
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
import torch

from beliefkv.experiments.source_provenance import sha256_file
from scripts import qualify_free_page_h2d_fence as qualifier
from tests.test_native_free_page_h2d_fence import native


class Timeline:
    def __init__(self):
        self.now, self.order, self.tasks = 0., 0, []

    def enqueue(self, at, action):
        self.order += 1
        heapq.heappush(self.tasks, (at, self.order, action))

    def advance(self, at):
        while self.tasks and self.tasks[0][0] <= at:
            when, _, action = heapq.heappop(self.tasks)
            self.now = max(self.now, when)
            action()
        self.now = max(self.now, at)


class Cuda:
    """Ordered stream/event stand-ins; tensor writes are explicitly deferred."""

    def __init__(self):
        self.timeline = Timeline()
        self.current = self.Stream()

    def Stream(self):
        cuda = self

        class Stream:
            clock = 0.

            def wait_event(self, event):
                self.clock = max(self.clock, cuda.timeline.now, event.at or 0.)

            def wait_stream(self, other):
                self.clock = max(self.clock, cuda.timeline.now, other.clock)

            def synchronize(self):
                cuda.timeline.advance(self.clock)

        return Stream()

    def Event(self):
        cuda = self

        class Event:
            at = None

            def record(self, stream=None):
                stream = stream or cuda.current
                stream.clock = max(stream.clock, cuda.timeline.now)
                self.at = stream.clock

            def query(self):
                return self.at is None or self.at <= cuda.timeline.now

        return Event()

    @contextmanager
    def stream(self, stream):
        previous, self.current = self.current, stream
        try:
            yield
        finally:
            self.current = previous

    def current_stream(self):
        return self.current

    def _sleep(self, cycles):
        self.current.clock = max(self.current.clock, self.timeline.now) + cycles / 1_000_000.

    def synchronize(self):
        while self.timeline.tasks:
            self.timeline.advance(self.timeline.tasks[0][0])


@pytest.fixture
def fixture_factory(native, monkeypatch):
    from sglang.srt.mem_cache.memory_pool_host import LogicalHostPool

    class Fixture(qualifier.NativeFenceFixture):
        def __init__(self, *, enabled):
            self.torch, self.cuda, self.device = torch, Cuda(), "cpu"
            self.pool = NS(
                device="cpu", layer_num=2, register_layer_transfer_counter=Mock(),
                k_buffer=[torch.zeros((129, 2, 64), dtype=torch.bfloat16) for _ in range(2)],
                v_buffer=[torch.zeros((129, 2, 64), dtype=torch.bfloat16) for _ in range(2)],
            )
            self.rows = NS(size=4, available_size=lambda: 4)
            self.allocator = native.allocator(128, torch.bfloat16, "cpu", self.pool, need_sort=False)
            slots = LogicalHostPool(size=257, page_size=1)
            host = NS(
                size=257, size_per_token=1024,
                alloc=slots.alloc, free=slots.free, available_size=slots.available_size,
            )
            entry = NS(name=native.module.PoolName.KV, host_pool=host)
            host_pool = NS(
                anchor_entry=entry, size_per_token=1024,
                entry_map={native.module.PoolName.KV: entry},
                alloc=slots.alloc, free=slots.free, available_size=slots.available_size,
                resolve_host_transfers=lambda *_a, **_k: None,
            )
            self.storage = [
                (torch.zeros((257, 2, 64), dtype=torch.bfloat16),
                 torch.zeros((257, 2, 64), dtype=torch.bfloat16))
                for _ in range(2)
            ]
            copy, write = self.cuda.Stream(), self.cuda.Stream()
            selected = {}

            def move(operation, direction):
                selected[direction] = operation
                return operation.host_indices, operation.device_indices, operation.pool_transfers

            def submit(direction, *, start_event=None, on_layer_done=None, layer_num=2, **kwargs):
                stream = copy if direction == "h2d" else write
                if start_event is None:
                    start_event = self.cuda.Event()
                    start_event.record()
                stream.wait_event(start_event)
                operation = selected[direction]
                host_indices, device_indices = operation.host_indices.clone(), operation.device_indices.clone()
                first = self.cuda.Event()
                first.record(stream)
                with self.cuda.stream(stream):
                    for layer in range(layer_num):
                        stream.clock += 10.

                        def action(layer=layer):
                            key, value = self.pool.k_buffer[layer], self.pool.v_buffer[layer]
                            host_key, host_value = self.storage[layer]
                            if direction == "h2d":
                                key[device_indices], value[device_indices] = host_key[host_indices], host_value[host_indices]
                            else:
                                host_key[host_indices], host_value[host_indices] = key[device_indices], value[device_indices]

                        self.cuda.timeline.enqueue(stream.clock, action)
                        if on_layer_done:
                            on_layer_done(layer)
                finish = self.cuda.Event()
                finish.record(stream)
                return NS(start_event=first, finish_event=finish, timing_enabled=False)

            engine = NS(
                host_to_device_stream=copy,
                submit_host_to_device=lambda _transfers, **kwargs: submit("h2d", **kwargs),
                submit_device_to_host=lambda _transfers, **kwargs: submit("d2h", **kwargs),
            )
            monkeypatch.setenv("BELIEFKV_FREE_PAGE_H2D_FENCE", str(int(enabled)))
            monkeypatch.setattr(native.module, "device_module", self.cuda)
            monkeypatch.setattr(native.module, "L2TransferEngine", lambda _: engine)
            self.controller = native.hybrid(
                self.allocator, host_pool, 1, None, None, io_backend="kernel",
            )
            self.controller._move_op_indices = lambda operation: move(operation, "h2d")
            self.controller._move_write_operation = lambda operation: move(operation, "d2h")
            self.controller._l2_load_transfers = lambda *_: ()
            self.controller._l2_transfers = lambda *_: ()
            self.forward = self.cuda.Stream()
            self.controller.load_fence_stream = self.forward
            self.cache = NS(release_host_resources=lambda: None)
            self.initialize(enabled=enabled)

        def wait(self, event):
            self.cuda.timeline.advance(event.at or self.cuda.timeline.now)
            assert event.query()

        def poison(self, indices):
            indices = indices.clone()
            self.cuda.timeline.enqueue(
                self.cuda.current_stream().clock,
                lambda: super(Fixture, self).poison(indices),
            )

    return Fixture


@pytest.mark.parametrize("name", qualifier.SCENARIOS)
def test_the_actual_gpu_scenario_logic_uses_native_controller_and_deferred_writers(
    fixture_factory, name,
):
    fixture = fixture_factory(enabled=name != "disabled_free")
    result = qualifier.scenario(fixture, name)
    assert result["final_device_free_tokens"] == 128
    assert result["initial_host_free_tokens"] == result["final_host_free_tokens"]
    assert result["restored_bytes_match_after_writer_completion"] is (name != "negative_control")
    if name in qualifier.EARLY:
        assert result["writer_finished_at_copy_ack"] is False
    elif name != "negative_control":
        assert result["writer_finished_at_copy_ack"] is True


def test_removing_the_fallback_is_detected_after_the_old_writer_really_executes(fixture_factory):
    fixture = fixture_factory(enabled=True)
    fixture.controller._beliefkv_wait_load_fence = lambda _op: None
    with pytest.raises(ValueError, match="ordering|bytes|unsafe"):
        qualifier.scenario(fixture, "reclaimed")


def test_a_global_forward_wait_cannot_be_reported_as_early_overlap(fixture_factory):
    fixture = fixture_factory(enabled=True)
    fixture.controller._beliefkv_wait_load_fence = lambda _op: (
        fixture.controller.l2_transfer_engine.host_to_device_stream.wait_stream(fixture.forward)
    )
    with pytest.raises(ValueError, match="ordering"):
        qualifier.scenario(fixture, "controlled_free")


def test_negative_control_must_really_corrupt_the_reused_pages(fixture_factory):
    fixture = fixture_factory(enabled=True)
    fixture.poison = lambda _: None
    with pytest.raises(ValueError, match="bytes|unsafe"):
        qualifier.scenario(fixture, "negative_control")


@pytest.mark.parametrize("fault", ("duplicate", "bytes"))
def test_tagged_child_receipts_preserve_unique_ownership_and_exact_work(fixture_factory, fault):
    fixture = fixture_factory(enabled=True)
    submit = fixture.submit_loads

    def broken_receipts():
        ack = submit()
        if not ack.child_receipts:
            return ack
        child = ack.child_receipts[0]
        children = (
            (child, child) if fault == "duplicate"
            else (child._replace(num_bytes=child.num_bytes - 1),)
        )
        return ack._replace(child_receipts=children)

    fixture.submit_loads = broken_receipts
    with pytest.raises(ValueError, match="receipts"):
        qualifier.scenario(fixture, "controlled_free")


@pytest.fixture
def report(fixture_factory):
    rows = {
        name: qualifier.scenario(fixture_factory(enabled=name != "disabled_free"), name)
        for name in qualifier.SCENARIOS
    }
    return {
        "status": "native_free_page_fence_components_qualified",
        "native_engine": {
            "actual_tree": "engine", "expected_tree": "engine", "matches_worktree": True,
        },
        "native_compatibility": {
            "policy": "opt_in_preexisting_free_page_h2d_fence",
            "source_engine_tree": json.loads((qualifier.ROOT / qualifier.CONFIG).read_bytes())["engine_candidate"]["base_tree"],
            "candidate_engine_tree": "engine",
            "native_method_projection_matches": True, "same_proof_applies_to_native_demand": True,
        },
        "owned_gpu_pid": 12345, "gpu": "CPU test report template, not GPU evidence",
        "native_patch_sha256": "patch",
        "source_sha256": {name: sha256_file(qualifier.ROOT / name) for name in qualifier.SOURCES},
        "dimensions": {"device_tokens": 128, "host_tokens": 257, "layers": 2, "bytes_per_token": 1024},
        "scenarios": rows, **dict.fromkeys(qualifier.REQUIRED, True),
        "whole_model_qualified": False, "model_numerical_equivalence_verified": False,
        "performance_accepted": False,
    }


def test_complete_report_remains_a_component_check_without_model_or_performance_claims(report):
    qualifier.validate_report(report, engine_tree="engine", patch_sha256="patch")


@pytest.mark.parametrize("change", (
    "engine", "engine_mismatch", "compatibility", "native_proof", "gpu_pid", "gpu",
    "source", "missing_case", "missing_property", "model", "performance",
    "missing_ordering", "early_waited", "bad_bytes", "no_negative_corruption", "unreused",
    "ack_bytes", "child_receipt", "leaked_device", "leaked_host", "missing_event", "wrong_fallback",
))
def test_stale_or_incomplete_component_evidence_is_not_accepted(report, change):
    result = deepcopy(report)
    if change == "engine":
        result["native_engine"]["actual_tree"] = "other"
    elif change == "engine_mismatch":
        result["native_engine"]["matches_worktree"] = False
    elif change == "compatibility":
        del result["native_compatibility"]
    elif change == "native_proof":
        result["native_compatibility"]["same_proof_applies_to_native_demand"] = False
    elif change == "gpu_pid":
        result["owned_gpu_pid"] = False
    elif change == "gpu":
        result["gpu"] = ""
    elif change == "source":
        result["source_sha256"][qualifier.SOURCES[0]] = "stale"
    elif change == "missing_case":
        del result["scenarios"]["native_free"]
    elif change == "missing_property":
        result[qualifier.REQUIRED[0]] = False
    elif change in ("model", "performance"):
        result["whole_model_qualified" if change == "model" else "performance_accepted"] = True
    else:
        key, field, value = {
            "missing_ordering": ("controlled_free", "writer_finished_at_copy_ack", None),
            "early_waited": ("controlled_free", "writer_finished_at_copy_ack", True),
            "bad_bytes": ("native_free", "restored_bytes_match_after_writer_completion", False),
            "no_negative_corruption": ("negative_control", "restored_bytes_match_after_writer_completion", True),
            "unreused": ("reclaimed", "actual_reused_writer_pages", False),
            "ack_bytes": ("mixed_proof", "controller_h2d_bytes", 8192),
            "child_receipt": ("native_free", "controlled_child_receipts", 1),
            "leaked_device": ("reclaimed", "final_device_free_tokens", 127),
            "leaked_host": ("reclaimed", "final_host_free_tokens", 0),
            "missing_event": ("controlled_free", "fence_counts", {}),
            "wrong_fallback": ("mixed_proof", "fence_counts", {"free_boundary_events": 1, "free_event_batches": 1}),
        }[change]
        result["scenarios"][key][field] = value
    with pytest.raises(ValueError, match="native fence"):
        qualifier.validate_report(result, engine_tree="engine", patch_sha256="patch")


@pytest.mark.parametrize("when", ("before_fixed_deadline", "extended_hold", "pause", "foreign_gpu"))
def test_gpu_guard_precedes_all_pool_allocation(monkeypatch, tmp_path, when):
    hold = tmp_path / "hold.json"
    hold.write_text(json.dumps({
        "automatic_resume_allowed": True,
        "gpu_not_before": "2026-10-27T11:00:00+08:00",
    }))
    monkeypatch.setattr(qualifier, "HOLD", hold)
    pause = tmp_path / "paused"
    monkeypatch.setattr(qualifier.controls.fresh, "PAUSE", pause)
    at = datetime(2026, 10, 27, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
    if when == "before_fixed_deadline":
        at = at.replace(hour=10)
    elif when == "extended_hold":
        hold.write_text(json.dumps({
            "automatic_resume_allowed": True,
            "gpu_not_before": "2026-10-27T13:00:00+08:00",
        }))
    elif when == "pause":
        pause.touch()
    monkeypatch.setattr(qualifier.controls.reservation, "now", lambda: at)
    monkeypatch.setattr(qualifier, "gpu_processes", lambda: [999999] if when == "foreign_gpu" else [])
    create = Mock(side_effect=AssertionError("pool allocation must never occur"))
    monkeypatch.setattr(qualifier, "NativeFenceFixture", create)
    with pytest.raises(ValueError, match="reserved|reservation|occupied"):
        qualifier.qualify(tmp_path, tmp_path / "not_read.patch")
    create.assert_not_called()


def test_a_new_hold_during_a_scenario_stops_before_a_writer_is_started(fixture_factory):
    fixture = fixture_factory(enabled=True)
    count = [0]

    def pulse():
        count[0] += 1
        if count[0] == 2:
            raise ValueError("new user GPU hold")

    with pytest.raises(ValueError, match="new user"):
        qualifier.scenario(fixture, "controlled_free", pulse=pulse)
    assert fixture.writer_done is None
