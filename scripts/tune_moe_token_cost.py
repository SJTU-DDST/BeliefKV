"""Bounded BF16 MoE launch tuning; validate any candidate with real serving."""

from contextlib import ExitStack
from datetime import datetime, timezone
import argparse
import json
import math
import os
from pathlib import Path
import signal
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts.run_causal_comparison import fresh, qualified
from scripts.train_workflow_work import write

BASE_SMALL = {"BLOCK_SIZE_M": 16, "BLOCK_SIZE_N": 32, "BLOCK_SIZE_K": 64, "GROUP_SIZE_M": 1}
BASE_LARGE = {"BLOCK_SIZE_M": 64, "BLOCK_SIZE_N": 64, "BLOCK_SIZE_K": 32, "GROUP_SIZE_M": 8}
SIZES = (1, 2, 4, 8, 12, 16, 24)


def candidate_configs():
    return [
        BASE_SMALL,
        {**BASE_SMALL, "BLOCK_SIZE_N": 64, "num_warps": 4, "num_stages": 5},
        {**BASE_SMALL, "BLOCK_SIZE_N": 64, "BLOCK_SIZE_K": 128, "num_warps": 4, "num_stages": 3},
        {**BASE_SMALL, "BLOCK_SIZE_N": 64, "BLOCK_SIZE_K": 256, "num_warps": 4, "num_stages": 2},
        {**BASE_SMALL, "BLOCK_SIZE_N": 128, "BLOCK_SIZE_K": 128, "num_warps": 8, "num_stages": 5},
        {**BASE_SMALL, "BLOCK_SIZE_N": 128, "num_warps": 4, "num_stages": 3},
        {**BASE_SMALL, "BLOCK_SIZE_N": 64, "BLOCK_SIZE_K": 128, "num_warps": 4, "num_stages": 2},
        {**BASE_SMALL, "BLOCK_SIZE_K": 128, "num_warps": 4, "num_stages": 2},
    ]


def select_candidate(rows):
    baseline = next(row for row in rows if row["candidate_index"] == 0)
    reference = baseline["scenario_median_ms"]
    eligible = [
        row for row in rows if row["numerically_close"]
        and all(row["scenario_median_ms"][key] <= 1.02 * value for key, value in reference.items())
    ]
    best = min(
        eligible,
        key=lambda row: math.prod(
            row["scenario_median_ms"][key] / value for key, value in reference.items()
        ),
    )
    ratio = math.prod(
        best["scenario_median_ms"][key] / value for key, value in reference.items()
    ) ** (1 / len(reference))
    return best if ratio <= .97 else baseline


def guarded_lookup_table(selected):
    table = {str(size): selected[size] for size in SIZES}
    # Native lookup selects the nearest M. Explicit boundary keys preserve the
    # original default on every integer M above the bounded decode tuning range.
    table.update({str(size): BASE_SMALL for size in range(25, 129)})
    table.update({str(size): BASE_LARGE for size in range(129, 2049)})
    return table


def compact_table(table):
    original = {int(key): value for key, value in table.items()}
    runs, run = [], []
    for key in sorted(original):
        if run and original[key] != original[run[-1]]:
            runs.append(run)
            run = []
        run.append(key)
    runs.append(run)
    keep = {edge for run in runs for edge in (run[0], run[-1])}
    # Preserve native insertion order, including nearest-key tie breaking.
    compacted = {key: value for key, value in original.items() if key in keep}
    for size in range(1, 2 * max(original) + 1):
        before = original[min(original, key=lambda key: abs(key - size))]
        after = compacted[min(compacted, key=lambda key: abs(key - size))]
        if before != after:
            raise ValueError("compaction changed native nearest-M parameter selection")
    return {str(key): value for key, value in compacted.items()}


def compact(configuration, output):
    record = json.loads((configuration / "result.json").read_bytes())
    path = Path(record["configuration_path"])
    if sha256_file(path) != record["configuration_sha256"]:
        raise ValueError("tuned configuration changed")
    before = json.loads(path.read_bytes())
    after = compact_table(before)
    target = output / path.relative_to(configuration.resolve())
    write(target, after)
    write(output / "result.json", {
        **record, "configuration_path": str(target.resolve()),
        "configuration_sha256": sha256_file(target),
        "original_tuner_result": str((configuration / "result.json").resolve()),
        "original_tuner_result_sha256": sha256_file(configuration / "result.json"),
        "original_configuration_keys": len(before), "compacted_configuration_keys": len(after),
        "native_nearest_lookup_equivalent_for_M": [1, 2 * max(map(int, before))],
        "larger_M_always_uses_same_maximum_key": True,
        "CPU_only_compaction": True, "GPU_action": False,
        "compaction_source_sha256": sha256_file(Path(__file__)),
    })


def tune(inputs, output):
    document = json.loads(inputs.read_bytes())
    fresh.check_hold(document, released=True)
    resources = qualified.resources(18959)
    if not resources["ready"]:
        raise ValueError("GPU resources unavailable")
    engine = Path(document["engine_checkout"])
    state = git_source_state(engine, canonical_patch=engine_patch(ROOT, document["engine_tree"]))
    if state["canonical_patch"]["actual_tree"] != document["engine_tree"]:
        raise ValueError("native source changed")
    if os.environ.get("SGLANG_SOURCE_CHECKOUT") != str(engine):
        raise ValueError("select the pinned native source")
    output.mkdir(parents=True, exist_ok=False)
    write(output / "startup.json", {
        "resources": resources, "native": state, "inputs_sha256": sha256_file(inputs),
        "script_sha256": sha256_file(Path(__file__)),
    })
    import torch
    import triton
    from sglang.srt.distributed.parallel_state import (
        destroy_distributed_environment, destroy_model_parallel,
        init_distributed_environment, initialize_model_parallel,
    )
    from sglang.srt.layers.moe import initialize_moe_config
    from sglang.srt.layers.moe.moe_runner import MoeRunnerConfig
    from sglang.srt.layers.moe.moe_runner.triton_utils import override_config
    from sglang.srt.layers.moe.moe_runner.triton_utils.fused_moe import fused_moe
    from sglang.srt.layers.moe.moe_runner.triton_utils.fused_moe_triton_config import get_config_file_name
    from sglang.srt.layers.moe.topk import TopKConfig, select_experts
    from sglang.srt.runtime_context import publish
    from sglang.srt.server_args import PortArgs, ServerArgs

    signal.alarm(600)
    args = ServerArgs(model_path="/srv/ai/models/Qwen/Qwen3-Coder-30B-A3B-Instruct", random_seed=21)
    args.resolve_once()
    publish(args, role="scheduler")
    initialize_moe_config()
    port = PortArgs.init_new(args).nccl_port
    init_distributed_environment(
        world_size=1, rank=0, local_rank=0,
        distributed_init_method=f"tcp://127.0.0.1:{port}",
    )
    initialize_model_parallel()
    torch.manual_seed(21)
    torch.set_default_device("cuda")
    records, selected = {}, {}
    with ExitStack() as cleanup_stack, torch.inference_mode():
        cleanup_stack.callback(destroy_distributed_environment)
        cleanup_stack.callback(destroy_model_parallel)
        w1 = torch.randn(128, 1536, 2048, dtype=torch.bfloat16) / math.sqrt(2048)
        w2 = torch.randn(128, 2048, 768, dtype=torch.bfloat16) / math.sqrt(768)
        for size in SIZES:
            fresh.check_hold(document, released=True)
            x = torch.randn(size, 2048, dtype=torch.bfloat16)
            gating = torch.randn(size, 128, dtype=torch.float32)
            gates = {
                "uniform": gating,
                "skewed": gating + torch.cat((torch.full((size, 16), 1.5), torch.zeros(size, 112)), dim=1),
            }
            routed = {name: select_experts(x, gate, TopKConfig(top_k=8, renormalize=True)) for name, gate in gates.items()}
            references, graphs, rows = {}, {}, []
            for index, config in enumerate(candidate_configs()):
                captures, close, deltas = {}, True, {}
                for scenario, topk in routed.items():
                    def forward():
                        with override_config(config):
                            return fused_moe(
                                x, w1, w2, topk,
                                moe_runner_config=MoeRunnerConfig(inplace=False),
                            )
                    result = forward()
                    torch.cuda.synchronize()
                    if index == 0:
                        references[scenario] = result.detach().clone()
                    reference = references[scenario]
                    deltas[scenario] = (result.float() - reference.float()).abs().max().item()
                    close = close and torch.allclose(result, reference, rtol=.03, atol=.02)
                    graph = torch.cuda.CUDAGraph()
                    with torch.cuda.graph(graph):
                        for _ in range(5):
                            forward()
                    torch.cuda.synchronize()
                    captures[scenario] = graph
                graphs[index] = captures
                rows.append({
                    "candidate_index": index, "config": config, "numerically_close": close,
                    "max_absolute_output_delta": deltas, "scenario_samples_ms": defaultdict_list(gates),
                })
            # Reverse each round's order to expose drift, keeping all launch
            # candidates in the same allocation and routing conditions.
            for repeat in range(7):
                order = list(range(len(rows)))
                if repeat % 2:
                    order.reverse()
                for index in order:
                    for scenario, graph in graphs[index].items():
                        graph.replay()
                        start, finish = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
                        start.record()
                        graph.replay()
                        finish.record()
                        finish.synchronize()
                        rows[index]["scenario_samples_ms"][scenario].append(start.elapsed_time(finish) / 5)
            for row in rows:
                row["scenario_median_ms"] = {
                    scenario: statistics.median(samples)
                    for scenario, samples in row["scenario_samples_ms"].items()
                }
            best = select_candidate(rows)
            selected[size] = best["config"]
            records[str(size)] = {
                "candidates": rows, "selected_candidate_index": best["candidate_index"],
                "selected_config": best["config"],
            }
            write(output / f"size{size}.json", records[str(size)])
            print(json.dumps({
                "size": size, "selected": best["candidate_index"],
                "baseline_ms": rows[0]["scenario_median_ms"],
                "selected_ms": best["scenario_median_ms"],
            }), flush=True)
            for captures in graphs.values():
                for graph in captures.values():
                    graph.reset()
            del graphs
        table = guarded_lookup_table(selected)
        config = output / "configs" / ("triton_" + triton.__version__.replace(".", "_")) / get_config_file_name(128, 768, None)
        write(config, table)
        write(output / "result.json", {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "device": torch.cuda.get_device_name(), "triton_version": triton.__version__,
            "records": records, "configuration_path": str(config.resolve()),
            "configuration_sha256": sha256_file(config), "script_sha256": sha256_file(Path(__file__)),
            "numerical_dtype": "bfloat16", "top_k": 8, "experts": 128,
            "default_for_every_M_above_24_preserved": True,
            "routing_is_synthetic_not_captured_model_routing": True,
            "real_model_serving_validation_required": True,
            "GPU_action": True, "performance_accepted": False,
        })
    signal.alarm(0)


def defaultdict_list(gates):
    return {name: [] for name in gates}


def serve(inputs, configuration, output):
    from scripts.profile_native_token_cost import run

    record = json.loads((configuration / "result.json").read_bytes())
    config = Path(record["configuration_path"])
    if sha256_file(config) != record["configuration_sha256"]:
        raise ValueError("tuned configuration changed")
    original = os.environ.get("SGLANG_MOE_CONFIG_DIR")
    os.environ["SGLANG_MOE_CONFIG_DIR"] = str(configuration.resolve())
    write(output.with_suffix(".configuration.json"), {
        "environment": {"SGLANG_MOE_CONFIG_DIR": str(configuration.resolve())},
        "configuration": str(config), "configuration_sha256": sha256_file(config),
        "tuner_result_sha256": sha256_file(configuration / "result.json"),
        "wrapper_source_sha256": sha256_file(Path(__file__)),
        "compares_to": "same_sparse_graph_native_component",
    })
    try:
        run(inputs, output, modes=["sparse"], profile=True)
    finally:
        if original is None:
            os.environ.pop("SGLANG_MOE_CONFIG_DIR")
        else:
            os.environ["SGLANG_MOE_CONFIG_DIR"] = original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("tune", "serve", "compact"):
        selected = subparsers.add_parser(command)
        if command != "compact":
            selected.add_argument("--inputs", type=Path, required=True)
        selected.add_argument("--output", type=Path, required=True)
        if command in ("serve", "compact"):
            selected.add_argument("--configuration", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "tune":
        tune(args.inputs, args.output)
    elif args.command == "compact":
        compact(args.configuration.resolve(), args.output.resolve())
    else:
        serve(args.inputs, args.configuration, args.output)


if __name__ == "__main__":
    main()
