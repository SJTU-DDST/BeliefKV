"""Explicit Agentix paper-port parameters, separate from preemption fixtures."""

import json
from pathlib import Path

from beliefkv.experiments.native_engine_variant import AGENTIX_TREE
from beliefkv.policy.agentix import AgentixConfig


FORMAL_POLICY = "agentix_atlas_paper_native"
FORMAL_PURPOSE = "complete_same_work_paper_strategy_reproduction"
POLICY_FIELDS = {
    "mode", "band_edges_ms", "quanta_ms", "starvation_ratio", "decode_steps_per_schedule",
}
SERVING_FIELDS = {"context_limit", "running_limit", "decode_growth_tokens"}


def load_formal(path):
    if not path:
        raise ValueError("formal Agentix requires an explicit policy declaration")
    document = json.loads(Path(path).read_bytes())
    if (
        type(document) is not dict
        or set(document) != {
            "schema_version", "purpose", "policy", "native_engine_tree",
            "parameter_origin", "author_parameter_defaults_verified", "parameters",
        }
        or type(document.get("schema_version")) is not int or document["schema_version"] != 1
        or document["purpose"] != FORMAL_PURPOSE or document["policy"] != FORMAL_POLICY
        or document["native_engine_tree"] != AGENTIX_TREE
        or document["parameter_origin"] != "predeclared_native_port_not_author_defaults"
        or document["author_parameter_defaults_verified"] is not False
        or type(document["parameters"]) is not dict
    ):
        raise ValueError("formal Agentix declaration or paper-port provenance differs")
    parameters = document["parameters"]
    if (
        set(parameters) != POLICY_FIELDS | SERVING_FIELDS
        or parameters.get("mode") != "atlas"
        or any(type(parameters.get(name)) is not list for name in ("band_edges_ms", "quanta_ms"))
        or any(type(parameters.get(name)) is not int or parameters[name] <= 0 for name in SERVING_FIELDS)
        or type(parameters.get("decode_steps_per_schedule")) is not int
        or any(
            type(value) not in (int, float)
            for name in ("band_edges_ms", "quanta_ms") for value in parameters[name]
        )
        or type(parameters.get("starvation_ratio")) not in (int, float)
    ):
        raise ValueError("formal Agentix needs complete explicit policy and serving parameters")
    config = AgentixConfig(**{
        name: tuple(parameters[name]) if name in ("band_edges_ms", "quanta_ms") else parameters[name]
        for name in POLICY_FIELDS
    })
    if parameters["decode_growth_tokens"] < config.decode_steps_per_schedule + 2:
        raise ValueError("formal Agentix growth does not cover its native overlap pipeline")
    return document, config, {name: parameters[name] for name in SERVING_FIELDS}
