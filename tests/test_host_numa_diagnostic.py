"""NUMA observations do not gate the explicit larger-pool diagnostic configuration."""

import json
import subprocess

import pytest

from beliefkv.experiments import host_numa as numa
from scripts import run_causal_comparison as comparison
from scripts.preflight_qwen35_host_pool import check_host_pool, GIB
from tests.test_causal_comparison import reference
from tests.test_host_numa import PAGE, observed
from tests.test_host_pool_interleave import INTERLEAVE, SIZE, mapping, nodes, launcher_environment
from tests.test_qwen35_host_pool import LAUNCH


DIAGNOSTIC = {**INTERLEAVE, "HOST_NUMA_PLACEMENT_CHECK": "diagnostic"}


def test_capacity_and_allocation_policy_are_independent_of_placement_acceptance():
    selected = numa.configuration(DIAGNOSTIC)
    assert not selected["require_balanced_pool"]
    assert not numa.requires_placement(DIAGNOSTIC)
    assert selected["memory_nodes"] == [0, 1]
    assert numa.expected_full_bytes(DIAGNOSTIC) == SIZE
    assert numa.environment(DIAGNOSTIC)["HOST_NUMA_MEMORY_NODES"] == "0,1"
    assert numa.environment(DIAGNOSTIC)["HOST_NUMA_PLACEMENT_CHECK"] == "diagnostic"
    assert numa.preflight_arguments(DIAGNOSTIC)[-2:] == ["--placement-check", "diagnostic"]
    assert numa.server_parameters(DIAGNOSTIC, {"hicache_size": 200})["hicache_size"] == 400
    captured = {
        "pool_layout": "static_full_only", "host_full_bytes": numa.LEGACY_HOST_FULL_BYTES,
        "host_total_bytes": numa.LEGACY_HOST_FULL_BYTES, "host_full_tokens": 2034506,
        "host_mamba_bytes": 0, "host_mamba_slots": 0, "device_full_tokens": 805198,
    }
    assert numa.replay_capacity(DIAGNOSTIC, captured) == numa.replay_capacity(INTERLEAVE, captured)
    numa.verify_qualified_configuration(DIAGNOSTIC, {})
    assert numa.verify_completed_configuration({"reference_environment": DIAGNOSTIC}) is None


@pytest.mark.parametrize("arm", ("full", "native_matched", "thunderagent_acting_decay"))
def test_diagnostic_configuration_keeps_400_gb_in_each_method(reference, tmp_path, arm):
    reference["arms"]["full"].update(DIAGNOSTIC, HOST_SPLIT="auto", MAX_RUNNING_REQUESTS="24")
    settings, _, _ = comparison.environment(
        reference, arm, tmp_path, tmp_path / "engine", tmp_path / arm,
        18998, tmp_path / "plugin", max_running_requests=24,
    )
    assert settings["HICACHE_SIZE_GB"] == "400"
    assert settings["HOST_NUMA_PLACEMENT_CHECK"] == "diagnostic"
    assert settings["MAX_RUNNING_REQUESTS"] == "24"


@pytest.mark.parametrize("defect", ("skew", "partial", "unavailable"))
def test_skew_incomplete_counters_and_missing_observations_cannot_block_serving(observed, defect):
    directory, proc, scheduler, _topology = observed
    capacity = directory / "server/native_capacity_census.json"
    capacity.write_text(json.dumps({"capacity": {
        "host_full_bytes": SIZE, "host_total_bytes": SIZE, "host_mamba_bytes": 0,
    }}))
    maps, pages = mapping()
    half = SIZE // PAGE // 2
    if defect == "skew":
        pages = pages.replace(f"N0={half} N1={half}", f"N0=1 N1={half * 2 - 1}")
    elif defect == "partial":
        pages = pages.replace(f"N0={half}", f"N0={half - 130}")
    (proc / "42/maps").write_text(maps)
    (proc / "42/numa_maps").write_text(pages)
    if defect == "unavailable":
        (proc / "42/numa_maps").unlink()
    assert numa.observe(DIAGNOSTIC, scheduler, directory, proc_root=proc) is None
    path, = (directory / "server").glob("host_numa_diagnostic_*.json")
    record = json.loads(path.read_bytes())
    assert record["placement_is_an_acceptance_gate"] is False
    if defect == "unavailable":
        assert "observation_error" in record
    elif defect == "partial":
        assert record["reported_resident_counter_gap_bytes"] == 130 * PAGE
    else:
        assert record["reported_resident_bytes_by_node"]["0"] == PAGE
    assert not list((directory / "server").glob("host_numa_observation_*"))


def test_diagnostic_budget_accepts_uneven_free_memory_but_still_needs_total_capacity(tmp_path):
    root = nodes(tmp_path, first=250, second=170)
    assert check_host_pool(
        1, 400, node_root=root, memory_policy="interleave", memory_nodes="0,1",
        placement_check="diagnostic",
    ) == (400 * 10**9, 420 * GIB)
    with pytest.raises(ValueError, match="node 1"):
        check_host_pool(1, 400, node_root=root, memory_policy="interleave", memory_nodes="0,1")
    short = tmp_path / "short"
    short.mkdir()
    with pytest.raises(ValueError, match="total Host"):
        check_host_pool(
            1, 400, node_root=nodes(short, first=190, second=180),
            memory_policy="interleave", memory_nodes="0,1", placement_check="diagnostic",
        )


def test_launcher_passes_diagnostic_budget_without_changing_capacity_or_cpu_affinity(tmp_path):
    env = {**launcher_environment(tmp_path), "HOST_NUMA_PLACEMENT_CHECK": "diagnostic"}
    result = subprocess.run(["bash", str(LAUNCH)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "numactl-args").read_text().splitlines()[:2] == [
        "--cpunodebind=1", "--interleave=0,1",
    ]
    arguments = (tmp_path / "preflight-args").read_text().splitlines()
    assert arguments[arguments.index("--placement-check") + 1] == "diagnostic"
    assert arguments[arguments.index("--size-gb") + 1] == "400"
