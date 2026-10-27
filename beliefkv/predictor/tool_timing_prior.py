"""Pinned, domain-specific duration priors with session-disjoint evaluation."""

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

from beliefkv.predictor.tool_survival import KaplanMeierCurve


def tool_signature(attrs):
    domain = attrs.get("timing_domain")
    if domain:
        category = attrs.get("observed_timing_class")
        if not isinstance(domain, str) or not isinstance(category, str) or not category:
            raise ValueError("a timing domain requires an observable tool category")
        return domain, category
    command = attrs.get("observed_command_shape")
    if not command or command == "unknown":
        command = attrs.get("observed_command_class") or attrs.get("tool_name", "")
    return str(attrs.get("backend_class") or "unknown"), str(command)


@dataclass(frozen=True)
class ToolTimingPrior:
    curves: dict
    sha256: str
    strength: int = 8

    @classmethod
    def load(cls, path, sha256):
        payload = Path(path).read_bytes()
        if hashlib.sha256(payload).hexdigest() != sha256:
            raise ValueError("tool timing prior SHA256 mismatch")
        raw = json.loads(payload)
        train, test = raw.get("training_groups"), raw.get("evaluation_groups")
        if (
            raw.get("schema_version") != 1 or raw.get("kind") != "tool_duration_prior"
            or raw.get("grouping") != "public_provider_session"
            or not isinstance(train, list) or not isinstance(test, list)
            or not train or not test or set(train) & set(test)
            or raw.get("strength") != 8 or not raw.get("domain")
        ):
            raise ValueError("tool timing prior requires disjoint session provenance")
        curves = {}
        for row in raw.get("curves", ()):
            key = (raw["domain"], row["category"])
            curve = KaplanMeierCurve.from_dict(row["curve"])
            if (
                key in curves or curve.event_count < 8 or curve.event_count != curve.sample_count
                or any(not math.isfinite(t) or not math.isfinite(s) for t, s in curve._steps)
                or not curve._steps or curve._steps[-1][1] != 0
            ):
                raise ValueError("tool timing prior has unsupported category evidence")
            curves[key] = curve
        if not curves:
            raise ValueError("tool timing prior is empty")
        return cls(curves, sha256)


def split_public_sessions(samples, *, seed=21):
    """Assign sessions before inspecting durations; keep every reported time."""
    train, test = [], []
    for sample in samples:
        group = sample.get("source_group")
        if not isinstance(group, str) or len(group) != 64:
            raise ValueError("session-disjoint timing preparation needs group identities")
        digest = hashlib.sha256(f"tool_prior_split_v1:{seed}:{group}".encode()).hexdigest()
        (test if int(digest, 16) % 5 == 0 else train).append(sample)
    if not train or not test:
        raise ValueError("tool prior split has an empty population")
    return train, test


def build_public_prior(train, test, *, domain, public_source):
    train_groups = sorted({item["source_group"] for item in train})
    test_groups = sorted({item["source_group"] for item in test})
    if not domain or not train_groups or not test_groups or set(train_groups) & set(test_groups):
        raise ValueError("tool timing training and evaluation sessions must be disjoint")
    rows, calibration = [], {}
    categories = sorted({item["tool_name"] for item in train + test})
    for category in categories:
        durations = [item["duration_ms"] for item in train if item["tool_name"] == category]
        held_out = [item["duration_ms"] for item in test if item["tool_name"] == category]
        if len(durations) < 8 or not held_out:
            raise ValueError("tool timing category lacks training or held-out support")
        curve = KaplanMeierCurve().fit([(value, True) for value in durations])
        rows.append({"category": category, "curve": curve.to_dict()})
        calibration[category] = {
            "train_calls": len(durations), "held_out_calls": len(held_out),
            "held_out_fraction_at_or_above_p10": sum(
                value >= curve.remaining_quantile(0., .1) for value in held_out
            ) / len(held_out),
            "held_out_fraction_at_or_below_p90": sum(
                value <= curve.remaining_quantile(0., .9) for value in held_out
            ) / len(held_out),
            "held_out_brier_at_1s": sum(
                (float(value <= 1000.) - (1. - curve.survival(1000.))) ** 2
                for value in held_out
            ) / len(held_out),
        }
    return {
        "schema_version": 1, "kind": "tool_duration_prior", "domain": domain,
        "grouping": "public_provider_session", "strength": 8,
        "training_groups": train_groups, "evaluation_groups": test_groups,
        "public_source": public_source, "curves": rows,
        "held_out_evaluation": calibration,
        "scope": "Offline family-duration prior; not SWE-bench model calibration or GPU performance.",
    }
