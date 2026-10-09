import json
import subprocess

from scripts.resume_predictive_after_native import engine_delta, revised_plan


def test_revision_transition_preserves_workload_models_and_policy_configuration():
    plan = {
        "order": ["native", "predictive_h2d"],
        "code_commit": "native-code",
        "sglang_patch_sha256": "native-patch",
        "arrival_schedule": [{"instance_id": "task", "offset_seconds": 3600}],
        "semantic_artifact_sha256": "frozen-model",
        "policy_configuration": {"predictive_h2d": {"prepare_host": True}},
        "host_split": "80:20",
    }
    snapshot = json.loads(json.dumps(plan))
    updated = revised_plan(plan, "predictive-code", "predictive-patch")
    assert plan == snapshot
    for field in (
        "arrival_schedule", "semantic_artifact_sha256",
        "policy_configuration", "host_split",
    ):
        assert updated[field] == plan[field]
    assert updated["arm_revisions"]["native"]["code_commit"] == "native-code"
    assert updated["arm_revisions"]["predictive_h2d"]["code_commit"] == "predictive-code"
    assert not updated["revision_transition"]["isolated_policy_effect_verified"]


def test_engine_delta_preserves_the_live_engine_until_explicit_application(tmp_path):
    engine = tmp_path / "engine"
    engine.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(engine), *args])

    git("init", "-q")
    source = engine / "scheduler.py"
    source.write_text("upstream\n")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "upstream")
    source.write_text("native-patched\n")
    old_patch = tmp_path / "native.patch"
    old_patch.write_bytes(git("diff", "--binary", "HEAD"))
    source.write_text("predictive-patched\n")
    new_patch = tmp_path / "predictive.patch"
    new_patch.write_bytes(git("diff", "--binary", "HEAD"))
    source.write_text("native-patched\n")
    original_index = git("write-tree")
    delta = engine_delta(engine, old_patch, new_patch)
    assert source.read_text() == "native-patched\n"
    assert git("write-tree") == original_index
    delta_path = tmp_path / "delta.patch"
    delta_path.write_bytes(delta)
    git("apply", "--check", str(delta_path))
    git("apply", str(delta_path))
    assert source.read_text() == "predictive-patched\n"
    git("apply", "--reverse", "--check", str(new_patch))
