"""CPU execution of the actual native checkpoint and pressure-backup methods."""

import ast
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1] / "third_party/sglang-v0.5.20/python/sglang/srt"


def native_method(file, cls, method, namespace):
    source = ROOT / file
    tree = ast.parse(source.read_text())
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == cls)
    body = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == method)
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        ast.ClassDef(name="Native", bases=[], keywords=[], body=[body], decorator_list=[]),
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(source), "exec"), namespace)
    return namespace["Native"]()


@pytest.mark.parametrize("tagged,expected", [(True, 11264), (False, 11328)])
def test_aligned_prefill_tracks_the_safe_reentry_state(tagged, expected):
    batch = native_method(
        "managers/schedule_batch.py", "ScheduleBatch",
        "_mamba_radix_cache_v2_req_prepare_for_extend",
        {
            "mamba_cache_chunk_size": lambda: 64,
            "mamba_checkpoint_grid": lambda page: 64,
            "get_exec": lambda: NS(mamba=NS(enable_mamba_extra_buffer_lazy=False)),
            "_MambaRadixCacheV2TrackEntry": namedtuple("Track", "track_mask track_index track_seqlen"),
        },
    )
    batch.model_config = NS(hf_text_config=NS(mamba_chunk_size=64))
    batch.tree_cache = NS(page_size=1)
    batch.req_to_token_pool = NS(get_mamba_ping_pong_other_idx=lambda index: 1-index)
    request = NS(
        beliefkv_metadata={} if tagged else None,
        origin_input_ids=list(range(11328)), prefix_indices=list(range(8192)),
        extend_range=NS(length=3136), mamba_branching_seqlen=None,
        kv=NS(
            mamba_ping_pong_track_buffer=[NS(item=lambda: 10), NS(item=lambda: 11)],
            mamba_next_track_idx=0,
        ),
    )
    entry = batch._mamba_radix_cache_v2_req_prepare_for_extend(request)
    assert request.kv.mamba_last_track_seqlen == expected
    assert entry.track_index == 10
    assert entry.track_seqlen == (11265 if tagged else 11328)


@pytest.mark.parametrize("backed,live,leaf", [(False, True, False), (True, True, False),
                                          (False, False, False), (False, True, True)])
def test_only_live_unbacked_interior_pressure_victims_get_preserved(backed, live, leaf):
    cache = native_method(
        "mem_cache/unified_radix_cache.py", "UnifiedRadixCache",
        "_preserve_mamba_reentry_candidates",
        {"ComponentType": NS(MAMBA=2), "BackupKV": lambda ids: NS(node_ids=ids)},
    )
    node = NS(
        id=7, component_data=[None, None, NS(
            value=object(), host_value=object() if backed else None,
            session_ref=int(live), lock_ref=0,
        )],
        write_through_pending_id=None, load_back_pending_id=None,
    )
    cache.is_write_back = True
    cache.enable_session_radix_cache = True
    cache.host_memory_mode = "cache"
    cache.cache_controller = object()
    cache.tree_components = (0, 2)
    cache.tree_core = NS(
        lru_lists={2: NS(get_lru_no_lock=lambda: node, get_prev_no_lock=lambda current: None)},
        node_by_id=lambda node_id: node,
        evictable_device_leaves=[node] if leaf else [],
    )
    cache.ongoing_write_through = {}
    cache._execute_and_commit_kv_backup = Mock(
        side_effect=lambda *args, **kwargs: cache.ongoing_write_through.update({7: True}),
    )
    cache.writing_check = Mock()
    cache._preserve_mamba_reentry_candidates(1)
    expected = not backed and live and not leaf
    assert cache._execute_and_commit_kv_backup.call_count == int(expected)
    assert cache.writing_check.call_count == int(expected)
    if expected:
        assert cache._execute_and_commit_kv_backup.call_args.kwargs["no_host_reclaim"] is True
        assert node.component_data[2].value is not None


def test_finished_request_keeps_real_prefill_anchor_when_decode_added_no_checkpoint():
    cache = native_method(
        "mem_cache/unified_radix_cache.py", "UnifiedRadixCache",
        "_finished_session_anchor", {},
    )
    cache.tree_core = NS(root_node=NS(id=0))
    req = NS(beliefkv_metadata={}, last_node=11, kv=NS(cache_protected_len=8192))
    assert cache._finished_session_anchor(req, NS(last_device_node=0)) == 11
    assert cache._finished_session_anchor(req, NS(last_device_node=12)) == 12
    req.kv.cache_protected_len = 0
    assert cache._finished_session_anchor(req, NS(last_device_node=0)) == 0
    req.beliefkv_metadata = None
    assert cache._finished_session_anchor(req, NS(last_device_node=0)) == 0
