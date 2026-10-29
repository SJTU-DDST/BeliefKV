import copy
import json
from pathlib import Path

import pytest

from beliefkv.experiments.agentix_formal import load_formal
from beliefkv.experiments.agentix_qualification import load_qualification


ROOT = Path(__file__).resolve().parents[1]
DECLARATION = ROOT / "configs/baselines/agentix_atlas_locobench_policy_20261029.json"


def test_formal_policy_preserves_paper_scope_and_declares_full_serving_limits():
    document, config, serving = load_formal(DECLARATION)
    assert not document["author_parameter_defaults_verified"]
    assert config.quanta_ms == (500., 1000., 2000.)
    assert config.decode_steps_per_schedule == 16 and config.starvation_ratio == 10.
    assert serving == {"context_limit": 262144, "running_limit": 24, "decode_growth_tokens": 100}
    from beliefkv.experiments.paper_baselines import RUNTIME_ARMS

    assert "agentix_atlas" not in RUNTIME_ARMS


def test_fixture_and_formal_declarations_cannot_be_used_interchangeably():
    with pytest.raises(ValueError, match="formal Agentix"):
        load_formal(ROOT / "configs/baselines/agentix_locobench_model_qualification_20261029.json")
    with pytest.raises(ValueError, match="qualification"):
        load_qualification(DECLARATION)


@pytest.mark.parametrize("fault", (
    "author_defaults", "different_engine", "future_length", "partial_parameters",
    "missing_growth", "boolean_quantum", "nonfinite_quantum", "zero_beta", "fixture_purpose",
))
def test_formal_parameters_reject_unreviewed_scope_or_invalid_capacity(tmp_path, fault):
    document = copy.deepcopy(json.loads(DECLARATION.read_bytes()))
    parameters = document["parameters"]
    if fault == "author_defaults":
        document["author_parameter_defaults_verified"] = True
    elif fault == "different_engine":
        document["native_engine_tree"] = "unreviewed"
    elif fault == "future_length":
        parameters["recorded_final_output_length"] = 100
    elif fault == "partial_parameters":
        parameters.pop("quanta_ms")
    elif fault == "missing_growth":
        parameters["decode_growth_tokens"] = 16
    elif fault == "boolean_quantum":
        parameters["quanta_ms"][0] = True
    elif fault == "nonfinite_quantum":
        parameters["quanta_ms"][0] = float("inf")
    elif fault == "zero_beta":
        parameters["starvation_ratio"] = 0
    else:
        document["purpose"] = "bounded_native_preemption_validation"
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        load_formal(path)
