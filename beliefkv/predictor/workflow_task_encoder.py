"""Pinned CPU sentence embeddings for bounded, explicit workflow instructions."""

import hashlib
import math
from pathlib import Path
import re

from beliefkv.predictor.workflow_rank_input import TASK_CHARS


KIND = "minilm_task_cpu_v1"
FILES = (
    "config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
    "special_tokens_map.json", "vocab.txt",
)
DIMENSION = 384
TASK_FEATURES = (
    "characters", "words", "lines", "numbered_requirements", "file_references",
    "code_references", "sessions", "implement", "refactor", "explain",
    "investigate", "test", "integrate", "documentation", "read_only", "requirements",
)


def task_features(text):
    """Task content only; dataset identities and future work are not inputs."""
    lower = text.lower()
    patterns = (
        r"(?m)^\s*\d+[.)]\s+", r"\b[\w/-]+\.(?:py|js|ts|jsx|tsx|json|yml|yaml|md)\b",
        r"`[^`\n]+`", r"\bsession\s+\d+", r"\bimplement\w*", r"\brefactor\w*",
        r"\b(?:explain|describe|understand|comprehension)\w*", r"\b(?:investigat|analy[sz])\w*",
        r"\b(?:test|verif|validat)\w*", r"\bintegrat\w*", r"\bdocument\w*",
        r"\b(?:read.only|do not (?:modify|change|edit))\b", r"\b(?:requirement|objective|step)\w*",
    )
    values = [len(text), len(text.split()), len(text.splitlines())]
    values.extend(len(re.findall(pattern, lower)) for pattern in patterns)
    return [math.log1p(value) / 8. for value in values]


def encoder_contract(directory, *, revision):
    directory = Path(directory).resolve()
    return {
        "kind": KIND, "directory": str(directory), "revision": revision,
        "files_sha256": {
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in FILES
        },
        "embedding_dimension": DIMENSION, "task_features": list(TASK_FEATURES),
        "maximum_task_characters": TASK_CHARS, "chunk_tokens": 256,
        "pooling": "token_mean_then_length_weighted_chunk_mean_l2",
        "device": "cpu", "remote_code": False,
    }


def validate_encoder(contract):
    if not isinstance(contract, dict) or contract.keys() != {
        "kind", "directory", "revision", "files_sha256", "embedding_dimension",
        "task_features", "maximum_task_characters", "chunk_tokens", "pooling",
        "device", "remote_code",
    } or (
        contract["kind"] != KIND or contract["embedding_dimension"] != DIMENSION
        or contract["task_features"] != list(TASK_FEATURES)
        or contract["maximum_task_characters"] != TASK_CHARS
        or contract["chunk_tokens"] != 256
        or contract["pooling"] != "token_mean_then_length_weighted_chunk_mean_l2"
        or contract["device"] != "cpu" or contract["remote_code"] is not False
    ):
        raise ValueError("invalid bounded CPU task encoder contract")
    if (
        type(contract["revision"]) is not str
        or not re.fullmatch(r"[0-9a-f]{40}", contract["revision"])
        or type(contract["directory"]) is not str
        or not Path(contract["directory"]).is_absolute()
        or not isinstance(contract["files_sha256"], dict)
        or contract["files_sha256"].keys() != set(FILES)
        or any(type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value)
               for value in contract["files_sha256"].values())
    ):
        raise ValueError("task encoder requires pinned local files")
    return dict(contract)


class WorkflowTaskEncoder:
    dimension = DIMENSION + len(TASK_FEATURES)

    def __init__(self, contract):
        self.contract = validate_encoder(contract)
        self._tokenizer = self._model = None

    def _load(self):
        from transformers import AutoModel, AutoTokenizer

        path = Path(self.contract["directory"])
        for name, digest in self.contract["files_sha256"].items():
            if hashlib.sha256((path / name).read_bytes()).hexdigest() != digest:
                raise ValueError("workflow task encoder file changed: " + name)
        tokenizer = AutoTokenizer.from_pretrained(
            path, local_files_only=True, trust_remote_code=False,
        )
        model = AutoModel.from_pretrained(
            path, local_files_only=True, trust_remote_code=False, use_safetensors=True,
        ).to("cpu").eval()
        if model.config.hidden_size != DIMENSION:
            raise ValueError("workflow task embedding dimension changed")
        self._tokenizer, self._model = tokenizer, model

    def encode(self, text):
        import numpy as np
        import torch

        if type(text) is not str or len(text) > TASK_CHARS:
            raise ValueError("semantic task encoding accepts bounded explicit instructions only")
        if not text.strip():
            return None, 0.
        if self._model is None:
            self._load()
        tokenizer = self._tokenizer
        batch = tokenizer(
            text, max_length=self.contract["chunk_tokens"], truncation=True,
            return_overflowing_tokens=True, padding=True, return_tensors="pt",
        )
        batch.pop("overflow_to_sample_mapping", None)
        weights = (
            batch["attention_mask"].sum(1) - tokenizer.num_special_tokens_to_add(pair=False)
        ).clamp(min=1)
        with torch.inference_mode():
            states = self._model(**batch).last_hidden_state
            mask = batch["attention_mask"].unsqueeze(-1)
            means = (states * mask).sum(1) / mask.sum(1).clamp(min=1)
            means = torch.nn.functional.normalize(means, p=2, dim=1)
            weights = weights.to(means.dtype)
            vector = torch.nn.functional.normalize((means * weights[:, None]).sum(0), p=2, dim=0)
        values = np.concatenate((vector.numpy(), np.asarray(task_features(text), dtype=np.float32)))
        unknown = (
            (batch["input_ids"] == tokenizer.unk_token_id) * batch["attention_mask"]
        ).sum().item()
        return values.astype(np.float32), max(0., 1. - unknown / weights.sum().item())
