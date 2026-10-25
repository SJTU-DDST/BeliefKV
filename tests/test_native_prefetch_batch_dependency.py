"""Exercise the real native batch-binding path without importing CUDA."""

import ast
from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest


def native_types():
    checkout = os.environ.get("SGLANG_SOURCE_CHECKOUT")
    if not checkout:
        pytest.skip("set SGLANG_SOURCE_CHECKOUT to the qualified native engine")
    package = Path(checkout) / "python/sglang/srt"
    cache_path = package / "mem_cache/unified_radix_cache.py"
    cache_class = next(
        node for node in ast.parse(cache_path.read_text()).body
        if isinstance(node, ast.ClassDef) and node.name == "UnifiedRadixCache"
    )
    methods = [
        deepcopy(node) for node in cache_class.body
        if isinstance(node, ast.FunctionDef) and node.name in (
            "ready_to_load_host_cache", "beliefkv_prefill_load_consumer",
        )
    ]
    scheduler_path = package / "managers/scheduler.py"
    scheduler_class = next(
        node for node in ast.parse(scheduler_path.read_text()).body
        if isinstance(node, ast.ClassDef) and node.name == "Scheduler"
    )
    binding = next(
        deepcopy(node) for method in scheduler_class.body
        if isinstance(method, ast.FunctionDef) for node in method.body
        if isinstance(node, ast.If)
        and any(
            isinstance(target, ast.Attribute) and target.attr == "hicache_consumer_index"
            for child in ast.walk(node) if isinstance(child, ast.Assign) for target in child.targets
        )
    )
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        ast.ClassDef(name="Cache", bases=[], keywords=[], body=methods, decorator_list=[]),
        ast.FunctionDef(
            name="bind", args=ast.arguments(
                posonlyargs=[], args=[ast.arg(arg="self"), ast.arg(arg="new_batch")],
                kwonlyargs=[], kw_defaults=[], defaults=[],
            ), body=[binding], decorator_list=[],
        ),
    ], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"BASE_COMPONENT_TYPE": 0, "ComponentType": NS(FULL=0)}
    exec(compile(module, str(scheduler_path), "exec"), namespace)
    return namespace["Cache"], namespace["bind"]


def fixture():
    Cache, bind = native_types()
    root = NS(id=0, parent=None, component_data=[NS(value=None)], load_back_pending_id=None)
    resident = NS(id=1, parent=root, component_data=[NS(value=[1, 2])], load_back_pending_id=None)
    unrelated = NS(id=2, parent=root, component_data=[NS(value=[3, 4])], load_back_pending_id=9)
    cache = Cache()
    cache.linker = cache.buffer_pipeline = None
    cache.tree_components = (0,)
    nodes = {node.id: node for node in (root, resident, unrelated)}
    cache.tree_core = NS(root_node=root, node_by_id=nodes.__getitem__)
    cache.ongoing_load_back = {unrelated.id: object()}
    cache._beliefkv_prefetch_consumer_index = 2
    cache.cache_controller = NS(
        start_loading=lambda: -1, load_queue=[], write_policy="write_back",
        layer_done_counter=NS(events={2: NS(finish_event=NS(query=lambda: False))}),
    )
    request = NS(last_node=resident.id)
    batch = NS(reqs=[request], hicache_consumer_index=-1)
    scheduler = NS(
        tree_cache=cache, enable_hierarchical_cache=True,
        enable_unified_cache_external_linker=False,
    )
    return cache, resident, request, batch, lambda: bind(scheduler, batch)


def test_resident_prefill_does_not_inherit_an_unrelated_speculative_load_fence():
    _cache, _resident, _request, batch, bind = fixture()
    bind()
    assert batch.hicache_consumer_index == -1


def test_actual_pending_prefix_keeps_the_native_layer_dependency():
    cache, resident, _request, batch, bind = fixture()
    resident.load_back_pending_id = 9
    cache.ongoing_load_back[resident.id] = object()
    bind()
    assert batch.hicache_consumer_index == 2


@pytest.mark.parametrize("guard", (
    "missing_anchor", "unknown_anchor", "missing_value", "ongoing_without_marker",
    "pending_shared_ancestor", "mixed_ready_and_pending", "hybrid", "buffer",
    "unknown_request_list", "cycle", "write_through",
))
def test_uncertain_or_required_dependencies_keep_the_original_fence(guard):
    cache, resident, request, batch, bind = fixture()
    if guard == "missing_anchor":
        request.last_node = None
    elif guard == "unknown_anchor":
        request.last_node = 999
    elif guard == "missing_value":
        resident.component_data[0].value = None
    elif guard == "ongoing_without_marker":
        cache.ongoing_load_back[resident.id] = object()
    elif guard == "pending_shared_ancestor":
        resident.parent = NS(
            id=9, parent=cache.tree_core.root_node,
            component_data=[NS(value=[8])], load_back_pending_id=9,
        )
    elif guard == "mixed_ready_and_pending":
        batch.reqs.append(NS(last_node=2))
    elif guard == "hybrid":
        cache.tree_components = (0, 2)
    elif guard == "buffer":
        cache.buffer_pipeline = object()
    elif guard == "unknown_request_list":
        batch.reqs = None
    elif guard == "cycle":
        resident.parent = resident
    elif guard == "write_through":
        cache.cache_controller.write_policy = "write_through"
    bind()
    assert batch.hicache_consumer_index == 2
    assert not getattr(cache, "beliefkv_prefetch_unrelated_waits_skipped", 0)


def test_a_cold_first_prefill_can_run_while_an_unrelated_prefetch_continues():
    cache, _resident, request, batch, bind = fixture()
    request.last_node = cache.tree_core.root_node.id
    bind()
    assert batch.hicache_consumer_index == -1
    assert cache.ongoing_load_back


def test_resident_prefix_d2h_read_and_native_locks_are_not_released():
    cache, resident, _request, batch, bind = fixture()
    resident.write_through_pending_id = 17
    resident.component_data[0].lock_ref = 2
    bind()
    assert batch.hicache_consumer_index == -1
    assert resident.write_through_pending_id == 17
    assert resident.component_data[0].lock_ref == 2
    assert cache.beliefkv_prefetch_unrelated_waits_skipped == 1


def test_ordinary_native_load_producer_is_unchanged_without_speculative_prefetch():
    cache, _resident, _request, batch, bind = fixture()
    del cache._beliefkv_prefetch_consumer_index
    cache.cache_controller.start_loading = lambda: 1
    bind()
    assert batch.hicache_consumer_index == 1
    assert not getattr(cache, "beliefkv_prefetch_dependency_checks", 0)
