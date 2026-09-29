from __future__ import annotations

import json

import pytest

from scripts.audit_child_server_eos_cue import (
    earliest_eligible_cue,
    expected_eos_ids,
)


def test_server_cue_waits_for_delivered_content_and_precedes_tool() -> None:
    assert earliest_eligible_cue(100, 500, 900, 800) == 500
    assert earliest_eligible_cue(600, 500, 900, 800) == 600
    assert earliest_eligible_cue(800, 500, 900, 800) is None
    assert earliest_eligible_cue(901, 500, 900, float("inf")) is None


def test_server_cue_resolves_eos_ids_from_tokenizer(tmp_path) -> None:
    path = tmp_path / "tokenizer.json"
    path.write_text(json.dumps({
        "added_tokens": [
            {"content": "<|im_end|>", "id": 42, "special": True},
            {"content": "<|endoftext|>", "id": 41, "special": True},
        ],
    }))
    assert expected_eos_ids(path) == frozenset({41, 42})
    path.write_text(json.dumps({"added_tokens": [
        {"content": "<|im_end|>", "id": 42, "special": True},
    ]}))
    with pytest.raises(ValueError, match="missing"):
        expected_eos_ids(path)
