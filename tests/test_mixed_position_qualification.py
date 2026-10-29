from copy import deepcopy
import json

import pytest

from scripts.run_position_replay_calibration import mixed_output_evidence
from scripts.audit_mixed_position_outputs import verify_mixed_output_evidence
from beliefkv.experiments.source_provenance import sha256_file


def captured(tmp_path):
    (tmp_path / "server").mkdir()
    (tmp_path / "smoke").mkdir()

    def member(request, phase, before, delta):
        return {
            "request_id": request, "phase": phase,
            "output_tokens_reported_before": before,
            "output_tokens_after": before + delta, "output_token_delta": delta,
        }

    rows = [
        [member("resident", "prefill", 0, 1)],
        [member("cold", "prefill", 0, 0), member("resident", "decode", 1, 1)],
        [member("cold", "prefill", 0, 1), member("resident", "decode", 2, 1)],
        [member("cold", "decode", 1, 1), member("resident", "decode", 3, 0)],
    ]
    batches = [{
        "event": "gpu_service_sample", "phase": "prefill" if index < 3 else "decode",
        "output_token_accounting": "observed_output_ids_delta_all_forward_modes_v1",
        "request_samples": members,
    } for index, members in enumerate(rows)]
    receipts = [{
        "request_id": request, "output_tokens": count, "exact_tokens_verified": True,
        "native_receipt": {"native_sampler_preserved": True},
    } for request, count in (("resident", 3), ("cold", 2))]
    return batches, receipts


def write_capture(path, batches, receipts, *, truncated=False):
    (path / "server/runtime_audit.jsonl").write_text(
        "\n".join(json.dumps(row) for row in batches) + ("" if truncated else "\n"),
    )
    (path / "smoke/native_requests.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in receipts),
    )


def test_actual_mixed_progress_and_all_mode_output_must_be_conserved(tmp_path):
    batches, receipts = captured(tmp_path)
    write_capture(tmp_path, batches, receipts)
    report = mixed_output_evidence(tmp_path)
    assert report["total_output_tokens"] == 5
    assert report["mixed_prefill_batches"] == 2
    assert report["decode_members_inside_prefill"] == 2
    assert report["decode_output_tokens_inside_prefill"] == 2
    assert len(report["input_jsonl_prefixes"]) == 2


@pytest.mark.parametrize("fault", (
    "no_mixed_progress", "missing_output", "duplicate_receipt",
    "changed_sampler", "truncated_journal",
))
def test_flags_alone_or_incomplete_mixed_evidence_cannot_qualify(tmp_path, fault):
    batches, receipts = captured(tmp_path)
    if fault == "no_mixed_progress":
        for row in batches:
            row["phase"] = "decode"
            for item in row["request_samples"]:
                item["phase"] = "decode"
    elif fault == "missing_output":
        receipts[0]["output_tokens"] += 1
    elif fault == "duplicate_receipt":
        receipts.append(deepcopy(receipts[0]))
    elif fault == "changed_sampler":
        receipts[0]["native_receipt"]["native_sampler_preserved"] = False
    write_capture(tmp_path, batches, receipts, truncated=fault == "truncated_journal")
    with pytest.raises(ValueError):
        mixed_output_evidence(tmp_path)


def smoke_with_proof(tmp_path):
    batches, receipts = captured(tmp_path)
    write_capture(tmp_path, batches, receipts)
    path = tmp_path / "smoke/native_requests.jsonl"
    return {
        "native_requests": str(path), "native_requests_sha256": sha256_file(path),
        "mixed_chunk_output_conservation": mixed_output_evidence(tmp_path),
    }


def test_formal_mixed_entry_recomputes_actual_output_proof(tmp_path):
    smoke = smoke_with_proof(tmp_path)
    assert verify_mixed_output_evidence(
        smoke, {"enable_mixed_chunk": True},
    ) == smoke["mixed_chunk_output_conservation"]
    assert verify_mixed_output_evidence({}, {"enable_mixed_chunk": False}) is None


@pytest.mark.parametrize("fault", (
    "missing_proof", "false_proof", "altered_totals", "altered_runtime",
    "altered_receipts", "foreign_receipts", "truncated_runtime",
))
def test_formal_mixed_entry_rejects_absent_or_changed_gpu_evidence(tmp_path, fault):
    smoke = smoke_with_proof(tmp_path)
    if fault == "missing_proof":
        del smoke["mixed_chunk_output_conservation"]
    elif fault == "false_proof":
        smoke["mixed_chunk_output_conservation"]["exact_request_output_conserved"] = False
    elif fault == "altered_totals":
        smoke["mixed_chunk_output_conservation"]["total_output_tokens"] += 1
    elif fault == "altered_runtime":
        path = tmp_path / "server/runtime_audit.jsonl"
        with path.open("a") as output:
            output.write('{"event":"different_settled_journal"}\n')
    elif fault == "altered_receipts":
        path = tmp_path / "smoke/native_requests.jsonl"
        with path.open("a") as output:
            output.write("{}\n")
    elif fault == "foreign_receipts":
        smoke["native_requests"] = str(tmp_path / "other.jsonl")
    elif fault == "truncated_runtime":
        path = tmp_path / "server/runtime_audit.jsonl"
        path.write_text(path.read_text().rstrip("\n"))
    with pytest.raises(ValueError):
        verify_mixed_output_evidence(smoke, {"enable_mixed_chunk": True})
