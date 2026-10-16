import hashlib

import pytest

from scripts.audit_native_system_costs import analyze, read_prefix, union_ms


def submit(request="r", *, full=90, device=60, host=20, timing=None):
    return {
        "kind": "llm_submit",
        "attributes": {
            "request_id": request, "prompt_tokens": 100,
            "cached_tokens_device": device, "cached_tokens_host": host,
            "native_full_kv_hit_length": full,
            "native_ingress_timing_ms": timing,
        },
    }


def test_union_does_not_add_overlapping_wall_spans_twice():
    assert union_ms([(0, 10), (2, 3), (5, 15), (20, 21)]) == 16


def test_frontend_spans_keep_missing_invalid_and_nonfinite_values_unknown():
    rows = [
        submit("a", timing={
            "api_received_ts_ms": 0,
            "chat_conversion_started_ts_ms": 1,
            "chat_conversion_finished_ts_ms": 6,
            "api_tokenized_ts_ms": 5,
            "api_dispatch_started_ts_ms": float("nan"),
        }),
        submit("b"),
    ]
    result = analyze(rows)["all"]["stages_ms"]
    conversion = result["chat_conversion_including_template_tokenization"]
    assert conversion["count"] == 1
    assert conversion["sum"] == 5
    assert conversion["missing_or_invalid_requests"] == 1
    assert result["post_conversion_to_tokenized"]["count"] == 0
    assert result["tokenized_to_dispatch"]["count"] == 0


def test_hybrid_gap_accounts_for_both_tiers_and_not_branch_target_as_hit():
    row = submit()
    row["attributes"]["native_mamba_branching_seqlen"] = 88
    result = analyze([row])["all"]["hybrid_prefix"]
    assert result["positive_gap_tokens"]["sum"] == 10
    assert result["gap_fraction_of_observed_uncached_tokens"] == .5
    assert result["branching_target_gap_tokens"]["sum"] == 10


def test_missing_or_impossible_cache_metrics_are_not_filled_as_misses():
    result = analyze([
        submit("a", full=None),
        submit("b", full=101),
        submit("c", device=90, host=20),
        submit("d", full=60),
    ])["all"]
    assert result["hybrid_prefix"]["cache_status_counts"] == {
        "missing": 1, "invalid_lengths": 2, "observed": 1,
    }
    assert result["hybrid_prefix"]["positive_full_match_gap_requests"] == 0
    assert result["uncached_prompt_tokens"]["sum"] == 20


def test_duplicate_native_request_identity_fails_instead_of_double_counting():
    with pytest.raises(ValueError, match="duplicate"):
        analyze([submit(), submit()])


def test_subpage_rounding_is_separated_from_large_match_gaps_and_new_contexts():
    rows = [
        submit("a", full=100, device=0, host=0),
        submit("b", full=90, device=60, host=20),
    ]
    for row in rows:
        row.update(workflow_id="wf", context_id="context")
    result = analyze(rows)
    assert result["all"]["hybrid_prefix"]["positive_gap_tokens"]["sum"] == 110
    assert result["all"]["hybrid_prefix"]["gap_at_least_threshold"]["64"] == {
        "requests": 1, "tokens": 100,
    }
    assert result["by_observed_context_position"]["first"]["requests"] == 1
    assert result["by_observed_context_position"]["continuation"]["requests"] == 1


def test_live_source_records_only_complete_prefix_and_its_digest(tmp_path):
    path = tmp_path / "events.jsonl"
    complete = b'{"kind":"llm_result"}\n'
    path.write_bytes(complete + b'{"kind":')
    rows, binding = read_prefix(path)
    assert rows == [{"kind": "llm_result"}]
    assert binding["analyzed_bytes"] == len(complete)
    assert binding["discarded_partial_tail_bytes"] == 8
    assert binding["sha256"] == hashlib.sha256(complete).hexdigest()
