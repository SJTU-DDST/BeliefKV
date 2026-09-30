"""Train-only adaptation of the final MiniLM layers for child phase semantics."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import time

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from beliefkv.predictor.child_report_phase import PHASES
from beliefkv.predictor.child_semantic_work import FrozenTextEncoder


def adapt_encoder(
    encoder: FrozenTextEncoder, samples: list[dict], output: Path, *, epochs: int = 4,
) -> dict:
    torch.manual_seed(21)
    torch.cuda.manual_seed_all(21)
    observations = [row["observation"] for row in samples]
    tokenized = encoder.tokenizer(
        [row.content_tail for row in observations], padding="max_length",
        truncation=True, max_length=encoder.max_tokens, return_tensors="pt",
    )
    raw = np.asarray([row.features(with_events=True) for row in observations], dtype=np.float32)
    center, scale = raw.mean(axis=0), np.maximum(raw.std(axis=0), .25)
    numeric = torch.from_numpy((raw - center) / scale)
    labels = torch.tensor([row["phase_label"] for row in samples])
    counts = Counter(row["task"] for row in samples)
    weights = torch.tensor([1. / counts[row["task"]] for row in samples])
    weights /= weights.mean()
    mass = torch.stack([weights[labels == i].sum() for i in range(len(PHASES))]).clamp(min=1e-4)
    class_weights = torch.sqrt(mass.sum() / mass)
    class_weights /= (mass * class_weights).sum() / mass.sum()
    for parameter in encoder.model.parameters():
        parameter.requires_grad_(False)
    layers = encoder.model.encoder.layer
    for layer in layers[-2:]:
        for parameter in layer.parameters():
            parameter.requires_grad_(True)
    classifier = nn.Sequential(nn.Linear(384 + 8, 64), nn.Tanh(), nn.Linear(64, 3))
    encoder.model.to("cuda").train()
    classifier.to("cuda").train()
    optimizer = torch.optim.AdamW([
        {"params": [p for p in encoder.model.parameters() if p.requires_grad], "lr": 2e-5},
        {"params": classifier.parameters(), "lr": 1e-3},
    ], weight_decay=.01)
    weights, class_weights, labels, numeric = (
        value.to("cuda") for value in (weights, class_weights, labels, numeric)
    )
    losses = []
    started = time.perf_counter()
    try:
        for epoch in range(epochs):
            accumulated = []
            for indices in torch.randperm(len(samples)).split(32):
                batch = {key: value[indices].to("cuda") for key, value in tokenized.items()}
                hidden = encoder.model(**batch).last_hidden_state
                mask = batch["attention_mask"].unsqueeze(-1)
                pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
                pooled = F.normalize(pooled, dim=1)
                logits = classifier(torch.cat((pooled, numeric[indices]), dim=1))
                loss = (
                    F.cross_entropy(logits, labels[indices], reduction="none")
                    * weights[indices] * class_weights[labels[indices]]
                ).mean()
                optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(
                    [p for group in optimizer.param_groups for p in group["params"]], 1.,
                )
                optimizer.step()
                accumulated.append(float(loss.detach()))
            losses.append(float(np.mean(accumulated)))
            print(f"Phase adaptation epoch {epoch + 1}/{epochs}: loss={losses[-1]:.4f}", flush=True)
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
        maximum_memory = torch.cuda.max_memory_allocated()
        output.mkdir(parents=True)
        encoder.model.to("cpu").eval()
        classifier.to("cpu").eval()
        encoder.model.save_pretrained(output, safe_serialization=True)
        encoder.tokenizer.save_pretrained(output)
        torch.save({
            "state_dict": classifier.state_dict(), "center": center.tolist(),
            "scale": scale.tolist(), "training_projects": sorted({
                row["project"] for row in samples
            }),
        }, output / "phase_adapter.pt")
    finally:
        encoder.model.to("cpu").eval()
        classifier.to("cpu").eval()
        del optimizer
        del weights, class_weights, labels, numeric
        if "batch" in locals():
            del batch, hidden, pooled, logits, loss, mask
        torch.cuda.empty_cache()
    return {
        "epochs": epochs, "training_projects": sorted({row["project"] for row in samples}),
        "training_loss_by_epoch": losses, "seconds": elapsed,
        "maximum_allocated_gpu_bytes": maximum_memory,
        "unfrozen_encoder_layers": 2, "snapshot": str(output.resolve()),
        "scope": "Train projects only; no validation labels or early stopping.",
    }
