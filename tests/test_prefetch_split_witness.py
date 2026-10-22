"""Reproduce v2 witness undercount with the selected native FULL split method."""

from array import array
import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import torch

from beliefkv.runtime.v0520_native_telemetry import NativeReactiveTelemetry


def native_full_split():
    checkout = Path(os.environ.get(
        "SGLANG_SOURCE_CHECKOUT",
        Path(__file__).resolve().parents[1] / "third_party/sglang-v0.5.20",
    ))
    path = checkout / "python/sglang/srt/mem_cache/unified_cache/components/full.py"
    owner = next(
        node for node in ast.parse(path.read_bytes()).body
        if isinstance(node, ast.ClassDef) and node.name == "FullComponent"
    )
    method = next(
        node for node in owner.body if isinstance(node, ast.FunctionDef)
        and node.name == "redistribute_on_node_split"
    )
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        ast.ClassDef(name="Native", bases=[], keywords=[], body=[method], decorator_list=[]),
    ], type_ignores=[])
    namespace = {}
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    instance = namespace["Native"]()
    instance.component_type = 0
    return instance


@pytest.mark.parametrize("mode", (
    "unchanged", "split_full_prefix", "split_partial_prefix", "replaced_allocation",
))
def test_v2_negative_witness_cannot_distinguish_split_from_replaced_slots(tmp_path, mode):
    cuda_initialized = torch.cuda.is_initialized()
    slots = torch.arange(100, 110, dtype=torch.int64)
    root = NS(id=0, creation_time=0, parent=None, key=array("q"), component_data={0: NS(value=[])})
    leaf = NS(
        id=11, creation_time=7, parent=root, key=array("q", range(10)),
        component_data={0: NS(
            value=slots, host_value=None, lock_ref=0, session_ref=1, session_ids={"s"},
        )},
    )
    nodes = {0: root, 11: leaf}
    audit = NativeReactiveTelemetry(tmp_path / "service")
    audit._cache = NS(tree_core=NS(node_by_id=nodes.__getitem__))
    audit.on_verified_action_ack(NS(
        command_id="prefetch", action="PREFETCH_GPU", context_id="ctx", context_epoch=2,
        node_ids=(11,), pool_bytes=(("kv", 10240),), num_bytes=10240,
    ))
    try:
        if mode.startswith("split_"):
            parent = NS(
                id=12, creation_time=7, parent=root, key=array("q", range(4)),
                component_data={0: NS(
                    value=None, host_value=None, lock_ref=0, session_ref=0, session_ids=None,
                )},
            )
            leaf.parent, leaf.key = parent, array("q", range(4, 10))
            nodes[12] = parent
            native_full_split().redistribute_on_node_split(parent, leaf)
            full_indices = torch.cat((parent.component_data[0].value, leaf.component_data[0].value))
            assert torch.equal(full_indices, slots)
            assert leaf.component_data[0].value is not slots
        else:
            if mode == "replaced_allocation":
                leaf.component_data[0].value = torch.arange(200, 210, dtype=torch.int64)
            full_indices = leaf.component_data[0].value
        used = parent.component_data[0].value if mode == "split_partial_prefix" else full_indices
        request = NS(
            rid="next", last_node=12 if mode == "split_partial_prefix" else 11,
            origin_input_ids=list(range(20)), prefix_indices=used,
            cached_tokens_device=0, cached_tokens_host=len(used),
        )
        audit._record_prefetch_first_service(request, {"context_id": "ctx", "context_epoch": 3})
    finally:
        audit.close()
    records = [
        json.loads(line)
        for line in (tmp_path / "service/physical_action_use.jsonl").read_text().splitlines()
    ]
    [witness] = [row for row in records if row["event"] == "beliefkv_prefetch_first_service"]
    consumed = len(set(slots.tolist()) & set(used.tolist()))
    assert witness["full_node_reused"] is (mode == "unchanged")
    assert consumed == {
        "unchanged": 10, "split_full_prefix": 10, "split_partial_prefix": 4,
        "replaced_allocation": 0,
    }[mode]
    assert torch.cuda.is_initialized() == cuda_initialized
