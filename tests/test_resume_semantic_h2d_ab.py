from pathlib import Path

from scripts.resume_semantic_h2d_ab import resume_environment, update_predictive_revision


def plan():
    return {
        "code_commit": "old-predictive",
        "sglang_patch_sha256": "same-patch",
        "arm_revisions": {
            "native": {"code_commit": "collected-native", "sglang_patch_sha256": "native-patch"},
            "predictive_h2d": {"code_commit": "old-predictive", "sglang_patch_sha256": "same-patch"},
        },
        "revision_transition": {"native_frozen_plan": "ab_plan.native_frozen.json"},
        "root_count": 156, "workflow_arrival_batch_size": 108,
        "workflow_arrival_batch_interval_ms": 3600000,
        "order": ["native", "predictive_h2d"],
        "host_split": "80:20", "sampling_seed": 21, "repetition_id": 0,
        "activation_wall_clock_seconds": 14400.,
        "semantic_artifact": "/models/semantic.json", "semantic_artifact_sha256": "frozen",
        "policy_configuration": {"predictive_h2d": {"prepare_host": True}},
        "h2d_seed_artifact": "/models/h2d.json", "tool_timing_artifact": "/models/tool.json",
        "transfer_service_seed": "/models/service.json", "tool_timing_enabled": True,
        "prefetch_lead_ms": 500, "semantic_work_statistic": "center",
        "eos_protocol_window_ms": 250, "fanout_profile": "native_in_graph_2to4",
        "child_final_report_shadow": True, "workload_manifest": "/tasks/manifest.json",
        "arrival_schedule": [{"instance_id": "task", "offset_seconds": 3600.}],
    }


def test_resume_preserves_collected_native_and_all_non_revision_configuration():
    original = plan()
    changed = update_predictive_revision(original, "new-predictive", "same-patch")
    assert changed["arm_revisions"]["native"] == original["arm_revisions"]["native"]
    assert changed["arm_revisions"]["predictive_h2d"]["code_commit"] == "new-predictive"
    for name in original.keys() - {"code_commit", "arm_revisions", "revision_transition"}:
        assert changed[name] == original[name]
    assert original["code_commit"] == "old-predictive"


def test_resume_derives_environment_from_frozen_configuration_including_arrivals():
    environment = resume_environment(plan(), Path("/run"), python="/python", port=18454)
    assert environment["RESUME_PENDING"] == "1"
    assert environment["ROOT_COUNT"] == "156"
    assert environment["ARRIVAL_BATCH_SIZE"] == "108"
    assert environment["ARRIVAL_BATCH_INTERVAL_MS"] == "3600000"
    assert environment["CHILD_FINAL_REPORT_SHADOW"] == "1"
    assert environment["PREPARE_HOST"] == "1"
    assert environment["ARM_ORDER"] == "native predictive_h2d"
    assert environment["SEMANTIC_REPORT_ARTIFACT"] == "/models/semantic.json"
