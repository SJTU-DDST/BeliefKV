"""Exercise actual prepared protocols and the single-supervisor handoff."""

from copy import deepcopy
import json
import os
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


@pytest.mark.parametrize("labels", (campaign.LABELS, campaign.LABELS[:2], campaign.PRESSURE_ONLY))
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


@pytest.mark.parametrize("baseline", campaign.LABELS[2:])
@pytest.mark.parametrize("baseline_cap", (None, "1"))
def test_batched_write_back_requires_matching_baseline_caps(baseline, baseline_cap):
    values = configurations()
    key = campaign.WRITE_BACK_CAP_SETTING
    for spec in values.values():
        spec["common_environment"][key] = "32"
    assert campaign.matching_preparations(values)["resources"][key] == 32

    common = values[baseline]["common_environment"]
    if baseline_cap is None:
        del common[key]
        inherited = campaign.matching_preparations(values, inherited_write_back_cap="32")
        assert inherited["resources"][key] == 32
    else:
        common[key] = baseline_cap
    with pytest.raises(ValueError, match=f"{baseline}.*write-back cap 1.*32"):
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


@pytest.mark.parametrize("label", ("control", "pressure"))
def test_resume_revalidates_actual_execution_and_review_before_skipping(tmp_path, monkeypatch, label):
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
        "completed_stage_proofs": {label: str(saved)}, "cohort": str(tmp_path / "cohort"),
        "stages": {label: {"registration": str(tmp_path / "registration"), "review": str(review)}},
    }
    if label == "pressure":
        plan.update(
            GPU_stage_order=["control", "pressure"],
            reuse_completed_pressure=True,
            stages={"control": {}, **plan["stages"]},
        )
    assert campaign.completed_stage_proofs(plan) == {label: proof}
    assert [kind for kind, _args in calls] == ["execution", "review"]
    changed = deepcopy(proof)
    changed["work"]["r"][1] = 5
    saved.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="proof changed"):
        campaign.completed_stage_proofs(plan)
    assert [kind for kind, _args in calls] == ["execution", "review", "execution"]


@pytest.mark.parametrize("change", (None, "early", "argv"))
def test_reuse_pressure_preflight_preserves_original_serving_arguments(tmp_path, monkeypatch, change):
    state = tmp_path / "ranking_state.json"
    state.write_text(json.dumps({
        "status": "running" if change == "early" else "complete",
        "completed": ["full", "oracle", "learned"],
    }))
    stages = {}
    for index, label in enumerate(campaign.LABELS[:2]):
        stage = {
            "registration": str(tmp_path / (label + ".registration.json")),
            "run_root": str(tmp_path / label),
            "configuration": str(PREPARED / (label + ".json")),
            "port": 19420 + index,
        }
        stage["command"] = [
            "python", str(tmp_path / "scripts/run_causal_comparison.py"),
            "--registration", stage["registration"], "--run-root", stage["run_root"],
            "--port", str(stage["port"]), "--experiment-config", stage["configuration"],
            "--request-timeout-s", "7200" if change == "argv" and label == "control" else "3600",
        ]
        Path(stage["registration"]).write_text(json.dumps({
            "order": ["full"], "code_commit": "frozen", "run_root": stage["run_root"],
            "experiment_configuration": stage["configuration"],
        }))
        stages[label] = stage
    monkeypatch.setattr(campaign.causal.fresh, "_require_clean", lambda path: None)
    monkeypatch.setattr(campaign.subprocess, "check_output", lambda *args, **kwargs: "frozen\n")
    plan = {
        "stages": stages, "operator": str(tmp_path), "shared_gpu": True,
        "preceding_ranking_campaign_state": str(state),
        "cohort": configurations()["pressure"]["private_analysis_cohort"]["path"],
    }
    if change is None:
        campaign.verify_pressure_reuse_plan(plan)
    else:
        with pytest.raises(ValueError, match="wait for the complete|changed the frozen serving arguments"):
            campaign.verify_pressure_reuse_plan(plan)


@pytest.mark.parametrize("control_cap", (32, 1, True))
def test_reused_pressure_pair_checks_real_write_back_cap(tmp_path, monkeypatch, control_cap):
    stages, proofs = {}, {}
    monkeypatch.setattr(campaign, "matching_serving_traffic", lambda *args: None)
    for label, cap, policy in (("control", control_cap, "off"), ("pressure", 32, "pressure_v2")):
        directory = tmp_path / label
        directory.mkdir()
        registration = directory / "registration.json"
        registration.write_text(json.dumps({
            "code_commit": "frozen", "engine_tree": "native",
            "reference_environment": {
                "BELIEFKV_SUBAGENT_MATERIALIZATION": policy,
                "SGLANG_HICACHE_WRITE_BACK_BATCH_NODES": "32",
            },
        }))
        drain = directory / "telemetry_drain.json"
        drain.write_text(json.dumps({"drained_status": {"write_back_batch": {"max_nodes": cap}}}))
        stages[label] = {"registration": str(registration)}
        proofs[label] = {"certificate": str(directory / "completion.json"), "work": {}}
    if control_cap == 32:
        campaign.verify_reused_pressure_match({"stages": stages}, proofs)
    else:
        with pytest.raises(ValueError, match="matching native write-back caps"):
            campaign.verify_reused_pressure_match({"stages": stages}, proofs)


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


@pytest.mark.parametrize("labels", (campaign.LABELS, campaign.LABELS[:2], campaign.PRESSURE_ONLY))
def test_completed_campaign_reviews_only_the_declared_methods(tmp_path, monkeypatch, labels):
    oracle = tmp_path / "oracle.json"
    oracle.write_text("{}")
    plan = tmp_path / "plan.json"
    spec = {
        "inputs_sha256": {},
        "GPU_stage_order": list(labels), "stages": dict.fromkeys(labels),
    }
    if labels != campaign.PRESSURE_ONLY:
        spec["preceding_Oracle_comparison"] = str(oracle)
    plan.write_text(json.dumps(spec))
    calls, closed, oracle_checks = [], [], []
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "99999")
    monkeypatch.setattr(campaign.os, "getpriority", lambda *_: 0)
    monkeypatch.setattr(campaign.signal, "signal", lambda *_: None)
    monkeypatch.setattr(campaign, "oracle_complete", oracle_checks.append)
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
    assert state["status"] == {
        campaign.LABELS: "complete_four_matched_arms",
        campaign.LABELS[:2]: "complete_materialization_pair",
        campaign.PRESSURE_ONLY: "complete_materialization_pressure",
    }[labels]
    assert state["GPU_stage_order"] == list(labels)
    assert state["completed_stages"] == list(labels)
    assert not state["GPU_registered_stages"]
    if labels == campaign.PRESSURE_ONLY:
        assert not calls
        assert not oracle_checks
    else:
        assert len(calls) == 1 and ("--baselines" in calls[0]) is (labels == campaign.LABELS)
        assert oracle_checks == [{}]
    assert closed == ([] if labels == campaign.PRESSURE_ONLY else [True])


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


def prepared_queue(
    tmp_path, monkeypatch, *, pair_only=False, pressure_only=False, shared_gpu=False,
    relative_reference=False,
):
    oracle, report, sources, template, supervisor, reference = (
        tmp_path / name for name in (
            "oracle.json", "cpu.xml", "sources.json", "template.json", "supervisor.py",
            "reference.json",
        )
    )
    for path in (report, sources, supervisor):
        path.write_text("{}")
    reference.write_text(json.dumps({"arms": {"full": {campaign.WRITE_BACK_CAP_SETTING: "32"}}}))
    if not pressure_only:
        oracle.write_text("{}")
    flags = (
        "--registration", "--run-root", "--port", "--cpu-report", "--cpu-sources",
        "--experiment-config", "--arms", "--workflow-arrivals",
    )
    command = [campaign.sys.executable, "unused.py"]
    if relative_reference:
        command.append("--reference=" + os.path.relpath(reference, campaign.ROOT))
    else:
        command.extend(("--reference", str(reference)))
    for flag in flags:
        command.extend((flag, "unused"))
    stage = "full" if pressure_only else "oracle"
    template.write_text(json.dumps({
        "stages": {stage: {"command": command}}, "minimum_model_work_p80_p20": 2.6,
    }))
    monkeypatch.setattr(campaign.causal.fresh, "_require_clean", lambda _: None)
    monkeypatch.setattr(campaign, "oracle_complete", lambda _: None)
    monkeypatch.setattr(campaign.causal.comparison, "qualify_report", lambda *_: 1)
    monkeypatch.setattr(campaign.subprocess, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(campaign.subprocess, "Popen", lambda *_args, **_kwargs: SimpleNamespace(pid=123))
    monkeypatch.setattr(campaign.causal.reservation, "process", lambda pid: {"pid": pid, "start_ticks": "1"})
    output = tmp_path / "queue"
    campaign.prepare(SimpleNamespace(
        output=output, prepared=PREPARED, oracle_comparison=None if pressure_only else oracle,
        cpu_report=report,
        cpu_sources=sources, template_plan=template, fast_supervisor=supervisor,
        suffix=tmp_path.name, pair_only=pair_only, pressure_only=pressure_only,
        shared_gpu=shared_gpu,
    ))
    return campaign.load(output / "plan.json")


@pytest.mark.parametrize("relative_reference", (False, True))
def test_actual_preparation_builds_four_matching_queue_entries(tmp_path, monkeypatch, relative_reference):
    plan = prepared_queue(tmp_path, monkeypatch, relative_reference=relative_reference)
    assert plan["minimum_model_work_p80_p20"] == 2.6
    reference = tmp_path / "reference.json"
    assert plan["inputs_sha256"][str(reference)] == campaign.sha256_file(reference)
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


def test_shared_pair_preparation_and_registration_use_the_shared_supervisor(tmp_path, monkeypatch):
    plan = prepared_queue(tmp_path, monkeypatch, pair_only=True, shared_gpu=True)
    owner = {"pid": 1, "start_ticks": "1"}
    watcher = {"pid": 2, "start_ticks": "2"}
    frozen = tmp_path / "frozen"
    registered = []
    assert plan["shared_gpu"] is True

    for label, stage in plan["stages"].items():
        stage["registration"] = str(tmp_path / (label + ".registration.json"))
        registration = Path(stage["registration"])

        def register():
            registration.write_text(json.dumps({"execution_checkout": str(frozen)}))
            campaign.causal.subprocess.Popen([
                campaign.sys.executable, str(frozen / "scripts/run_causal_comparison.py"),
                "--watch", str(registration),
            ])
            print(json.dumps({"watcher": watcher}))

        monkeypatch.setattr(campaign.causal, "main", register)
        monkeypatch.setattr(campaign.subprocess, "Popen", lambda command, **_: registered.append(command))
        assert campaign.register_stage(stage, plan, owner) == watcher
        assert campaign.load(Path(stage["evidence"]) / "preparation.json")["shared_gpu"] is True
    assert len(registered) == 2


def test_pressure_only_preparation_registers_no_control(tmp_path, monkeypatch):
    plan = prepared_queue(tmp_path, monkeypatch, pressure_only=True)
    assert list(plan["stages"]) == plan["GPU_stage_order"] == ["pressure"]
    assert "preceding_Oracle_comparison" not in plan
    stage = plan["stages"]["pressure"]
    assert campaign.load(stage["configuration"])["materialization_experiment"]["policy"] in (
        campaign.PRESSURE_POLICIES
    )
    assert campaign.stage_labels(plan) == campaign.PRESSURE_ONLY


def test_pressure_only_registers_the_shared_supervisor(tmp_path, monkeypatch):
    owner = {"pid": 1, "start_ticks": "1"}
    watcher = {"pid": 2, "start_ticks": "2"}
    root = tmp_path / "frozen"
    registration = tmp_path / "registration.json"
    stage = {
        "command": [campaign.sys.executable, "run_causal_comparison.py"],
        "registration": str(registration),
        "evidence": str(tmp_path / "evidence"),
    }
    plan = {"GPU_stage_order": ["pressure"], "plugin": "plugin", "fast_supervisor": "supervisor"}
    calls = []

    def register():
        registration.write_text(json.dumps({"execution_checkout": str(root)}))
        campaign.causal.subprocess.Popen([
            campaign.sys.executable, str(root / "scripts/run_causal_comparison.py"),
            "--watch", str(registration),
        ])
        print(json.dumps({"watcher": watcher}))

    monkeypatch.setattr(campaign.causal, "main", register)
    monkeypatch.setattr(campaign.subprocess, "Popen", lambda command, **_: calls.append(command))
    assert campaign.register_stage(stage, plan, owner) == watcher
    assert len(calls) == 1 and calls[0][1] == str(root / "scripts/supervise_materialization_stage.py")
    assert campaign.load(tmp_path / "evidence/preparation.json")["shared_gpu"] is True


def test_shared_supervisor_reuses_fast_wait_without_a_bridge(tmp_path, monkeypatch):
    from scripts import supervise_materialization_stage as supervision

    registration = tmp_path / "registration.json"
    registration.write_text(json.dumps({"execution_checkout": str(campaign.ROOT)}))
    accelerated_wait = object()
    calls = []
    original_wait = campaign.causal.wait_for_resources

    def watch(path):
        assert path == registration
        assert campaign.causal.wait_for_resources is accelerated_wait
        calls.append("watch")

    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "99999")
    monkeypatch.setattr(supervision.sys, "path", supervision.sys.path.copy())
    monkeypatch.setattr(campaign.causal, "watch", watch)
    monkeypatch.setattr(supervision, "load_module", lambda name, _: SimpleNamespace(
        NvmlDevice=lambda: SimpleNamespace(snapshot=None, close=lambda: calls.append("close")),
        FastWait=lambda gpu, machine: accelerated_wait,
    ) if name == "materialization_fast_wait" else pytest.fail("shared run requested the bridge"))
    supervision.watch_shared(registration, campaign.sha256_file(registration))
    assert calls == ["watch", "close"]
    assert campaign.causal.wait_for_resources is original_wait
