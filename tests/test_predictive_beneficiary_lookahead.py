from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_predictive_beneficiary_lookahead import audit


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_lookahead_separates_strict_and_capacity_proxy_false_negatives(
    tmp_path: Path,
) -> None:
    source = tmp_path / "audit.jsonl"
    _write(
        source,
        [
            {
                "event": "predictive_beneficiary_hint_published",
                "ts_ms": 100.0,
                "request_id": "strict",
                "hint_signature": ["strict", "context", 0, 40, 20],
                "beneficiary_opportunity_classification": "capacity_available",
                "beneficiary_predicted_deficit_bytes": 0,
            },
            {
                "event": "predictive_beneficiary_hint_published",
                "ts_ms": 100.0,
                "request_id": "proxy",
                "hint_signature": ["proxy", "context", 0, 70, 20],
                "beneficiary_opportunity_classification": "slot_only",
                "beneficiary_predicted_deficit_bytes": 0,
            },
            {
                "event": "admission_ticket_epoch_finished",
                "ts_ms": 500.0,
                "native_rejected": [
                    {"request_id": "strict", "native_result": "NO_TOKEN"}
                ],
            },
            {
                "event": "resource_snapshot",
                "ts_ms": 600.0,
                "hbm_capacity_bytes": 1000,
                "hbm_used_bytes": 920,
                "running_request_count": 32,
            },
        ],
    )

    result = audit(source, horizon_ms=2000.0)

    assert result["strict_hbm_false_negative_count"] == 1
    assert result["capacity_proxy_false_negative_count"] == 1
    assert result["observed_outcome_counts"] == {
        "native_hbm_rejected": 1,
        "slot_saturated_no_service": 1,
    }


def test_lookahead_service_evidence_precedes_capacity_proxy(tmp_path: Path) -> None:
    source = tmp_path / "audit.jsonl"
    _write(
        source,
        [
            {
                "event": "predictive_beneficiary_hint_published",
                "ts_ms": 100.0,
                "request_id": "served",
                "hint_signature": ["served", "context", 0, 70, 20],
                "beneficiary_opportunity_classification": "capacity_available",
                "beneficiary_predicted_deficit_bytes": 0,
            },
            {
                "event": "request_started",
                "ts_ms": 200.0,
                "request_id": "served",
            },
            {
                "event": "resource_snapshot",
                "ts_ms": 300.0,
                "hbm_capacity_bytes": 1000,
                "hbm_used_bytes": 920,
                "running_request_count": 1,
            },
        ],
    )

    result = audit(source, horizon_ms=2000.0)

    assert result["observed_outcome_counts"] == {"service_started": 1}
    assert result["capacity_proxy_false_negative_count"] == 0
