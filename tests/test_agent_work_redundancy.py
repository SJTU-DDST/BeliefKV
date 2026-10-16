import hashlib
import json

from scripts.audit_agent_work_redundancy import (
    analyze, prefill_service, read_source, repeated_results, root_evidence, tool_pairs,
)


def event(kind, time, invocation="root", attrs=None, workflow="wf", sequence=0):
    return {
        "kind": kind, "ts_ms": time, "sequence": sequence,
        "workflow_id": workflow, "invocation_id": invocation,
        "attributes": attrs or {},
    }


def pair(invocation="root", start=0, end=1, call="a", workflow="wf"):
    attrs = {
        "tool_call_id": call, "tool_name": "execute",
        "input_sha256": "input", "output_sha256": "output", "status": "success",
    }
    rows, missing = tool_pairs([
        event("tool_start", start, invocation, attrs, workflow),
        event("tool_end", end, invocation, attrs, workflow),
    ])
    assert not missing
    return rows[0]


def test_tool_ids_are_scoped_to_invocation_and_pending_tools_remain_unknown():
    attrs = {"tool_call_id": "same-id", "tool_name": "execute"}
    rows, missing = tool_pairs([
        event("tool_start", 0, "one", attrs),
        event("tool_start", 1, "two", attrs),
        event("tool_end", 2, "two", attrs),
        event("tool_end", 4, "one", attrs),
        event("tool_start", 6, "pending", attrs),
    ])
    assert [(row["invocation"], row["duration_ms"]) for row in rows] == [
        ("two", 1), ("one", 4),
    ]
    assert missing == 1


def test_equal_future_outputs_and_other_workflows_are_not_prior_hits():
    rows = [
        pair("one", 0, 10),
        pair("two", 1, 2),
        pair("one", 3, 4),
        pair("one", 11, 12),
        pair("three", 13, 14, workflow="different"),
    ]
    within, across = repeated_results(rows)
    assert [row["start_ms"] for row in within] == [11]
    assert [row["start_ms"] for row in across] == [3, 11]


def test_same_input_does_not_require_equal_callback_output():
    rows = [pair(start=0, end=1), pair(start=2, end=3)]
    rows[1]["output_sha256"] = "different-message-id"
    assert repeated_results(rows)[0] == []
    assert len(repeated_results(rows, require_equal_output=False)[0]) == 1


def tool_message(call, before=1, after=1, output="output", physical=True):
    return {
        "type": "tool", "tool_call_id": call,
        "additional_kwargs": {
            "beliefkv_workspace_epoch_before": before,
            "beliefkv_workspace_epoch_after": after,
            "beliefkv_workspace_changed": before != after,
            "beliefkv_tool_signature": "signature",
            "beliefkv_source_result_sha256": output,
            "beliefkv_physical_execution": physical,
        },
    }


def test_root_repeats_require_physical_stable_observed_epoch_and_prior_result():
    pairs = [
        pair(start=i * 2, end=i * 2 + 1, call=str(i)) for i in range(6)
    ]
    messages = [
        tool_message("0"), tool_message("1"), tool_message("2", before=2, after=2),
        tool_message("3", before=1, after=2),
        tool_message("4", physical=False), tool_message("5", output="different"),
    ]
    result = root_evidence(messages, pairs)
    assert [row["call_id"] for row in result["repeated"]] == ["1"]
    assert len(result["eligible"]) == 4


def test_overlapping_root_tools_are_not_past_completed_hits():
    result = root_evidence(
        [tool_message("a"), tool_message("b")],
        [pair(start=0, end=5, call="a"), pair(start=1, end=2, call="b")],
    )
    assert result["repeated"] == []


def test_live_prefix_is_bound_to_complete_rows(tmp_path):
    path = tmp_path / "live.jsonl"
    valid = b'{"kind": "one"}\n'
    path.write_bytes(valid + b'{"kind":')
    bindings = []
    assert read_source(path, bindings, jsonl=True) == [{"kind": "one"}]
    assert bindings[0]["analyzed_bytes"] == len(valid)
    assert bindings[0]["sha256"] == hashlib.sha256(valid).hexdigest()


def test_prefill_extends_are_not_allocated_as_gpu_time_or_missed_geometry(tmp_path):
    path = tmp_path / "audit.jsonl"
    rows = [
        {"event": "gpu_service_sample", "phase": "decode", "sample_id": "decode"},
        {
            "event": "gpu_service_sample", "phase": "prefill", "sample_id": "prefill",
            "request_samples": [
                {
                    "request_id": "s", "token_delta": 100,
                    "token_delta_semantics": "prefill_extend_input_len",
                },
                {
                    "request_id": "r", "token_delta": 1,
                    "token_delta_semantics": "prefill_extend_input_len",
                },
                {"request_id": "unknown", "token_delta": None},
            ],
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    bindings = []
    result = prefill_service(
        path, {"s": {"role": "summary"}, "r": {"role": "root"}}, ["r"], bindings
    )
    assert result["prefill_batches"] == 1
    assert result["extend_tokens_by_role"] == {
        "summary": 100, "root": 1, "next_ordinary_after_compaction": 1,
    }
    assert result["samples_without_proven_extend_geometry"] == {"unknown": 1}
    assert result["batches_by_role_set"] == {"root+summary+unknown": 1}


def test_roles_and_summary_per_workflow_include_zero_summary_workflows(tmp_path):
    client = tmp_path / "arm/client"
    for name, events in {
        "a": [
            event("invocation_create", 0, attrs={"parent_invocation_id": None}),
            {
                **event("invocation_create", 1, "summary"),
                "parent_invocation_id": "root",
            },
            event("llm_submit", 2, "summary", {
                "request_id": "summary-request", "runtime_internal": True,
            }),
            event("llm_result", 12, "summary", {"request_id": "summary-request"}),
            event("context_compact", 12.5),
            event("llm_submit", 13, attrs={"request_id": "after-compact"}),
            event("llm_result", 14, attrs={"request_id": "after-compact"}),
            event("workflow_end", 15),
        ],
        "b": [event("workflow_start", 0, workflow="wf2")],
    }.items():
        target = client / "workflows" / name
        target.mkdir(parents=True)
        (target / "runtime_events.deepagents.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in events)
        )
    server = client.parent / "server"
    server.mkdir()
    (server / "runtime_events.sglang.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in [
            event("llm_submit", 2, attrs={
                "request_id": "summary-request", "uncached_prompt_tokens": 100,
            }),
            event("llm_result", 12, attrs={
                "request_id": "summary-request", "output_tokens": 5,
            }),
            event("llm_submit", 13, attrs={
                "request_id": "after-compact", "uncached_prompt_tokens": 20,
            }),
            event("llm_result", 14, attrs={
                "request_id": "after-compact", "output_tokens": 2,
            }),
        ])
    )
    result = analyze(client)
    assert result["requests_by_role"]["summary"]["requests"] == 1
    assert result["summary_span_sum_per_workflow_ms"]["count"] == 2
    assert result["summary_span_sum_per_workflow_ms"]["p50"] == 5
    assert result["workflow_counts"]["ended_in_observed_prefix"] == 1
    assert result["requests_by_role"]["summary"]["output_tokens"] == 5
    assert result["context_compaction"][
        "next_ordinary_request_native_ingress"
    ]["fields"]["uncached_prompt_tokens"]["sum"] == 20
