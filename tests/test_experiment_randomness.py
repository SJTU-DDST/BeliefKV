import json
import os
from pathlib import Path
import random
import subprocess
import sys
from unittest.mock import Mock

import numpy as np
import pytest
import torch

from beliefkv.experiments.deepagents_swebench import DockerWorkspaceBackend
from beliefkv.runtime.experiment_randomness import seed_experiment_process


def test_client_rngs_repeat_without_initializing_cuda(monkeypatch):
    monkeypatch.setenv("PYTHONHASHSEED", "21")
    before = torch.cuda.is_initialized()
    first = seed_experiment_process(21)
    values = random.random(), float(np.random.random()), float(torch.rand(()))
    second = seed_experiment_process(21)
    assert values == (random.random(), float(np.random.random()), float(torch.rand(())))
    assert first == second
    assert first["python_hash_seed"] == "21"
    assert torch.cuda.is_initialized() is before


@pytest.mark.parametrize("seed", (-1, 4294967296, True, 1.5))
def test_invalid_global_seed_is_rejected(seed):
    with pytest.raises(ValueError):
        seed_experiment_process(seed)


def test_optional_unseeded_client_remains_explicit():
    assert seed_experiment_process(None) == {"seed": None, "configured": False}


def test_sandbox_start_and_every_exec_receive_the_same_hash_and_random_seed(tmp_path):
    backend = DockerWorkspaceBackend(tmp_path, image="fixture", audit=Mock(), random_seed=21)
    environment = backend._docker_environment_args()
    assert "PYTHONHASHSEED=21" in environment
    assert "BELIEFKV_SANDBOX_RANDOM_SEED=21" in environment
    command = backend._docker_exec_argv("python -c 'print(1)'")
    assert "PYTHONHASHSEED=21" in command
    assert "BELIEFKV_SANDBOX_RANDOM_SEED=21" in command


def test_tool_python_global_random_and_hash_repeat_across_processes():
    support = Path(__file__).resolve().parents[1] / "beliefkv/experiments/sandbox_support"
    environment = {
        **os.environ, "PYTHONPATH": str(support),
        "PYTHONHASHSEED": "21", "BELIEFKV_SANDBOX_RANDOM_SEED": "21",
        "CUDA_VISIBLE_DEVICES": "99999",
    }
    command = [
        sys.executable, "-c",
        "import random,json; print(json.dumps([random.random(),hash('fixed-tool-seed')]))",
    ]
    first = subprocess.check_output(command, env=environment, text=True)
    second = subprocess.check_output(command, env=environment, text=True)
    assert json.loads(first) == json.loads(second)
