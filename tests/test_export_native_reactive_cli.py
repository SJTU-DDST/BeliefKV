"""CLI contract for the train-only native reactive P6 exporter."""

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.export_native_reactive_p6_dataset import main


def test_native_export_cli_requires_explicit_frozen_split(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="2"):
        main([str(tmp_path), "--output-dir", str(tmp_path / "output")])


def test_native_export_cli_passes_frozen_split_and_reports_eligibility(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = tmp_path / "run"
    output = tmp_path / "output"
    split = tmp_path / "split.json"
    with patch(
        "scripts.export_native_reactive_p6_dataset.export_native_reactive_p6_dataset",
        return_value={
            "formal_local_training_eligible": True,
            "source": {"native_request_evidence": {"telemetry_complete": True}},
            "training_readiness": {},
        },
    ) as export:
        assert main([
            str(run), "--output-dir", str(output), "--split-manifest", str(split)
        ]) == 0
    export.assert_called_once_with(run, output, split_manifest=split)
    assert '"telemetry_complete": true' in capsys.readouterr().out
