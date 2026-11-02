from collections import defaultdict
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from beliefkv.experiments import causal_replay_native as bridge
from beliefkv.runtime import host_registered_copy as copy


def host_pool(page=1):
    buffer = torch.empty((2, 24, 2, 8), dtype=torch.float32)
    base, row = buffer.data_ptr(), 64
    # K and V cross different registration boundaries.
    setattr(buffer, copy.RANGES, [(base + i * 640, 640) for i in range(4)] + [(base + 2560, 512)])
    return SimpleNamespace(
        kv_buffer=buffer, k_buffer=buffer[0], v_buffer=buffer[1], page_size=page,
        layout="page_first", can_use_write_back_jit=True,
    )


def test_K_and_V_boundaries_are_both_respected_without_changing_work():
    pool, calls = host_pool(), []
    host = torch.tensor([2, 3, 9, 10, 11, 15, 16, 17, 19, 20, 23])
    device = torch.arange(host.numel())
    original = torch.arange(device.numel() * 32, dtype=torch.float32).reshape(device.numel(), 2, 2, 8)

    def native(owner, source, selected_host, selected_device, backend):
        assert owner is pool and source is original and backend == "kernel"
        for view in (owner.k_buffer, owner.v_buffer):
            low = view.data_ptr() + int(selected_host.min()) * 64
            high = view.data_ptr() + (int(selected_host.max()) + 1) * 64
            assert any(start <= low < high <= start + size for start, size in getattr(owner.kv_buffer, copy.RANGES))
        pool.k_buffer[selected_host] = source[selected_device, 0]
        pool.v_buffer[selected_host] = source[selected_device, 1]
        calls.append((selected_host.clone(), selected_device.clone()))

    copy.backup(native, pool, original, host, device, "kernel")
    assert len(calls) > 1
    assert torch.equal(torch.cat([row[0] for row in calls]), host)
    assert torch.equal(torch.cat([row[1] for row in calls]), device)
    assert torch.equal(pool.k_buffer[host], original[:, 0].float())
    assert torch.equal(pool.v_buffer[host], original[:, 1].float())
    assert getattr(pool, copy.STATS)["native_submissions"] == len(calls)


@pytest.mark.parametrize("indices", ([0, 1, 2], [9, 10, 19, 20, 9], [23, 22, 10, 9, 0]))
def test_unsorted_and_fragmented_indices_preserve_native_submission_order(indices):
    pool, calls = host_pool(), []
    host = torch.tensor(indices)
    device = torch.arange(len(indices)) + 50
    copy.backup(
        lambda owner, source, h, d, backend: calls.append((h.tolist(), d.tolist())),
        pool, object(), host, device, "kernel",
    )
    assert [value for h, _d in calls for value in h] == indices
    assert [value for _h, d in calls for value in d] == device.tolist()
    for h, _d in calls:
        regions = np.searchsorted(copy.token_cuts(pool), h, side="right")
        assert len(set(regions)) == 1


@pytest.mark.parametrize("changed", ("layout", "backend", "no_jit", "one_registration", "empty"))
def test_other_native_paths_and_non_crossing_work_are_not_expanded(changed):
    pool, calls = host_pool(), []
    host, device, backend = torch.tensor([1, 2]), torch.tensor([5, 6]), "kernel"
    if changed == "layout":
        pool.layout = "layer_first"
    elif changed == "backend":
        backend = "direct"
    elif changed == "no_jit":
        pool.can_use_write_back_jit = False
    elif changed == "one_registration":
        setattr(pool.kv_buffer, copy.RANGES, [(pool.kv_buffer.data_ptr(), 3072)])
    elif changed == "empty":
        host, device = host[:0], device[:0]
    copy.backup(
        lambda *args: calls.append(args), pool, object(), host, device, backend,
    )
    assert len(calls) == 1 and calls[0][2] is host and calls[0][3] is device


def test_whole_pages_are_kept_together():
    pool, calls = host_pool(page=2), []
    host, device = torch.arange(8, 20), torch.arange(12)
    copy.backup(
        lambda _owner, _source, h, d, _backend: calls.append((h.clone(), d.clone())),
        pool, object(), host, device, "kernel",
    )
    assert all(h.numel() % 2 == 0 and int(h[0]) % 2 == 0 for h, _d in calls)
    assert torch.equal(torch.cat([h for h, _d in calls]), host)


@pytest.mark.parametrize("defect", ("gap", "short", "page_split"))
def test_incomplete_or_page_splitting_registration_cannot_reach_native_copy(defect):
    pool = host_pool()
    ranges = list(getattr(pool.kv_buffer, copy.RANGES))
    if defect == "gap":
        ranges[1] = (ranges[1][0] + 64, ranges[1][1])
    elif defect == "short":
        ranges[-1] = (ranges[-1][0], ranges[-1][1] - 64)
    else:
        ranges[0] = (ranges[0][0], ranges[0][1] + 1)
        ranges[1] = (ranges[1][0] + 1, ranges[1][1] - 1)
    setattr(pool.kv_buffer, copy.RANGES, ranges)
    with pytest.raises(ValueError, match="Host registration"):
        copy.token_cuts(pool)


def test_actual_bridge_registers_optional_copy_hook_even_without_replay(monkeypatch):
    from sglang.srt.plugins.hook_registry import HookRegistry

    monkeypatch.setenv(copy.ENV, "1")
    monkeypatch.setenv(bridge.BRIDGE_ENV, "0")
    monkeypatch.setattr(HookRegistry, "_hooks", defaultdict(list))
    bridge.register()
    assert copy.TARGET in HookRegistry._hooks
