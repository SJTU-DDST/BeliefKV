"""Profile captured batches through the native serving lifecycle, without Full."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import argparse
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
from scripts.audit_decode_batch_efficiency import OutputConservation
from scripts.audit_native_system_costs import distribution, union_ms
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.benchmark_decode_graph_cost import SPARSE
from scripts.run_causal_comparison import cleanup, fresh, qualified, reservation, wait_for_telemetry_drain
from scripts.train_workflow_work import write


def check_measurement_tokens(tokens):
    if type(tokens) is not int or not 1 <= tokens <= 4096:
        raise ValueError("component measurement tokens must be an integer in 1..4096")


def payload(case, label, tokens):
    ids = [f"cost:w{case['width']}:{label}:{index}" for index in range(case["width"])]
    return {
        "rid": ids, "input_ids": [row["tokens"][:-1] for row in case["members"]],
        "beliefkv_metadata": [
            BeliefKVRequestMetadata(
                root_workflow_id=identity, invocation_id=identity, context_id=identity,
                context_epoch=0,
            ).to_wire() for identity in ids
        ],
        "sampling_params": {
            "temperature": 0., "max_new_tokens": tokens, "min_new_tokens": tokens,
            "ignore_eos": True, "sampling_seed": 21,
        },
        "return_logprob": True, "logprob_start_len": -1, "stream": False,
    }


def checked_response(result, request, tokens):
    if type(result) is not list or len(result) != len(request["rid"]):
        raise ValueError("native batch did not return one response per requested member")
    outputs = {}
    for identity, row in zip(request["rid"], result, strict=True):
        meta = row["meta_info"]
        logprobs = meta.get("output_token_logprobs")
        if (
            meta["id"] != identity or meta["completion_tokens"] != tokens
            or meta["finish_reason"] != {"type": "length", "length": tokens}
            or not isinstance(logprobs, list) or len(logprobs) != tokens
        ):
            raise ValueError("native token/count/completion evidence is incomplete")
        outputs[identity] = {
            "tokens": [item[1] for item in logprobs],
            "logprobs": [item[0] for item in logprobs],
            "cached_tokens": meta.get("cached_tokens"),
        }
    return outputs


def trace_summary(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as stream:
        trace = json.load(stream)
    kernels, counts, intervals = Counter(), Counter(), []
    for event in trace["traceEvents"]:
        if event.get("cat") != "kernel" or event.get("ph") != "X":
            continue
        duration = event["dur"]
        kernels[event["name"]] += duration
        counts[event["name"]] += 1
        intervals.append((event["ts"], event["ts"] + duration))
    total = sum(kernels.values())
    if not total:
        raise ValueError("profiler has no actual CUDA kernels")
    return {
        "trace": str(path.resolve()), "sha256": sha256_file(path),
        "kernel_duration_sum_ms": total / 1000,
        "kernel_interval_union_ms": union_ms(intervals) / 1000,
        "top_kernels": [
            {
                "name": name, "count": counts[name], "total_ms": duration / 1000,
                "fraction_of_kernel_duration_sum": duration / total,
            }
            for name, duration in kernels.most_common(30)
        ],
        "scope": "CUDA kernel durations in a profiled component burst, not removable workflow JCT.",
    }


def service_summary(path, receipts):
    conservation, stats = OutputConservation(), defaultdict(Counter)
    by_width, prefills = defaultdict(Counter), defaultdict(Counter)
    reader = SealedRecords()
    for row in reader(path):
        if row.get("event") != "gpu_service_sample":
            continue
        conservation.observe(row)
        labels = {
            member["request_id"].split(":")[2] for member in row["request_samples"]
        }
        widths = {
            int(member["request_id"].split(":")[1][1:]) for member in row["request_samples"]
        }
        if len(labels) != 1 or len(widths) != 1:
            raise ValueError("sequential component cases unexpectedly interleaved")
        label, width = next(iter(labels)), next(iter(widths))
        if row["phase"] == "prefill":
            target = prefills[(width, label)]
            target["forwards"] += 1
            target["worker_ms"] += row["service_elapsed_ms"]
            target["executed_input_tokens"] += sum(member["token_delta"] for member in row["request_samples"])
            target["output_tokens"] += sum(member["output_token_delta"] for member in row["request_samples"])
            continue
        if row["phase"] != "decode":
            raise ValueError("unknown native component service phase")
        target = stats[(width, label)]
        target["forwards"] += 1
        target["worker_ms"] += row["service_elapsed_ms"]
        target["output_tokens"] += sum(member["output_token_delta"] for member in row["request_samples"])
        target["graph_forwards"] += row["cuda_graph_replayed"] is True
        by_width[f"{width}:{label}"][row["batch_size"]] += 1
        if row["batch_size"] == width:
            target["full_width_forwards"] += 1
            target["full_width_worker_ms"] += row["service_elapsed_ms"]
            target["full_width_outputs"] += sum(member["output_token_delta"] for member in row["request_samples"])
    conservation.verify({identity: {"output_tokens": len(row["tokens"])} for identity, row in receipts.items()})
    return {
        "prefill_by_case": {
            f"{width}:{label}": {
                **dict(value), "worker_ms_per_input_token": value["worker_ms"] / value["executed_input_tokens"],
            }
            for (width, label), value in prefills.items()
        },
        "decode_by_case": {
            f"{width}:{label}": {
                **dict(value),
                "worker_ms_per_output": value["worker_ms"] / value["output_tokens"] if value["output_tokens"] else None,
                "full_width_ms_per_forward": (
                    value["full_width_worker_ms"] / value["full_width_forwards"]
                    if value["full_width_forwards"] else None
                ),
            }
            for (width, label), value in stats.items()
        },
        "batch_width_counts": {key: dict(value) for key, value in by_width.items()},
        "all_output_ordinals_match_native_responses": True,
        "input_jsonl_prefixes": reader.prefixes,
    }


def cold_prefill_case(document):
    case, member = min(
        ((case, member) for case in document["cases"] for member in case["members"]),
        key=lambda item: abs(len(item[1]["tokens"]) - 65536),
    )
    return {
        "width": 1, "members": [member],
        "captured_from_width": case["width"],
        "context_lengths": [len(member["tokens"])],
        "scope": "One exact captured context prefix near 64K; no task or source trace is changed.",
    }


def finish_prefill_profile(client, directory):
    traces = list((directory / "profile").glob("*.trace.json*"))
    if traces:
        # The native profiler may export automatically at the request boundary.
        for path in traces:
            trace_summary(path)
        return {"automatic_export_already_complete": True}
    response = client.post("/stop_profile")
    response.raise_for_status()
    traces = list((directory / "profile").glob("*.trace.json*"))
    if not traces:
        raise ValueError("native prefill stop did not export a trace")
    for path in traces:
        trace_summary(path)
    return {"automatic_export_already_complete": False}


def run_cold_prefill(client, document, directory, receipts):
    case = cold_prefill_case(document)
    write(directory / "prefill_case.json", {
        **{key: value for key, value in case.items() if key != "members"},
        "source_request_id": case["members"][0]["request_id"],
        "executed_input_tokens_per_request": len(case["members"][0]["tokens"]) - 1,
    })
    for label in ("prefill_measure0", "prefill_measure1", "prefill_measure2", "prefill_profile"):
        fresh.check_hold(document, released=True)
        response = client.post("/flush_cache")
        response.raise_for_status()
        if label == "prefill_profile":
            response = client.post("/start_profile", json={
                "output_dir": str(directory / "profile"), "num_steps": 128,
                "profile_stages": ["prefill"], "profile_by_stage": True,
                "activities": ["CPU", "GPU"], "with_stack": False, "record_shapes": False,
            })
            response.raise_for_status()
        request = payload(case, label, 1)
        begin = time.monotonic()
        response = client.post("/generate", json=request)
        response.raise_for_status()
        records = checked_response(response.json(), request, 1)
        if any(row["cached_tokens"] != 0 for row in records.values()):
            raise ValueError("cold prefill unexpectedly reused cached input")
        receipts.update(records)
        write(directory / f"width1_{label}.json", {
            "records": records, "wall_seconds": time.monotonic() - begin,
            "is_profiled": label == "prefill_profile",
        })
        if label == "prefill_profile":
            write(directory / "prefill_profile_completion.json", finish_prefill_profile(client, directory))
        print(json.dumps({
            "stage": label, "actual_output_tokens": 1,
            "actual_input_tokens": len(request["input_ids"][0]),
            "wall_seconds": time.monotonic() - begin,
        }), flush=True)


def run_mode(
    document, mode, directory, *, profile, cold_prefill_only=False,
    measurement_tokens=96,
):
    check_measurement_tokens(measurement_tokens)
    if cold_prefill_only and measurement_tokens != 96:
        raise ValueError("cold prefill does not accept a decode-length override")
    fresh.check_hold(document, released=True)
    resources = qualified.resources(18959)
    if not resources["ready"]:
        raise ValueError("GPU resources unavailable: " + json.dumps(resources))
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "server").mkdir()
    buckets = document["capture_buckets"] if mode == "exact" else SPARSE
    graph = {
        "decode": {"backend": "full", "max_bs": 24, "bs": buckets},
        "prefill": {"backend": "breakable", "max_bs": 2048},
    }
    environment = {
        name: value for name, value in os.environ.items()
        if not name.startswith("BELIEFKV_") and name not in ("SGLANG_PLUGINS",)
    }
    environment.update(
        CUDA_VISIBLE_DEVICES="0", PYTHONHASHSEED="21", PYTHONUNBUFFERED="1",
        TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
        SGLANG_AUTO_NUMA_BIND="0", SGLANG_PLUGINS="",
        SGLANG_PROFILE_BY_STAGE_DECODE_MIN_BS="9",
        BELIEFKV_NATIVE_TELEMETRY_DIR=str(directory / "server"),
        RUN_ROOT=str(directory), BELIEFKV_RECORD_OUTPUT_TOKEN_IDS="1",
    )
    argv = [
        "numactl", "--cpunodebind=1", "--preferred=1", sys.executable,
        "-m", "sglang.launch_server", "--model-path",
        "/srv/ai/models/Qwen/Qwen3-Coder-30B-A3B-Instruct",
        "--host", "127.0.0.1", "--port", "18959",
        "--dtype", "bfloat16", "--kv-cache-dtype", "bfloat16",
        "--attention-backend", "fa3", "--context-length", "262144",
        "--max-running-requests", "24", "--max-total-tokens", str(document["device_full_tokens"]),
        "--mem-fraction-static", ".94", "--page-size", "1",
        "--chunked-prefill-size", "2048", "--random-seed", "21",
        "--cuda-graph-config", json.dumps(graph),
    ]
    write(directory / "startup.json", {
        "resources": resources, "argv": argv, "native_Host_cache_enabled": False,
        "cold_prefill_only": cold_prefill_only,
        "measurement_tokens": 1 if cold_prefill_only else measurement_tokens,
        "execution_environment": {
            name: environment[name] for name in ("CUDA_HOME", "SGLANG_MOE_CONFIG_DIR")
            if name in environment
        },
        "script_sha256": sha256_file(Path(__file__)),
    })
    receipts, owned = {}, []
    with (directory / "server.log").open("x") as log:
        server = subprocess.Popen(
            argv, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    identity = reservation.process(server.pid)
    if identity is None:
        raise ValueError("native component process identity is missing")
    owned.append(identity)
    success = False
    try:
        base = "http://127.0.0.1:18959"
        with httpx.Client(base_url=base, timeout=600.) as client:
            deadline = time.monotonic() + 360.
            while True:
                fresh.check_hold(document, released=True)
                if server.poll() is not None:
                    raise ValueError("native component server exited before startup")
                try:
                    ready = client.get("/health", timeout=2.).is_success
                except httpx.HTTPError:
                    ready = False
                if ready:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("native component startup expired")
                time.sleep(.5)
            info = client.get("/get_server_info").json()
            if any(info[name] != expected for name, expected in {
                "max_total_tokens": document["device_full_tokens"], "max_running_requests": 24,
                "chunked_prefill_size": 2048, "enable_mixed_chunk": False,
                "attention_backend": "fa3", "enable_beliefkv_admission": False,
                "enable_hierarchical_cache": False,
            }.items()) or info["cuda_graph_config"]["decode"]["bs"] != buckets:
                raise ValueError("native component configuration changed")
            write(directory / "server/server_info.json", info)
            if cold_prefill_only:
                run_cold_prefill(client, document, directory, receipts)
            for case in (() if cold_prefill_only else document["cases"]):
                fresh.check_hold(document, released=True)
                response = client.post("/flush_cache")
                response.raise_for_status()
                stages = ["warm", "measure0", "measure1", "measure2"]
                if profile and case["width"] == 9:
                    stages.append("profile")
                for label in stages:
                    count = 1 if label == "warm" else measurement_tokens
                    request = payload(case, label, count)
                    if label == "profile":
                        response = client.post("/start_profile", json={
                            "output_dir": str(directory / "profile"), "num_steps": 24,
                            "profile_stages": ["decode"], "profile_by_stage": True,
                            "activities": ["CPU", "GPU"], "with_stack": False, "record_shapes": False,
                        })
                        response.raise_for_status()
                    begin = time.monotonic()
                    response = client.post("/generate", json=request)
                    response.raise_for_status()
                    records = checked_response(response.json(), request, count)
                    receipts.update(records)
                    write(directory / f"width{case['width']}_{label}.json", {
                        "records": records, "wall_seconds": time.monotonic() - begin,
                    })
                    print(json.dumps({
                        "mode": mode, "width": case["width"], "stage": label,
                        "actual_output_tokens": count * case["width"],
                        "wall_seconds": time.monotonic() - begin,
                    }), flush=True)
                # Profile is stopped automatically at its registered decode count.
            wait_for_telemetry_drain(directory, server=server, pulse=lambda: fresh.check_hold(document, released=True))
            write(directory / "receipts.json", receipts)
        success = True
    finally:
        cleanup(directory, owned, "component_complete" if success else "component_failed")
        server.wait(timeout=15.)
    summary = service_summary(directory / "server/runtime_audit.jsonl", receipts)
    summary["profiles"] = [
        trace_summary(path) for path in (directory / "profile").glob("*.trace.json*")
    ]
    if profile and not summary["profiles"]:
        raise ValueError("native profiling did not produce a CUDA trace")
    write(directory / "summary.json", summary)
    return summary


def run(
    inputs, output, *, modes, profile, cold_prefill_only=False,
    measurement_tokens=96,
):
    check_measurement_tokens(measurement_tokens)
    if cold_prefill_only and measurement_tokens != 96:
        raise ValueError("cold prefill does not accept a decode-length override")
    if cold_prefill_only and not profile:
        raise ValueError("cold prefill probe requires an explicit CUDA profile")
    document = json.loads(inputs.read_bytes())
    for filename, expected in document["source_sha256"].items():
        if sha256_file(Path(filename)) != expected:
            raise ValueError("prepared component source changed: " + filename)
    engine = Path(document["engine_checkout"])
    state = git_source_state(engine, canonical_patch=engine_patch(ROOT, document["engine_tree"]))
    if state["canonical_patch"]["actual_tree"] != document["engine_tree"]:
        raise ValueError("native engine changed")
    if os.environ.get("SGLANG_SOURCE_CHECKOUT") != str(engine):
        raise ValueError("select the pinned native source before starting")
    output.mkdir(parents=True, exist_ok=False)
    write(output / "plan.json", {
        "inputs": str(inputs.resolve()), "inputs_sha256": sha256_file(inputs),
        "native": state, "modes": modes, "profile": profile,
        "cold_prefill_only": cold_prefill_only,
        "measurement_tokens": 1 if cold_prefill_only else measurement_tokens,
        "script_sha256": sha256_file(Path(__file__)),
        "independent_greedy_component_only": True, "performance_accepted": False,
    })
    results = {
        mode: run_mode(
            document, mode, output / mode, profile=profile,
            cold_prefill_only=cold_prefill_only, measurement_tokens=measurement_tokens,
        )
        for mode in modes
    }
    write(output / "result.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "modes": results,
        "GPU_action": True, "workflow_JCT_measured": False, "performance_accepted": False,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--modes", nargs="+", choices=("sparse", "exact"), default=["sparse", "exact"])
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--cold-prefill-only", action="store_true")
    parser.add_argument("--measurement-tokens", type=int, default=96)
    args = parser.parse_args()
    if len(args.modes) != len(set(args.modes)):
        parser.error("each native mode can run once per evidence directory")
    run(
        args.inputs, args.output, modes=args.modes, profile=args.profile,
        cold_prefill_only=args.cold_prefill_only, measurement_tokens=args.measurement_tokens,
    )


if __name__ == "__main__":
    main()
