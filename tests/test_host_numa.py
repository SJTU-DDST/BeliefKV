from copy import deepcopy
import json
import os
from pathlib import Path
import shutil

import pytest

from beliefkv.experiments import host_numa as numa
from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as comparison
from scripts.review_predictive_thunder_cohort import matching_traffic, TRAFFIC_FIELDS
from tests.test_causal_comparison import reference


ROOT = Path(__file__).resolve().parents[1]
SIZE = 200000077824
PAGE = os.sysconf("SC_PAGE_SIZE")
START = 0x1000000000
COMMON = {
    "HOST_NUMA_NODE": "1", "HOST_NUMA_MEMORY_POLICY": "bind",
    "HOST_NUMA_RECLAIM_POLICY": "clean_file", "MODEL_CACHE_LAYOUT": "full_only",
}


def mapping():
    return (
        f"{START:x}-{START + SIZE:x} rw-s 00000000 00:01 1 /dev/zero (deleted)\n",
        f"{START:x} bind:1 file=/dev/zero\\040(deleted) dirty={SIZE // PAGE} "
        f"N1={SIZE // PAGE} kernelpagesize_kB={PAGE // 1024}\n",
    )


def test_historical_preferred_profile_needs_no_pool_or_extra_qualification(tmp_path):
    assert numa.configuration({})["memory_policy"] == "preferred"
    assert numa.observe({}, None, tmp_path / "absent") is None
    assert numa.verify_completed_configuration({}) is None
    numa.require_source_binding({}, {}, tmp_path)
    numa.verify_qualified_configuration({}, {})


@pytest.mark.parametrize("field,value", [
    ("HOST_NUMA_NODE", "-1"), ("HOST_NUMA_NODE", "01"),
    ("HOST_NUMA_NODE", True), ("HOST_NUMA_NODE", "x"),
    ("HOST_NUMA_MEMORY_POLICY", "interleave"), ("HOST_NUMA_RECLAIM_POLICY", "all"),
    ("HOST_NUMA_MEMORY_POLICY", "preferred"),
])
def test_invalid_or_nonlocal_cache_reclaim_contract_is_rejected(field, value):
    with pytest.raises(ValueError, match="NUMA"):
        numa.configuration({**COMMON, field: value})


@pytest.mark.parametrize("arm", ("full", "native_matched", "thunderagent_acting_decay"))
def test_strict_policy_reaches_every_actual_arm_without_changing_capacity(reference, tmp_path, arm):
    reference["arms"]["full"].update(COMMON, HOST_SPLIT="auto", MAX_RUNNING_REQUESTS="24")
    settings, _, _ = comparison.environment(
        reference, arm, tmp_path, tmp_path / "engine", tmp_path / arm,
        18998, tmp_path / "plugin", max_running_requests=24,
    )
    assert {name: settings[name] for name in numa.environment(COMMON)} == numa.environment(COMMON)
    assert settings["HICACHE_SIZE_GB"] == "200"
    assert settings["MAX_RUNNING_REQUESTS"] == "24"
    assert numa.preflight_arguments(COMMON) == [
        "--memory-policy", "bind", "--reclaim-policy", "clean_file",
        "--node", "1", "--size-gb", "200",
    ]


def test_actual_complete_local_mapping_is_verified_without_allocating_a_large_pool():
    maps, pages = mapping()
    result = numa.inspect_mapping(maps, pages, expected_bytes=SIZE, node=1, page_bytes=PAGE)
    assert result["fully_local_pool_verified"]
    assert result["pool_resident_bytes_by_node"] == {"1": SIZE}
    assert result["remote_pool_bytes"] == 0


@pytest.mark.parametrize("defect", ("remote", "partial", "preferred", "missing", "page_size", "duplicate"))
def test_partial_remote_unbound_or_ambiguous_mapping_cannot_qualify(defect):
    maps, pages = mapping()
    if defect == "remote":
        pages = pages.replace(f"N1={SIZE // PAGE}", f"N0=1 N1={SIZE // PAGE - 1}")
    elif defect == "partial":
        pages = pages.replace(f"N1={SIZE // PAGE}", f"N1={SIZE // PAGE - 1}")
    elif defect == "preferred":
        pages = pages.replace("bind:1", "prefer:1")
    elif defect == "missing":
        pages = ""
    elif defect == "page_size":
        pages = pages.replace(f"kernelpagesize_kB={PAGE // 1024}", "kernelpagesize_kB=2048")
    else:
        maps += maps
    with pytest.raises(ValueError):
        numa.inspect_mapping(maps, pages, expected_bytes=SIZE, node=1, page_bytes=PAGE)


@pytest.fixture
def observed(tmp_path):
    directory = tmp_path / "arm"
    (directory / "server").mkdir(parents=True)
    capacity = {
        "host_full_bytes": SIZE, "host_total_bytes": SIZE, "host_mamba_bytes": 0,
    }
    (directory / "server/native_capacity_census.json").write_text(json.dumps({"capacity": capacity}))
    (directory / "server/native_telemetry_ready.json").write_text('{"scheduler_pid":42}')
    proc = tmp_path / "proc"
    worker = proc / "42"
    worker.mkdir(parents=True)
    maps, pages = mapping()
    (worker / "maps").write_text(maps)
    (worker / "numa_maps").write_text(pages)
    (worker / "stat").write_text("42 (worker) S " + " ".join(["0"] * 18 + ["100", "0"]))
    scheduler = {"pid": 42, "start_ticks": "100"}
    topology = "GPU0 X 32-63,96-127 1 N/A\n"
    return directory, proc, scheduler, topology


@pytest.mark.parametrize("defect", (None, "GPU_node", "worker_reused", "capacity"))
def test_observed_pool_is_bound_to_actual_worker_GPU_and_full_capacity(observed, defect):
    directory, proc, scheduler, topology = observed
    if defect == "GPU_node":
        topology = topology.replace(" 1 N/A", " 0 N/A")
    elif defect == "worker_reused":
        scheduler["start_ticks"] = "101"
    elif defect == "capacity":
        path = directory / "server/native_capacity_census.json"
        value = json.loads(path.read_bytes())
        value["capacity"]["host_mamba_bytes"] = 1
        path.write_text(json.dumps(value))
    if defect:
        with pytest.raises(ValueError):
            numa.observe(COMMON, scheduler, directory, proc_root=proc, topology=topology)
    else:
        proof = numa.observe(COMMON, scheduler, directory, proc_root=proc, topology=topology)
        assert proof["scheduler"] == scheduler
        assert proof["GPU_local_NUMA_node"] == 1
        assert proof["fully_local_pool_verified"]


@pytest.mark.parametrize("defect", (None, "old_smoke", "remote", "different_node", "capacity_changed"))
def test_gpu_qualification_and_complete_review_require_actual_placement(observed, defect):
    directory, proc, scheduler, topology = observed
    proof = numa.observe(COMMON, scheduler, directory, proc_root=proc, topology=topology)
    registration = {
        "host_numa_configuration": numa.configuration(COMMON), "host_numa_observation": proof,
    }
    if defect == "old_smoke":
        registration.pop("host_numa_observation")
    elif defect == "remote":
        proof["remote_pool_bytes"] = PAGE
    elif defect == "different_node":
        registration["host_numa_configuration"] = {**numa.configuration(COMMON), "node": 0}
    if defect in ("old_smoke", "remote", "different_node"):
        with pytest.raises(ValueError, match="qualification"):
            numa.verify_qualified_configuration(COMMON, registration)
        return
    numa.verify_qualified_configuration(COMMON, registration)
    (directory / "server/host_numa_placement.json").write_text(json.dumps(proof))
    if defect == "capacity_changed":
        path = directory / "server/native_capacity_census.json"
        path.write_text(path.read_text() + "\n")
    plan = {"reference_environment": COMMON, "host_numa_configuration": numa.configuration(COMMON)}
    if defect:
        with pytest.raises(ValueError, match="completed Host"):
            numa.verify_completed_configuration(plan, directory)
    else:
        result = numa.verify_completed_configuration(plan, directory)
        assert result["configuration"] == numa.configuration(COMMON)


def test_strict_configuration_requires_its_current_CPU_bound_sources(tmp_path):
    with pytest.raises(ValueError, match="CPU-bound"):
        numa.require_source_binding(COMMON, {}, tmp_path)
    names = (
        "beliefkv/experiments/host_numa.py", "tests/test_host_numa.py",
        "scripts/preflight_qwen35_host_pool.py", "tests/test_qwen35_host_pool.py",
    )
    for name in names:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    bound = {name: sha256_file(tmp_path / name) for name in names}
    numa.require_source_binding(COMMON, bound, tmp_path)


def test_paired_review_cannot_mix_preferred_and_strict_local_allocation():
    shared = {name: {"fixed": name} for name in TRAFFIC_FIELDS}
    full = {**shared, "order": ["full"], "reference_environment": COMMON}
    thunder = {**shared, "order": ["thunderagent_acting_decay"], "reference_environment": deepcopy(COMMON)}
    matching_traffic(full, thunder)
    thunder["reference_environment"] = {}
    with pytest.raises(ValueError, match="NUMA"):
        matching_traffic(full, thunder)
