"""Check actual mixed-forward progress and exact position-replay output totals."""

from pathlib import Path

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_decode_batch_efficiency import OutputConservation
from scripts.audit_sealed_wait_prefetch import SealedRecords


def mixed_output_evidence(arm):
    reader, expected = SealedRecords(), {}
    receipts = arm / "smoke/native_requests.jsonl"
    for row in reader(receipts):
        request = row["request_id"]
        if (
            request in expected or row.get("exact_tokens_verified") is not True
            or row.get("native_receipt", {}).get("native_sampler_preserved") is not True
        ):
            raise ValueError("mixed position check lacks unique exact native-sampled receipts")
        expected[request] = row
    conservation, batches, members, tokens = OutputConservation(), 0, 0, 0
    for row in reader(arm / "server/runtime_audit.jsonl"):
        if row.get("event") != "gpu_service_sample":
            continue
        conservation.observe(row)
        if row.get("phase") == "prefill":
            decoding = [
                member for member in row["request_samples"] if member.get("phase") == "decode"
            ]
            batches += bool(decoding)
            members += len(decoding)
            tokens += sum(member["output_token_delta"] for member in decoding)
    verified = conservation.verify(expected)
    if not batches or not tokens:
        raise ValueError("mixed position check observed no real decode progress inside prefill")
    if any(
        Path(path).stat().st_size != evidence["complete_prefix_bytes"]
        for path, evidence in reader.prefixes.items()
    ):
        raise ValueError("mixed position journals must be complete and settled after cleanup")
    return {
        **verified, "mixed_prefill_batches": batches, "decode_members_inside_prefill": members,
        "decode_output_tokens_inside_prefill": tokens,
        "input_jsonl_prefixes": reader.prefixes,
        "scope": "Actual sampled mixed-batch output conservation after owned cleanup; not workflow performance.",
    }


def verify_mixed_output_evidence(smoke, server_parameters):
    if not server_parameters.get("enable_mixed_chunk"):
        return None
    embedded = smoke.get("mixed_chunk_output_conservation")
    if type(embedded) is not dict or embedded.get("exact_request_output_conserved") is not True:
        raise ValueError("mixed serving requires actual GPU mixed-output qualification")
    receipts = Path(smoke["native_requests"])
    if (
        receipts.name != "native_requests.jsonl" or receipts.parent.name != "smoke"
        or sha256_file(receipts) != smoke["native_requests_sha256"]
    ):
        raise ValueError("mixed output qualification belongs to another receipt journal")
    actual = mixed_output_evidence(receipts.parent.parent)
    if embedded != actual:
        raise ValueError("mixed output qualification changed after GPU verification")
    return actual
