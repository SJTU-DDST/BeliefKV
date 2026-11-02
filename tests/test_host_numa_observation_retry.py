"""Retain raw placement failures and require a complete subsequent observation."""

import json
from pathlib import Path

import pytest

from beliefkv.experiments import host_numa as numa
from tests.test_host_numa import COMMON, PAGE, SIZE, mapping, observed


@pytest.mark.parametrize("eventually_complete", (False, True))
def test_only_a_complete_read_can_finish_bounded_residency_rechecks(
    observed, monkeypatch, eventually_complete,
):
    directory, proc, scheduler, topology = observed
    maps, complete = mapping()
    partial = complete.replace(f"N1={SIZE // PAGE}", f"N1={SIZE // PAGE - 8}")
    original = Path.read_bytes
    reads, sleeps = [], []

    def read(path):
        if path == proc / "42/numa_maps":
            reads.append(True)
            return (complete if eventually_complete and len(reads) > 1 else partial).encode()
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(numa.time, "sleep", sleeps.append)
    if eventually_complete:
        proof = numa.observe(COMMON, scheduler, directory, proc_root=proc, topology=topology)
        assert proof["pool_resident_bytes_by_node"] == {"1": SIZE}
        assert len(proof["placement_observation_attempts"]) == 2
    else:
        with pytest.raises(ValueError, match="complete Host pool must be resident"):
            numa.observe(COMMON, scheduler, directory, proc_root=proc, topology=topology)
    observation, = (directory / "server").glob("host_numa_observation_*")
    records = sorted(observation.glob("attempt_*.json"))
    assert len(records) == len(reads) == (2 if eventually_complete else 3)
    assert len(sleeps) == len(reads) - 1
    assert (observation / "attempt_1.maps.txt").read_text() == maps
    assert (observation / "attempt_1.numa_maps.txt").read_text() == partial
    first = json.loads(records[0].read_bytes())
    assert first["error"] == "the complete Host pool must be resident"
    assert all(Path(item["path"]).is_file() for item in first["snapshots"].values())
    last = json.loads(records[-1].read_bytes())
    assert bool(last.get("complete_residency_verified")) is eventually_complete


def test_wrong_placement_policy_is_preserved_and_rejected_without_retry(observed, monkeypatch):
    directory, proc, scheduler, topology = observed
    path = proc / "42/numa_maps"
    path.write_text(path.read_text().replace("bind:1", "prefer:1"))
    sleeps = []
    monkeypatch.setattr(numa.time, "sleep", sleeps.append)
    with pytest.raises(ValueError, match="strictly bound"):
        numa.observe(COMMON, scheduler, directory, proc_root=proc, topology=topology)
    observation, = (directory / "server").glob("host_numa_observation_*")
    assert len(list(observation.glob("attempt_*.json"))) == 1
    assert "prefer:1" in (observation / "attempt_1.numa_maps.txt").read_text()
    assert not sleeps


def test_residency_retry_cannot_accept_a_reused_worker_identity(observed, monkeypatch):
    directory, proc, scheduler, topology = observed
    path = proc / "42/numa_maps"
    path.write_text(path.read_text().replace(f"N1={SIZE // PAGE}", f"N1={SIZE // PAGE - 1}"))

    def replace_worker(_delay):
        stat = proc / "42/stat"
        stat.write_text(stat.read_text().replace("100", "101"))
        path.write_text(mapping()[1])

    monkeypatch.setattr(numa.time, "sleep", replace_worker)
    with pytest.raises(ValueError, match="worker identity changed"):
        numa.observe(COMMON, scheduler, directory, proc_root=proc, topology=topology)
