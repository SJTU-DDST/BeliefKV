#!/usr/bin/env python3
"""Prepare or replay the paper workload with the pinned MARS and MORI engines."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
import importlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments import native_execution
from beliefkv.experiments.causal_replay import execute_causal_replay
from beliefkv.experiments.causal_replay_native import BRIDGE_ENV
from beliefkv.experiments.causal_replay_sampling import SAMPLING_ENV
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.paper_baseline_replay import PaperReplayBackend, baseline_payload
from beliefkv.experiments.paper_baseline_sglang import ARM_ENV, ENGINE, MARS_AUTHOR_ENV, MORI_CAP_ENV, STATE_KEY
from beliefkv.experiments.paper_mars_admission import MarsAdmission, mars_defaults
from beliefkv.experiments.replay_arrivals import arrival_offsets_ms, load_arrival_plan
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.experiments.tool_timing_overlay import load_overlay
from beliefkv.experiments.workflow_cohort import validate_cohort
from beliefkv.experiments.workflow_metrics import StreamingWorkflowMetrics, distribution
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from beliefkv.runtime.event_channel import JsonlRuntimeEventSink
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import write


SWEEP = ROOT / "experiments/reports/d3_reclaim_r5_20261118/host200_arrival_rate_80_65_55_r1/plan.json"
WORKTREES = ROOT.parent / ".worktrees"
COMMON_PARENT = "c5ad39a1e1285532335139fa3bc33d49c7eabcf6"
COMMON_TREE = "434c35dd66211d0a230717c88bf1452056769a40"
COMMON_PYTHON = Path.home() / "miniconda3/envs/beliefkv-next/bin/python"
PINS = {
    "mars": "d2e34d007ad2013b4a2513efdb9b2f9b606369f6",
    "mori": "b4f6a1d50fe50a09b522f85333fa432871533690",
}
MARS_NATIVE_EXTENSIONS = (
    "_C", "_moe_C", "cumem_allocator",
    "vllm_flash_attn/_vllm_fa2_C", "vllm_flash_attn/_vllm_fa3_C",
)
REPORT_RECEIPTS = {18: (2991, 362157), 38: (6519, 834692)}
ADAPTER_SOURCES = (
    Path(__file__).relative_to(ROOT),
    Path("beliefkv/experiments/paper_baseline_replay.py"),
    Path("beliefkv/experiments/paper_baseline_native.py"),
    Path("beliefkv/experiments/paper_mars_admission.py"),
    Path("beliefkv/experiments/causal_replay.py"),
    Path("beliefkv/experiments/causal_replay_sampling.py"),
    Path("beliefkv/experiments/source_provenance.py"),
)
LEGACY_DRAFT_SHA256 = {
    "scripts/run_paper_author_baseline.py": "b1d8e4b3ad3ece269f4f89bdf8e610622a0e233eb01a6f80c4fd7cc9ebf7ac04",
    "beliefkv/experiments/paper_baseline_replay.py": "e86d5dad996094bcbad27fc7b3cb6f497f42ca7e847d2e94fd91fcd524d6d442",
    "beliefkv/experiments/paper_mars_admission.py": "991e9a39695e262f27d36a35878d2c740a0e783bc0091e58109aa32581bf723b",
}
WORKER_SOURCES = tuple(Path("beliefkv/experiments") / name for name in (
    "causal_replay_native.py", "causal_replay_sampling.py", "paper_baseline_sglang.py",
))
COMMON_SOURCES = tuple(dict.fromkeys((*ADAPTER_SOURCES, *WORKER_SOURCES, *map(Path, (
    "beliefkv/experiments/causal_replay_tokens.py", "beliefkv/experiments/native_execution.py",
    "beliefkv/experiments/paper_mars_policy.py", "beliefkv/experiments/paper_mars_sglang.py",
    "beliefkv/runtime/clock_evidence.py", "beliefkv/runtime/replay_token_capture.py",
    "configs/causal_replay_plugin/pyproject.toml",
)))))
MORI_INPUT_TREATMENT = {
    "request_source": "captured_prompt_token_ids",
    "router_prompt_length": "exact_full_prompt_token_count",
    "router_prefix_group": "sha256_of_first_256_prompt_token_ids_as_uint32_le",
    "author_json_character_estimator_used": False,
}
MARS_REQUIRED_ZERO = ("frequency_penalty", "presence_penalty", "min_new_tokens")


def read(path):
    return json.loads(Path(path).read_bytes())


def engine_source(arm, checkout):
    checkout = Path(checkout).resolve()
    patch = ROOT / "patches" / f"{arm}-paper-replay.patch"
    if arm == "mars":
        package = checkout / "vllm-continuum/vllm"
        extensions = sorted(package.rglob("*.so"))
        modules = {
            str(path.relative_to(package).with_name(path.name.split(".", 1)[0]))
            for path in extensions
        }
        if missing := set(MARS_NATIVE_EXTENSIONS) - modules:
            raise ValueError(f"MARS build is missing native extensions: {sorted(missing)}")
        generated = ("vllm-continuum/vllm/_version.py",) + tuple(
            str(path.relative_to(checkout)) for path in extensions
        )
        metadata = Path("vllm-continuum/vllm.egg-info")
        if not (checkout / metadata / "PKG-INFO").is_file():
            raise ValueError("MARS build is missing package metadata")
    else:
        metadata = Path("ThunderAgent/ThunderAgent.egg-info")
        generated = ()
    generated += tuple(
        str(metadata / name) for name in (
            "PKG-INFO", "SOURCES.txt", "dependency_links.txt", "entry_points.txt",
            "requires.txt", "top_level.txt",
        ) if (checkout / metadata / name).is_file()
    )
    state = git_source_state(checkout, canonical_patch=patch, exclude_paths=generated)
    if state["commit"] != PINS[arm]:
        raise ValueError(f"unexpected {arm} author revision")
    return {
        "checkout": str(checkout), "author_commit": state["commit"],
        "adapted_tree": state["canonical_patch"]["actual_tree"],
        "patch_sha256": state["canonical_patch"]["sha256"],
        "generated_build_files_sha256": {name: sha256_file(checkout / name) for name in generated},
    }


def common_engine_source(arm, checkout):
    checkout = Path(checkout).resolve()
    patch = ROOT / "patches" / f"{arm}-sglang0520-paper-replay.patch"
    if not patch.is_file() or not checkout.is_dir():
        raise ValueError(f"{arm} requires {checkout} with its canonical patch {patch}")
    parent = subprocess.check_output(
        ("git", "show", "-s", "--format=%H %T", "HEAD"), cwd=checkout, text=True,
    ).split()
    if parent != [COMMON_PARENT, COMMON_TREE]:
        raise ValueError(f"{arm} must apply its replay patch to the pinned SGLang commit and tree")
    state = git_source_state(checkout, canonical_patch=patch)
    return {
        "checkout": str(checkout), "parent_commit": state["commit"], "parent_tree": parent[1],
        "adapted_tree": state["canonical_patch"]["actual_tree"], "patch_sha256": state["canonical_patch"]["sha256"],
    }


def mars_author_source(checkout):
    checkout = Path(checkout).resolve()
    state = git_source_state(checkout)
    if state["commit"] != PINS["mars"] or state["dirty"]:
        raise ValueError("MARS author source must be the clean pinned author revision")
    return {
        "checkout": str(checkout), "author_commit": state["commit"],
        "admission_defaults": vars(mars_defaults(checkout)),
    }


def adapter_hashes(engine):
    sources = COMMON_SOURCES if engine == ENGINE else ADAPTER_SOURCES
    return {str(path): sha256_file(ROOT / path) for path in sources}


def verify_adapters(plan, engine):
    actual = adapter_hashes(engine)
    recorded = plan["adapter_sources_sha256"]
    if recorded == actual:
        return
    # Accept the original draft as a unit. New plans bind the current sources.
    if "engine" not in plan and recorded == {**actual, **LEGACY_DRAFT_SHA256}:
        return
    raise ValueError("baseline adapter changed after preparation")


def launches(arms, model, context, capacity):
    mars, mori = (Path(arms[arm]["checkout"]) for arm in PINS)
    environments = Path(sys.executable).resolve().parents[2]
    block_size = 16
    blocks = capacity["device_full_tokens"] // block_size
    for arm, engine, env, port in (
        ("mars", mars / "vllm-continuum", "mars", 19140),
        ("mori", mori / "sglang/python", "mori-paper", 19141),
    ):
        arms[arm].update(
            server_port=port, server_environment={
                "PYTHONPATH": f"{engine}:{ROOT}", "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1", "PYTHONHASHSEED": "21",
            },
        )
        arms[arm]["server_argv"] = [str(environments / env / "bin/python")]
    arms["mars"]["server_environment"]["BELIEFKV_PAPER_REPLAY"] = "mars"
    arms["mars"]["server_environment"]["VLLM_USE_V1"] = "1"
    arms["mars"]["server_argv"] += [
        "-m", "vllm.entrypoints.openai.api_server",
        "--host", "127.0.0.1", "--port", "19140", "--model", model,
        "--served-model-name", Path(model).name,
        "--dtype", "bfloat16", "--max-model-len", str(context),
        "--scheduling-policy", "mars", "--max-num-seqs", "24",
        "--max-num-batched-tokens", "2048", "--enable-chunked-prefill", "--enable-prefix-caching",
        "--seed", "21", "--block-size", str(block_size), "--num-gpu-blocks-override", str(blocks + 1),
        "--enable-prompt-tokens-details",
    ]
    arms["mars"].update(device_tokens=blocks * block_size, host_tokens=0, write_back_max_nodes=None)
    arms["mori"]["server_environment"]["BELIEFKV_MORI_WRITE_BACK_CAP"] = "1"
    arms["mori"]["server_argv"] += [
        "-m", "sglang.launch_server",
        "--host", "127.0.0.1", "--port", "19141", "--model-path", model,
        "--dtype", "bfloat16", "--context-length", str(context),
        "--schedule-policy", "lpm", "--max-running-requests", "24",
        "--chunked-prefill-size", "2048", "--cuda-graph-max-bs", "24",
        "--random-seed", "21", "--page-size", "1", "--max-total-tokens", str(capacity["device_full_tokens"]),
        "--enable-hierarchical-cache", "--hicache-size", "200", "--hicache-write-policy", "write_back",
        "--hicache-mem-layout", "page_first", "--enable-metrics", "--enable-cache-report",
        "--enable-custom-logit-processor", "--stream-interval", "1", "--incremental-streaming-output",
    ]
    arms["mori"].update(
        device_tokens=capacity["device_full_tokens"], host_tokens=capacity["host_full_tokens"],
        write_back_max_nodes=1,
    )


def common_launches(arms, archive, execution, write_back_mode):
    common = archive["common_environment"]
    server_info = archive["server_info"]
    capacity = archive["capacity"]
    model = execution["model_path"]
    for name, arm in arms.items():
        mars = name == "mars"
        page_size = 16 if mars else 1
        device_tokens = capacity["device_full_tokens"] // page_size * page_size
        port = 19140 if mars else 19141
        host_gb = 0 if mars else int(common["HICACHE_SIZE_GB"])
        cap = None if mars or write_back_mode == "author" else 1
        settings = {
            "model_path": model, "served_model_name": common["SERVED_MODEL_NAME"],
            "host": "127.0.0.1", "port": port,
            "dtype": server_info["dtype"], "kv_cache_dtype": server_info["kv_cache_dtype"],
            "attention_backend": "fa3", "context_length": server_info["context_length"],
            "mem_fraction_static": server_info["mem_fraction_static"],
            "max_running_requests": server_info["max_running_requests"],
            "chunked_prefill_size": server_info["chunked_prefill_size"],
            "random_seed": int(common["SERVER_RANDOM_SEED"]),
            "page_size": page_size, "max_total_tokens": device_tokens,
            "schedule_policy": "fcfs" if mars else "lpm",
            "tp_size": 1, "dp_size": 1, "pp_size": 1,
            "stream_interval": 1, "enable_metrics": True, "enable_cache_report": True,
            "enable_custom_logit_processor": True, "incremental_streaming_output": True,
            "enable_hierarchical_cache": not mars, "hicache_size": host_gb,
            "disable_overlap_schedule": mars, "enable_mixed_chunk": False,
            "disable_radix_cache": False, "enable_beliefkv_admission": False,
            "hicache_storage_backend": None,
        }
        if mars:
            settings["max_prefill_tokens"] = settings["chunked_prefill_size"]
        else:
            settings.update({key: server_info[key] for key in (
                "hicache_write_policy", "hicache_mem_layout", "hicache_io_backend", "hicache_host_memory_mode",
            )})
        cli_only_true = (
            "enable_hierarchical_cache", "enable_metrics", "enable_cache_report",
            "enable_custom_logit_processor", "incremental_streaming_output", "disable_overlap_schedule",
        )
        argv = [str(COMMON_PYTHON), "-m", "sglang.launch_server", *execution["flags"]]
        for key, value in settings.items():
            if type(value) is bool:
                if value and key in cli_only_true:
                    argv.append("--" + key.replace("_", "-"))
            elif value is not None:
                argv.extend(("--" + key.replace("_", "-"), str(value)))
        cuda = common["CUDA_HOME"]
        arm.update(
            server_port=port, server_argv=argv, effective_settings=settings,
            server_environment={
                "PYTHONPATH": f"{arm['checkout']}/python:{ROOT}", "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1", "PYTHONHASHSEED": common["PYTHONHASHSEED"],
                "CUDA_VISIBLE_DEVICES": "0", "CUDA_HOME": cuda, "LIBRARY_PATH": f"{cuda}/lib",
                "LD_LIBRARY_PATH": f"{cuda}/lib",
                "SGLANG_PLUGINS": "beliefkv_causal_replay", "SGLANG_ENABLE_DELAY_SAMPLE": "0",
                BRIDGE_ENV: "1", SAMPLING_ENV: "position", ARM_ENV: name,
                "SGLANG_ENABLE_MORI": str(int(not mars)), MORI_CAP_ENV: str(cap or 0),
                MARS_AUTHOR_ENV: arm["author_source"]["checkout"] if mars else "",
                **execution["environment"],
            },
            device_tokens=device_tokens, host_tokens=0 if mars else capacity["host_full_tokens"],
            write_back_max_nodes=cap,
        )
        if mars:
            arm["num_gpu_blocks_with_sentinel"] = device_tokens // page_size + 1
        else:
            arm["write_back_mode"] = write_back_mode


def bound_input(ref):
    path = Path(ref["path"])
    if sha256_file(path) != ref["sha256"]:
        raise ValueError(f"paper evaluation input changed: {path}")
    return path


def frozen_workload(plan):
    source = bound_input(plan["expected"]).parent
    token_file = bound_input(plan["tokens"])
    graph, expected, _ = source_plan(source)
    graph, overlay = load_overlay(graph, bound_input(plan["tool_overlay"]))
    workflows = sorted({node.workflow_id for node in graph.nodes})
    arrivals = load_arrival_plan(bound_input(plan["arrivals"]), workflows)
    cohort = read(bound_input(plan["cohort"]))
    validate_cohort(cohort, arrivals)
    if (
        token_file != source / "server/request_tokens.jsonl"
        or len(workflows) != 133 or arrivals["initial_workflows"] != 20 or arrivals["seed"] != 35
        or arrivals["rate_per_second"] != 1 / 55
    ):
        raise ValueError("baseline comparison requires the frozen 133-project mean55 workload")
    return graph, expected, token_file, overlay, arrivals, cohort


def frozen_reference():
    sweep = read(SWEEP)
    fixed = sweep["fixed"]
    point = next(point for point in sweep["points"] if point["mean_interval_seconds"] == 55)
    common = read(bound_input(point["full_d3_configuration"]))["common_environment"]
    return fixed, point, common


def common_reference(fixed, common):
    execution = native_execution.configuration(common, ROOT)
    if execution is None or execution["identity"] != fixed["native_execution_profile"]:
        raise ValueError("paper baseline must use the archived SGLang execution profile")
    source = Path(fixed["source"]["path"]).parent
    name = "server/server_info.json"
    digest = read(source / "composite_source.json")["artifacts_sha256"][name]
    server_info = read(bound_input({"path": source / name, "sha256": digest}))
    return {"common_environment": common, "server_info": server_info,
            "capacity": fixed["actual_KV_capacity"]}, execution


def prompt_limits(tokens, engine):
    peak_context = count = 0
    with tokens.open("rb") as stream:
        for line in stream:
            row = json.loads(line)
            if row["event"] != "request_prompt":
                continue
            stopping = row["sampling_parameters"]
            if stopping["stop"] or stopping["stop_regex"] or row["extra_key"] or row["cache_salt"]:
                raise ValueError("source requires an unsupported stop or cache namespace")
            if engine == ENGINE:
                generation = row["generation_parameters"] or {}
                settings = {**generation, "min_new_tokens": stopping.get("min_new_tokens")}
                if unsupported := [field for field in MARS_REQUIRED_ZERO if settings.get(field) != 0]:
                    raise ValueError(
                        f"common MARS requires zero {', '.join(unsupported)} for {row['request_id']}"
                    )
            peak_context = max(peak_context, row["token_count"] + stopping["max_new_tokens"])
            count += 1
    return peak_context, count


def prepare(args):
    fixed, point, common = frozen_reference()
    source = fixed["source"]
    profile = fixed["native_execution_profile"]
    configuration = bound_input({
        "path": ROOT / profile["configuration"], "sha256": profile["configuration_sha256"],
    })
    model = read(configuration)["model_path"]
    context = int(common["CONTEXT_WINDOW_TOKENS"])
    engine = getattr(args, "engine", None) or "legacy"
    worktrees = {
        arm: getattr(args, arm) or WORKTREES / (
            f"sglang-{arm}-paper-0520-20261123" if engine == ENGINE else f"{arm}-paper-replay-20261123"
        )
        for arm in PINS
    }
    plan = {
        "schema_version": 1, "kind": "paper_author_baseline_mean55", "engine": engine,
        "expected": source,
        "tokens": {
            "path": str(Path(source["path"]).parent / "server/request_tokens.jsonl"),
            "sha256": fixed["token_source_sha256"],
        },
        "tool_overlay": fixed["TraceLab_tool_overlay"],
        "arrivals": point["arrivals"], "cohort": point["cohort"],
        "model_path": model, "context_limit": context,
        "model_metadata_sha256": {
            name: sha256_file(Path(model) / name) for name in ("config.json", "generation_config.json")
        },
        "adapter_sources_sha256": adapter_hashes(engine),
        "request_timeout_s": fixed["request_timeout_s"],
        "mori_input_treatment": MORI_INPUT_TREATMENT,
    }
    if engine == ENGINE:
        mars_source = os.environ.get(MARS_AUTHOR_ENV)
        if not mars_source:
            raise ValueError(f"set {MARS_AUTHOR_ENV} to the pinned MARS author repository")
        arms = {name: common_engine_source(name, checkout) for name, checkout in worktrees.items()}
        arms["mars"]["author_source"] = mars_author_source(mars_source)
        arms["mori"]["author_router"] = engine_source(
            "mori", args.mori_router or WORKTREES / "mori-paper-replay-20261123",
        )
        archive, execution = common_reference(fixed, common)
        mode = args.mori_write_back or "author"
        common_launches(arms, archive, execution, mode)
        plan.update(arms=arms, mori_write_back_mode=mode,
                    native_execution_configuration=execution["identity"])
    else:
        plan["arms"] = {arm: engine_source(arm, checkout) for arm, checkout in worktrees.items()}
        launches(plan["arms"], model, context, fixed["actual_KV_capacity"])
        plan["mori_write_back_mode"] = "matched"
    graph, expected, tokens, overlay, _, cohort = frozen_workload(plan)
    fields = ("warmup_workflow_ids", "measured_workflow_ids")
    groups = [cohort[fields[1]], [key for field in fields for key in cohort[field]]]
    for group in groups:
        members = set(group)
        usage = [graph.request_usage[rid] for rid, rows in expected.items() if rows["llm_submit"][0][0] in members]
        if (len(usage), sum(output for _, output in usage)) != REPORT_RECEIPTS[len(members)]:
            raise ValueError("fixed cohort source work differs from the reference")
    with CapturedTokenCorpus(tokens, expected) as corpus:
        if corpus.evidence["token_source_sha256"] != plan["tokens"]["sha256"]:
            raise ValueError("token source changed while preparing baseline")
        captured = corpus.get(next(iter(graph.request_usage)))
        baseline_payload("mars", Path(model).name, captured, "cpu-preflight", engine=engine)
        baseline_payload("mori", Path(model).name, captured, "cpu-preflight", engine=engine)
        plan["token_validation"] = corpus.evidence
    peak_context, scanned = prompt_limits(tokens, engine)
    if peak_context > context:
        raise ValueError("author engines would truncate a source generation budget")
    if engine == ENGINE:
        if scanned != plan["token_validation"]["requests"]:
            raise ValueError("common MARS sampling preflight did not cover all requests")
        plan["mars_sampling_preflight"] = {"requests": scanned, "required_zero": list(MARS_REQUIRED_ZERO)}
    plan.update(
        workload=graph.summary(), tool_timing_overlay=overlay,
        largest_prompt_plus_generation_budget=peak_context,
        throughput_window_offset_seconds=cohort["throughput_window_offset_seconds"],
        gpu_replay_qualified=False,
    )
    write(args.prepare, plan)
    return plan


def verify_common_plan(plan, name):
    fixed, point, common = frozen_reference()
    archive, execution = common_reference(fixed, common)
    if (
        plan["native_execution_configuration"] != execution["identity"]
        or plan["model_path"] != execution["model_path"]
        or plan["context_limit"] != int(common["CONTEXT_WINDOW_TOKENS"])
        or plan["request_timeout_s"] != fixed["request_timeout_s"]
        or plan["expected"] != fixed["source"]
        or plan["tokens"]["sha256"] != fixed["token_source_sha256"]
        or plan["tool_overlay"] != fixed["TraceLab_tool_overlay"]
        or plan["arrivals"] != point["arrivals"] or plan["cohort"] != point["cohort"]
        or plan["mori_input_treatment"] != MORI_INPUT_TREATMENT
        or plan["mori_write_back_mode"] not in ("author", "matched")
    ):
        raise ValueError("common author replay differs from the archived experiment")
    arm = plan["arms"][name]
    actual = common_engine_source(name, arm["checkout"])
    if name == "mars":
        actual["author_source"] = mars_author_source(arm["author_source"]["checkout"])
    else:
        actual["author_router"] = engine_source("mori", arm["author_router"]["checkout"])
    common_launches({name: actual}, archive, execution, plan["mori_write_back_mode"])
    if arm != actual:
        raise ValueError("common author engine sources or launch settings changed")
    return execution


async def common_server_info(client, plan, name, execution):
    response = await client.get("/server_info", timeout=10.)
    response.raise_for_status()
    info = response.json()
    arm = plan["arms"][name]
    if info.get("version") != "0.5.20" or any(
        info.get(key) != value for key, value in arm["effective_settings"].items()
    ):
        raise ValueError("SGLang version or effective launch settings differ from the plan")
    graph = info.get("cuda_graph_config", {})
    if any(
        graph.get(phase, {}).get(key) != value
        for phase, fields in execution["graph"].items() for key, value in fields.items()
    ):
        raise ValueError("SGLang did not resolve the archived prefill and decode graphs")
    states = info.get("internal_states")
    if type(states) is not list or len(states) != 1 or type(states[0]) is not dict:
        raise ValueError("author baseline requires one SGLang scheduler rank")
    state = states[0]
    if (
        state.get("world_size") != 1 or state.get("effective_max_running_requests_per_dp") != 24
        or state.get("memory_usage", {}).get("token_capacity") != arm["device_tokens"]
    ):
        raise ValueError("SGLang actual GPU KV or request capacity differs from the plan")
    proof = state.get(STATE_KEY, {})
    clock = local_monotonic_clock_domain()
    if not clock or any(proof.get(key) != value for key, value in {
        "arm": name, "clock_domain": clock, "sampling_mode": "position",
        "python": str(COMMON_PYTHON.resolve()),
        "engine_package": str(Path(arm["checkout"]) / "python/sglang"),
        "adapter_sources_sha256": {
            str(ROOT / path): plan["adapter_sources_sha256"][str(path)] for path in WORKER_SOURCES
        },
        "mars_author_source": arm["server_environment"][MARS_AUTHOR_ENV] or None,
    }.items()):
        raise ValueError("SGLang worker did not bind the planned engine, adapters and clock")
    if proof.get("moe_configurations", {}).get(execution["kernel_path"]) != (
        execution["identity"]["kernel_configuration_sha256"]
    ):
        raise ValueError("SGLang worker did not confirm loading the archived H200 MoE kernel configuration")
    if name == "mori":
        mori = state.get("mori", {})
        if (
            mori.get("enabled") is not True or mori.get("cache_class") != "HiRadixCache"
            or mori.get("write_policy") != "write_back" or "write_back_batch_cap" not in mori
            or mori["write_back_batch_cap"] != arm["write_back_max_nodes"]
            or any(type(mori.get(key)) is not int or mori[key] < 0 for key in (
                "registered_programs", "pending_h2d_host_releases",
            ))
        ):
            raise ValueError("MORI native cache policy or write-back cap differs from the plan")
    return info


async def common_load(client, arm):
    response = await client.get("/v1/loads", params={"include": "core"}, timeout=5.)
    response.raise_for_status()
    info = response.json()
    loads = info.get("loads")
    if (
        info.get("accelerator") != "NVIDIA H200 NVL" or info.get("num_accelerators") != 1
        or type(loads) is not list or len(loads) != 1 or type(loads[0]) is not dict
    ):
        raise ValueError("common author replay requires one H200 NVL scheduler")
    load = loads[0]
    running, waiting = (load.get(key) for key in ("num_running_reqs", "num_waiting_reqs"))
    if (
        load.get("dp_rank") != 0 or load.get("max_total_num_tokens") != arm["device_tokens"]
        or load.get("max_running_requests") != 24
        or any(type(value) is not int or value < 0 for value in (running, waiting))
    ):
        raise ValueError("SGLang live load or resource census differs from the plan")
    return running, waiting


async def start_mori(arm, client, model, context_limit, *, info=None, control_errors=None):
    scheduler_path = Path(arm.get("author_router", arm)["checkout"]) / "ThunderAgent"
    sys.path.insert(0, str(scheduler_path))
    module = importlib.import_module("ThunderAgent.scheduler.router")
    if not Path(module.__file__).resolve().is_relative_to(scheduler_path):
        raise RuntimeError("wrong MORI scheduler import")
    base = str(client.base_url).rstrip("/")
    router = module.MultiBackendRouter(base, backend_type="sglang")
    if control_errors is not None:
        async def verify_control(response):
            if response.url.path not in ("/mark_program_type", "/release_program", "/batch_program_ops"):
                return
            try:
                await response.aread()
                response.raise_for_status()
                if response.json().get("success") is not True:
                    raise ValueError("native program control reported failure")
            except Exception as error:
                control_errors.append((str(response.url), repr(error)))

        router.client.event_hooks["response"].append(verify_control)
    try:
        if info is None:
            response = await client.get("/server_info", timeout=10.)
            response.raise_for_status()
            info = response.json()
        settings = {
            "schedule_policy": "lpm", "max_running_requests": 24,
            "model_path": model, "context_length": context_limit, "chunked_prefill_size": 2048,
            "hicache_size": 200, "hicache_write_policy": "write_back",
            "enable_hierarchical_cache": True, "enable_cache_report": True,
            "enable_custom_logit_processor": True, "incremental_streaming_output": True,
        }
        if any(info.get(key) != value for key, value in settings.items()):
            raise ValueError("MORI server policy or replay settings do not match")
        await router.start()
        backend = router.get_default_backend()
        if not await backend.fetch_metrics():
            raise ValueError("MORI backend metrics are unavailable")
        config = backend.cache_config
        if (
            not config or config.total_tokens_capacity != arm["device_tokens"]
            or config.cpu_total_tokens_capacity != arm["host_tokens"]
        ):
            raise ValueError("MORI live GPU or Host KV capacity differs from the declared launch")
        return router
    except BaseException:
        await router.stop()
        raise


async def finish_common_mori(client, programs, state):
    if not state["registered_programs"]:
        return False
    # A paused author-router program may have no backend_url at retirement.
    # Remove any remaining per-run registrations after stopping the workload.
    response = await client.post(
        "/batch_program_ops", json={"marks": [], "releases": sorted(programs)}, timeout=10.,
    )
    response.raise_for_status()
    if response.json().get("success") is not True:
        raise ValueError("MORI did not clear retired program registrations")
    return True


class CohortObserver:
    def __init__(self, sink, cohort, arrivals):
        self.sink = sink
        self.offsets = arrival_offsets_ms(arrivals)
        self.measured = set(cohort["measured_workflow_ids"])
        self.warmup = set(cohort["warmup_workflow_ids"])
        self.origin = None
        self.arrivals, self.completed = {}, {}
        self.window_start_ms, self.window_end_ms = (
            seconds * 1000. for seconds in cohort["throughput_window_offset_seconds"]
        )
        self.window_output_tokens = 0
        self.stop = asyncio.Event()

    def emit_batch(self, events):
        self.sink.emit_batch(events)
        for event in events:
            workflow = event.workflow_id
            if event.kind is Kind.WORKFLOW_START:
                self.arrivals[workflow] = event.ts_ms
                if self.origin is None:
                    self.origin = event.ts_ms - self.offsets[workflow]
            elif event.kind is Kind.WORKFLOW_END:
                self.completed[workflow] = event.ts_ms - self.arrivals[workflow]
                if self.measured <= self.completed.keys():
                    self.stop.set()

    def record_output(self, row):
        relative = row["emit_monotonic_ms"] - self.origin
        if self.window_start_ms <= relative < self.window_end_ms:
            self.window_output_tokens += row["output_token_delta"]

    def summary(self, expected, request_usage, verified_requests):
        if self.origin is None or not self.measured <= self.completed.keys():
            raise ValueError("fixed measured workflows must finish before reporting")
        last_arrival_offset_ms = max(self.offsets.values())
        measured_completion_offsets = {
            key: self.offsets[key] + self.completed[key] for key in sorted(self.measured)
        }
        measured_end_offset_ms = max(measured_completion_offsets.values())
        if any(
            offset < measured_end_offset_ms and key not in self.arrivals
            for key, offset in self.offsets.items()
        ):
            raise ValueError("author replay missed a scheduled arrival before measured completion")

        requests_by_workflow = {}
        for rid, rows in expected.items():
            workflow = rows["llm_submit"][0][0]
            requests_by_workflow.setdefault(workflow, []).append(rid)
        groups = {"fixed18": self.measured, "fixed38": self.measured | self.warmup}
        reports = {}
        for label, group in groups.items():
            missing = sorted(group - self.completed.keys())
            required = {
                rid for key in group & self.completed.keys() for rid in requests_by_workflow[key]
            }
            work = (len(required), sum(request_usage[rid][1] for rid in required))
            if not required <= verified_requests or (
                not missing and work != REPORT_RECEIPTS[len(group)]
            ):
                raise ValueError(f"{label} native receipts differ from the frozen work")
            reports[label] = {
                "missing": missing,
                "complete_jct_ms": None if missing else distribution(self.completed[key] for key in group),
                "verified_requests_in_completed_workflows": work[0],
                "verified_output_tokens_in_completed_workflows": work[1],
            }
        return {
            "arrival_origin_monotonic_ms": self.origin,
            "observed_arrivals": len(self.arrivals), "naturally_completed": len(self.completed),
            "workflow_elapsed_ms": self.completed,
            "unarrived": sorted(self.offsets.keys() - self.arrivals.keys()),
            "arrived_unfinished": sorted(self.arrivals.keys() - self.completed.keys()),
            "background_submission_coverage": {
                "last_scheduled_arrival_offset_s": last_arrival_offset_ms / 1000.,
                "last_observed_arrival_offset_s": (max(self.arrivals.values()) - self.origin) / 1000.,
                "last_measured_completion_offset_s": measured_end_offset_ms / 1000.,
                "covers_all_measured_completions": last_arrival_offset_ms >= measured_end_offset_ms,
                "uncovered_drain_seconds": max(0., measured_end_offset_ms - last_arrival_offset_ms) / 1000.,
                "uncovered_seconds_by_measured_workflow": {
                    key: (offset - last_arrival_offset_ms) / 1000.
                    for key, offset in measured_completion_offsets.items() if offset > last_arrival_offset_ms
                },
            },
            "common_window_output_tokens": self.window_output_tokens,
            "common_window_output_tokens_per_second": (
                self.window_output_tokens * 1000. / (self.window_end_ms - self.window_start_ms)
            ),
            "groups": reports,
        }


async def idle_after_replay(client, mars, *, common_arm=None):
    snapshots = []
    deadline = time.monotonic() + 30
    while len(snapshots) < 2:
        if mars:
            stats = await mars.refresh()
            running, waiting = (stats[name] for name in ("running_requests", "waiting_requests"))
        elif common_arm is not None:
            running, waiting = await common_load(client, common_arm)
        else:
            response = await client.get("/metrics", timeout=5.)
            response.raise_for_status()
            values = []
            for name in ("sglang:num_running_reqs", "sglang:num_queue_reqs"):
                samples = [
                    float(line.rsplit(None, 1)[-1])
                    for line in response.text.splitlines() if line.startswith(name + "{")
                ]
                if not samples:
                    raise ValueError(f"MORI drain requires the native {name} gauge")
                values.append(sum(samples))
            running, waiting = values
        if running == waiting == 0:
            snapshots.append({"monotonic_ms": time.monotonic() * 1000., "running": 0, "waiting": 0})
        else:
            snapshots.clear()
        if len(snapshots) < 2:
            if time.monotonic() >= deadline:
                raise TimeoutError("author engine did not drain cancelled background requests")
            await asyncio.sleep(.25)
    return snapshots


async def replay(args):
    plan_sha256 = sha256_file(args.plan)
    plan = read(args.plan)
    name = args.replay
    engine = plan_engine(plan, getattr(args, "engine", None))
    arm = plan["arms"][name]
    verify_adapters(plan, engine)
    execution = None
    if engine == ENGINE:
        execution = verify_common_plan(plan, name)
    elif engine_source(name, arm["checkout"]) != {
        name: arm[name] for name in (
            "checkout", "author_commit", "adapted_tree", "patch_sha256", "generated_build_files_sha256",
        )
    }:
        raise ValueError("adapted engine checkout changed")
    url = urlsplit(args.base_url)
    if (
        url.scheme != "http" or url.netloc != f"127.0.0.1:{url.port}"
        or url.path or url.query or url.fragment
        or engine == ENGINE and url.port != arm["server_port"]
    ):
        raise ValueError("connect to an explicit local author engine port")
    graph, expected, tokens, overlay, arrivals, cohort = frozen_workload(plan)
    for config_file, digest in plan["model_metadata_sha256"].items():
        if sha256_file(Path(plan["model_path"]) / config_file) != digest:
            raise ValueError(f"model metadata changed: {config_file}")
    args.output.mkdir(parents=True, exist_ok=False)
    common_evidence = {}
    with ExitStack() as stack:
        corpus = stack.enter_context(CapturedTokenCorpus(tokens, expected))
        if engine == ENGINE and corpus.evidence != plan["token_validation"]:
            raise ValueError("captured tokens changed after preparation")
        event_sink = stack.enter_context(JsonlRuntimeEventSink(args.output / "runtime_events.replay.jsonl"))
        observer = CohortObserver(event_sink, cohort, arrivals)
        sinks = [stack.enter_context((args.output / name).open("x")) for name in (
            "native_requests.jsonl", "native_output.jsonl", "admission.jsonl",
        )]

        def writer(stream):
            return lambda row: stream.write(json.dumps(row, allow_nan=False) + "\n")

        def output_writer(row):
            observer.record_output(row)
            sinks[1].write(json.dumps(row, allow_nan=False) + "\n")

        async with httpx.AsyncClient(
            base_url=args.base_url, timeout=plan["request_timeout_s"],
            limits=httpx.Limits(max_connections=128, max_keepalive_connections=0),
        ) as client:
            mars = mori = None
            initial = None
            control_errors = []
            if execution is not None:
                initial = await common_server_info(client, plan, name, execution)
                if await common_load(client, arm) != (0, 0):
                    raise ValueError("replay needs an initially idle SGLang engine")
                if name == "mori" and any(initial["internal_states"][0]["mori"][key] for key in (
                    "registered_programs", "pending_h2d_host_releases",
                )):
                    raise ValueError("MORI has prior program or transfer state")
            if name == "mori":
                mori = await start_mori(
                    arm, client, plan["model_path"], plan["context_limit"], info=initial,
                    control_errors=control_errors if execution is not None else None,
                )
            else:
                if execution is None:
                    models = await client.get("/v1/models", timeout=10.)
                    models.raise_for_status()
                    if not any(
                        model["id"] == Path(plan["model_path"]).name and model["root"] == plan["model_path"]
                        for model in models.json()["data"]
                    ):
                        raise ValueError("MARS is serving a different checkpoint")
                mars = MarsAdmission(
                    Path(arm.get("author_source", arm)["checkout"]), client, writer(sinks[2]),
                    device_tokens=arm["device_tokens"], deadline_s=plan["request_timeout_s"], engine=engine,
                )
                await mars.refresh()
            try:
                backend = PaperReplayBackend(
                    name, corpus, client, observer, writer(sinks[0]), output_writer, writer(sinks[2]),
                    StreamingWorkflowMetrics(), model=Path(plan["model_path"]).name,
                    mars=mars, mori=mori, timeout_s=plan["request_timeout_s"], engine=engine,
                )
                execute = asyncio.create_task(execute_causal_replay(
                    graph, backend.execute, backend.emit,
                    workflow_arrival_offsets_ms=arrival_offsets_ms(arrivals),
                ))
                stop = asyncio.create_task(observer.stop.wait())
                try:
                    completed, _ = await asyncio.wait((execute, stop), return_when=asyncio.FIRST_COMPLETED)
                    if execute in completed:
                        execute.result()
                    if not observer.stop.is_set():
                        raise ValueError("author replay exited before its measured cohort")
                finally:
                    execute.cancel()
                    stop.cancel()
                    await asyncio.gather(execute, stop, return_exceptions=True)
                    await backend.drain()
            finally:
                if mori is not None:
                    if execution is not None:
                        await asyncio.gather(*mori._pending_mark_tasks)
                    await mori.stop()
            drain = await idle_after_replay(client, mars, common_arm=arm if execution is not None else None)
            if execution is not None:
                final = await common_server_info(client, plan, name, execution)
                if not initial.get("startup_time") or final["startup_time"] != initial["startup_time"]:
                    raise ValueError("SGLang server restarted during the replay")
                if mori is not None:
                    if control_errors:
                        raise ValueError(f"MORI program control failed: {control_errors!r}")
                    cleared = await finish_common_mori(client, backend.closing, final["internal_states"][0]["mori"])
                    if cleared:
                        final = await common_server_info(client, plan, name, execution)
                    if any(final["internal_states"][0]["mori"][key] for key in (
                        "registered_programs", "pending_h2d_host_releases",
                    )):
                        raise ValueError("MORI native program or transfer work did not drain")
                common_evidence = {
                    "native_execution_configuration": execution["identity"],
                    "startup_server_info": initial, "drained_server_info": final,
                }
        report = observer.summary(expected, graph.request_usage, backend.verified_requests)
    if sha256_file(args.plan) != plan_sha256:
        raise ValueError("paper author plan changed during replay")
    result = {
        **report, "arm": name, "engine": engine, "tool_timing_overlay": overlay,
        "measured_verified_requests": report["groups"]["fixed18"]["verified_requests_in_completed_workflows"],
        "plan_sha256": plan_sha256,
        "request_timeout_s": plan["request_timeout_s"],
        "post_cancellation_idle_snapshots": drain,
        "throughput_window_offset_seconds": cohort["throughput_window_offset_seconds"],
        "output_boundary": "server_output_emit", "performance_accepted": False,
        **common_evidence,
    }
    if name == "mori":
        result.update(mori_input_treatment=MORI_INPUT_TREATMENT,
                      mori_write_back_mode=arm.get("write_back_mode", "matched"),
                      write_back_max_nodes=arm["write_back_max_nodes"])
    write(args.output / "summary.json", result)


def plan_engine(plan, selected):
    engine = plan.get("engine", "legacy")
    if engine not in ("legacy", ENGINE) or selected is not None and selected != engine:
        raise ValueError("select the engine recorded in the prepared plan")
    return engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", type=Path)
    mode.add_argument("--replay", choices=tuple(PINS))
    mode.add_argument("--print-launch", choices=tuple(PINS))
    parser.add_argument("--engine", choices=("legacy", ENGINE),
                        help="prepare using this engine (default: legacy), or check the recorded engine")
    for arm in PINS:
        parser.add_argument(f"--{arm}", type=Path, help="override the engine worktree when preparing")
    parser.add_argument("--mori-router", type=Path, help="override the pinned adapted author router checkout")
    parser.add_argument("--mori-write-back", choices=("author", "matched"),
                        help="common SGLang MORI write-back mode (default: author)")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--base-url")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if (args.mori_router or args.mori_write_back) and (args.engine != ENGINE or not args.prepare):
        parser.error("MORI router and write-back overrides require a common SGLang preparation")
    if args.prepare is not None:
        if args.prepare.exists():
            parser.error("preserve earlier baseline preparations")
        prepare(args)
    elif args.print_launch is not None:
        if args.plan is None:
            parser.error("printing a launch command requires a prepared plan")
        plan = read(args.plan)
        engine = plan_engine(plan, args.engine)
        verify_adapters(plan, engine)
        if engine == ENGINE:
            verify_common_plan(plan, args.print_launch)
        arm = plan["arms"][args.print_launch]
        print(shlex.join(["env", *(f"{key}={value}" for key, value in arm["server_environment"].items()),
                          *arm["server_argv"]]))
    else:
        if args.plan is None or args.base_url is None or args.output is None:
            parser.error("replay requires a prepared plan, local engine URL, and fresh output path")
        asyncio.run(replay(args))


if __name__ == "__main__":
    main()
