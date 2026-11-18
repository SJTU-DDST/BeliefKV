"""Exercise actual prepared protocols and the single-supervisor handoff."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_materialization_campaign as campaign

PREPARED = campaign.ROOT / (
    "experiments/reports/subagent_materialization_20261116/host200_mean55_prepared_r3"
)


def configurations():
    return {label: campaign.load(PREPARED / (label + ".json")) for label in campaign.LABELS}


def test_actual_prepared_pair_and_baselines_match():
    shared = campaign.matching_preparations(configurations())
    assert shared["resources"]["HICACHE_SIZE_GB"] == "200"
    assert shared["resources"]["MAX_RUNNING_REQUESTS"] == "24"


def test_pair_only_preparation_keeps_the_same_shared_work_and_resources():
    values = configurations()
    pair = {label: values[label] for label in campaign.LABELS[:2]}
    assert campaign.matching_preparations(pair) == campaign.matching_preparations(values)
    pair["pressure"]["common_environment"]["HICACHE_SIZE_GB"] = "201"
    with pytest.raises(ValueError, match="resources|more than"):
        campaign.matching_preparations(pair)


@pytest.mark.parametrize("labels", (campaign.LABELS, campaign.LABELS[:2]))
def test_declared_stage_order_accepts_only_its_complete_stage_map(labels):
    plan = {"GPU_stage_order": list(labels), "stages": dict.fromkeys(labels)}
    assert campaign.stage_labels(plan) == labels
    plan["stages"]["foreign"] = {}
    with pytest.raises(ValueError, match="matched Full pair"):
        campaign.stage_labels(plan)


@pytest.mark.parametrize("labels", (
    ("control",), ("pressure", "control"), campaign.LABELS[:3],
    ("control", "native_matched"), tuple(reversed(campaign.LABELS)),
))
def test_partial_reordered_or_foreign_stage_orders_are_rejected(labels):
    with pytest.raises(ValueError, match="matched Full pair"):
        campaign.stage_labels({"GPU_stage_order": list(labels), "stages": dict.fromkeys(labels)})


@pytest.mark.parametrize("label", campaign.LABELS)
def test_resources_cannot_change_in_an_arm(label):
    values = configurations()
    values[label]["common_environment"]["HICACHE_SIZE_GB"] = "201"
    with pytest.raises(ValueError, match="resources|more than"):
        campaign.matching_preparations(values)


@pytest.mark.parametrize("label", ("control", "thunderagent_acting_decay", "native_matched"))
def test_backpressure_cannot_leak_to_controls(label):
    values = configurations()
    values[label]["common_environment"]["BELIEFKV_SUBAGENT_MATERIALIZATION"] = "pressure_v1"
    with pytest.raises(ValueError, match="leaked"):
        campaign.matching_preparations(values)


def test_full_pair_cannot_hide_an_extra_policy_change():
    values = configurations()
    values["pressure"]["common_environment"]["AGENT_CONTROL_INTERVAL_MS"] = "3000"
    with pytest.raises(ValueError, match="more than"):
        campaign.matching_preparations(values)


def test_receipt_comparison_preserves_all_content_after_json_roundtrip():
    work = {"request": (128, 4, "prompt", "output", {"type": "stop", "matched": 151645})}
    loaded = json.loads(json.dumps(work))
    assert campaign.canonical_work(work) == campaign.canonical_work(loaded)
    changed = deepcopy(loaded)
    changed["request"][4]["matched"] = 151646
    assert campaign.canonical_work(work) != campaign.canonical_work(changed)


@pytest.mark.parametrize("saved", ([], {"pressure": "x"}, {"control": "x", "native_matched": "y"}))
def test_resume_requires_a_completed_prefix(saved):
    with pytest.raises(ValueError, match="ordered campaign prefix"):
        campaign.completed_stage_proofs({"completed_stage_proofs": saved})


def test_pair_resume_cannot_hide_an_additional_baseline():
    with pytest.raises(ValueError, match="ordered campaign prefix"):
        campaign.completed_stage_proofs({
            "GPU_stage_order": list(campaign.LABELS[:2]),
            "stages": dict.fromkeys(campaign.LABELS[:2]),
            "completed_stage_proofs": dict.fromkeys(campaign.LABELS[:3], "saved"),
        })


def test_resume_revalidates_actual_execution_and_review_before_skipping(tmp_path, monkeypatch):
    from scripts import verify_measured_execution as verification

    proof = {
        "protocol": "all_fixed_measured_workflows_completed",
        "registration_sha256": "registration", "cohort_sha256": "cohort",
        "certificate_sha256": "certificate", "actual_owned_exit_verified": True,
        "measured_requests": 1, "measured_output_tokens": 4,
        "work": {"r": [128, 4, "prompt", "output", {"type": "stop"}]},
    }
    saved, review = tmp_path / "proof.json", tmp_path / "review"
    saved.write_text(json.dumps(proof))
    review.mkdir()
    (review / "review.json").write_text('{"matching_review": true}')
    calls = []
    monkeypatch.setattr(verification, "verify_execution", lambda *args: (
        calls.append(("execution", args)) or deepcopy(proof)
    ))
    monkeypatch.setattr(verification, "review_matches_execution", lambda *args: (
        calls.append(("review", args))
    ))
    plan = {
        "completed_stage_proofs": {"control": str(saved)}, "cohort": str(tmp_path / "cohort"),
        "stages": {"control": {"registration": str(tmp_path / "registration"), "review": str(review)}},
    }
    assert campaign.completed_stage_proofs(plan) == {"control": proof}
    assert [kind for kind, _args in calls] == ["execution", "review"]
    changed = deepcopy(proof)
    changed["work"]["r"][1] = 5
    saved.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="proof changed"):
        campaign.completed_stage_proofs(plan)
    assert [kind for kind, _args in calls] == ["execution", "review", "execution"]


def test_queue_heartbeat_and_bridge_controls_can_be_updated(tmp_path):
    path = tmp_path / "state.json"
    campaign.write(path, {"status": "waiting", "polls": 1})
    campaign.write(path, {"status": "waiting", "polls": 2})
    assert campaign.load(path)["polls"] == 2
    assert not path.with_suffix(".json.tmp").exists()


def test_watch_preserves_multiple_wait_heartbeats_without_registering_gpu(tmp_path, monkeypatch):
    oracle = tmp_path / "oracle.json"
    oracle.write_text("{}")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({
        "inputs_sha256": {}, "preceding_Oracle_comparison": str(oracle),
        "stages": {"control": {"port": 19999, "evidence": str(tmp_path / "stage")}},
    }))
    calls = []

    class Wait:
        def __init__(self, *_args, **_kwargs):
            pass

        def __call__(self, _stage, state, *, pulse):
            for index in (1, 2):
                state["test_polls"] = index
                pulse()
            raise RuntimeError("controlled CPU wait termination")

    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "99999")
    monkeypatch.setattr(campaign.os, "getpriority", lambda *_: 0)
    monkeypatch.setattr(campaign.signal, "signal", lambda *_: None)
    monkeypatch.setattr(campaign, "oracle_complete", lambda _: None)
    monkeypatch.setattr(campaign, "register_stage", lambda *_: calls.append("GPU"))
    monkeypatch.setattr(campaign, "helper_module", lambda: SimpleNamespace(
        NvmlDevice=lambda: SimpleNamespace(snapshot=lambda: {}, close=lambda: calls.append("closed")),
        FastWait=Wait,
    ))
    campaign.watch(plan)
    state = campaign.load(tmp_path / "state.json")
    assert state["test_polls"] == 2
    assert "controlled CPU wait termination" in state["error"]
    assert not state["GPU_registered_stages"]
    assert calls == ["closed"]


@pytest.mark.parametrize("labels", (campaign.LABELS, campaign.LABELS[:2]))
def test_completed_campaign_reviews_only_the_declared_methods(tmp_path, monkeypatch, labels):
    oracle = tmp_path / "oracle.json"
    oracle.write_text("{}")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({
        "inputs_sha256": {}, "preceding_Oracle_comparison": str(oracle),
        "GPU_stage_order": list(labels), "stages": dict.fromkeys(labels),
    }))
    calls, closed = [], []
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "99999")
    monkeypatch.setattr(campaign.os, "getpriority", lambda *_: 0)
    monkeypatch.setattr(campaign.signal, "signal", lambda *_: None)
    monkeypatch.setattr(campaign, "oracle_complete", lambda _: None)
    monkeypatch.setattr(campaign, "completed_stage_proofs", lambda _: dict.fromkeys(labels, {}))
    monkeypatch.setattr(campaign, "register_stage", lambda *_: pytest.fail("unexpected GPU stage"))
    monkeypatch.setattr(campaign.causal.reservation, "process", lambda pid: {
        "pid": pid, "start_ticks": "fake_CPU_owner",
    })
    monkeypatch.setattr(campaign.subprocess, "Popen", lambda command, **kwargs: (
        calls.append(command) or SimpleNamespace(pid=123, poll=lambda: 0, returncode=0)
    ))
    monkeypatch.setattr(campaign, "helper_module", lambda: SimpleNamespace(
        NvmlDevice=lambda: SimpleNamespace(close=lambda: closed.append(True)),
    ))
    campaign.watch(plan)
    state = campaign.load(tmp_path / "state.json")
    pair_only = labels == campaign.LABELS[:2]
    assert state["status"] == (
        "complete_materialization_pair" if pair_only else "complete_four_matched_arms"
    )
    assert state["GPU_stage_order"] == list(labels)
    assert state["completed_stages"] == list(labels)
    assert not state["GPU_registered_stages"]
    assert len(calls) == 1 and ("--baselines" in calls[0]) is (not pair_only)
    assert closed == [True]


def test_router_redirects_exact_watcher_once(tmp_path):
    frozen = tmp_path / "frozen"
    registration = tmp_path / "trial.registration.json"
    registration.write_text(json.dumps({"execution_checkout": str(frozen)}))
    calls, preparations = [], []
    original = SimpleNamespace(Popen=lambda command, **kwargs: calls.append((command, kwargs)))
    router = campaign.WatcherRouter(
        original, registration, tmp_path, lambda: preparations.append(True),
    )
    command = [
        campaign.sys.executable, str(frozen / "scripts/run_causal_comparison.py"),
        "--watch", str(registration),
    ]
    router.Popen(command, cwd=frozen)
    assert len(calls) == len(preparations) == 1
    assert calls[0][0][1] == str(frozen / "scripts/supervise_materialization_stage.py")
    assert calls[0][0][-2:] == ["--evidence-dir", str(tmp_path)]
    with pytest.raises(ValueError, match="only one"):
        router.Popen(command)


def test_router_rejects_a_different_process_before_spawning(tmp_path):
    registration = tmp_path / "trial.registration.json"
    registration.write_text(json.dumps({"execution_checkout": str(tmp_path)}))
    calls = []
    router = campaign.WatcherRouter(
        SimpleNamespace(Popen=lambda *args, **kwargs: calls.append(args)),
        registration, tmp_path, lambda: None,
    )
    with pytest.raises(ValueError, match="exact registered"):
        router.Popen(["bash", "unrelated.sh"])
    assert not calls


def test_oracle_review_must_include_complete_native_work():
    with pytest.raises(ValueError, match="original Oracle"):
        campaign.oracle_complete({
            "complete_measured_work_fresh_drain_and_actual_exit_verified": True,
            "complete_measured_requests": 2990,
            "complete_measured_output_tokens": 362157,
        })


def prepared_queue(tmp_path, monkeypatch, *, pair_only=False):
    oracle, report, sources, template, supervisor = (
        tmp_path / name for name in (
            "oracle.json", "cpu.xml", "sources.json", "template.json", "supervisor.py",
        )
    )
    for path in (oracle, report, sources, supervisor):
        path.write_text("{}")
    flags = (
        "--registration", "--run-root", "--port", "--cpu-report", "--cpu-sources",
        "--experiment-config", "--arms", "--workflow-arrivals",
    )
    command = [campaign.sys.executable, "unused.py"]
    for flag in flags:
        command.extend((flag, "unused"))
    template.write_text(json.dumps({
        "stages": {"oracle": {"command": command}}, "minimum_model_work_p80_p20": 2.6,
    }))
    monkeypatch.setattr(campaign.causal.fresh, "_require_clean", lambda _: None)
    monkeypatch.setattr(campaign, "oracle_complete", lambda _: None)
    monkeypatch.setattr(campaign.causal.comparison, "qualify_report", lambda *_: 1)
    monkeypatch.setattr(campaign.subprocess, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(campaign.subprocess, "Popen", lambda *_args, **_kwargs: SimpleNamespace(pid=123))
    monkeypatch.setattr(campaign.causal.reservation, "process", lambda pid: {"pid": pid, "start_ticks": "1"})
    output = tmp_path / "queue"
    campaign.prepare(SimpleNamespace(
        output=output, prepared=PREPARED, oracle_comparison=oracle, cpu_report=report,
        cpu_sources=sources, template_plan=template, fast_supervisor=supervisor,
        suffix=tmp_path.name, pair_only=pair_only,
    ))
    return campaign.load(output / "plan.json")


def test_actual_preparation_builds_four_matching_queue_entries(tmp_path, monkeypatch):
    plan = prepared_queue(tmp_path, monkeypatch)
    assert plan["minimum_model_work_p80_p20"] == 2.6
    assert list(plan["stages"]) == list(campaign.LABELS)
    assert len({row["port"] for row in plan["stages"].values()}) == 4
    for label, stage in plan["stages"].items():
        configuration = campaign.load(stage["configuration"])
        assert stage["arm"] == configuration["order"][0]
        assert stage["command"][stage["command"].index("--workflow-arrivals") + 1] == (
            configuration["workflow_arrivals"]["path"]
        )


def test_actual_pair_preparation_builds_only_two_full_entries(tmp_path, monkeypatch):
    plan = prepared_queue(tmp_path, monkeypatch, pair_only=True)
    assert list(plan["stages"]) == plan["GPU_stage_order"] == ["control", "pressure"]
    assert {row["arm"] for row in plan["stages"].values()} == {"full"}
    assert len({row["port"] for row in plan["stages"].values()}) == 2
    assert campaign.stage_labels(plan) == campaign.LABELS[:2]
