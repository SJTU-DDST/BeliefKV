"""Pinned, CPU-only Qwen task embeddings for offline continuous-work studies."""

import hashlib
from pathlib import Path
import re
import time

from beliefkv.predictor.workflow_rank_input import TASK_CHARS
from beliefkv.predictor.workflow_task_encoder import TASK_FEATURES, task_features


KIND = "qwen3_0_6b_frozen_task_cpu_v1"
REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
WEIGHTS_SHA256 = "0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd"
FILES = ("config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt")
INSTRUCTION = "Represent a software-agent task for predicting its total model execution work."
DIMENSION = 1024


def encoder_contract(directory):
    directory = Path(directory).resolve()
    digests = {}
    for name in FILES:
        with (directory / name).open("rb") as stream:
            digests[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    if digests["model.safetensors"] != WEIGHTS_SHA256:
        raise ValueError("Qwen workflow study requires the pinned public 0.6B weights")
    return {
        "kind": KIND, "directory": str(directory), "revision": REVISION, "files_sha256": digests,
        "instruction": INSTRUCTION, "maximum_task_characters": TASK_CHARS,
        "maximum_tokens": 8192, "embedding_dimension": DIMENSION, "task_features": list(TASK_FEATURES),
        "pooling": "last_nonpadding_token_then_float32_l2",
        "device": "cpu", "dtype": "bfloat16", "maximum_cpu_threads": 1, "remote_code": False,
    }


def validate_contract(raw):
    if not isinstance(raw, dict) or raw.keys() != {
        "kind", "directory", "revision", "files_sha256", "instruction", "maximum_task_characters",
        "maximum_tokens", "embedding_dimension", "task_features", "pooling", "device",
        "dtype", "maximum_cpu_threads", "remote_code",
    } or (
        raw["kind"] != KIND or raw["revision"] != REVISION or raw["instruction"] != INSTRUCTION
        or raw["maximum_task_characters"] != TASK_CHARS or raw["maximum_tokens"] != 8192
        or raw["embedding_dimension"] != DIMENSION or raw["task_features"] != list(TASK_FEATURES)
        or raw["pooling"] != "last_nonpadding_token_then_float32_l2"
        or raw["device"] != "cpu" or raw["dtype"] != "bfloat16"
        or raw["maximum_cpu_threads"] != 1 or raw["remote_code"] is not False
        or type(raw["directory"]) is not str or not Path(raw["directory"]).is_absolute()
        or not isinstance(raw["files_sha256"], dict) or raw["files_sha256"].keys() != set(FILES)
        or any(type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value)
               for value in raw["files_sha256"].values())
        or raw["files_sha256"]["model.safetensors"] != WEIGHTS_SHA256
    ):
        raise ValueError("invalid pinned CPU Qwen workflow encoder contract")
    return dict(raw)


def last_token_pool(states, attention):
    import torch

    if (
        states.ndim != 3 or attention.shape != states.shape[:2]
        or not (attention.sum(1) > 0).all() or not torch.isfinite(states).all()
    ):
        raise ValueError("task embeddings require finite hidden states and nonempty attention")
    positions = torch.arange(attention.shape[1], device=attention.device)
    final = torch.where(attention.bool(), positions, -1).max(1).values
    return states[torch.arange(len(states), device=states.device), final].float()


class QwenTaskEncoder:
    dimension = DIMENSION + len(TASK_FEATURES)

    def __init__(self, contract):
        self.contract = validate_contract(contract)
        self._tokenizer = self._model = None
        self.startup_seconds = 0.
        self.measurements = []

    def _load(self):
        import torch
        from transformers import AutoModel, AutoTokenizer

        if torch.get_num_threads() > self.contract["maximum_cpu_threads"]:
            raise ValueError("the offline Qwen task encoder requires its bounded CPU thread configuration")
        started = time.perf_counter()
        directory = Path(self.contract["directory"])
        for name, expected in self.contract["files_sha256"].items():
            with (directory / name).open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != expected:
                raise ValueError("pinned Qwen workflow encoder file changed: " + name)
        tokenizer = AutoTokenizer.from_pretrained(
            directory, local_files_only=True, trust_remote_code=False, padding_side="left",
        )
        model = AutoModel.from_pretrained(
            directory, local_files_only=True, trust_remote_code=False,
            dtype=torch.bfloat16, attn_implementation="sdpa",
        ).to("cpu").eval()
        if model.config.model_type != "qwen3" or model.config.hidden_size != DIMENSION:
            raise ValueError("Qwen workflow embedding architecture differs from the pinned model")
        self._tokenizer, self._model = tokenizer, model
        self.startup_seconds = time.perf_counter() - started

    def encode(self, text):
        import numpy as np
        import torch

        if type(text) is not str or len(text) > TASK_CHARS:
            raise ValueError("Qwen workflow encoding accepts bounded explicit task instructions only")
        if not text.strip():
            return None, 0.
        if self._model is None:
            self._load()
        wall, cpu = time.perf_counter(), time.thread_time()
        query = "Instruct: " + self.contract["instruction"] + "\nQuery: " + text
        batch = self._tokenizer(query, padding=True, truncation=False, return_tensors="pt")
        tokens = int(batch["attention_mask"].sum())
        if batch["input_ids"].shape[1] > self.contract["maximum_tokens"]:
            raise ValueError("Qwen workflow task exceeds the declared token budget; do not silently truncate")
        with torch.inference_mode():
            states = self._model(**batch, use_cache=False).last_hidden_state
            vector = torch.nn.functional.normalize(last_token_pool(states, batch["attention_mask"]), p=2, dim=1)[0]
        values = np.concatenate((vector.numpy(), np.asarray(task_features(text), dtype=np.float32)))
        if values.shape != (self.dimension,) or not np.isfinite(values).all():
            raise ValueError("invalid Qwen workflow task embedding")
        self.measurements.append({
            "input_tokens": tokens, "wall_seconds": time.perf_counter() - wall,
            "thread_cpu_seconds": time.thread_time() - cpu,
        })
        # Tokenization success is not a semantic confidence or a workload-accuracy claim.
        return values.astype(np.float32), 1.
