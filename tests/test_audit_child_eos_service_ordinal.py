import json

import pytest

from scripts.audit_child_eos_service_ordinal import (
    _client_ordinals,
    latest_server_ordinal,
)


def test_latest_ordinal_places_all_unscored_tokens_before_cue():
    # The EOS hit is in scored tokens 4..7 of 20, within 27 server tokens.
    assert latest_server_ordinal(27, 20, 7) == 14
    assert latest_server_ordinal(27, 20, 20) == 27


@pytest.mark.parametrize(
    "output_tokens,scored_tokens,through",
    [(9, 10, 4), (10, 10, 0), (10, 10, 11), (10, 0, 0), (True, 2, 1)],
)
def test_latest_ordinal_rejects_invalid_accounting(
    output_tokens, scored_tokens, through,
):
    with pytest.raises(ValueError, match="accounting"):
        latest_server_ordinal(output_tokens, scored_tokens, through)


def test_client_ordinals_preserve_request_identity_within_workflow(tmp_path):
    task = "project__task"
    workflow = tmp_path / task
    workflow.mkdir()
    rows = [
        {
            "task": task,
            "rid": f"request-{i}",
            "invocation_id": f"child-{i}",
            "context_id": f"context-{i}",
            "context_epoch": i,
            "snapshots": [{"ts_ms": float(i)}],
        }
        for i in (1, 2)
    ]
    events = [
        {
            "kind": "llm_result",
            "invocation_id": row["invocation_id"],
            "context_id": row["context_id"],
            "context_epoch": row["context_epoch"],
            "attributes": {
                "request_id": row["rid"],
                "eos_shadow_scored_tokens": i + 1,
            },
        }
        for i, row in enumerate(rows, 1)
    ]
    snapshots = [
        {
            "event": "child_stream_content",
            "request_id": row["rid"],
            "ts_ms": float(i),
            "eos_shadow_scored_tokens_since_previous_snapshot": i + 1,
            "eos_shadow_max_logprob_since_previous_snapshot": -1.0,
        }
        for i, row in enumerate(rows, 1)
    ]
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    (workflow / "child_stream_content.jsonl").write_text(
        "".join(json.dumps(snap) + "\n" for snap in snapshots),
        encoding="utf-8",
    )

    candidates, excluded = _client_ordinals(tmp_path, rows)
    assert not excluded
    assert set(candidates) == {"request-1", "request-2"}
    for row in rows:
        assert candidates[row["rid"]]["row"] == row
