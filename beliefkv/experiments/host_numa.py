"""Declare Host allocation placement and verify the actual complete FULL pool."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from beliefkv.experiments.source_provenance import sha256_file


FULL_BYTES_PER_TOKEN = 98304
LEGACY_HOST_FULL_BYTES = 200000077824
INTERLEAVE_IMBALANCE_DENOMINATOR = 100


def configuration(common):
    node = common.get("HOST_NUMA_NODE", "1")
    policy = common.get("HOST_NUMA_MEMORY_POLICY", "preferred")
    reclaim = common.get("HOST_NUMA_RECLAIM_POLICY", "conservative")
    registration = common.get("HOST_NUMA_REGISTRATION_POLICY", "strict")
    memory_nodes = common.get("HOST_NUMA_MEMORY_NODES", "")
    placement_check = common.get("HOST_NUMA_PLACEMENT_CHECK", "strict")
    if (
        type(node) is not str or not node.isascii() or not node.isdigit()
        or str(int(node)) != node or policy not in ("bind", "preferred", "interleave")
        or reclaim not in ("conservative", "clean_file")
        or reclaim == "clean_file" and policy not in ("bind", "interleave")
        or registration not in ("strict", "preferred")
        or registration == "preferred" and policy != "bind"
        or policy != "interleave" and memory_nodes != ""
        or placement_check not in ("strict", "diagnostic")
        or placement_check == "diagnostic" and policy != "interleave"
    ):
        raise ValueError("invalid explicit Host NUMA allocation configuration")
    result = {
        "node": int(node), "memory_policy": policy, "reclaim_policy": reclaim,
        "require_fully_local_pool": policy == "bind",
    }
    if registration == "preferred":
        result["registration_policy"] = registration
    if policy == "interleave":
        from scripts.preflight_qwen35_host_pool import interleave_nodes

        size = common.get("HICACHE_SIZE_GB", "")
        if (
            size not in ("200", "400") or common.get("MODEL_CACHE_LAYOUT") != "full_only"
            or common.get("BELIEFKV_HOST_REGISTER_NUMA_FALLBACK_NODE", "") != ""
        ):
            raise ValueError("interleaved Host NUMA experiment requires explicit 200/400 GB FULL-only pools")
        result.update(
            memory_nodes=list(interleave_nodes(memory_nodes, int(node))),
            pool_size_gb=int(size), require_balanced_pool=placement_check == "strict",
            maximum_node_page_imbalance_fraction=1. / INTERLEAVE_IMBALANCE_DENOMINATOR,
        )
        if placement_check == "diagnostic":
            result["placement_check"] = placement_check
            result.pop("maximum_node_page_imbalance_fraction")
    return result


def requires_placement(common):
    selected = configuration(common)
    return selected["require_fully_local_pool"] or selected.get("require_balanced_pool", False)


def expected_full_bytes(common):
    selected = configuration(common)
    if selected["memory_policy"] == "interleave":
        return (selected["pool_size_gb"] * 1_000_000_000 // FULL_BYTES_PER_TOKEN + 1) * FULL_BYTES_PER_TOKEN
    return LEGACY_HOST_FULL_BYTES


def replay_capacity(common, captured):
    """Declare a Host-only capacity change while preserving the captured HBM."""
    selected = configuration(common)
    result = dict(captured)
    if selected["memory_policy"] != "interleave":
        return result
    if (
        captured.get("pool_layout") != "static_full_only"
        or captured.get("host_full_bytes") != LEGACY_HOST_FULL_BYTES
        or captured.get("host_total_bytes") != LEGACY_HOST_FULL_BYTES
        or captured.get("host_full_tokens") != LEGACY_HOST_FULL_BYTES // FULL_BYTES_PER_TOKEN
        or captured.get("host_mamba_bytes") != 0 or captured.get("host_mamba_slots") != 0
    ):
        raise ValueError("interleaved replay requires the captured 200 GB FULL-only geometry")
    size = expected_full_bytes(common)
    result.update(host_full_bytes=size, host_total_bytes=size, host_full_tokens=size // FULL_BYTES_PER_TOKEN)
    return result


def environment(common):
    selected = configuration(common)
    result = {
        "HOST_NUMA_NODE": str(selected["node"]),
        "HOST_NUMA_MEMORY_POLICY": selected["memory_policy"],
        "HOST_NUMA_MEMORY_NODES": "",
        "HOST_NUMA_RECLAIM_POLICY": selected["reclaim_policy"],
        "BELIEFKV_HOST_REGISTER_NUMA_FALLBACK_NODE": (
            str(selected["node"]) if selected.get("registration_policy") == "preferred" else ""
        ),
    }
    if selected["memory_policy"] == "interleave":
        result["HOST_NUMA_MEMORY_NODES"] = ",".join(map(str, selected["memory_nodes"]))
    if selected.get("placement_check") == "diagnostic":
        result["HOST_NUMA_PLACEMENT_CHECK"] = "diagnostic"
    return result


def preflight_arguments(common):
    selected = configuration(common)
    result = [
        "--memory-policy", selected["memory_policy"],
        "--reclaim-policy", selected["reclaim_policy"],
        "--node", str(selected["node"]), "--size-gb", str(selected.get("pool_size_gb", 200)),
    ]
    if selected["memory_policy"] == "interleave":
        result.extend(["--memory-nodes", ",".join(map(str, selected["memory_nodes"]))])
    if selected.get("placement_check") == "diagnostic":
        result.extend(["--placement-check", "diagnostic"])
    return result


def server_parameters(common, captured):
    selected = configuration(common)
    result = dict(captured)
    if selected["memory_policy"] == "interleave":
        result["hicache_size"] = selected["pool_size_gb"]
    return result


def exact_mapping(maps, size):
    matches = []
    for line in maps.splitlines():
        fields = line.split(maxsplit=5)
        left, right = (int(value, 16) for value in fields[0].split("-"))
        if right - left == size and "s" in fields[1] and len(fields) == 6 and any(
            name in fields[5] for name in ("/dev/zero", "/dev/shm", "memfd:")
        ):
            matches.append({
                "start_address": fields[0].split("-")[0],
                "mapping_bytes": size, "raw_maps_line": line,
            })
    if len(matches) != 1:
        raise ValueError("Host NUMA proof requires one exact complete shared FULL mapping")
    return matches[0]


def inspect_mapping(maps, numa_maps, *, expected_bytes, node, page_bytes, memory_nodes=None):
    if (
        type(expected_bytes) is not int or expected_bytes <= 0
        or type(node) is not int or node < 0
        or type(page_bytes) is not int or page_bytes <= 0
    ):
        raise ValueError("Host NUMA proof requires known positive pool/page geometry")
    rounded = (expected_bytes + page_bytes - 1) // page_bytes * page_bytes
    mapping = exact_mapping(maps, rounded)
    lines = [
        line for line in numa_maps.splitlines()
        if line.split() and line.split()[0] == mapping["start_address"]
    ]
    if len(lines) != 1:
        raise ValueError("Host NUMA proof lacks an exact native mapping policy")
    fields = lines[0].split()
    values = dict(field.split("=", 1) for field in fields if "=" in field)
    interleaved = memory_nodes is not None
    if interleaved:
        from scripts.preflight_qwen35_host_pool import interleave_nodes

        nodes = interleave_nodes(",".join(map(str, memory_nodes)), node)
        policy_match = re.fullmatch(r"interleave:([0-9,-]+)", fields[1]) if len(fields) >= 2 else None
        actual_nodes = set()
        if policy_match:
            for part in policy_match[1].split(","):
                ends = part.split("-")
                if len(ends) == 1 and ends[0].isdigit():
                    actual_nodes.add(int(ends[0]))
                elif (
                    len(ends) == 2 and all(end.isdigit() for end in ends)
                    and int(ends[0]) <= int(ends[1]) <= max(nodes)
                ):
                    actual_nodes.update(range(int(ends[0]), int(ends[1]) + 1))
                else:
                    raise ValueError("invalid native interleave NUMA mask")
        policy_matches = actual_nodes == set(nodes)
    else:
        policy_matches = len(fields) >= 2 and fields[1] == f"bind:{node}"
    if not policy_matches or (
        int(values.get("kernelpagesize_kB", "0")) * 1024 != page_bytes
    ):
        raise ValueError("Host NUMA mapping is not strictly bound at the known page size")
    pages = {
        int(name[1:]): int(value) for name, value in values.items()
        if re.fullmatch(r"N\d+", name)
    }
    if not pages or any(count < 0 for count in pages.values()):
        raise ValueError("Host NUMA mapping lacks physical page residency")
    resident = {str(owner): count * page_bytes for owner, count in pages.items()}
    remote = sum(count for owner, count in resident.items() if int(owner) != node)
    if sum(resident.values()) != rounded:
        raise ValueError("the complete Host pool must be resident")
    if interleaved:
        if set(pages) != set(nodes) or (
            (max(pages.values()) - min(pages.values())) * INTERLEAVE_IMBALANCE_DENOMINATOR
            > sum(pages.values())
        ):
            raise ValueError("the complete Host pool must be evenly interleaved over the declared nodes")
    elif remote:
        raise ValueError("the complete Host pool must be resident on the selected NUMA node")
    result = {
        "mapping": mapping, "mapping_policy": fields[1],
        "raw_numa_maps_line": lines[0], "kernel_page_bytes": page_bytes,
        "pool_resident_bytes_by_node": resident, "remote_pool_bytes": remote,
        "remote_pool_fraction": remote / rounded, "fully_local_pool_verified": not interleaved,
    }
    if interleaved:
        result.update(
            fully_interleaved_pool_verified=True, declared_memory_nodes=list(nodes),
            maximum_node_page_imbalance_fraction=1. / INTERLEAVE_IMBALANCE_DENOMINATOR,
            actual_node_page_imbalance_fraction=(max(pages.values()) - min(pages.values())) / sum(pages.values()),
        )
    return result


def registration_evidence(path, scheduler, mapping, node):
    from beliefkv.runtime.host_registration_numa import implementation_sha256

    records = [json.loads(line) for line in path.read_text().splitlines() if line]
    matched = [row for row in records if (
        row.get("buffer_bytes") == mapping["mapping_bytes"]
        and row.get("buffer_address") == int(mapping["start_address"], 16)
    )]
    if len(matched) != 1:
        raise ValueError("Host NUMA registration proof lacks the exact complete native buffer")
    record = matched[0]
    if (
        record.get("pid") != scheduler["pid"]
        or record.get("worker_start_ticks") != scheduler["start_ticks"]
        or record.get("status") != "completed"
        or record.get("node") != node
        or record.get("implementation_sha256") != implementation_sha256()
        or record.get("before_policy") != {"mode": 2, "nodes": [node]}
        or record.get("during_policy") != {"mode": 1, "nodes": [node]}
        or record.get("after_policy") != {"mode": 2, "nodes": [node]}
    ):
        raise ValueError("Host registration policy or exact worker proof is incomplete")
    return {
        "complete_native_registration_and_original_bind_restoration_verified": True,
        "native_buffer_address": record["buffer_address"],
        "native_buffer_bytes": record["buffer_bytes"],
        "inputs_sha256": {str(path.resolve()): sha256_file(path)},
    }


def observe_diagnostic(selected, scheduler, directory, proc_root):
    record = {
        "configuration": selected, "scheduler": scheduler,
        "placement_is_an_acceptance_gate": False,
        "scope": "One best-effort NUMA observation; capacity and CUDA transfer checks are independent.",
    }
    target = Path(directory) / "server"
    try:
        capacity = json.loads((target / "native_capacity_census.json").read_bytes())["capacity"]
        size = capacity["host_full_bytes"]
        record["host_full_capacity_bytes"] = size
        proc = Path(proc_root) / str(scheduler["pid"])
        fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
        if fields[19] != scheduler["start_ticks"]:
            raise ValueError("NUMA diagnostic worker identity changed")
        maps, numa = (proc / "maps").read_text(), (proc / "numa_maps").read_text()
        page = os.sysconf("SC_PAGE_SIZE")
        mapping = exact_mapping(maps, (size + page - 1) // page * page)
        line, = (line for line in numa.splitlines() if line.split()[0] == mapping["start_address"])
        values = dict(field.split("=", 1) for field in line.split() if "=" in field)
        page_bytes = int(values.get("kernelpagesize_kB", "0")) * 1024
        resident = {
            name[1:]: int(value) * page_bytes
            for name, value in values.items() if re.fullmatch(r"N\d+", name)
        }
        total = sum(resident.values())
        record.update(
            mapping=mapping, raw_numa_maps_line=line, kernel_page_bytes=page_bytes,
            reported_resident_bytes_by_node=resident, reported_resident_bytes=total,
            reported_resident_counter_gap_bytes=size - total,
            reported_remote_fraction=(
                sum(value for owner, value in resident.items() if int(owner) != selected["node"]) / total
                if total else None
            ),
        )
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        record["observation_error"] = repr(error)
    try:
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"host_numa_diagnostic_{os.getpid()}_{time.monotonic_ns()}.json"
        with path.open("x") as stream:
            json.dump(record, stream)
            stream.write("\n")
    except OSError:
        pass


def observe(common, scheduler, directory, *, proc_root=Path("/proc"), topology=None):
    selected = configuration(common)
    if not requires_placement(common):
        if selected.get("placement_check") == "diagnostic":
            observe_diagnostic(selected, scheduler, directory, proc_root)
        return None
    capacity_path = Path(directory) / "server/native_capacity_census.json"
    capacity = json.loads(capacity_path.read_bytes())["capacity"]
    if (
        capacity.get("host_mamba_bytes") != 0
        or capacity.get("host_total_bytes") != capacity.get("host_full_bytes")
        or common.get("MODEL_CACHE_LAYOUT") != "full_only"
    ):
        raise ValueError("strict Host NUMA proof currently requires a static FULL-only pool")
    proc = Path(proc_root) / str(scheduler["pid"])
    def check_identity():
        fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
        if len(fields) < 20 or fields[19] != scheduler["start_ticks"]:
            raise ValueError("Host NUMA worker identity changed")
    observation = Path(directory) / "server" / f"host_numa_observation_{os.getpid()}_{time.monotonic_ns()}"
    observation.mkdir()
    attempts = []
    deadline = time.monotonic() + 30.
    for attempt in range(1, 17):
        check_identity()
        maps, numa = (proc / "maps").read_bytes(), (proc / "numa_maps").read_bytes()
        snapshots = {
            "maps": observation / f"attempt_{attempt}.maps.txt",
            "numa_maps": observation / f"attempt_{attempt}.numa_maps.txt",
        }
        for name, data in (("maps", maps), ("numa_maps", numa)):
            snapshots[name].write_bytes(data)
        record = {
            "attempt": attempt,
            "snapshots": {name: {"path": str(path), "sha256": sha256_file(path)}
                          for name, path in snapshots.items()},
        }
        attempts.append(record)
        try:
            result = inspect_mapping(
                maps.decode(), numa.decode(), expected_bytes=capacity["host_full_bytes"],
                node=selected["node"], page_bytes=os.sysconf("SC_PAGE_SIZE"),
                memory_nodes=selected.get("memory_nodes"),
            )
        except ValueError as error:
            record["error"] = str(error)
            (observation / f"attempt_{attempt}.json").write_text(json.dumps(record) + "\n")
            if (
                str(error) != "the complete Host pool must be resident"
                or attempt == 16 or time.monotonic() >= deadline
            ):
                raise
            time.sleep(.1)
        else:
            record["complete_residency_verified"] = True
            (observation / f"attempt_{attempt}.json").write_text(json.dumps(record) + "\n")
            break
    if capacity["host_full_bytes"] != expected_full_bytes(common):
        raise ValueError("actual Host pool differs from its explicitly declared capacity")
    if topology is None:
        topology = subprocess.check_output(["nvidia-smi", "topo", "-m"], text=True, timeout=15.)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", topology)
    devices = [line.split() for line in plain.splitlines() if line.startswith("GPU0")]
    if len(devices) != 1 or len(devices[0]) < 4 or (
        not devices[0][-2].isdigit() or int(devices[0][-2]) != selected["node"]
    ):
        raise ValueError("the strict Host pool node differs from the actual GPU0 affinity")
    if exact_mapping((proc / "maps").read_text(), result["mapping"]["mapping_bytes"]) != result["mapping"]:
        raise ValueError("the Host pool mapping changed while reading placement")
    check_identity()
    proof = {
        **result, "configuration": selected,
        "scheduler": {"pid": scheduler["pid"], "start_ticks": scheduler["start_ticks"]},
        "host_full_capacity_bytes": capacity["host_full_bytes"],
        "GPU_local_NUMA_node": selected["node"], "GPU_topology": plain,
        "raw_proc_read_sha256": {
            "maps": hashlib.sha256(maps).hexdigest(), "numa_maps": hashlib.sha256(numa).hexdigest(),
        },
        "placement_observation_attempts": attempts,
        "inputs_sha256": {str(capacity_path.resolve()): sha256_file(capacity_path)},
        "scope": "Initial complete static pinned Host allocation, checked before measured request submission.",
    }
    if selected.get("registration_policy") == "preferred":
        proof["registration_observation"] = registration_evidence(
            Path(directory) / "server/host_registration_numa.jsonl",
            scheduler, result["mapping"], selected["node"],
        )
    return proof


def require_source_binding(common, sources, checkout):
    if not requires_placement(common):
        return
    required = {
        "beliefkv/experiments/host_numa.py", "tests/test_host_numa.py",
        "scripts/preflight_qwen35_host_pool.py", "tests/test_qwen35_host_pool.py",
    }
    if configuration(common).get("require_balanced_pool"):
        required.update({
            "scripts/launch_qwen35_native_v0520.sh",
            "scripts/run_causal_comparison.py", "scripts/run_position_replay_calibration.py",
            "scripts/review_predictive_thunder_cohort.py", "tests/test_host_pool_interleave.py",
            "tests/test_host_interleave_balance.py",
        })
    if configuration(common).get("registration_policy") == "preferred":
        required.update({
            "beliefkv/runtime/host_registration_numa.py",
            "beliefkv/experiments/causal_replay_native.py",
            "tests/test_host_registration_numa.py",
        })
    if not required <= sources.keys() or any(
        sha256_file(Path(checkout) / name) != sources[name] for name in required
    ):
        raise ValueError("strict Host NUMA configuration lacks current CPU-bound sources")


def verify_qualified_configuration(common, registration):
    selected = configuration(common)
    if not requires_placement(common):
        return
    proof = registration.get("host_numa_observation", {})
    if registration.get("host_numa_configuration") != selected or (
        proof.get("configuration") != selected
        or proof.get("host_full_capacity_bytes") != expected_full_bytes(common)
    ):
        raise ValueError("fresh GPU qualification lacks the exact strictly local Host pool")
    if selected.get("require_balanced_pool"):
        try:
            checked = inspect_mapping(
                proof["mapping"]["raw_maps_line"], proof["raw_numa_maps_line"],
                expected_bytes=expected_full_bytes(common), node=selected["node"],
                page_bytes=os.sysconf("SC_PAGE_SIZE"), memory_nodes=selected["memory_nodes"],
            )
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("fresh GPU qualification lacks complete balanced Host placement") from error
        if proof.get("fully_interleaved_pool_verified") is not True or any(
            proof.get(key) != value for key, value in checked.items()
        ):
            raise ValueError("fresh GPU qualification lacks complete balanced Host placement")
    elif proof.get("fully_local_pool_verified") is not True or proof.get("remote_pool_bytes") != 0:
        raise ValueError("fresh GPU qualification lacks the exact strictly local Host pool")
    if selected.get("registration_policy") == "preferred" and proof.get(
        "registration_observation", {},
    ).get("complete_native_registration_and_original_bind_restoration_verified") is not True:
        raise ValueError("fresh GPU qualification lacks actual native Host registration")


def verify_completed_configuration(plan, arm=None):
    common = plan.get("reference_environment", {})
    selected = configuration(common)
    if not requires_placement(common):
        return None
    if arm is None:
        if len(plan.get("order", ())) != 1:
            raise ValueError("strict Host NUMA proof requires one complete declared arm")
        arm = Path(plan["run_root"]) / plan["order"][0]
    path = Path(arm) / "server/host_numa_placement.json"
    proof = json.loads(path.read_bytes())
    verify_qualified_configuration(common, {
        "host_numa_configuration": plan.get("host_numa_configuration"),
        "host_numa_observation": proof,
    })
    capacity_path = Path(arm) / "server/native_capacity_census.json"
    ready_path = Path(arm) / "server/native_telemetry_ready.json"
    ready = json.loads(ready_path.read_bytes())
    if (
        proof.get("GPU_local_NUMA_node") != selected["node"]
        or selected["require_fully_local_pool"] and proof.get("pool_resident_bytes_by_node") != {
            str(selected["node"]): proof["host_full_capacity_bytes"],
        }
        or proof.get("inputs_sha256", {}).get(str(capacity_path.resolve())) != sha256_file(capacity_path)
        or proof.get("scheduler", {}).get("pid") != ready.get("scheduler_pid")
    ):
        raise ValueError("completed Host placement differs from the complete declared pool")
    inputs = {
        str(item.resolve()): sha256_file(item) for item in (path, capacity_path, ready_path)
    }
    if selected.get("registration_policy") == "preferred":
        expected = registration_evidence(
            Path(arm) / "server/host_registration_numa.jsonl",
            proof["scheduler"], proof["mapping"], selected["node"],
        )
        if expected != proof["registration_observation"]:
            raise ValueError("the completed Host registration evidence changed")
        inputs.update(expected["inputs_sha256"])
    return {
        "configuration": selected,
        "inputs_sha256": inputs,
    }
