import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import audit_child_final_token_budget as audit_module


def _episode(project, task, rid, duration):
    return {
        "project": project,
        "task_id": task,
        "post_notice": {
            "final_request_id": rid,
            "llm_submit_to_result_ms": duration,
        },
    }


def test_budget_audit_pairs_exact_server_request_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(
        audit_module, "require_complete_batch",
        lambda _: (["alpha__one", "beta__one"], []),
    )
    monkeypatch.setattr(
        audit_module, "load_episodes",
        lambda _: ([
            _episode("alpha", "alpha__one", "a", 3000),
            _episode("beta", "beta__one", "b", 4000),
            {"project": "beta", "task_id": "beta__one",
             "post_notice": None},
        ], {"natural_returns": 3}),
    )
    server = tmp_path / "server"
    server.mkdir()
    (server / "runtime_events.sglang.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in [
            {"kind": "llm_result", "attributes": {
                "request_id": "a", "output_tokens": 600,
            }},
            {"kind": "llm_result", "attributes": {
                "request_id": "b", "output_tokens": 200,
            }},
            {"kind": "llm_result", "attributes": {
                "request_id": "unrelated", "output_tokens": 1000,
            }},
        ]),
    )
    report = audit_module.audit(tmp_path)
    assert report["natural_notice_episodes"] == 3
    assert report["episodes_without_single_final_request"] == 1
    assert report["missing_server_result"] == 0
    assert report["matched"]["matched_natural_reports"] == 2
    assert report["matched"]["would_exceed_token_cap"]["512"] == 1
    assert report["by_project"]["beta"]["would_exceed_token_cap"]["256"] == 0


def test_budget_audit_rejects_duplicate_request_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(
        audit_module, "require_complete_batch",
        lambda _: (["alpha__one"], []),
    )
    monkeypatch.setattr(
        audit_module, "load_episodes",
        lambda _: ([
            _episode("alpha", "alpha__one", "same", 100),
            _episode("alpha", "alpha__one", "same", 200),
        ], {"natural_returns": 2}),
    )
    with pytest.raises(ValueError, match="duplicate final request"):
        audit_module.audit(tmp_path)


def test_direct_budget_audit_cli_imports():
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts/audit_child_final_token_budget.py"
    )
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--run-root" in result.stdout
