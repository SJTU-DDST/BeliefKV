import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace as NS

import pytest

from beliefkv.control.causal_graph import RuntimeCausalContextGraph
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
from scripts import qualify_full_native_model as qualifier


def response(label, prompt=65536):
    count = qualifier.configuration()["generated_tokens"]
    return {
        "output_ids": list(range(10, 10 + count)),
        "meta_info": {
            "id": "full-model:" + label, "prompt_tokens": prompt, "completion_tokens": count,
            "finish_reason": {"type": "length", "length": count},
            "output_token_logprobs": [[-.5 - index / 100., 10 + index, None] for index in range(count)],
            "cached_tokens": prompt - 1,
            "cached_tokens_details": {
                "device": 0 if label == "demand" else prompt - 1,
                "host": prompt - 1 if label == "demand" else 0,
            },
        },
    }


def rows():
    return {
        label: qualifier.response_record(
            response(label), request="full-model:" + label,
            prompt_tokens=65536, document=qualifier.configuration(),
        ) for label in qualifier.LABELS
    }


def runtime():
    return {
        "physical_disabled": False, "paper_baseline": None, "internal_comparison_mode": "full",
        "full_prefix_migration": {
            "counts": {
                "completed": 6, "demoted_tokens": 2 * 65535,
                "h2d_enqueued_tokens": 65535, "confirmed_join_restore_batches": 1,
            },
            "native_pending_commands": 0, "requests": 0, "contexts": 0,
        },
        "agent_admission": {"counts": {"native_restore_consumers": 1}, "recovery_consumer": None},
    }


def test_model_payload_cannot_force_recorded_output_or_create_fake_sessions():
    document = qualifier.configuration()
    metadata = BeliefKVRequestMetadata("workflow", "parent", "context", 0)
    body = qualifier.payload("request", (1, 2, 3), metadata, document)
    assert body["input_ids"] == [1, 2, 3]
    assert body["sampling_params"] == {
        "temperature": 0., "top_k": 1, "sampling_seed": 21,
        "min_new_tokens": 8, "max_new_tokens": 8, "ignore_eos": True,
    }
    assert body["return_logprob"] is True
    assert not {"custom_logit_processor", "session_id"} & body.keys()
    assert "custom_params" not in body["sampling_params"]
    other = qualifier.payload("second", (1, 2, 3), metadata, document)
    assert (body["extra_key"], body["cache_salt"]) == (other["extra_key"], other["cache_salt"])


@pytest.mark.parametrize("change", (
    "id", "usage", "finish", "missing_logprobs", "nan", "positive", "token_type", "output_ids",
))
def test_invalid_native_output_cannot_supply_model_numerical_evidence(change):
    result = response("resident")
    meta = result["meta_info"]
    if change == "id":
        meta["id"] = "different"
    elif change == "usage":
        meta["completion_tokens"] = 0
    elif change == "finish":
        meta["finish_reason"] = {"type": "stop", "matched": 99}
    elif change == "missing_logprobs":
        meta["output_token_logprobs"] = []
    elif change == "nan":
        meta["output_token_logprobs"][0][0] = float("nan")
    elif change == "positive":
        meta["output_token_logprobs"][0][0] = .1
    elif change == "token_type":
        meta["output_token_logprobs"][0][1] = "10"
    else:
        result["output_ids"][0] += 1
    with pytest.raises(ValueError):
        qualifier.response_record(
            result, request="full-model:resident",
            prompt_tokens=65536, document=qualifier.configuration(),
        )


@pytest.mark.parametrize("change", ("token", "logprob", "missing_probe"))
def test_actual_output_changes_cannot_be_hidden_by_successful_token_usage(change):
    evidence = rows()
    if change == "token":
        evidence["join"]["output_token_ids"][0] += 1
    elif change == "logprob":
        evidence["demand"]["output_logprobs"][0] -= .1
    else:
        del evidence["join_child"]
    with pytest.raises(ValueError):
        qualifier.compare_outputs(evidence, qualifier.configuration())


def test_equal_real_outputs_still_require_actual_host_recovery_and_reservation_consumption():
    evidence, final = rows(), runtime()
    stages = {"tool_demoted": deepcopy(final), "join_restored": deepcopy(final)}
    assert qualifier.compare_outputs(evidence, qualifier.configuration())["actual_greedy_tokens_equal"]
    assert qualifier.validate_lifecycle(
        evidence, stages, final, qualifier.configuration(),
    )["actual_native_restore_consumers"] == 1


@pytest.mark.parametrize("change", (
    "demand_recomputed", "prefetch_unused", "pending_dma", "no_consumer", "live_context",
    "uncompleted_request", "no_prefetch", "physical_failure",
))
def test_matching_outputs_do_not_hide_missing_physical_model_lifecycle(change):
    evidence, final = rows(), runtime()
    stages = {"tool_demoted": deepcopy(final), "join_restored": deepcopy(final)}
    if change == "demand_recomputed":
        evidence["demand"]["cached_tokens_details"]["host"] = 0
    elif change == "prefetch_unused":
        evidence["join"]["cached_tokens_details"]["device"] = 0
    elif change == "pending_dma":
        stages["join_restored"]["full_prefix_migration"]["native_pending_commands"] = 1
    elif change == "no_consumer":
        final["agent_admission"]["counts"]["native_restore_consumers"] = 0
    elif change == "live_context":
        final["full_prefix_migration"]["contexts"] = 1
    elif change == "uncompleted_request":
        final["full_prefix_migration"]["counts"]["completed"] = 5
    elif change == "no_prefetch":
        final["full_prefix_migration"]["counts"]["confirmed_join_restore_batches"] = 0
    else:
        final["physical_disabled"] = True
    with pytest.raises(ValueError, match="actual demotion"):
        qualifier.validate_lifecycle(evidence, stages, final, qualifier.configuration())


def test_probe_executes_control_events_in_causal_order_without_replay_sampler(monkeypatch, tmp_path):
    """Protocol stand-ins only; actual GPU and numerical evidence stays pending."""
    graph = RuntimeCausalContextGraph()
    state, sent, submitted = runtime(), [], []
    state["full_prefix_migration"]["counts"] = {}
    state["full_prefix_migration"]["contexts"] = 1
    state["agent_admission"]["counts"] = {}

    class Corpus:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, _request):
            return NS(prompt_tokens=(1,) * 65536)

    class Sink(Corpus):
        def emit_batch(self, events):
            for event in events:
                graph.apply(event)
                sent.append(event)
                counts = state["full_prefix_migration"]["counts"]
                if event.kind is Kind.TOOL_START:
                    counts["demoted_tokens"] = 65535
                if event.kind is Kind.JOIN_WAIT:
                    counts["demoted_tokens"] = 2 * 65535
                if event.kind is Kind.RETURN and event.invocation_id == "join-child":
                    assert graph.joins["controlled-join"].satisfied
                    counts.update(h2d_enqueued_tokens=65535, confirmed_join_restore_batches=1)
                if event.kind is Kind.WORKFLOW_END:
                    state["full_prefix_migration"]["contexts"] = 0

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, route, *, json):
            assert route == "/generate"
            assert "custom_params" not in json["sampling_params"]
            assert "custom_logit_processor" not in json
            submitted.append(json)
            label = json["rid"].split(":")[1]
            counts = state["full_prefix_migration"]["counts"]
            counts["completed"] = counts.get("completed", 0) + 1
            if label == "join":
                state["agent_admission"]["counts"]["native_restore_consumers"] = 1
            return NS(
                raise_for_status=lambda: None,
                json=lambda: response(label, len(json["input_ids"])),
            )

    monkeypatch.setattr(qualifier, "AgentXTokenCorpus", Corpus)
    monkeypatch.setattr(qualifier, "UnixDatagramRuntimeEventSink", Sink)
    monkeypatch.setattr(qualifier.httpx, "AsyncClient", Client)
    monkeypatch.setattr(qualifier.controls.qualified, "last_runtime_state", lambda _path: deepcopy(state))
    result = asyncio.run(qualifier.probe({
        "configuration": qualifier.configuration(), "prepared": "fixture", "tokens": "fixture",
        "probe_request": "source", "port": 1,
    }, tmp_path, "fixture.sock"))
    assert len(submitted) == 6
    assert [body["rid"] for body in submitted] == ["full-model:" + label for label in qualifier.LABELS]
    assert result["actual_greedy_tokens_equal"] is True
    assert result["original_agentx_semantic_eos_used"] is False
    assert [event.kind for event in sent].count(Kind.JOIN_WAIT) == 1
    assert graph.joins["controlled-join"].satisfied


def test_model_qualifier_never_starts_gpu_while_predecessor_is_alive(monkeypatch, tmp_path):
    class StopObservation(Exception):
        pass

    path = tmp_path / "registration.json"
    path.write_text(json.dumps({"run_root": str(tmp_path), "port": 18641, "after_run_root": "prior"}))
    monkeypatch.setattr(qualifier, "check_registration", lambda _row: None)
    monkeypatch.setattr(qualifier, "predecessor_finished", lambda _root: False)
    monkeypatch.setattr(qualifier.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(qualifier.controls.fresh, "check_hold", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(qualifier.controls, "owned_processes", lambda rows: rows)
    monkeypatch.setattr(qualifier.controls, "cleanup", lambda *_args: None)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("model check must wait for actual predecessor exit and cleanup")

    def stop(_seconds):
        raise StopObservation()

    monkeypatch.setattr(qualifier.controls.qualified, "resources", forbidden)
    monkeypatch.setattr(qualifier.subprocess, "Popen", forbidden)
    monkeypatch.setattr(qualifier.time, "sleep", stop)
    qualifier.watch(path)
    result = json.loads((tmp_path / "summary.json").read_bytes())
    assert result["error_type"] == "StopObservation"
    assert result["performance_accepted"] is False


@pytest.mark.parametrize("change", (None, "source", "preparation", "tokens", "supervisor"))
def test_model_registration_pins_candidate_population_and_predecessor_identity(tmp_path, change):
    prior, prepared, tokens = (tmp_path / name for name in ("prior", "prepared", "tokens"))
    for directory in (prior, prepared, tokens):
        directory.mkdir()
    for path in (prior / "registration.json", prepared / "preparation.json", tokens / "manifest.json"):
        path.write_text("{}")
    supervisor = {"pid": 123, "start_ticks": 456}
    (prior / "supervisor.json").write_text(json.dumps(supervisor))
    registration = {
        "after_run_root": str(prior), "prepared": str(prepared), "tokens": str(tokens),
        "configuration": qualifier.configuration(), "predecessor_supervisor": supervisor,
        "preparation_sha256": sha256_file(prepared / "preparation.json"),
        "token_manifest_sha256": sha256_file(tokens / "manifest.json"),
        "predecessor_registration_sha256": sha256_file(prior / "registration.json"),
        "source_sha256": {name: sha256_file(qualifier.ROOT / name) for name in qualifier.SOURCES},
    }
    if change == "source":
        registration["source_sha256"][qualifier.SOURCES[0]] = "old"
    elif change == "preparation":
        (prepared / "preparation.json").write_text('{"changed":true}')
    elif change == "tokens":
        (tokens / "manifest.json").write_text('{"changed":true}')
    elif change == "supervisor":
        (prior / "supervisor.json").write_text('{"pid":123,"start_ticks":789}')
    if change is None:
        qualifier.check_registration(registration)
    else:
        with pytest.raises(ValueError):
            qualifier.check_registration(registration)


def test_bounded_probe_selection_does_not_read_recorded_final_output_lengths():
    document = qualifier.configuration()
    index = {
        "one": {"prompt_tokens": 70000, "output_tokens": 1},
        "two": {"prompt_tokens": 65536, "output_tokens": 10000},
    }
    assert qualifier.select_probe(index, document) == "two"
    index["two"]["output_tokens"] = 0
    assert qualifier.select_probe(index, document) == "two"
