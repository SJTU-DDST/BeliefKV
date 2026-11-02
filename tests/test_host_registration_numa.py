from collections import defaultdict
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.experiments import causal_replay_native as bridge, host_numa
from beliefkv.runtime import host_registration_numa as registration
from tests.test_host_numa import COMMON, SIZE, START, observed


@pytest.mark.parametrize("value,expected", (("", None), ("0", 0), ("1", 1)))
def test_registration_fallback_is_explicitly_optional(monkeypatch, value, expected):
    monkeypatch.setenv(registration.NODE_ENV, value)
    assert registration.configured_node() == expected


@pytest.mark.parametrize("value", ("-1", "01", "yes", "1,0", " 1", "\uff11"))
def test_registration_fallback_requires_one_canonical_node(monkeypatch, value):
    monkeypatch.setenv(registration.NODE_ENV, value)
    with pytest.raises(ValueError, match="canonical"):
        registration.configured_node()


class Policy:
    def __init__(self):
        self.current = {"mode": 2, "nodes": [1]}
        self.writes = []

    def read(self):
        return deepcopy(self.current)

    def set(self, value):
        self.writes.append(deepcopy(value))
        self.current = deepcopy(value)


@pytest.mark.parametrize("native_fails", (False, True))
def test_native_failure_and_success_restore_the_exact_original_policy(monkeypatch, native_fails):
    policy = Policy()
    monkeypatch.setattr(registration, "LinuxMemoryPolicy", lambda: policy)
    state = None
    try:
        with registration.registration_policy(1) as state:
            assert policy.read() == {"mode": 1, "nodes": [1]}
            if native_fails:
                raise LookupError("native registration failed")
    except LookupError:
        assert native_fails
    assert state["before_policy"] == state["after_policy"] == {"mode": 2, "nodes": [1]}
    assert state["during_policy"] == {"mode": 1, "nodes": [1]}
    assert policy.writes == [
        {"mode": 1, "nodes": [1]}, {"mode": 2, "nodes": [1]},
    ]


@pytest.mark.parametrize("original", (
    {"mode": 0, "nodes": []}, {"mode": 1, "nodes": [1]},
    {"mode": 2, "nodes": [0]}, {"mode": 2, "nodes": [0, 1]},
))
def test_wrong_original_policy_cannot_be_silently_replaced(monkeypatch, original):
    policy = Policy()
    policy.current = original
    monkeypatch.setattr(registration, "LinuxMemoryPolicy", lambda: policy)
    with pytest.raises(ValueError, match="existing exact local bind"):
        with registration.registration_policy(1):
            pytest.fail("wrong original policy was accepted")
    assert not policy.writes


def test_failure_to_apply_preference_still_restores_the_bind(monkeypatch):
    policy = Policy()
    original = policy.set

    def fail_preference(value):
        original(value)
        if value["mode"] == 1:
            raise OSError("policy syscall failed")

    monkeypatch.setattr(policy, "set", fail_preference)
    monkeypatch.setattr(registration, "LinuxMemoryPolicy", lambda: policy)
    with pytest.raises(OSError, match="policy syscall"):
        with registration.registration_policy(1):
            pytest.fail("failed policy change reached CUDA")
    assert policy.current == {"mode": 2, "nodes": [1]}


@pytest.mark.parametrize("raises", (False, True))
def test_failure_to_restore_the_bind_cannot_return_success(monkeypatch, raises):
    policy = Policy()
    original = policy.set

    def fail_restore(value):
        if value["mode"] == 2:
            if raises:
                raise OSError("restore syscall failed")
            return
        original(value)

    monkeypatch.setattr(policy, "set", fail_restore)
    monkeypatch.setattr(registration, "LinuxMemoryPolicy", lambda: policy)
    with pytest.raises((OSError, RuntimeError), match="restore"):
        with registration.registration_policy(1):
            pass


def test_real_linux_thread_policy_is_preferred_only_inside_the_context():
    root = Path(__file__).resolve().parents[1]
    script = """
import json
from beliefkv.runtime.host_registration_numa import LinuxMemoryPolicy, registration_policy
policy = LinuxMemoryPolicy()
before = policy.read()
with registration_policy(1) as evidence:
    during = policy.read()
after = policy.read()
print(json.dumps([before, during, after, evidence]))
"""
    result = subprocess.run(
        ["numactl", "--membind=1", sys.executable, "-c", script],
        cwd=root, env={**os.environ, "PYTHONPATH": str(root), "CUDA_VISIBLE_DEVICES": "99999"},
        capture_output=True, text=True, check=True, timeout=20,
    )
    before, during, after, evidence = json.loads(result.stdout)
    assert before == after == {"mode": 2, "nodes": [1]}
    assert during == {"mode": 1, "nodes": [1]}
    assert evidence["after_policy"] == before


def test_default_native_registration_has_no_extra_policy_calls_or_audit(monkeypatch):
    monkeypatch.delenv(registration.NODE_ENV, raising=False)
    monkeypatch.setattr(registration, "LinuxMemoryPolicy", Mock(side_effect=AssertionError))
    original, buffer = Mock(return_value="native"), object()
    assert registration.host_register(original, buffer, 4096) == "native"
    original.assert_called_once_with(buffer, 4096)


@pytest.mark.parametrize("native_fails", (False, True))
def test_actual_plugin_hook_calls_native_registration_and_records_restoration(
    monkeypatch, tmp_path, native_fails,
):
    from sglang.srt.mem_cache.pool_host import common
    from sglang.srt.plugins.hook_registry import HookRegistry

    policy = Policy()
    monkeypatch.setattr(registration, "LinuxMemoryPolicy", lambda: policy)
    monkeypatch.setenv(registration.NODE_ENV, "1")
    monkeypatch.setenv("BELIEFKV_NATIVE_TELEMETRY_DIR", str(tmp_path))
    monkeypatch.setenv(bridge.BRIDGE_ENV, "0")
    monkeypatch.setattr(HookRegistry, "_hooks", defaultdict(list))
    monkeypatch.setattr(HookRegistry, "_patched", set())
    buffer = NS(data_ptr=lambda: START, numel=lambda: SIZE, element_size=lambda: 1)
    calls = []

    def native(buffer_arg, *, registration_granularity_bytes):
        assert buffer_arg is buffer
        assert policy.read() == {"mode": 1, "nodes": [1]}
        calls.append(registration_granularity_bytes)
        if native_fails:
            raise RuntimeError("native CUDA error")
        return "native"

    monkeypatch.setattr(common, "_cuda_host_register", native)
    bridge.register()
    assert set(HookRegistry._hooks) == {registration.TARGET}
    HookRegistry.apply_hooks()
    if native_fails:
        with pytest.raises(RuntimeError, match="native CUDA error"):
            common._cuda_host_register(buffer, registration_granularity_bytes=4096)
    else:
        assert common._cuda_host_register(buffer, registration_granularity_bytes=4096) == "native"
    assert calls == [4096]
    assert policy.current == {"mode": 2, "nodes": [1]}
    record = json.loads((tmp_path / registration.AUDIT_NAME).read_text())
    assert record["status"] == ("failed" if native_fails else "completed")
    assert record["buffer_bytes"] == SIZE and record["buffer_address"] == START
    assert record["before_policy"] == record["after_policy"] == policy.current
    assert record["pid"] == os.getpid()


def registration_record():
    return {
        "pid": 42, "worker_start_ticks": "100", "status": "completed", "node": 1,
        "buffer_address": START, "buffer_bytes": SIZE,
        "before_policy": {"mode": 2, "nodes": [1]},
        "during_policy": {"mode": 1, "nodes": [1]},
        "after_policy": {"mode": 2, "nodes": [1]},
        "implementation_sha256": registration.implementation_sha256(),
    }


@pytest.mark.parametrize("defect", (
    None, "pid", "worker_start_ticks", "status", "node", "buffer_address",
    "buffer_bytes", "before_policy", "during_policy", "after_policy",
    "implementation_sha256", "duplicate",
))
def test_pool_placement_requires_the_matching_completed_native_registration(observed, defect):
    directory, proc, scheduler, topology = observed
    common = {**COMMON, "HOST_NUMA_REGISTRATION_POLICY": "preferred"}
    record = registration_record()
    if defect == "duplicate":
        rows = [record, record]
    else:
        if defect:
            record[defect] = None
        rows = [record]
    audit = directory / "server" / registration.AUDIT_NAME
    audit.write_text("".join(json.dumps(row) + "\n" for row in rows))
    if defect:
        with pytest.raises(ValueError, match="registration"):
            host_numa.observe(common, scheduler, directory, proc_root=proc, topology=topology)
    else:
        proof = host_numa.observe(common, scheduler, directory, proc_root=proc, topology=topology)
        host_numa.verify_qualified_configuration(common, {
            "host_numa_configuration": host_numa.configuration(common),
            "host_numa_observation": proof,
        })
        (directory / "server/host_numa_placement.json").write_text(json.dumps(proof))
        plan = {
            "reference_environment": common,
            "host_numa_configuration": host_numa.configuration(common),
        }
        assert str(audit.resolve()) in host_numa.verify_completed_configuration(
            plan, directory,
        )["inputs_sha256"]
        audit.write_text(audit.read_text() + "\n")
        with pytest.raises(ValueError, match="registration evidence changed"):
            host_numa.verify_completed_configuration(plan, directory)


def test_preferred_registration_cannot_relax_the_pool_binding_contract():
    with pytest.raises(ValueError, match="NUMA"):
        host_numa.configuration({
            "HOST_NUMA_MEMORY_POLICY": "preferred",
            "HOST_NUMA_REGISTRATION_POLICY": "preferred",
        })
    selected = {**COMMON, "HOST_NUMA_REGISTRATION_POLICY": "preferred"}
    assert host_numa.environment(selected)[registration.NODE_ENV] == "1"
    assert host_numa.environment(COMMON)[registration.NODE_ENV] == ""
