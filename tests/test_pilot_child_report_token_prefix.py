from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from scripts.pilot_child_report_token_prefix import (
    _ngrams,
    _predict_remaining,
    evaluate,
)


def test_text_features_use_content_at_fixed_prefix_size() -> None:
    assert _ngrams("review complete", "word") != _ngrams(
        "review pending ", "word"
    )
    assert _ngrams("review complete", "char") != _ngrams(
        "review pending ", "char"
    )


def test_text_fit_distinguishes_same_length_prefixes() -> None:
    train = [
        {
            "task_id": f"alpha-{index}",
            "observed_prefix": "complete " * 128,
            "final_output_chars_oracle": 1200,
        }
        for index in range(2)
    ] + [
        {
            "task_id": f"beta-{index}",
            "observed_prefix": "pending  " * 128,
            "final_output_chars_oracle": 3000,
        }
        for index in range(2)
    ]
    outputs = _predict_remaining(
        train, [train[0], train[-1]], analyzer="word", alpha=0.1
    )
    assert len(train[0]["observed_prefix"]) == len(train[-1]["observed_prefix"])
    assert np.isfinite(outputs).all()
    assert outputs[0] < outputs[1]


def test_evaluate_rejects_project_leakage() -> None:
    training = Path("training")
    heldout = Path("heldout")
    row = {
        "task_id": "same-project-different-task",
        "project": "same-project",
    }
    with patch(
        "scripts.pilot_child_report_token_prefix.load_text_prefixes",
        side_effect=[
            ([{**row, "task_id": "train-task"}], {}),
            ([row], {}),
        ],
    ):
        with pytest.raises(ValueError, match="overlaps training"):
            evaluate([training], heldout)
