"""Explicit, request-bounded Agentix parameters for native qualification only."""

import json
from pathlib import Path

from beliefkv.policy.agentix import AgentixConfig


QUALIFICATION_POLICY = "agentix_atlas_native_qualification"
QUALIFICATION_PURPOSE = "bounded_native_preemption_validation"
_POLICY_FIELDS = {
    "mode", "band_edges_ms", "quanta_ms", "starvation_ratio", "decode_steps_per_schedule",
}
_SERVING_FIELDS = {"context_limit", "running_limit", "decode_growth_tokens"}


def load_qualification(path):
    if not path:
        raise ValueError("Agentix qualification requires an explicit configuration file")
    document = json.loads(Path(path).read_bytes())
    if type(document) is not dict or type(document.get("parameters")) is not dict:
        raise ValueError("Agentix qualification scope and parameters must be objects")
    parameters = document.get("parameters", {})
    if (
        set(document) != {"schema_version", "purpose", "policy", "max_requests", "parameters"}
        or type(document["schema_version"]) is not int or document["schema_version"] != 1
        or document["purpose"] != QUALIFICATION_PURPOSE
        or document["policy"] != QUALIFICATION_POLICY
        or type(document["max_requests"]) is not int or not 1 <= document["max_requests"] <= 16
        or set(parameters) != _POLICY_FIELDS | _SERVING_FIELDS
        or parameters.get("mode") != "atlas"
        or type(parameters.get("band_edges_ms")) is not list
        or type(parameters.get("quanta_ms")) is not list
        or any(type(parameters.get(name)) is not int for name in _SERVING_FIELDS)
        or not 1 <= parameters["running_limit"] <= min(4, document["max_requests"])
        or parameters["context_limit"] <= 0
    ):
        raise ValueError("Agentix qualification scope or explicit parameters differ")
    policy = AgentixConfig(**{
        name: tuple(parameters[name]) if name in ("band_edges_ms", "quanta_ms") else parameters[name]
        for name in _POLICY_FIELDS
    })
    if parameters["decode_growth_tokens"] < policy.decode_steps_per_schedule + 2:
        raise ValueError("Agentix qualification growth does not cover its native pipeline")
    return document, policy, {name: parameters[name] for name in _SERVING_FIELDS}
