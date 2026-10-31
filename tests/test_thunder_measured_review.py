from pathlib import Path
from unittest.mock import patch

from scripts import stop_after_measured_cohort as stop


def test_paper_cohort_review_does_not_invent_full_only_decisions(tmp_path):
    output, run = tmp_path / "analysis", tmp_path / "run"
    comparison = {"run_root": str(run), "order": ["thunderagent_acting_decay"],
                  "workflow_arrivals_path": "arrivals.json", "source_arm": "source"}
    plan = {"analysis_output": str(output), "comparison": "comparison.json",
            "cohort": "cohort.json", "output_dir": str(tmp_path / "termination")}
    cohort = {"cohort": {}, "all_workflows_completed": False}
    written, commands = {}, []
    sample = {
        "event": "gpu_service_sample", "phase": "decode", "batch_size": 2,
        "service_elapsed_ms": 5.,
    }
    with (
        patch.object(stop.subprocess, "run", side_effect=lambda args, **kwargs: commands.append(args)),
        patch.object(stop.causal, "load", return_value=cohort),
        patch.object(stop, "sha256_file", return_value="digest"),
        patch.object(stop, "SealedRecords", return_value=lambda path: [sample]),
        patch.object(stop.capture.reservation, "write_json",
                     side_effect=lambda path, value: written.update({Path(path).name: value})),
    ):
        stop.review(plan, comparison, {
            "complete_measured_native_work_verified": True, "measured_requests": 2,
        })
    assert len(commands) == 1
    assert commands[0][1].endswith("audit_workflow_cohort.py")
    assert str(run / "thunderagent_acting_decay") in commands[0]
    assert written["review.json"]["analysis_arm"] == "thunderagent_acting_decay"
    decision = written["workload_decision.json"]
    assert decision["not_a_new_workload_acceptance_decision"]
    assert not decision["accepted_for_oracle_diagnostic"]
