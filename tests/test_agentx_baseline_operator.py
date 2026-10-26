from copy import deepcopy
import json

import pytest

from beliefkv.experiments.agentx_tokens import prepare_agentx_tokens, sha256_file
from beliefkv.experiments.causal_replay_delivery import JOIN_INFORMATION_CONTRACT
from scripts.run_agentx_baseline import (
    configured_reference, prefix_qualification_environment,
    review_completed_run, validate_published_result,
)
from scripts import run_agentx_baseline as operator
from tests.test_agentx_tokens import prepared_source, synthesizer
from beliefkv.experiments.paper_baselines import TOKENCAKE_PARAMETERS


@pytest.fixture
def tokencake_configuration(tmp_path):
    document = json.loads(
        (operator.ROOT / "configs/baselines/tokencake_agentx_20261025.json").read_bytes()
    )
    reference = {
        "reference_environment": {"MAX_COMPLETION_TOKENS": "16384", "SERVER_RANDOM_SEED": "21"},
        "source_server_parameters": {
            "context_length": 262144, "max_running_requests": 24,
            "max_prefill_tokens": 16384, "chunked_prefill_size": 4096, "page_size": 1,
        },
        "source_plan": {"output_tokens": 2114833},
    }
    path = tmp_path / "configuration.json"
    path.write_text(json.dumps(document))
    return path, document, reference


def test_tokencake_formal_parameters_are_independent_of_trace_output_lengths(tokencake_configuration):
    path, document, reference = tokencake_configuration
    configured, evidence = configured_reference(reference, "tokencake_paper_observed", path)
    assert evidence["sha256"] == sha256_file(path)
    assert evidence["document"] == document
    assert not any(key in reference["reference_environment"] for key in TOKENCAKE_PARAMETERS.values())
    assert {
        key: int(configured["reference_environment"][variable])
        for key, variable in TOKENCAKE_PARAMETERS.items()
    } == document["parameters"]
    reference["source_plan"]["output_tokens"] = 1
    repeated, _ = configured_reference(reference, "tokencake_paper_observed", path)
    assert repeated["reference_environment"] == configured["reference_environment"]


@pytest.mark.parametrize("missing", tuple(TOKENCAKE_PARAMETERS))
def test_tokencake_configuration_requires_every_declared_parameter(tokencake_configuration, missing):
    path, document, reference = tokencake_configuration
    del document["parameters"][missing]
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="configuration"):
        configured_reference(reference, "tokencake_paper_observed", path)


@pytest.mark.parametrize("change", (
    "trace_length", "context", "running", "prefill", "chunk", "page", "float", "boolean",
    "extra_parameter", "public_policy", "future_graph",
))
def test_tokencake_configuration_cannot_change_common_work_or_information(tokencake_configuration, change):
    path, document, reference = tokencake_configuration
    fields = {
        "trace_length": ("generation_budget", 948),
        "context": ("context_limit", 131072),
        "running": ("running_limit", 8),
        "prefill": ("prefill_token_budget", 32768),
        "chunk": ("chunked_prefill_tokens", 2048),
        "page": ("policy_block_tokens", 16),
        "float": ("generation_budget", 16384.0),
        "boolean": ("policy_block_tokens", True),
        "extra_parameter": ("recorded_output_tokens", 948),
    }
    if change in fields:
        key, value = fields[change]
        document["parameters"][key] = value
    elif change == "public_policy":
        document["policy"] = "tokencake_public_observed_metadata"
    else:
        document["information_contract"] = "recorded_future_graph"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        configured_reference(reference, "tokencake_paper_observed", path)


def test_tokencake_configuration_is_required_and_cannot_be_applied_to_thunderagent(tokencake_configuration):
    path, _, reference = tokencake_configuration
    with pytest.raises(ValueError, match="explicit policy configuration"):
        configured_reference(reference, "tokencake_paper_observed", None)
    with pytest.raises(ValueError, match="another baseline"):
        configured_reference(reference, "thunderagent", path)
    assert configured_reference(reference, "thunderagent", None) == (reference, None)


@pytest.fixture
def execution(tmp_path):
    prepared, workflows, plan = prepared_source(tmp_path)
    tokens = tmp_path / "tokens"
    manifest = prepare_agentx_tokens(prepared, tokens, synthesizer(), corpus_evidence={})
    index = json.loads((tokens / "index.json").read_bytes())
    arm = tmp_path / "arm"
    replay = arm / "replay"
    replay.mkdir(parents=True)
    requests = replay / "native_requests.jsonl"
    with requests.open("x") as stream:
        for rid, row in index.items():
            stream.write(json.dumps({
                "request_id": rid, "exact_tokens_verified": True,
                **{key: row[key] for key in (
                    "prompt_tokens", "output_tokens", "prompt_sha256", "output_sha256",
                )},
                "native_finish_reason": {"type": "length", "length": row["output_tokens"]},
                "native_receipt": {"sampling_path": "position"},
            }) + "\n")
    with (replay / "runtime_events.replay.jsonl").open("x") as stream:
        for workflow in workflows:
            for kind, when in (("workflow_start", 100.), ("workflow_end", 200.)):
                stream.write(json.dumps({
                    "kind": kind, "ts_ms": when, "workflow_id": workflow.workflow_id,
                }) + "\n")
    event_counts = {
        workflow.workflow_id: sum(
            node.event is not None and node.workflow_id == workflow.workflow_id
            for node in plan.nodes
        )
        for workflow in workflows
    }
    registration = {
        "source_plan": plan.summary(), "plan_sha256": manifest["source_plan_sha256"],
        "source_full_sha256": manifest["source_full_sha256"], "tokens": str(tokens),
        "token_artifacts": manifest["artifacts"],
        "workflow_ids": [workflow.workflow_id for workflow in workflows],
    }
    result = {
        "causal_execution_completed": True, "exact_tokens_verified": True,
        "published_length_stopping_verified": True, "original_native_stopping_verified": False,
        "live_control_events_delivered": True, "replay_process_python_hash_seed": "21",
        "completed_requests": len(index), "source_plan": plan.summary(),
        "source_plan_sha256": manifest["source_plan_sha256"],
        "source_full_sha256": manifest["source_full_sha256"],
        "control_delivery": {
            "mode": "per_workflow_harness_queued",
            "workflows": {wid: {"accepted_batch_count": count} for wid, count in event_counts.items()},
        },
        "native_requests_sha256": sha256_file(requests),
        "token_source": manifest,
        "streaming_response_metrics": {
            "all_workflows_completed": True, "coverage": {"completed_requests": len(index)},
        },
        "workflow_elapsed_ms": {workflow.workflow_id: 100. for workflow in workflows},
    }
    return registration, result, arm


def test_complete_published_baseline_uses_event_jct_and_exact_common_token_bundle(execution):
    registration, result, arm = execution
    metrics = validate_published_result(registration, result, arm)
    assert metrics["mean_jct_ms"] == metrics["workflow_event_makespan_ms"] == 100.
    assert len(metrics["workflow_jct_ms"]) == 2


@pytest.mark.parametrize("change", (None, "wrong_contract", "missing_private", "extra_online", "wrong_total"))
def test_projected_run_requires_exact_registered_private_and_online_event_counts(execution, change):
    registration, result, arm = execution
    counts = {
        wid: row["accepted_batch_count"]
        for wid, row in result["control_delivery"]["workflows"].items()
    }
    counts["w"] -= 3
    projection = {
        "join_information_contract": JOIN_INFORMATION_CONTRACT,
        "online_event_batches_by_workflow": counts,
        "private_join_events_by_workflow": {"w": 3},
    }
    registration["control_event_projection"] = deepcopy(projection)
    result["control_delivery"].update(
        join_information_contract=JOIN_INFORMATION_CONTRACT,
        private_join_events_by_workflow={"w": 3},
    )
    result["control_delivery"]["workflows"]["w"]["accepted_batch_count"] = counts["w"]
    if change == "wrong_contract":
        result["control_delivery"]["join_information_contract"] = "all_reconstructed_events"
    elif change == "missing_private":
        result["control_delivery"]["private_join_events_by_workflow"] = {}
    elif change == "extra_online":
        result["control_delivery"]["workflows"]["w"]["accepted_batch_count"] += 1
    elif change == "wrong_total":
        registration["control_event_projection"]["online_event_batches_by_workflow"]["w"] -= 1
    if change is None:
        assert validate_published_result(registration, result, arm)["mean_jct_ms"] == 100.
    else:
        with pytest.raises(ValueError, match="event projection"):
            validate_published_result(registration, result, arm)


@pytest.mark.parametrize("change", (
    "missing_workflow", "missing_events", "wrong_plan", "missing_request",
    "missing_streaming_completion", "changed_native_token", "changed_finish", "not_position_sampler",
))
def test_incomplete_or_changed_baseline_cannot_be_reported_as_same_work(execution, change):
    registration, original, arm = execution
    result = deepcopy(original)
    if change == "missing_workflow":
        result["workflow_elapsed_ms"].pop("other")
    elif change == "missing_events":
        result["control_delivery"]["workflows"]["other"]["accepted_batch_count"] -= 1
    elif change == "wrong_plan":
        result["source_plan_sha256"] = "other_plan"
    elif change == "missing_request":
        result["completed_requests"] -= 1
    elif change == "missing_streaming_completion":
        result["streaming_response_metrics"]["all_workflows_completed"] = False
    else:
        path = arm / "replay/native_requests.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        if change == "changed_native_token":
            rows[0]["prompt_sha256"] = "other_tokens"
        elif change == "changed_finish":
            rows[0]["native_finish_reason"] = {"type": "stop", "matched": 9}
        else:
            rows[0]["native_receipt"]["sampling_path"] = "legacy"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        result["native_requests_sha256"] = sha256_file(path)
    with pytest.raises(ValueError):
        validate_published_result(registration, result, arm)


def test_prefix_qualifier_uses_registered_cuda_without_inheriting_policy(tmp_path):
    cuda = tmp_path / "cuda"
    (cuda / "bin").mkdir(parents=True)
    (cuda / "lib").mkdir()
    nvcc = cuda / "bin/nvcc"
    nvcc.touch()
    nvcc.chmod(0o755)
    (cuda / "lib/libcudart.so.13").touch()
    inherited = {
        "CUDA_HOME": "/unrelated/cuda", "CUDA_VISIBLE_DEVICES": "99999",
        "BELIEFKV_PAPER_BASELINE": "unrelated_policy", "PATH": "/usr/bin",
        "LD_LIBRARY_PATH": "/unrelated/lib", "PYTHONPATH": "/old/application",
    }
    registration = {"reference_environment": {
        "CUDA_HOME": str(cuda), "CUDA_VISIBLE_DEVICES": "0",
    }}
    env = prefix_qualification_environment(registration, inherited)
    assert env["CUDA_HOME"] == str(cuda)
    assert env["CUDA_VISIBLE_DEVICES"] == "0"
    assert env["PATH"] == str(cuda / "bin") + ":/usr/bin"
    assert env["LD_LIBRARY_PATH"] == str(cuda / "lib") + ":/unrelated/lib"
    assert env["LIBRARY_PATH"] == str(cuda / "lib")
    assert env["PYTHONPATH"] != inherited["PYTHONPATH"]
    assert env["PYTHONHASHSEED"] == "21"
    assert not any(name.startswith("BELIEFKV_") for name in env)
    assert inherited["CUDA_HOME"] == "/unrelated/cuda"


def test_prefix_qualifier_rejects_missing_registered_toolchain(tmp_path):
    with pytest.raises(ValueError, match="registered CUDA 13"):
        prefix_qualification_environment({
            "reference_environment": {"CUDA_HOME": str(tmp_path / "missing")},
        }, {})


@pytest.fixture
def completed_run(execution, monkeypatch):
    registration, result, arm = execution
    run, source = arm.parent, registration["source_plan"]
    registration.update(
        arm=arm.name, all_normal_tails_retained=True, quality_exclusions=[],
        engine_checkout=str(run / "engine"), checkout=str(run / "application"),
        engine_tree="tree", native_patch_sha256="patch",
    )
    metrics = validate_published_result(registration, result, arm)
    stored = {
        **metrics, "requests": source["requests"], "prompt_tokens": source["prompt_tokens"],
        "output_tokens": source["output_tokens"], "all_normal_tails_retained": True,
        "whole_run_output_tokens_per_second":
            source["output_tokens"] * 1000 / metrics["workflow_event_makespan_ms"],
    }
    files = {
        run / "registration.json": registration,
        run / "state.json": {"status": "complete_review_pending"},
        run / "result.json": stored,
        arm / "replay/summary.json": result,
        arm / "telemetry_drain.json": {
            "previous_zero_snapshot_ts_ms": 201.,
            "drained_status": {
                "snapshot_ts_ms": 202., "pending_batch_count": 0,
                "pending_request_count": 0, "writer_queue_depth": 0,
                "dropped_records": 0, "failed_records": 0, "writer_error": None,
                "request_cache_evidence": {"all": {"request_count": source["requests"]}},
            },
        },
        arm / "owned_process_cleanup.json": {"remaining_processes": [], "identities": []},
    }
    for path, value in files.items():
        path.write_text(json.dumps(value))
    monkeypatch.setattr(operator.controls, "observed_configuration", lambda *_, **__: None)
    monkeypatch.setattr(operator, "git_source_state", lambda *_, **__: {
        "canonical_patch": {"actual_tree": "tree", "sha256": "patch"},
    })
    monkeypatch.setattr(operator.controls.qualified, "last_runtime_state", lambda _: {
        "physical_disabled": False,
        "paper_baseline": {"counts": {"public_timeout_force_resumed": 3}},
    })
    return run, files


def test_review_keeps_public_timeout_waits_and_does_not_accept_single_run_performance(completed_run):
    run, _ = completed_run
    result = review_completed_run(run)
    assert result["status"] == "complete_single_run_reviewed"
    assert result["public_timeout_force_resumed"] == 3
    assert result["public_timeout_wait_included_in_metrics"]
    assert result["join_information_contract"] == "legacy_replay_events_including_inferred_joins"
    assert result["same_information_comparison_requires_matching_contract"]
    assert not result["performance_accepted"]


@pytest.mark.parametrize("change", ("metrics", "throughput", "tails", "undrained", "unclean"))
def test_review_rejects_changed_metrics_or_incomplete_shutdown(completed_run, change):
    run, files = completed_run
    if change in ("metrics", "throughput"):
        path = run / "result.json"
        field = "mean_jct_ms" if change == "metrics" else "whole_run_output_tokens_per_second"
        files[path][field] += 1
    elif change == "tails":
        path = run / "registration.json"
        files[path]["quality_exclusions"] = ["slow_workflow"]
    elif change == "undrained":
        path = run / "arm/telemetry_drain.json"
        files[path]["drained_status"]["writer_queue_depth"] = 1
    else:
        path = run / "arm/owned_process_cleanup.json"
        files[path]["remaining_processes"] = [{"pid": 1}]
    path.write_text(json.dumps(files[path]))
    with pytest.raises(ValueError):
        review_completed_run(run)
