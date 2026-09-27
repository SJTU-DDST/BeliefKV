import json

from scripts.audit_join_parent_first_service import (
    _host_backed_window, candidate_parent_requests, server_service_offsets,
    summarize,
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


def test_host_backed_window_is_only_an_upper_bound_and_never_infers_missing_hits(
    tmp_path,
):
    server = tmp_path / "server.jsonl"
    samples = tmp_path / "samples.jsonl"
    _jsonl(server, [
        {
            "kind": "llm_submit", "ts_ms": 100.,
            "attributes": {
                "request_id": "full",
                "cached_tokens_host": 64,
                "mamba_host_hit_slots": 0,
                "cached_tokens_device": 0,
                "uncached_prompt_tokens": 3,
            },
        },
        {
            "kind": "llm_result", "ts_ms": 1000.,
            "attributes": {"request_id": "full"},
        },
        {
            "kind": "llm_submit", "ts_ms": 200.,
            "attributes": {
                "request_id": "mamba",
                "cached_tokens_host": 0,
                "mamba_host_hit_slots": 1,
                "cached_tokens_device": 70,
                "uncached_prompt_tokens": 0,
            },
        },
        {
            "kind": "llm_result", "ts_ms": 1200.,
            "attributes": {"request_id": "mamba"},
        },
        {
            "kind": "llm_submit", "ts_ms": 300.,
            "attributes": {"request_id": "missing", "cached_tokens_host": 4},
        },
        {
            "kind": "llm_result", "ts_ms": 1300.,
            "attributes": {"request_id": "missing"},
        },
        {
            "kind": "llm_submit", "ts_ms": 400.,
            "attributes": {
                "request_id": "device",
                "cached_tokens_host": 0,
                "mamba_host_hit_slots": 0,
                "cached_tokens_device": 100,
                "uncached_prompt_tokens": 0,
            },
        },
        {
            "kind": "llm_result", "ts_ms": 1400.,
            "attributes": {"request_id": "device"},
        },
    ])
    _jsonl(samples, [
        {
            "event": "gpu_service_sample", "ts_ms": 650.,
            "service_start_ts_ms": 500.,
            "request_samples": [{"request_id": "full"}],
        },
        {
            "event": "gpu_service_sample", "ts_ms": 1200.,
            "service_start_ts_ms": 1200.,
            "request_samples": [{"request_id": "mamba"}],
        },
        {
            "event": "gpu_service_sample", "ts_ms": 1300.,
            "service_start_ts_ms": 1300.,
            "request_samples": [{"request_id": "missing"}],
        },
        {
            "event": "gpu_service_sample", "ts_ms": 1400.,
            "service_start_ts_ms": 1400.,
            "request_samples": [{"request_id": "device"}],
        },
    ])
    hits = {}
    device_uncached = {}
    offsets, _ = server_service_offsets(
        server, samples, {"full", "mamba", "missing", "device"},
        host_hits=hits, device_uncached=device_uncached, use_service_start=True,
    )
    assert offsets["full"] == 400.
    assert hits == {
        "full": (64, 0), "mamba": (0, 1),
        "missing": None, "device": (0, 0),
    }
    assert device_uncached == {
        "full": (0, 3), "mamba": (70, 0),
        "missing": None, "device": (100, 0),
    }
    rows = [
        {
            "parent_request_id": rid,
            "parent_service_lead_ms": offset + 100.,
        }
        for rid, offset in offsets.items()
    ]
    bound = _host_backed_window(rows, hits, device_uncached)
    assert bound["matched_groups"] == 4
    assert bound["missing_host_hit_observation"] == 1
    assert bound["host_backed_at_parent_submit"] == 2
    assert bound["full_host_hit_groups"] == 1
    assert bound["mamba_host_hit_groups"] == 1
    assert bound["full_device_hit_groups"] == 2
    assert bound["uncached_prompt_groups"] == 1
    assert bound["uncached_prompt_tokens"] == 3
    assert bound["host_backed_service_window_ge_500ms"] == 2
    assert bound["host_backed_service_window_ge_2000ms"] == 0
    assert bound["physical_opportunity_proven"] is False
