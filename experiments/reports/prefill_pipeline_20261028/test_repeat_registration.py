"""CPU boundaries for metadata-only reuse of an exact qualified Full."""

import importlib.util
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("qualified_full_repeat", HERE / "repeat_registration.py")
REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REVIEW)


def original():
    return {
        "run_root": "/old", "port": 1, "source_sha256": {"runtime.py": "a"},
        "engine_tree": "engine", "reference_environment": {"SEED": "21"},
        "source_plan": {"requests": 3674}, "external_inputs": [{"path": "/p", "sha256": "b"}],
    }


def repeated():
    value = original()
    value.update(run_root="/new", port=2, after_registration="/original.json")
    value["external_inputs"].append({"path": "/proof", "sha256": "c"})
    return value


def test_exact_serving_state_and_additional_proofs_are_metadata_only():
    assert REVIEW.validate_metadata(original(), repeated()) == [
        "after_registration", "external_inputs", "port", "run_root",
    ]


@pytest.mark.parametrize("field,value", [
    ("source_sha256", {"runtime.py": "changed"}),
    ("engine_tree", "other_engine"),
    ("reference_environment", {"SEED": "22"}),
    ("source_plan", {"requests": 10}),
])
def test_source_work_seed_and_engine_cannot_change(field, value):
    value_copy = repeated()
    value_copy[field] = value
    with pytest.raises(ValueError, match="serving source"):
        REVIEW.validate_metadata(original(), value_copy)


@pytest.mark.parametrize("proofs", [[], [{"path": "/p", "sha256": "changed"}]])
def test_repeat_cannot_remove_or_replace_original_qualification(proofs):
    value = repeated()
    value["external_inputs"] = proofs
    with pytest.raises(ValueError, match="qualified inputs"):
        REVIEW.validate_metadata(original(), value)


@pytest.mark.parametrize("field,value", [("run_root", "/old"), ("port", 1)])
def test_repeat_requires_fresh_output_and_port(field, value):
    candidate = repeated()
    candidate[field] = value
    with pytest.raises(ValueError, match="fresh output"):
        REVIEW.validate_metadata(original(), candidate)
