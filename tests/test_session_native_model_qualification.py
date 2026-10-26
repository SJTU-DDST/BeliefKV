import asyncio
from copy import deepcopy
from datetime import datetime
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from beliefkv.control.causal_graph import RuntimeCausalContextGraph
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
from beliefkv.runtime.replay_token_capture import capture_record
from scripts import qualify_session_native_model as qualifier


def native_response(request, *, host=0):
    count = 8
    return {
        "output_ids": list(range(10, 10 + count)),
        "meta_info": {
            "id": request, "prompt_tokens": 32768, "completion_tokens": count,
            "finish_reason": {"type": "length", "length": count},
            "output_token_logprobs": [[-.5 - index / 100., 10 + index, None] for index in range(count)],
            "cached_tokens": 32767,
            "cached_tokens_details": {"device": 32767 - host, "host": host},
        },
    }


@pytest.fixture
def results():
    document, output = qualifier.configuration(), {}
    for mode in document["arms"]:
        bodies, rows = {}, {}
        for label in qualifier.LABELS:
            identity = "child" if label == "child" else "parent"
            metadata = BeliefKVRequestMetadata("model", identity, identity, 0)
            request = mode + ":" + label
            bodies[label] = qualifier.payload(
                request, list(range(32768)), metadata, "beliefkv-" + mode + "-" + identity, document,
            )
            rows[label] = qualifier.numerical.response_record(
                native_response(request, host=8192 if mode == "native_matched" and label == "recovered" else 0),
                request=request, prompt_tokens=32768, document=document,
            )
        output[mode] = {
            "requests": rows, "submitted": bodies,
            "closed_sessions": ["beliefkv-" + mode + "-" + identity for identity in ("child", "parent")],
        }
    return output


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_small_pool_is_explicit_and_two_long_sessions_cannot_both_fit():
    document = qualifier.configuration()
    assert document["prompt_tokens"] < document["device_full_tokens"]
    assert 2 * document["prompt_tokens"] > document["device_full_tokens"]
    assert document["max_running_requests"] == 24
    assert document["whole_population_qualified"] is document["performance_accepted"] is False


@pytest.mark.parametrize("mode", ("native_matched", "full"))
def test_both_arms_use_same_fence_and_real_session_sampler_without_a_replay_processor(tmp_path, mode):
    settings, flags, socket = qualifier.environment({
        "port": 12345, "configuration": qualifier.configuration(),
        "engine_checkout": "/fixture/engine", "engine_base_commit": "a" * 40,
    }, mode, tmp_path)
    assert settings["BELIEFKV_FREE_PAGE_H2D_FENCE"] == "1"
    assert settings["BELIEFKV_CAUSAL_REPLAY_BRIDGE"] == "0"
    assert settings["SGLANG_PLUGINS"] == ""
    assert settings["BELIEFKV_REPLAY_TOKEN_CAPTURE"] == "1"
    assert settings["SGLANG_SOURCE_BASE_COMMIT"] == "a" * 40
    assert settings["CONTEXT_LENGTH"] == "49152" and settings["HICACHE_SIZE_GB"] == "8"
    assert flags[-2:] == ["--max-total-tokens", "49152"]
    assert "--enable-custom-logit-processor" not in flags
    assert (socket is None) is (mode == "native_matched")


def test_matching_model_outputs_remain_a_correctness_fixture(results):
    value = qualifier.validate_model(results, qualifier.configuration())
    assert value["actual_greedy_tokens_equal"]
    assert all(delta == 0 for delta in value["maximum_logprob_absolute_delta"].values())
    assert "throughput" not in value and "mean_jct" not in value


@pytest.mark.parametrize("fault", (
    "missing_arm", "missing_call", "changed_token", "changed_logprob", "new_session",
    "same_child_session", "missing_session", "unclosed_session", "no_demand",
    "no_resident", "join_not_resident", "forced_output", "changed_seed",
    "changed_prompt", "changed_namespace", "cross_arm_prompt", "wrong_usage",
))
def test_model_evidence_rejects_false_recovery_or_unmatched_work(results, fault):
    full, native = results["full"], results["native_matched"]
    if fault == "missing_arm":
        del results["native_matched"]
    elif fault == "missing_call":
        del full["requests"]["child"]
    elif fault == "changed_token":
        full["requests"]["recovered"]["output_token_ids"][0] += 1
    elif fault == "changed_logprob":
        full["requests"]["recovered"]["output_logprobs"][0] -= .1
    elif fault == "new_session":
        full["submitted"]["recovered"]["session_id"] = "beliefkv-new"
    elif fault == "same_child_session":
        full["submitted"]["child"]["session_id"] = full["submitted"]["cold"]["session_id"]
    elif fault == "missing_session":
        del full["submitted"]["cold"]["session_id"]
    elif fault == "unclosed_session":
        full["closed_sessions"].pop()
    elif fault == "no_demand":
        native["requests"]["recovered"]["cached_tokens_details"]["host"] = 0
    elif fault == "no_resident":
        native["requests"]["resident"]["cached_tokens_details"]["device"] = 0
    elif fault == "join_not_resident":
        full["requests"]["recovered"]["cached_tokens_details"]["device"] = 0
    elif fault == "forced_output":
        full["submitted"]["recovered"]["sampling_params"]["custom_params"] = {"fake": True}
    elif fault == "changed_seed":
        full["submitted"]["recovered"]["sampling_params"]["sampling_seed"] = 22
    elif fault == "changed_prompt":
        full["submitted"]["recovered"]["input_ids"][0] += 1
    elif fault == "changed_namespace":
        full["submitted"]["recovered"]["cache_salt"] = "other"
    elif fault == "cross_arm_prompt":
        for label in ("cold", "resident", "recovered"):
            full["submitted"][label]["input_ids"][0] += 1
    else:
        full["requests"]["recovered"]["prompt_tokens"] = 0
    with pytest.raises(ValueError):
        qualifier.validate_model(results, qualifier.configuration())


def test_probe_selection_uses_observed_input_and_distinct_workflows_not_output_lengths():
    def entry(workflow, count, output):
        return {"llm_submit": ((workflow, "i", "c", 0), count), "llm_result": (None, output)}

    expected = {
        "small": entry("a", 100, 1), "one": entry("a", 40000, 10000),
        "same_workflow": entry("a", 41000, 2), "sessionless": entry("b", 42000, 1),
        "two": entry("c", 43000, 5000),
    }
    corpus = NS(get=lambda request: NS(
        session_id=None if request == "sessionless" else "beliefkv-s",
        session_generation=1,
    ))
    assert qualifier.select_probes(expected, corpus, qualifier.configuration()) == ["one", "two"]
    expected["one"]["llm_result"] = (None, 0)
    assert qualifier.select_probes(expected, corpus, qualifier.configuration()) == ["one", "two"]


@pytest.mark.parametrize("fault", (None, "session", "generation", "identity", "output", "sampler"))
def test_native_capture_checks_actual_session_generation_tokens_and_sampler(tmp_path, results, fault):
    result, captured, events = results["full"], [], []
    for label, body in result["submitted"].items():
        metadata = body["beliefkv_metadata"]
        raw = {
            "request_id": body["rid"], "ts_ms": 1.,
            "workflow_id": metadata["root_workflow_id"], "invocation_id": metadata["invocation_id"],
            "context_id": metadata["context_id"], "context_epoch": metadata["context_epoch"],
            "session_id": body["session_id"],
            "session_generation": 2 if fault == "generation" and label == "recovered" else 1,
            "extra_key": body["extra_key"], "cache_salt": body["cache_salt"],
            "_replay_request_parameters": {
                "max_new_tokens": 8, "min_new_tokens": 8, "ignore_eos": True,
                "stop": [], "stop_regex": [], "stop_token_ids": [],
            },
            "_replay_generation_parameters": {
                "temperature": 1., "top_p": 1., "top_k": 1, "min_p": 0.,
                "frequency_penalty": 0., "presence_penalty": 0., "repetition_penalty": 1.,
                "sampling_seed": 22 if fault == "sampler" else 21,
            },
            "_replay_finish_reason": {"type": "length", "length": 8},
        }
        if fault == "session":
            raw["session_id"] = "beliefkv-ignored-session"
        if fault == "identity":
            raw["context_id"] = "wrong"
        output = result["requests"][label]["output_token_ids"]
        if fault == "output":
            output = [token + 1 for token in output]
        captured.extend((
            capture_record(raw, body["input_ids"], event="request_prompt"),
            capture_record(raw, output, event="request_output"),
        ))
        events.extend(
            {**raw, "kind": kind, "attributes": {"request_id": body["rid"], field: count}}
            for kind, field, count in (("llm_submit", "prompt_tokens", 32768), ("llm_result", "output_tokens", 8))
        )
    write_jsonl(tmp_path / "server/request_tokens.jsonl", captured)
    write_jsonl(tmp_path / "server/runtime_events.sglang.jsonl", events)
    if fault is None:
        evidence = qualifier.validate_native_capture(tmp_path, result, qualifier.configuration())
        assert set(evidence["native_session_generations"].values()) == {1}
        assert evidence["capture"]["token_evidence_complete"]
    else:
        with pytest.raises(ValueError, match="native session|native capture|reopened"):
            qualifier.validate_native_capture(tmp_path, result, qualifier.configuration())


def test_protocol_probe_binds_real_session_helper_and_observed_join_order(monkeypatch, tmp_path):
    """CPU HTTP/event stand-ins; no actual GPU or model result is produced."""
    graph, submitted, closed = RuntimeCausalContextGraph(), [], []
    state = {
        "counts": {"spawn_full_prepare_settled_contexts": 1},
        "spawn_full_prepare": {"pending_contexts": 0},
    }

    class Sink:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def emit_batch(self, events):
            for event in events:
                graph.apply(event)

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, route, *, json):
            if route == "/close_session":
                closed.append(json["session_id"])
                return NS(raise_for_status=lambda: None)
            assert route == "/generate"
            submitted.append(json)
            if json["rid"].endswith(":child"):
                assert not graph.joins["model-join"].satisfied
                assert graph.invocations["parent"].state.value == "wait_join"
            elif json["rid"].endswith(":recovered"):
                assert graph.joins["model-join"].satisfied
            return NS(raise_for_status=lambda: None, json=lambda: native_response(json["rid"]))

    monkeypatch.setattr(qualifier, "UnixDatagramRuntimeEventSink", Sink)
    monkeypatch.setattr(qualifier.httpx, "AsyncClient", Client)
    monkeypatch.setattr(qualifier.controls.qualified, "last_runtime_state", lambda _: state)
    restored = Mock(return_value=[{"command_id": "actual-ack"}])
    monkeypatch.setattr(qualifier, "restored_join", restored)
    result = asyncio.run(qualifier.probe({
        "configuration": qualifier.configuration(), "port": 1, "bytes_per_token": 98304,
    }, "full", tmp_path, "fixture.sock", [list(range(32768))] * 2))
    assert [body["rid"].rsplit(":", 1)[-1] for body in submitted] == list(qualifier.LABELS)
    assert len(set(body["session_id"] for body in submitted)) == 2
    assert result["closed_sessions"] == closed and len(closed) == 2
    assert graph.workflows["session-model-full"].end_ts_ms is not None
    restored.assert_called_with(tmp_path, 8192 * 98304)


def ack():
    return {
        "event": "beliefkv_physical_action_ack", "action": "PREFETCH_GPU",
        "command_id": "restore", "source": "join_ticket", "context_id": "parent",
        "context_epoch": 0, "num_bytes": 8192 * 98304,
        "evidence": "native_child_commit_reconciled_with_live_context",
    }


def runtime():
    return {
        "physical_disabled": False, "internal_comparison_mode": "full", "paper_baseline": None,
        "agent_admission": {"counts": {"native_restore_consumers": 1}},
        "native_free_page_h2d_fence": {
            "requested": True, "enabled": True, "supported": True,
            "counts": {"free_boundary_events": 1},
        },
    }


@pytest.mark.parametrize("fault", (None, "duplicate_ack", "wrong_context", "unacked_use", "no_reuse", "no_consumer", "disabled", "wrong_prefix_path"))
def test_join_validation_requires_verified_native_allocation_reuse(tmp_path, monkeypatch, results, fault):
    acknowledged, state = ack(), runtime()
    used = {
        "event": "beliefkv_prefetch_first_service", "command_id": "restore",
        "request_id": results["full"]["requests"]["recovered"]["request_id"],
        "context_id": "parent", "full_node_reused": True, "full_reuse_proof_version": 2,
        "full_reuse_proof": "acknowledged_allocation_in_materialized_service_prefix",
    }
    if fault == "wrong_context":
        acknowledged["context_id"] = "other"
    elif fault == "unacked_use":
        used["command_id"] = "other"
    elif fault == "no_reuse":
        used["full_node_reused"] = False
    elif fault == "no_consumer":
        state["agent_admission"]["counts"]["native_restore_consumers"] = 0
    elif fault == "disabled":
        state["physical_disabled"] = True
    elif fault == "wrong_prefix_path":
        state["full_prefix_migration"] = {}
    write_jsonl(tmp_path / "server/physical_action_ack.jsonl", [acknowledged] * (2 if fault == "duplicate_ack" else 1))
    write_jsonl(tmp_path / "server/physical_action_use.jsonl", [used])
    monkeypatch.setattr(qualifier.controls.qualified, "last_runtime_state", lambda _: state)
    if fault is None:
        assert qualifier.validate_join_use(tmp_path, results["full"], {
            "configuration": qualifier.configuration(), "bytes_per_token": 98304,
        })["verified_join_bytes_reused"] == 8192 * 98304
    else:
        with pytest.raises(ValueError, match="session|Full"):
            qualifier.validate_join_use(tmp_path, results["full"], {
                "configuration": qualifier.configuration(), "bytes_per_token": 98304,
            })


@pytest.mark.parametrize("guard", ("fixed_deadline", "shared_hold"))
def test_reservation_precedes_freezing_and_any_model_process(monkeypatch, tmp_path, guard):
    monkeypatch.setattr(qualifier.controls.reservation, "now", lambda: datetime(
        2026, 10, 27, 10 if guard == "fixed_deadline" else 12, tzinfo=ZoneInfo("Asia/Shanghai"),
    ))
    monkeypatch.setattr(
        qualifier.controls.fresh, "check_hold",
        Mock(side_effect=ValueError("shared hold remains active")),
    )
    forbidden = Mock(side_effect=AssertionError("must not reach source or GPU work"))
    monkeypatch.setattr(qualifier, "freeze", forbidden)
    monkeypatch.setattr(qualifier.subprocess, "Popen", forbidden)
    with pytest.raises(ValueError, match="reserved|hold"):
        qualifier.run(NS(reference=tmp_path / "not_read.json"))
    forbidden.assert_not_called()


@pytest.mark.parametrize("fault", (None, "source", "reference", "prompts", "engine", "base"))
def test_registration_rechecks_original_source_inputs_and_native_engine(monkeypatch, tmp_path, fault):
    reference = tmp_path / "reference.json"
    reference.write_text("{}")
    inputs = {"prompts": [[1], [2]], "bytes_per_token": 98304, "probe_requests": ["a", "b"]}
    candidate = json.loads((qualifier.ROOT / qualifier.components.CONFIG).read_bytes())["engine_candidate"]
    registration = {
        "reference": str(reference), "reference_sha256": qualifier.sha256_file(reference),
        "configuration": qualifier.configuration(), **deepcopy(inputs),
        "engine_tree": candidate["candidate_tree"], "engine_base_commit": candidate["base_commit"],
        "source_sha256": {name: qualifier.sha256_file(qualifier.ROOT / name) for name in qualifier.SOURCES},
    }
    monkeypatch.setattr(qualifier, "probe_inputs", lambda *_: deepcopy(inputs))
    if fault == "source":
        registration["source_sha256"][qualifier.SOURCES[0]] = "stale"
    elif fault == "reference":
        reference.write_text('{"changed":true}')
    elif fault == "prompts":
        registration["prompts"][0][0] += 1
    elif fault == "engine":
        registration["engine_tree"] = "wrong"
    elif fault == "base":
        registration["engine_base_commit"] = "wrong"
    if fault is None:
        qualifier.check_registration(registration)
    else:
        with pytest.raises(ValueError, match="session model"):
            qualifier.check_registration(registration)


def test_busy_foreign_gpu_is_waited_for_without_launch_or_termination(monkeypatch, tmp_path):
    path = tmp_path / "registration.json"
    path.write_text(json.dumps({"port": 1}))
    monkeypatch.setattr(qualifier, "check_hold", lambda: None)
    monkeypatch.setattr(qualifier, "check_registration", lambda _: None)
    monkeypatch.setattr(qualifier.signal, "signal", lambda *_: None)
    monkeypatch.setattr(qualifier.components, "gpu_processes", lambda: [999999])
    monkeypatch.setattr(qualifier.controls, "owned_processes", lambda rows: rows)
    monkeypatch.setattr(qualifier.controls, "cleanup", lambda *_: None)
    monkeypatch.setattr(qualifier.controls.qualified, "resources", lambda _: {"ready": False})
    launch = Mock(side_effect=AssertionError("foreign GPU must remain undisturbed"))
    monkeypatch.setattr(qualifier.subprocess, "Popen", launch)

    def stop_wait(_seconds):
        raise TimeoutError("CPU observation of queued resource wait")

    monkeypatch.setattr(qualifier.time, "sleep", stop_wait)
    assert qualifier.execute(path) is False
    result = json.loads((tmp_path / "summary.json").read_bytes())
    assert result["error_type"] == "TimeoutError"
    assert result["model_numerical_equivalence_verified"] is False
    launch.assert_not_called()


def test_execute_cannot_bypass_the_reservation_with_an_old_registration(monkeypatch, tmp_path):
    path = tmp_path / "registration.json"
    path.write_text("{}")
    monkeypatch.setattr(qualifier.signal, "signal", lambda *_: None)
    monkeypatch.setattr(qualifier.controls, "cleanup", lambda *_: None)
    monkeypatch.setattr(qualifier.controls.reservation, "now", lambda: datetime(
        2026, 10, 27, 10, tzinfo=ZoneInfo("Asia/Shanghai"),
    ))
    launch = Mock(side_effect=AssertionError("must not start GPU work"))
    monkeypatch.setattr(qualifier.subprocess, "Popen", launch)
    assert qualifier.execute(path) is False
    result = json.loads((tmp_path / "summary.json").read_bytes())
    assert result["error_type"] == "HoldChanged"
    assert result["model_numerical_equivalence_verified"] is False
    launch.assert_not_called()


@pytest.mark.parametrize("fault", (None, "custom_sampler", "session_disabled", "wrong_pool", "wrong_batch"))
def test_server_requires_the_actual_declared_bounded_geometry(tmp_path, fault):
    document = qualifier.configuration()
    settings, _, _ = qualifier.environment({
        "port": 1, "configuration": document, "engine_checkout": "/fixture",
        "engine_base_commit": "a" * 40,
    }, "full", tmp_path)
    info = {
        "model_path": settings["MODEL_PATH"], "dtype": "bfloat16", "kv_cache_dtype": "bfloat16",
        "context_length": 49152, "max_running_requests": 24, "chunked_prefill_size": 4096,
        "page_size": 1, "random_seed": 21, "enable_session_radix_cache": True,
        "enable_custom_logit_processor": False, "enable_beliefkv_admission": True,
        "hicache_size": 8, "hicache_write_policy": "write_back", "disable_overlap_schedule": False,
    }
    capacity = {"device_full_tokens": 49152, "device_mamba_slots": 0}
    if fault == "custom_sampler":
        info["enable_custom_logit_processor"] = True
    elif fault == "session_disabled":
        info["enable_session_radix_cache"] = False
    elif fault == "wrong_pool":
        capacity["device_full_tokens"] = 805198
    elif fault == "wrong_batch":
        info["max_running_requests"] = 8
    if fault is None:
        qualifier.verify_server(info, capacity, settings, document)
    else:
        with pytest.raises(ValueError, match="declared fixture"):
            qualifier.verify_server(info, capacity, settings, document)
