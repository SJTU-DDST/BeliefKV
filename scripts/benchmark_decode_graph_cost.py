"""Bounded same-model sparse/exact graph replay with captured request prefixes."""

from array import array
from datetime import datetime, timezone
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.native_engine_variant import engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts.audit_native_system_costs import distribution
from scripts.train_workflow_work import write

WIDTHS = (3, 5, 7, 8, 9, 13, 17)
SPARSE = [1, 2, 4, 8, 12, 16, 24]


def paired_summary(blocks):
    grouped = {name: [row for row in blocks if row["mode"] == name] for name in ("sparse", "exact")}
    if not grouped["sparse"] or len(grouped["sparse"]) != len(grouped["exact"]):
        raise ValueError("both graph modes require equal nonempty measurement blocks")
    medians = {
        name: statistics.median(row["cuda_ms_per_forward"] for row in rows)
        for name, rows in grouped.items()
    }
    return {
        "cuda_ms_per_forward_median_of_blocks": medians,
        "exact_cuda_time_reduction_percent": 100 * (1 - medians["exact"] / medians["sparse"]),
        "block_distributions": {
            name: distribution(row["cuda_ms_per_forward"] for row in rows)
            for name, rows in grouped.items()
        },
    }


def prepare(registration, accounting, output):
    plan, evidence = (json.loads(path.read_bytes()) for path in (registration, accounting))
    source = Path(plan["source_arm"])
    expected = {
        rid: {kind: (tuple(identity), count) for kind, (identity, count) in boundaries.items()}
        for rid, boundaries in json.loads((source / "expected.json").read_bytes()).items()
    }
    cases = []
    with CapturedTokenCorpus(source / "server/request_tokens.jsonl", expected) as corpus:
        if corpus.evidence["token_source_sha256"] != plan["token_source_sha256"]:
            raise ValueError("captured token source changed")
        for width in WIDTHS:
            options = [
                row for row in evidence["representative_decode_batches"]
                if row["width"] == width and 20000 <= statistics.mean(row["context_lengths"]) <= 65536
                and sum(row["context_lengths"]) + width < plan["source_capacity"]["device_full_tokens"]
            ]
            if not options:
                raise ValueError("no capacity-safe observed batch for width " + str(width))
            row = min(options, key=lambda item: abs(statistics.mean(item["context_lengths"]) - 40000))
            members = []
            for request, length in zip(row["request_ids"], row["context_lengths"], strict=True):
                captured = corpus.get(request)
                tokens = captured.prompt_tokens + captured.output_tokens
                if not 2 <= length <= len(tokens):
                    raise ValueError("decode context is not an actual captured token prefix")
                members.append({
                    "request_id": request, "tokens": list(tokens[:length]),
                    "source_prompt_sha256": captured.prompt_sha256,
                    "source_output_sha256": captured.output_sha256,
                })
            cases.append({**row, "members": members})
    document = {
        "schema_version": 1, "kind": "captured_decode_graph_component_probe",
        "registration": str(registration.resolve()), "cases": cases,
        "device_full_tokens": plan["source_capacity"]["device_full_tokens"],
        "engine_checkout": plan["engine_checkout"], "engine_tree": plan["engine_tree"],
        "hold_file": plan["hold_file"], "sparse_buckets": SPARSE,
        "capture_buckets": list(range(1, 25)),
        "source_sha256": {
            str(path.resolve()): sha256_file(path)
            for path in (registration, accounting, Path(__file__), source / "expected.json")
        },
        "scope": [
            "Seven actual decode member/context/token combinations from the completed common window.",
            "Prefixes are computed by the real model in 2048-token chunks, without dummy KV or weights.",
            "The same model and fixed KV batch are replayed under two graph bucket lists.",
            "Repeated static forwards measure component execution, not newly emitted serving throughput or workflow JCT.",
            "Device capacity equals the retained control; no Host cache, admission policy or transfer is exercised.",
            "Graph capture max24 differs from the old max256, identically for both component choices.",
        ],
        "GPU_action": False, "performance_accepted": False,
    }
    write(output, document)
    return document


def run(document, output_dir, *, profile):
    from scripts.run_causal_comparison import fresh, qualified

    for filename, expected in document["source_sha256"].items():
        if sha256_file(Path(filename)) != expected:
            raise ValueError("component source/input changed: " + filename)
    fresh.check_hold(document, released=True)
    resource = qualified.resources(18959)
    if not resource["ready"]:
        raise ValueError("component GPU resources unavailable: " + json.dumps(resource))
    engine = Path(document["engine_checkout"])
    native = git_source_state(engine, canonical_patch=engine_patch(ROOT, document["engine_tree"]))
    if native["canonical_patch"]["actual_tree"] != document["engine_tree"]:
        raise ValueError("component engine differs from the retained control")
    if os.environ.get("SGLANG_SOURCE_CHECKOUT") != str(engine):
        raise ValueError("launch component against its declared native checkout")
    import torch
    from sglang.benchmark import one_batch as bench
    from sglang.srt.distributed.parallel_state import destroy_distributed_environment, destroy_model_parallel
    from sglang.srt.entrypoints.engine import _set_envs_and_config
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.model_executor.forward_batch_info import ForwardBatch
    from sglang.srt.sampling.sampling_params import SamplingParams
    from sglang.srt.server_args import PortArgs, ServerArgs

    output_dir.mkdir(parents=True, exist_ok=False)
    write(output_dir / "startup.json", {
        "resources": resource, "native": native,
        "python": sys.executable, "torch": torch.__version__,
        "hold_sha256": sha256_file(Path(document["hold_file"])),
        "probe_source_sha256": sha256_file(Path(__file__)),
        "inputs_prepared_before_GPU": True,
    })
    signal.alarm(1200)
    torch.manual_seed(21)
    args = ServerArgs(
        model_path="/srv/ai/models/Qwen/Qwen3-Coder-30B-A3B-Instruct",
        dtype="bfloat16", kv_cache_dtype="bfloat16", tp_size=1,
        max_total_tokens=document["device_full_tokens"], max_running_requests=24,
        context_length=262144, chunked_prefill_size=2048,
        mem_fraction_static=.94, attention_backend="fa3", random_seed=21,
        cuda_graph_config={
            "decode": {"backend": "full", "max_bs": 24, "bs": document["capture_buckets"]},
            "prefill": {"backend": "breakable", "max_bs": 2048},
        },
    )
    args.resolve_once()
    _set_envs_and_config(args)
    bench.publish(args, role="scheduler")
    bench.initialize_moe_config()
    bench.initialize_fp8_gemm_config()
    bench.initialize_fp4_gemm_config()
    bench.configure_logger(args, prefix=" token-cost")
    wrapper, _tokenizer = bench.load_model(args, PortArgs.init_new(args), 0, 0)
    runner, reports = wrapper.torch_runner, []
    graph = runner.decode_cuda_graph_runner
    if graph.capture_bs != document["capture_buckets"]:
        raise ValueError("actual captured graph list changed")
    if runner.max_total_num_tokens != document["device_full_tokens"]:
        raise ValueError("component physical capacity differs from control")
    write(output_dir / "model.json", {
        "device": torch.cuda.get_device_name(), "max_total_num_tokens": runner.max_total_num_tokens,
        "decode_capture_bs": graph.capture_bs, "dtype": "bfloat16",
        "kv_cache_dtype": "bfloat16", "attention_backend": "fa3",
        "component_Host_KV_tokens": 0, "retained_control_Host_KV_exercised": False,
    })

    try:
        with torch.inference_mode():
            for case in document["cases"]:
                fresh.check_hold(document, released=True)
                wrapper.synchronize()
                wrapper.clear()
                print(json.dumps({"stage": "build_prefixes", "width": case["width"]}), flush=True)
                reqs = []
                for member in case["members"]:
                    tokens = array("q", member["tokens"][:-1])
                    req = Req(
                        rid=member["request_id"], origin_input_text="", origin_input_ids=tokens,
                        sampling_params=SamplingParams(temperature=0., max_new_tokens=1),
                    )
                    req.full_untruncated_fill_ids = tokens
                    req.logprob_start_len = -1
                    # Leave one final prefix token for a shared final extend.
                    end = 0
                    while end < len(tokens) - 1:
                        previous = end
                        end = min(len(tokens) - 1, end + 2048)
                        req.set_extend_range(previous, end)
                        if previous == 0:
                            print(json.dumps({
                                "stage": "first_chunk", "request_id": req.rid,
                                "prefix": previous, "end": end,
                            }), flush=True)
                        wrapper.extend([req])
                        # Direct benchmark calls lack the scheduler's stream/lifetime boundary.
                        wrapper.synchronize()
                        req.prefix_indices = runner.req_to_token_pool.req_to_token[
                            req.kv.req_pool_idx, :end
                        ].clone()
                    req.set_extend_range(end, len(tokens))
                    reqs.append(req)
                _next, _logits, batch = wrapper.extend(reqs)
                batch.input_ids = torch.tensor(
                    [member["tokens"][-1] for member in case["members"]],
                    dtype=torch.int64, device="cuda",
                )
                batch.prepare_for_decode()
                wrapper.synchronize()
                observed_lengths = batch.seq_lens.cpu().tolist()
                if observed_lengths != case["context_lengths"]:
                    raise ValueError("actual decode KV lengths differ from observed serving")

                def forward():
                    fb = ForwardBatch.init_new(
                        batch, runner, return_hidden_states_before_norm=False,
                    )
                    result = runner.forward(fb)
                    if not result.can_run_graph:
                        raise ValueError("component unexpectedly fell back from graph replay")
                    return result.logits_output.next_token_logits

                blocks, numerical = [], {}
                for block in range(8):
                    order = ("sparse", "exact") if block % 2 == 0 else ("exact", "sparse")
                    for mode in order:
                        graph.capture_bs = SPARSE if mode == "sparse" else document["capture_buckets"]
                        for _ in range(3):
                            forward()
                        wrapper.synchronize()
                        start, finish = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
                        cpu_start, wall_start = time.thread_time_ns(), time.perf_counter()
                        start.record()
                        for _ in range(12):
                            logits = forward()
                        finish.record()
                        wrapper.synchronize()
                        blocks.append({
                            "block": block, "mode": mode, "replays": 12,
                            "cuda_ms_per_forward": start.elapsed_time(finish) / 12,
                            "wall_ms_per_forward": (time.perf_counter() - wall_start) * 1000 / 12,
                            "thread_cpu_ms_per_forward": (time.thread_time_ns() - cpu_start) / 1e6 / 12,
                            "actual_graph_bs": graph.bs,
                        })
                        numerical[mode] = logits.detach().clone()
                delta = (numerical["sparse"].float() - numerical["exact"].float()).abs()
                validation = {
                    "max_logit_absolute_delta": delta.max().item(),
                    "mean_logit_absolute_delta": delta.mean().item(),
                    "greedy_token_ids_equal": torch.equal(
                        numerical["sparse"].argmax(-1), numerical["exact"].argmax(-1),
                    ),
                    "all_logits_finite": all(torch.isfinite(value).all().item() for value in numerical.values()),
                }
                if profile and case["width"] in (9, 17):
                    for mode in ("sparse", "exact"):
                        graph.capture_bs = SPARSE if mode == "sparse" else document["capture_buckets"]
                        with torch.profiler.profile(
                            activities=[torch.profiler.ProfilerActivity.CPU, torch.profiler.ProfilerActivity.CUDA],
                            record_shapes=False, with_stack=False,
                        ) as profiler:
                            for _ in range(3):
                                with torch.profiler.record_function("static_decode_forward"):
                                    forward()
                            wrapper.synchronize()
                        profiler.export_chrome_trace(str(output_dir / f"width{case['width']}_{mode}.trace.json.gz"))
                        (output_dir / f"width{case['width']}_{mode}.kernels.txt").write_text(
                            profiler.key_averages().table(sort_by="self_cuda_time_total", row_limit=40),
                        )
                record = {
                    "width": case["width"], "context_lengths": case["context_lengths"],
                    "forward_iter_from_control": case["forward_iter"], "blocks": blocks,
                    "numerical_check": validation, **paired_summary(blocks),
                }
                write(output_dir / f"width{case['width']}.json", record)
                reports.append(record)
                print(json.dumps({
                    "width": case["width"], "cost": record["cuda_ms_per_forward_median_of_blocks"],
                    "time_reduction_percent": record["exact_cuda_time_reduction_percent"],
                    "numerical": validation,
                }), flush=True)
        write(output_dir / "result.json", {
            "observed_at": datetime.now(timezone.utc).isoformat(), "cases": reports,
            "same_model_instance_KV_and_physical_capacity": True,
            "sparse_vs_exact_only_capture_list_changes": True,
            "all_greedy_tokens_equal": all(row["numerical_check"]["greedy_token_ids_equal"] for row in reports),
            "GPU_action": True, "whole_workflow_performance_test": False,
            "performance_accepted": False,
        })
    finally:
        graph.capture_bs = document["capture_buckets"]
        destroy_model_parallel()
        destroy_distributed_environment()
        signal.alarm(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pre = commands.add_parser("prepare")
    for name in ("registration", "accounting", "output"):
        pre.add_argument("--" + name, type=Path, required=True)
    gpu = commands.add_parser("run")
    gpu.add_argument("--inputs", type=Path, required=True)
    gpu.add_argument("--output-dir", type=Path, required=True)
    gpu.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.registration, args.accounting, args.output)
    else:
        with Path("/tmp/beliefkv-lowlevel-probe.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            run(json.loads(args.inputs.read_bytes()), args.output_dir, profile=args.profile)


if __name__ == "__main__":
    main()
