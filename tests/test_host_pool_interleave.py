"""CPU coverage for the explicit larger, balanced Host capacity experiment."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from beliefkv.experiments import host_numa as numa
from scripts import run_causal_comparison as comparison
from scripts.preflight_qwen35_host_pool import GB, GIB, check_host_pool
from scripts.qualify_interleaved_host_pool import configuration, copy_offsets
from scripts.prepare_host_pool_pressure import pressure_configuration, pressure_protocol
from beliefkv.experiments.replay_arrivals import (
    permuted_poisson_arrival_plan, substitute_background_arrivals,
)
from beliefkv.experiments.workflow_cohort import validate_cohort
from scripts.review_predictive_thunder_cohort import matching_traffic, TRAFFIC_FIELDS
from tests.test_causal_comparison import reference
from tests.test_host_numa import COMMON, PAGE, START, observed
from tests.test_qwen35_host_pool import LAUNCH, _node, _stub


INTERLEAVE = {
    **COMMON, "HOST_NUMA_MEMORY_POLICY": "interleave",
    "HOST_NUMA_MEMORY_NODES": "0,1", "HICACHE_SIZE_GB": "400",
}
SIZE = 400000057344


def nodes(tmp_path, *, first=210, second=210):
    root = _node(
        tmp_path, node_id=0, total_kb=250 * GIB // 1024, free_kb=first * GIB // 1024,
    )
    _node(root, node_id=1, total_kb=250 * GIB // 1024, free_kb=second * GIB // 1024)
    return root


def mapping(size=SIZE):
    pages = (size + PAGE - 1) // PAGE
    return (
        f"{START:x}-{START + pages * PAGE:x} rw-s 00000000 00:01 1 /dev/zero (deleted)\n",
        f"{START:x} interleave:0-1 file=/dev/zero\\040(deleted) dirty={pages} "
        f"N0={pages // 2} N1={pages - pages // 2} kernelpagesize_kB={PAGE // 1024}\n",
    )


def test_400_gb_needs_two_individually_sufficient_nodes(tmp_path):
    root = nodes(tmp_path)
    budget, available = check_host_pool(
        1, 400, node_root=root, memory_policy="interleave", memory_nodes="0,1",
    )
    assert budget == 400 * GB
    assert available == 420 * GIB
    with pytest.raises(ValueError, match="between 1 and 200"):
        check_host_pool(1, 400, node_root=root, memory_policy="preferred")
    with pytest.raises(ValueError, match="between 1 and 200"):
        check_host_pool(1, 400, node_root=root, memory_policy="bind")


def test_aggregate_free_memory_cannot_hide_one_interleave_node_shortage(tmp_path):
    root = nodes(tmp_path, first=250, second=170)
    with pytest.raises(ValueError, match="interleave NUMA node 1"):
        check_host_pool(1, 400, node_root=root, memory_policy="interleave", memory_nodes="0,1")


@pytest.mark.parametrize("value", ("", "1", "1,0", "0,0,1", "00,1", "0,1,", "0-1", "0,2", "x,1"))
def test_interleave_mask_is_explicit_canonical_and_contains_cpu_node(tmp_path, value):
    with pytest.raises(ValueError):
        check_host_pool(
            1, 400, node_root=tmp_path, memory_policy="interleave", memory_nodes=value,
        )


def test_interleave_preflight_bounds_capacity_and_rejects_unrelated_mask(tmp_path):
    for size in (0, True, 401):
        with pytest.raises(ValueError):
            check_host_pool(1, size, node_root=tmp_path, memory_policy="interleave", memory_nodes="0,1")
    with pytest.raises(ValueError, match="require interleave"):
        check_host_pool(1, 200, node_root=tmp_path, memory_nodes="0,1")


def test_interleaved_clean_file_credit_still_excludes_shared_and_dirty_memory(tmp_path):
    root = nodes(tmp_path, first=170, second=170)
    for owner in (0, 1):
        path = root / f"node{owner}/meminfo"
        with path.open("a") as stream:
            stream.write(
                f"Node {owner} FilePages: {40 * GIB // 1024} kB\n"
                f"Node {owner} Active(file): {40 * GIB // 1024} kB\n"
            )
    check_host_pool(
        1, 400, node_root=root, memory_policy="interleave",
        memory_nodes="0,1", reclaim_policy="clean_file",
    )
    with (root / "node1/meminfo").open("a") as stream:
        stream.write(f"Node 1 Shmem: {40 * GIB // 1024} kB\n")
    with pytest.raises(ValueError, match="node 1"):
        check_host_pool(
            1, 400, node_root=root, memory_policy="interleave",
            memory_nodes="0,1", reclaim_policy="clean_file",
        )


def launcher_environment(tmp_path):
    env = {
        **_stub(tmp_path), **INTERLEAVE,
        "BELIEFKV_HOST_REGISTER_NUMA_FALLBACK_NODE": "",
    }
    (Path(env["MODEL_PATH"]) / "config.json").write_text(json.dumps({
        "model_type": "qwen3_moe", "use_sliding_window": False, "sliding_window": None,
    }))
    return env


def test_launcher_preserves_cpu_node_and_explicit_balanced_400_gb_policy(tmp_path):
    env = launcher_environment(tmp_path)
    result = subprocess.run(["bash", str(LAUNCH)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    args = (tmp_path / "numactl-args").read_text().splitlines()
    assert args[:2] == ["--cpunodebind=1", "--interleave=0,1"]
    assert args[args.index("--hicache-size") + 1] == "400"
    preflight = (tmp_path / "preflight-args").read_text().splitlines()
    assert preflight[preflight.index("--memory-nodes") + 1] == "0,1"
    assert preflight[preflight.index("--size-gb") + 1] == "400"
    assert (tmp_path / "auto-numa").read_text() == "0"


@pytest.mark.parametrize("overrides", (
    {"HICACHE_SIZE_GB": "401"}, {"HOST_NUMA_MEMORY_NODES": ""},
    {"MODEL_CACHE_LAYOUT": "hybrid"},
    {"BELIEFKV_HOST_REGISTER_NUMA_FALLBACK_NODE": "1"},
    {"HOST_NUMA_MEMORY_POLICY": "bind"}, {"HOST_NUMA_MEMORY_POLICY": "preferred"},
))
def test_larger_launcher_rejects_undeclared_placement_or_capacity(tmp_path, overrides):
    env = {**launcher_environment(tmp_path), **overrides}
    result = subprocess.run(["bash", str(LAUNCH)], env=env, capture_output=True, text=True)
    assert result.returncode == 2
    assert not (tmp_path / "numactl-args").exists()


def test_explicit_interleave_configuration_reaches_preflight_without_local_fallback():
    selected = numa.configuration(INTERLEAVE)
    assert selected["require_balanced_pool"]
    assert not selected["require_fully_local_pool"]
    assert selected["memory_nodes"] == [0, 1]
    assert numa.requires_placement(INTERLEAVE)
    assert numa.environment(INTERLEAVE)["HOST_NUMA_MEMORY_NODES"] == "0,1"
    assert numa.environment(INTERLEAVE)["BELIEFKV_HOST_REGISTER_NUMA_FALLBACK_NODE"] == ""
    assert numa.expected_full_bytes(INTERLEAVE) == SIZE
    assert numa.preflight_arguments(INTERLEAVE) == [
        "--memory-policy", "interleave", "--reclaim-policy", "clean_file",
        "--node", "1", "--size-gb", "400", "--memory-nodes", "0,1",
    ]
    assert numa.environment(COMMON)["HOST_NUMA_MEMORY_NODES"] == ""


@pytest.mark.parametrize("overrides", (
    {"HICACHE_SIZE_GB": "399"}, {"HOST_NUMA_MEMORY_NODES": "1,0"},
    {"HOST_NUMA_REGISTRATION_POLICY": "preferred"},
    {"BELIEFKV_HOST_REGISTER_NUMA_FALLBACK_NODE": "1"},
))
def test_new_capacity_cannot_inherit_old_local_qualification(overrides):
    with pytest.raises(ValueError):
        numa.configuration({**INTERLEAVE, **overrides})


@pytest.mark.parametrize("arm", ("full", "native_matched", "thunderagent_acting_decay"))
def test_every_method_receives_same_400_gb_with_unchanged_running_cap(reference, tmp_path, arm):
    reference["arms"]["full"].update(INTERLEAVE, HOST_SPLIT="auto", MAX_RUNNING_REQUESTS="24")
    settings, _, _ = comparison.environment(
        reference, arm, tmp_path, tmp_path / "engine", tmp_path / arm,
        18998, tmp_path / "plugin", max_running_requests=24,
    )
    assert settings["HICACHE_SIZE_GB"] == "400"
    assert settings["MAX_RUNNING_REQUESTS"] == "24"
    assert settings["HOST_NUMA_MEMORY_POLICY"] == "interleave"
    assert settings["HOST_NUMA_MEMORY_NODES"] == "0,1"


def test_capacity_change_preserves_capture_and_every_hbm_field():
    captured = {
        "device_full_bytes": 79154282496, "device_full_tokens": 805198,
        "device_mamba_bytes": 0, "device_mamba_slots": 0, "device_total_bytes": 79154282496,
        "host_full_bytes": numa.LEGACY_HOST_FULL_BYTES, "host_full_tokens": 2034506,
        "host_mamba_bytes": 0, "host_mamba_slots": 0,
        "host_total_bytes": numa.LEGACY_HOST_FULL_BYTES, "pool_layout": "static_full_only",
    }
    before = deepcopy(captured)
    enlarged = numa.replay_capacity(INTERLEAVE, captured)
    assert captured == before
    assert enlarged["host_full_bytes"] == enlarged["host_total_bytes"] == SIZE
    assert enlarged["host_full_tokens"] == 4069011
    assert {k for k in captured if captured[k] != enlarged[k]} == {
        "host_full_bytes", "host_full_tokens", "host_total_bytes",
    }
    assert numa.replay_capacity(COMMON, captured) == captured
    assert numa.server_parameters(INTERLEAVE, {"hicache_size": 200, "page_size": 1}) == {
        "hicache_size": 400, "page_size": 1,
    }
    with pytest.raises(ValueError, match="captured 200"):
        numa.replay_capacity(INTERLEAVE, enlarged)


@pytest.mark.parametrize("defect", (None, "imbalance", "partial", "mask", "large_pages", "foreign_node"))
def test_actual_mapping_requires_complete_balanced_residency(defect):
    maps, pages = mapping()
    half = SIZE // PAGE // 2
    if defect == "imbalance":
        pages = pages.replace(f"N0={half} N1={half}", f"N0={half - 1} N1={half + 1}")
    elif defect == "partial":
        pages = pages.replace(f"N0={half}", f"N0={half - 1}")
    elif defect == "mask":
        pages = pages.replace("interleave:0-1", "prefer:1")
    elif defect == "large_pages":
        pages = pages.replace(f"kernelpagesize_kB={PAGE // 1024}", "kernelpagesize_kB=2048")
    elif defect == "foreign_node":
        pages = pages.replace("N0=", "N2=")
    if defect:
        with pytest.raises(ValueError):
            numa.inspect_mapping(
                maps, pages, expected_bytes=SIZE, node=1, page_bytes=PAGE, memory_nodes=[0, 1],
            )
    else:
        proof = numa.inspect_mapping(
            maps, pages, expected_bytes=SIZE, node=1, page_bytes=PAGE, memory_nodes=[0, 1],
        )
        assert proof["fully_interleaved_pool_verified"]
        assert proof["remote_pool_fraction"] == .5
        assert not proof["fully_local_pool_verified"]


def test_actual_placement_is_required_at_startup_and_complete_review(observed):
    directory, proc, scheduler, topology = observed
    worker = proc / str(scheduler["pid"])
    maps, pages = mapping()
    (worker / "maps").write_text(maps)
    (worker / "numa_maps").write_text(pages)
    census = directory / "server/native_capacity_census.json"
    census.write_text(json.dumps({"capacity": {
        "host_full_bytes": SIZE, "host_total_bytes": SIZE, "host_mamba_bytes": 0,
    }}))
    proof = numa.observe(INTERLEAVE, scheduler, directory, proc_root=proc, topology=topology)
    registration = {
        "host_numa_configuration": numa.configuration(INTERLEAVE),
        "host_numa_observation": proof,
    }
    numa.verify_qualified_configuration(INTERLEAVE, registration)
    with pytest.raises(ValueError):
        numa.verify_qualified_configuration(INTERLEAVE, {})
    (directory / "server/host_numa_placement.json").write_text(json.dumps(proof))
    plan = {"reference_environment": INTERLEAVE, "host_numa_configuration": numa.configuration(INTERLEAVE)}
    assert numa.verify_completed_configuration(plan, directory)["configuration"] == numa.configuration(INTERLEAVE)
    changed = deepcopy(registration)
    changed["host_numa_observation"]["remote_pool_bytes"] = 0
    with pytest.raises(ValueError, match="balanced"):
        numa.verify_qualified_configuration(INTERLEAVE, changed)


def test_pair_cannot_mix_target_capacity_contracts():
    shared = {name: {"fixed": name} for name in TRAFFIC_FIELDS}
    first = {**shared, "order": ["full"], "reference_environment": INTERLEAVE}
    second = {**shared, "order": ["thunderagent_acting_decay"], "reference_environment": INTERLEAVE}
    matching_traffic(first, second)
    first["expected_replay_capacity"] = {"host_full_bytes": SIZE}
    with pytest.raises(ValueError, match="capacity experiment"):
        matching_traffic(first, second)


def test_component_configuration_and_every_actual_registration_boundary():
    assert configuration(400, 1, "0,1") == {
        key: value for key, value in INTERLEAVE.items() if key != "HOST_NUMA_REGISTRATION_POLICY"
    }
    assert copy_offsets(1024, 10000, [(10000, 512), (10512, 512)]) == [0, 448, 896]
    for ranges in ([], [(10001, 1024)], [(10000, 500)], [(10000, 500), (10512, 512)], [(10000, 0)]):
        with pytest.raises(ValueError):
            copy_offsets(1024, 10000, ranges)


@pytest.mark.parametrize("substitute", (False, True))
def test_pressure_protocol_preserves_complete_work_order_and_redeclares_fixed_cohort(substitute):
    original = permuted_poisson_arrival_plan(
        [f"w{i}" for i in range(73)], rate_per_second=1. / 70.,
        seed=35, permutation_seed=47, initial_workflows=12,
    )
    arrivals = original
    if substitute:
        arrivals = substitute_background_arrivals(
            original, [{"old_workflow_id": original["arrival_schedule"][-1]["workflow_id"], "new_workflow_id": "new"}],
            preserved_prefix_workflows=30,
        )
    unchanged = deepcopy(arrivals)
    following, cohort = pressure_protocol(arrivals)
    assert arrivals == unchanged
    assert [r["workflow_id"] for r in following["arrival_schedule"]] == [
        r["workflow_id"] for r in arrivals["arrival_schedule"]
    ]
    assert following["initial_workflows"] == 20
    assert all(row["offset_seconds"] == 0 for row in following["arrival_schedule"][:20])
    assert following["rate_per_second"] == arrivals["rate_per_second"]
    assert len(cohort["warmup_workflow_ids"]) == 20
    assert len(cohort["measured_workflow_ids"]) == 18
    assert len(cohort["background_workflow_ids"]) == 35
    assert not cohort["cohort_identity_available_to_scheduler"]
    validate_cohort(cohort, following)


def test_pressure_configuration_retains_agent_and_migration_policy(tmp_path):
    arrival, cohort = (tmp_path / filename for filename in ("arrivals.json", "cohort.json"))
    arrival.write_text("{}")
    cohort.write_text("{}")
    previous = {
        "order": ["full"], "common_environment": {
            **COMMON, "HICACHE_SIZE_GB": "200", "MAX_RUNNING_REQUESTS": "24",
            "BELIEFKV_ENABLE_PREDICTED_TOOL_OFFLOAD": "1", "JOIN_PREFETCH_TRIGGER": "observed_eos",
            "HOST_NUMA_REGISTRATION_POLICY": "preferred",
        },
    }
    before = deepcopy(previous)
    prepared = pressure_configuration(previous, arrival, cohort)
    assert previous == before
    assert prepared["common_environment"]["HICACHE_SIZE_GB"] == "400"
    assert prepared["common_environment"]["HOST_NUMA_REGISTRATION_POLICY"] == "strict"
    for key in ("MAX_RUNNING_REQUESTS", "BELIEFKV_ENABLE_PREDICTED_TOOL_OFFLOAD", "JOIN_PREFETCH_TRIGGER"):
        assert prepared["common_environment"][key] == before["common_environment"][key]
    assert not prepared["gpu_execution"]["registered"]
