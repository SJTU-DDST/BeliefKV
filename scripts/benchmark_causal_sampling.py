#!/usr/bin/env python3
"""Measure replay sampling dispatch separately from model and workflow performance."""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.causal_replay_sampling import (
    CapturedOutputLogitsProcessor, PARAMETER_KEY, apply_position_logits,
    sample_with_positions,
)
from beliefkv.experiments.source_provenance import sha256_file

VARIANTS = ("native_greedy", "direct_forcing", "native_forcing_dispatch")


class ReplaySamplingBatch:
    def __init__(self, mask, parameters):
        self.custom_params = parameters
        self.custom_logit_processor = {
            "captured": (CapturedOutputLogitsProcessor(), mask),
        }
        import torch

        self.forward = SimpleNamespace(
            sampling_info=self,
            seq_lens_cpu=torch.tensor([
                len(row["__req__"].origin_input_ids) for row in parameters
            ]) if all(hasattr(row["__req__"], "origin_input_ids") for row in parameters) else None,
        )

    def __len__(self):
        return len(self.custom_params)


def step(variant, logits, batch, native_dispatch):
    import torch

    if variant == "direct_forcing":
        CapturedOutputLogitsProcessor()(logits, batch.custom_params)
    elif variant == "native_forcing_dispatch":
        native_dispatch(logits, batch)
    elif variant == "position_forcing_dispatch":
        def sample(_runner, values, forward):
            apply_position_logits(native_dispatch, values, forward.sampling_info)
        sample_with_positions(sample, None, logits, batch.forward)
    elif variant != "native_greedy":
        raise ValueError("unknown sampling benchmark variant")
    return torch.argmax(logits, -1)


def distribution(values):
    return {
        "samples": len(values), "mean_ms": statistics.mean(values),
        "median_ms": statistics.median(values), "min_ms": min(values),
        "max_ms": max(values),
    }


def validate_limits(samples, cycles):
    if (
        type(samples) is not int or not 3 <= samples <= 31
        or type(cycles) is not int or not 1 <= cycles <= 512
    ):
        raise ValueError("benchmark requires 3-31 samples and 1-512 cycles")


def calibration_batch_sizes(running):
    if type(running) is not int or not 1 <= running <= 9999:
        raise ValueError("sampling calibration requires a positive running limit")
    return sorted({1, 8, running})


def validate_batch_sizes(sizes):
    if (
        not isinstance(sizes, (list, tuple)) or not sizes
        or any(type(size) is not int or not 1 <= size <= 9999 for size in sizes)
        or list(sizes) != sorted(set(sizes))
    ):
        raise ValueError("sampling batch sizes must be sorted distinct positive integers")
    return tuple(sizes)


def run(args):
    validate_limits(args.samples, args.cycles)
    sizes = validate_batch_sizes(getattr(args, "batch_sizes", (1, 8)))
    if args.output.exists():
        raise FileExistsError("preserve previous sampling calibration")
    native_path = args.engine / "python/sglang/srt/layers/sampler.py"
    sys.path.insert(0, str(args.engine / "python"))
    native = importlib.import_module("sglang.srt.layers.sampler")
    if Path(native.__file__).resolve() != native_path.resolve():
        raise ValueError("benchmark imported another native sampler")
    import torch

    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise ValueError("calibration requires exactly one explicitly visible GPU")
    torch.manual_seed(21)
    config = json.loads((args.model / "config.json").read_bytes())
    vocabulary = config["text_config"]["vocab_size"]
    variants = (*VARIANTS, "position_forcing_dispatch") if args.position else VARIANTS
    cases = []
    for size in sizes:
        targets = torch.arange(3, size + 3, device="cuda", dtype=torch.int64)
        template = torch.zeros((size, vocabulary), device="cuda", dtype=torch.float32)
        template.scatter_(1, targets[:, None], 1.)
        work = template.clone()
        batch = ReplaySamplingBatch(
            torch.ones(size, device="cuda", dtype=torch.bool),
            [
                {
                    "__req__": SimpleNamespace(
                        rid=str(row), output_ids=[], origin_input_ids=[1] * 64,
                    ),
                    PARAMETER_KEY: {
                        "request_id": str(row), "output_token_ids": [row + 3],
                    },
                }
                for row in range(size)
            ],
        )
        costs = {variant: {"wall_ms": [], "device_stream_ms": []} for variant in variants}
        for sample in range(args.samples + 2):
            order = variants[sample % len(variants):] + variants[:sample % len(variants)]
            for variant in order:
                torch.cuda.synchronize()
                begin, end = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
                started = time.perf_counter_ns()
                begin.record()
                for _ in range(args.cycles):
                    work.copy_(template)
                    result = step(variant, work, batch, native.apply_custom_logit_processor)
                end.record()
                end.synchronize()
                elapsed = (time.perf_counter_ns() - started) / 1e6 / args.cycles
                device = begin.elapsed_time(end) / args.cycles
                if not torch.equal(result, targets):
                    raise ValueError("sampling benchmark changed expected token identities")
                if sample >= 2:
                    costs[variant]["wall_ms"].append(elapsed)
                    costs[variant]["device_stream_ms"].append(device)
        measured = {
            variant: {metric: distribution(values) for metric, values in metrics.items()}
            for variant, metrics in costs.items()
        }
        cases.append({
            "batch_size": size, "vocabulary": vocabulary, "logits_dtype": "float32",
            "cost_per_step": measured,
            "native_dispatch_increment_mean_wall_ms": (
                measured["native_forcing_dispatch"]["wall_ms"]["mean_ms"]
                - measured["native_greedy"]["wall_ms"]["mean_ms"]
            ),
        })
    return {
        "schema_version": 1, "samples": args.samples, "cycles": args.cycles,
        "position_dispatch_included": args.position,
        "gpu_name": torch.cuda.get_device_name(), "torch_version": torch.__version__,
        "model_config_sha256": sha256_file(args.model / "config.json"),
        "native_sampler": str(native_path.resolve()),
        "native_sampler_sha256": sha256_file(native_path),
        "benchmark_sha256": sha256_file(Path(__file__)),
        "forcing_sha256": sha256_file(ROOT / "beliefkv/experiments/causal_replay_sampling.py"),
        "seed": 21, "cases": cases, "performance_accepted": False,
        "scope": (
            "Actual native custom-logit dispatch plus greedy argmax on synthetic "
            f"batch sizes {list(sizes)} logits. All variants include the same logits reset. "
            "No model forward, delayed-sampling comparison, token transport, KV "
            "migration or workflow JCT is measured. The increment is a component "
            "cost, not a correction to subtract from end-to-end measurements."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=11)
    parser.add_argument("--cycles", type=int, default=64)
    parser.add_argument("--position", action="store_true")
    parser.add_argument("--batch-sizes", nargs="+", type=int, default=[1, 8])
    args = parser.parse_args()
    report = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "cases": [
            {key: row[key] for key in ("batch_size", "native_dispatch_increment_mean_wall_ms")}
            for row in report["cases"]
        ],
        "performance_accepted": False,
    }, indent=2))


if __name__ == "__main__":
    main()
