"""Screen native FA3 SplitKV parameters with captured shapes and synthetic Q/K/V."""

from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts.run_causal_comparison import fresh, qualified
from scripts.train_workflow_work import write
from scripts.tune_moe_token_cost import select_candidate

SPLITS = (0, 1, 4, 8, 16, 32, 64)


def describe_case(case):
    lengths = case["context_lengths"]
    if (
        len(lengths) != case["width"] or not lengths
        or any(type(value) is not int or not 1 <= value <= 262144 for value in lengths)
    ):
        raise ValueError("invalid captured attention shape")
    return {
        "width": len(lengths), "context_lengths": lengths,
        "kv_tokens": sum(lengths), "page_size": 1, "query_heads": 32,
        "kv_heads": 4, "head_dim": 128, "query_tokens_per_member": 1,
        "kv_bytes_per_layer": sum(lengths) * 4 * 128 * 2 * 2,
    }


def benchmark(inputs, output):
    document = json.loads(inputs.read_bytes())
    for filename, expected in document["source_sha256"].items():
        if sha256_file(Path(filename)) != expected:
            raise ValueError("captured component input changed")
    fresh.check_hold(document, released=True)
    resources = qualified.resources(18960)
    if not resources["ready"]:
        raise ValueError("GPU resources unavailable")
    engine = Path(document["engine_checkout"])
    native = git_source_state(engine, canonical_patch=engine_patch(ROOT, document["engine_tree"]))
    if (
        native["canonical_patch"]["actual_tree"] != document["engine_tree"]
        or os.environ.get("SGLANG_SOURCE_CHECKOUT") != str(engine)
    ):
        raise ValueError("select the pinned native source")
    output.mkdir(parents=True, exist_ok=False)
    import torch
    from sgl_kernel import flash_attn

    device = torch.cuda.get_device_name()
    if device != "NVIDIA H200 NVL":
        raise ValueError("this bounded shape screen targets the retained H200")
    write(output / "startup.json", {
        "resources": resources, "native": native, "device": device,
        "inputs_sha256": sha256_file(inputs), "script_sha256": sha256_file(Path(__file__)),
        "native_wrapper": flash_attn.__file__,
        "native_wrapper_sha256": sha256_file(Path(flash_attn.__file__)),
        "candidate_num_splits": SPLITS, "synthetic_QKV_not_captured_values": True,
        "GPU_action": True, "workflow_JCT_measured": False, "performance_accepted": False,
    })
    torch.manual_seed(21)
    records = []
    with torch.inference_mode(), torch.cuda.device(0):
        for case in document["cases"]:
            fresh.check_hold(document, released=True)
            shape = describe_case(case)
            width, lengths, slots = shape["width"], shape["context_lengths"], shape["kv_tokens"]
            q = torch.randn(width, 32, 128, dtype=torch.bfloat16, device="cuda")
            k = torch.randn(slots, 1, 4, 128, dtype=torch.bfloat16, device="cuda")
            v = torch.randn_like(k)
            seq = torch.tensor(lengths, dtype=torch.int32, device="cuda")
            cu = torch.arange(width + 1, dtype=torch.int32, device="cuda")
            layouts = {}
            for name in ("sequential", "shuffled"):
                pages = (
                    torch.arange(slots, dtype=torch.int32, device="cuda")
                    if name == "sequential"
                    else torch.randperm(slots, dtype=torch.int32, device="cuda")
                )
                table = torch.zeros(width, max(lengths), dtype=torch.int32, device="cuda")
                offset = 0
                for index, length in enumerate(lengths):
                    table[index, :length].copy_(pages[offset:offset + length])
                    offset += length
                layouts[name] = table
            graphs, results, references, rows = {}, {}, {}, []
            # Keep metadata tensors and output storage alive through every replay.
            buffers = []
            for index, splits in enumerate(SPLITS):
                samples, close, deltas, captures = {}, True, {}, {}
                for name, table in layouts.items():
                    metadata = flash_attn.get_scheduler_metadata(
                        batch_size=width, max_seqlen_q=1, max_seqlen_k=max(lengths),
                        num_heads=32, num_heads_k=4, headdim=128, cache_seqlens=seq,
                        qkv_dtype=torch.bfloat16, cu_seqlens_q=cu,
                        page_size=1, causal=True, num_splits=splits,
                    )

                    def forward():
                        return flash_attn.flash_attn_with_kvcache(
                            q, k, v, page_table=table, cache_seqlens=seq,
                            cu_seqlens_q=cu, max_seqlen_q=1, causal=True,
                            num_splits=splits, scheduler_metadata=metadata,
                        )

                    value = forward()
                    torch.cuda.synchronize()
                    if index == 0:
                        references[name] = value.detach().clone()
                    reference = references[name]
                    deltas[name] = (value.float() - reference.float()).abs().max().item()
                    close = close and bool(torch.isfinite(value).all()) and torch.allclose(
                        value, reference, rtol=.02, atol=.002,
                    )
                    graph = torch.cuda.CUDAGraph()
                    with torch.cuda.graph(graph):
                        for _ in range(5):
                            result = forward()
                    torch.cuda.synchronize()
                    captures[name] = graph
                    results[(index, name)] = result
                    buffers.append(metadata)
                    samples[name] = []
                graphs[index] = captures
                rows.append({
                    "candidate_index": index, "num_splits": splits,
                    "numerically_close": close, "maximum_absolute_output_delta": deltas,
                    "scenario_samples_ms": samples,
                })
            for repeat in range(7):
                fresh.check_hold(document, released=True)
                order = list(range(len(SPLITS)))
                if repeat % 2:
                    order.reverse()
                for index in order:
                    for name, graph in graphs[index].items():
                        graph.replay()
                        start, finish = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
                        start.record()
                        graph.replay()
                        finish.record()
                        finish.synchronize()
                        rows[index]["scenario_samples_ms"][name].append(start.elapsed_time(finish) / 5)
            for row in rows:
                row["scenario_median_ms"] = {
                    name: statistics.median(samples)
                    for name, samples in row["scenario_samples_ms"].items()
                }
            selected = select_candidate(rows)
            record = {
                **shape, "candidates": rows, "selected_num_splits": selected["num_splits"],
                "selected_synthetic_cost_reduction_percent": {
                    name: 100 * (1 - value / rows[0]["scenario_median_ms"][name])
                    for name, value in selected["scenario_median_ms"].items()
                },
            }
            records.append(record)
            write(output / f"width{width}.json", record)
            print(json.dumps({
                "width": width, "selected_num_splits": selected["num_splits"],
                "screen_reduction_percent": record["selected_synthetic_cost_reduction_percent"],
            }), flush=True)
            for captures in graphs.values():
                for graph in captures.values():
                    graph.reset()
            del graphs, captures, graph, buffers, results, references, layouts, table, pages
            del q, k, v, seq, cu, value, reference, result, metadata
            torch.cuda.empty_cache()
    write(output / "result.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "by_width": records, "inputs_sha256": sha256_file(inputs),
        "script_sha256": sha256_file(Path(__file__)),
        "scope": [
            "Context lengths are actual captured decode batches; Q/K/V values and page maps are synthetic.",
            "Both page layouts retain the same BF16 full-attention semantics within each candidate comparison.",
            "CUDA events measure five captured attention calls; metadata construction is outside the measured graph.",
            "Selection requires numerical closeness, <=2% regression per layout and >=3% geometric improvement.",
            "This is a potential screen, not a native model, scheduling, throughput or workflow JCT experiment.",
            "No engine default, KV capacity, source workload or baseline is changed.",
        ],
        "GPU_action": True, "real_model_validation_required": True,
        "performance_accepted": False,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    benchmark(args.inputs.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
