"""Exercise the pinned native Mamba match rules on CPU without a serving run."""

import ast
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace as NS


SOURCE = Path(__file__).resolve().parents[1] / (
    "third_party/sglang-v0.5.20/python/sglang/srt/mem_cache/unified_cache/components/mamba.py"
)


def native_mamba_methods():
    tree = ast.parse(SOURCE.read_text())
    component = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MambaComponent"
    )
    methods = [
        node for node in component.body if isinstance(node, ast.FunctionDef)
        and node.name in (
            "create_match_validator", "finalize_match_result_in_tree_core",
            "resolve_session_leaf",
        )
    ]
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        ast.ClassDef(name="NativeMamba", bases=[], keywords=[], body=methods, decorator_list=[]),
    ], type_ignores=[])
    namespace = {}
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)
    instance = namespace["NativeMamba"]()
    instance.component_type = 2
    instance.mamba_checkpoint_grid = 4
    instance.has_host_value_only = lambda node: (
        node.component_data[2].value is None and node.component_data[2].host_value is not None
    )
    return instance


def test_tagged_session_keeps_existing_input_checkpoint_not_output_tail():
    component = native_mamba_methods()
    root = NS(parent=None)
    prompt = NS(
        parent=root, key=list(range(8)),
        component_data=[None, None, NS(value=None, host_value=object())],
    )
    output = NS(
        parent=prompt, key=list(range(4)),
        component_data=[None, None, NS(value=object(), host_value=None)],
    )
    component.tree_core = NS(root_node=root)
    component._find_reusable_session_leaf = lambda node: output
    req = NS(beliefkv_metadata={"context_id": "parent"}, origin_input_ids=list(range(10)))
    assert component.resolve_session_leaf(req, output) is prompt
    prompt.component_data[2].host_value = None
    assert component.resolve_session_leaf(req, output) is output
    req.beliefkv_metadata = None
    assert component.resolve_session_leaf(req, output) is output


def test_full_match_cannot_cross_a_missing_native_mamba_checkpoint():
    component = native_mamba_methods()
    partial_input_node = NS(component_data=[
        NS(value=list(range(12)), host_value=None), None,
        NS(value=None, host_value=None),
    ])
    output_tail = NS(component_data=[
        NS(value=list(range(4)), host_value=None), None,
        NS(value=object(), host_value=object()),
    ])
    validate = component.create_match_validator()
    assert validate(output_tail)
    assert not validate(partial_input_node)
    Result = namedtuple("Result", (
        "device_indices host_hit_length full_kv_hit_length best_match_node "
        "mamba_branching_seqlen mamba_host_hit_length"
    ))
    result = component.finalize_match_result_in_tree_core(
        Result([], 0, 12, partial_input_node, None, 0),
        params=None, value_chunks=[], best_value_len=0,
    )
    assert result.full_kv_hit_length == 12
    assert len(result.device_indices) == 0
    assert result.mamba_branching_seqlen == 12
    partial_input_node.component_data[2].host_value = object()
    assert validate(partial_input_node)
