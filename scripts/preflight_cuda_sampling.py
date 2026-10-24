#!/usr/bin/env python3
"""Check the real seeded sampler before allocating a model and Host pools."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--engine", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve the previous sampling preflight")
    import torch
    from flashinfer.sampling import top_k_top_p_sampling_from_probs
    import sglang
    from sglang.kernels.ops.activation.activation import silu_and_mul

    if not Path(sglang.__file__).resolve().is_relative_to(args.engine.resolve() / "python"):
        raise ValueError("native preflight imported a different SGLang engine")

    started = time.monotonic()
    vocabulary = 248320
    rows = []
    for size in (1, 8, 24):
        logits = torch.linspace(-8., 8., vocabulary, device="cuda").repeat(size, 1)
        probabilities = torch.softmax(logits / .6, dim=-1)
        samples = []
        for _ in range(2):
            torch.manual_seed(21)
            samples.append(top_k_top_p_sampling_from_probs(
                probabilities, 20, .95, filter_apply_order="joint",
            ))
        torch.cuda.synchronize()
        assert torch.equal(*samples), "seeded sampler did not repeat"
        assert samples[0].numel() == size
        assert bool(torch.all((samples[0] >= vocabulary - 20) & (samples[0] < vocabulary)))
        rows.append({"batch_size": size, "seed_repeated": True, "top_k_support_verified": True})
    activation_input = torch.linspace(-4., 4., 4 * 512, device="cuda").to(torch.bfloat16).view(4, 512)
    activation_output = silu_and_mul(activation_input)
    reference = (
        torch.nn.functional.silu(activation_input[:, :256].float())
        * activation_input[:, 256:].float()
    ).to(torch.bfloat16)
    torch.testing.assert_close(activation_output, reference, rtol=.02, atol=.02)
    torch.cuda.synchronize()
    result = {
        "gpu_sampling_verified": True,
        "native_kernel_verified": True,
        "performance_accepted": False,
        "scope": "Synthetic seeded sampling and native BF16 activation checks before model startup, not a workload result.",
        "cuda_home": os.environ["CUDA_HOME"],
        "engine": str(args.engine.resolve()),
        "device_name": torch.cuda.get_device_name(),
        "torch_cuda_version": torch.version.cuda,
        "pid": os.getpid(),
        "elapsed_seconds": time.monotonic() - started,
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
