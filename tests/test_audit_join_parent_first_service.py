import json

from scripts.audit_join_parent_first_service import (
    candidate_parent_requests, server_service_offsets, summarize,
)


def _jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8",
    )


def test_parent_request_matched_by_original_client_clock(tmp_path):
    workflow = tmp_path / "task"
    workflow.mkdir()
    _jsonl(workflow / "runtime_events.deepagents.jsonl", [{
        "kind": "join_wait", "join_id": "join-1",
        "invocation_id": "root", "ts_ms": 20.,
    }, {
        "kind": "llm_submit", "ts_ms": 340.,
        "invocation_id": "other",
        "attributes": {"request_id": "wrong-parent"},
    }, {
        "kind": "llm_submit", "ts_ms": 340.,
        "invocation_id": "root",
        "attributes": {"request_id": "parent-request"},
    }, {
        "kind": "llm_submit", "ts_ms": 390.,
        "invocation_id": "root",
        "attributes": {"request_id": "other-request"},
    }])
    rows, errors = candidate_parent_requests(tmp_path, [
        {
            "task_id": "task", "join_id": "join-1", "label": "natural",
            "trigger_ts_ms": 140., "parent_reentry_lead_ms": 200.,
            "lead_ms": 130., "project": "heldout",
        },
        {
            "task_id": "task", "join_id": "join-1", "label": "revoked",
            "trigger_ts_ms": 150., "parent_reentry_lead_ms": 190.,
        },
    ])
    assert errors == {}
    assert len(rows) == 1
    assert rows[0]["parent_request_id"] == "parent-request"


def test_server_delay_stays_on_server_clock_and_rejects_late_service(tmp_path):
    server = tmp_path / "server.jsonl"
    samples = tmp_path / "samples.jsonl"
    _jsonl(server, [{
        "kind": "llm_submit", "ts_ms": 100_000.,
        "attributes": {"request_id": "parent-request"},
    }, {
        "kind": "llm_result", "ts_ms": 100_400.,
        "attributes": {"request_id": "parent-request"},
    }, {
        "kind": "llm_submit", "ts_ms": 110_000.,
        "attributes": {"request_id": "late"},
    }, {
        "kind": "llm_result", "ts_ms": 110_400.,
        "attributes": {"request_id": "late"},
    }])
    _jsonl(samples, [{
        "event": "gpu_service_sample", "ts_ms": 100_150.,
        "request_samples": [{"request_id": "parent-request"}],
    }, {
        "event": "gpu_service_sample", "ts_ms": 110_500.,
        "request_samples": [{"request_id": "late"}],
    }])
    offsets, errors = server_service_offsets(
        server, samples, {"parent-request", "late"},
    )
    assert offsets == {"parent-request": 150.}
    assert errors == {"service_outside_request": 1}
    row = {
        "task_id": "task", "project": "heldout",
        "lead_ms": 130., "parent_reentry_lead_ms": 200.,
        "submit_to_service_ms": offsets["parent-request"],
        "parent_service_lead_ms": 200. + offsets["parent-request"],
    }
    summary = summarize([row])
    assert summary["parent_service_lead_p50_ms"] == 350.
    assert summary["service_window_count"]["500"] == 0
