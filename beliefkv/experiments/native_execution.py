"""Explicit common engine configuration, independent of agent scheduling policy."""

import json
from pathlib import Path

from beliefkv.experiments.source_provenance import sha256_file

CONFIG_KEY = "NATIVE_EXECUTION_CONFIG"
HASH_KEY = "NATIVE_EXECUTION_CONFIG_SHA256"
MOE_KEY = "SGLANG_MOE_CONFIG_DIR"
KERNEL_FILE = Path("configs/triton_3_7_1/E=128,N=768,device_name=NVIDIA_H200_NVL.json")
GRAPH = {
    "decode": {"backend": "full", "max_bs": 24, "bs": list(range(1, 25))},
    "prefill": {"backend": "breakable", "max_bs": 2048},
}


def contained_path(root, relative):
    relative = Path(relative)
    target = (root / relative).resolve()
    if relative.is_absolute() or not target.is_relative_to(root.resolve()):
        raise ValueError("native execution artifacts must stay inside the frozen checkout")
    return target


def configuration(common, checkout):
    path, digest = common.get(CONFIG_KEY), common.get(HASH_KEY)
    if not path and not digest:
        return None
    if not path or not digest:
        raise ValueError("native execution configuration requires its pinned hash")
    checkout = Path(checkout).resolve()
    source = contained_path(checkout, path)
    if sha256_file(source) != digest:
        raise ValueError("native execution configuration changed")
    document = json.loads(source.read_bytes())
    if (
        document.get("schema_version") != 1
        or document.get("kind") != "opt_in_native_execution_configuration"
        or document.get("model_path") != common.get("MODEL_PATH")
        or document.get("dtype") != "bfloat16" or document.get("kv_cache_dtype") != "bfloat16"
        or document.get("attention_backend") != "fa3"
        or document.get("cuda_graph_config") != GRAPH
        or document.get("device_KV_token_capacity") != 805198
        or document.get("triton_version") != "3.7.1"
        or document.get("GPU") != "NVIDIA H200 NVL"
        or document.get("engine_tree") != "434c35dd66211d0a230717c88bf1452056769a40"
        or common.get("MODEL_CACHE_LAYOUT") != "full_only"
        or common.get("MAX_RUNNING_REQUESTS") != "24"
        or common.get("CHUNKED_PREFILL_SIZE") != "2048"
        or common.get("ENABLE_MIXED_CHUNK", "0") != "0"
        or common.get("DEVICE_FULL_TOKENS") not in (None, "", "805198")
        or set(document.get("environment", {})) != {MOE_KEY}
    ):
        raise ValueError("native execution configuration differs from its bounded BF16/FA3 profile")
    moe_root = contained_path(checkout, document["environment"][MOE_KEY])
    kernel = moe_root / KERNEL_FILE
    if (
        not kernel.resolve().is_relative_to(checkout)
        or sha256_file(kernel) != document.get("kernel_configuration_sha256")
    ):
        raise ValueError("native MoE kernel configuration changed")
    return {
        "identity": {
            "configuration": str(source.relative_to(checkout)),
            "configuration_sha256": digest,
            "kernel_configuration": str(kernel.relative_to(checkout)),
            "kernel_configuration_sha256": document["kernel_configuration_sha256"],
            "engine_tree": document["engine_tree"],
        },
        "environment": {MOE_KEY: str(moe_root)},
        "flags": ["--cuda-graph-config", json.dumps(GRAPH)],
        "graph": document["cuda_graph_config"],
        "kernel_path": str(kernel),
        "model_path": document["model_path"],
    }


def verify_server_info(execution, info):
    if execution is None:
        return
    expected = {
        "model_path": execution["model_path"], "dtype": "bfloat16",
        "kv_cache_dtype": "bfloat16", "attention_backend": "fa3",
        "max_total_tokens": 805198, "max_running_requests": 24, "tp_size": 1,
        "chunked_prefill_size": 2048, "enable_mixed_chunk": False, "page_size": 1,
    }
    if any(info.get(key) != value for key, value in expected.items()):
        raise ValueError("actual native engine differs from the shared execution profile")
    actual = info.get("cuda_graph_config", {})
    if any(
        actual.get(phase, {}).get(key) != value
        for phase, fields in execution["graph"].items() for key, value in fields.items()
    ):
        raise ValueError("actual native graph capture differs from the shared execution profile")


def require_source_binding(execution, sources, checkout):
    if execution is None:
        return
    identity = execution["identity"]
    required = {
        "beliefkv/experiments/native_execution.py", "tests/test_native_execution_configuration.py",
        identity["configuration"], identity["kernel_configuration"],
    }
    if not required <= sources.keys() or any(
        sha256_file(Path(checkout) / name) != sources[name] for name in required
    ):
        raise ValueError("shared execution profile lacks current CPU-bound source inputs")


def verify_qualified_configuration(execution, server_registration):
    requested = execution["identity"] if execution is not None else None
    if server_registration.get("native_execution_configuration") != requested:
        raise ValueError("fresh GPU qualification used a different native execution configuration")
    if execution is not None:
        proof = server_registration.get("native_execution_observation", {})
        if (
            proof.get("identity") != requested
            or any(proof.get(key) is not True for key in (
                "GPU_worker_environment_verified", "native_MoE_configuration_log_verified",
                "server_info_graph_verified",
            ))
            or proof.get("kernel_configuration_sha256") != requested["kernel_configuration_sha256"]
        ):
            raise ValueError("native execution GPU worker evidence is missing")


def observe(execution, info, scheduler, directory, *, proc_root=Path("/proc")):
    if execution is None:
        return None
    verify_server_info(execution, info)
    raw = (proc_root / str(scheduler["pid"]) / "environ").read_bytes()
    selected = [
        entry.split(b"=", 1)[1].decode()
        for entry in raw.split(b"\0") if entry.startswith((MOE_KEY + "=").encode())
    ]
    if selected != [execution["environment"][MOE_KEY]]:
        raise ValueError("native GPU worker did not inherit the frozen MoE configuration")
    line = "Using MoE kernel config from " + execution["kernel_path"] + "."
    if line not in (directory / "server.log").read_text():
        raise ValueError("native model did not confirm the selected MoE kernel file")
    kernel_sha256 = sha256_file(Path(execution["kernel_path"]))
    if kernel_sha256 != execution["identity"]["kernel_configuration_sha256"]:
        raise ValueError("native MoE kernel changed during startup")
    return {
        "identity": execution["identity"],
        "scheduler": {"pid": scheduler["pid"], "start_ticks": scheduler["start_ticks"]},
        "GPU_worker_environment_verified": True,
        "native_MoE_configuration_log_verified": True,
        "actual_MoE_configuration_directory": selected[0],
        "server_info_graph_verified": True,
        "kernel_configuration_sha256": kernel_sha256,
    }


def verify_completed_configuration(plan, arm=None):
    common = plan.get("reference_environment", {})
    if not common.get(CONFIG_KEY) and not plan.get("native_execution_configuration"):
        return None
    if arm is None:
        if plan.get("order") not in (["full"], ["thunderagent_acting_decay"], ["native_matched"]):
            raise ValueError("shared execution evidence requires one declared comparison arm")
        arm = Path(plan["run_root"]) / plan["order"][0]
    selected = configuration(common, Path(plan["execution_checkout"]))
    if selected is None or plan.get("native_execution_configuration") != selected["identity"]:
        raise ValueError("completed execution lacks its declared common native profile")
    paths = [arm / "server/native_execution_configuration.json", arm / "server/server_info.json"]
    proof, info = (json.loads(path.read_bytes()) for path in paths)
    verify_server_info(selected, info)
    verify_qualified_configuration(selected, {
        "native_execution_configuration": plan["native_execution_configuration"],
        "native_execution_observation": proof,
    })
    if proof.get("actual_MoE_configuration_directory") != selected["environment"][MOE_KEY]:
        raise ValueError("completed execution loaded MoE parameters from a different checkout")
    return {
        "identity": selected["identity"],
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in paths},
    }
