"""Initialize experiment-process RNGs without initializing a CUDA context."""

from __future__ import annotations

import os
import random


def seed_experiment_process(seed: int | None) -> dict:
    if seed is None:
        return {"seed": None, "configured": False}
    if type(seed) is not int or not 0 <= seed <= 4294967295:
        raise ValueError("experiment seed must be an unsigned 32-bit integer")
    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.random.default_generator.manual_seed(seed)
    return {
        "seed": seed, "configured": True,
        "python_random": seed, "numpy_legacy_random": seed, "torch_cpu": seed,
        "python_hash_seed": os.environ.get("PYTHONHASHSEED"),
        "python_hash_probe": hash("BeliefKV experiment hash seed"),
        "cuda_initialized": torch.cuda.is_initialized(),
        "limits": (
            "Independent generators, explicit task reseeding, OS entropy, concurrent tools "
            "and batch-dependent floating-point execution are not fixed by global RNG seeds."
        ),
    }
