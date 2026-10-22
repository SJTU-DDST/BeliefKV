from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.runtime.replay_token_capture import (
    capture_record, native_request_expectations,
)


def source(count=4, width=100_000):
    records, native, prompts = [], [], {}
    for index in range(count):
        request = f"r{index}"
        prompt = [3] * width + [index]
        prompts[request] = prompt
        raw = {
            "request_id": request, "ts_ms": 1.,
            "workflow_id": "w", "invocation_id": "i", "context_id": "c",
            "context_epoch": index, "session_id": "s", "session_generation": 1,
            "extra_key": "ns", "cache_salt": None,
            "_replay_request_parameters": {
                "max_new_tokens": 32, "min_new_tokens": 0, "ignore_eos": False,
                "stop": [], "stop_regex": [], "stop_token_ids": [9],
            },
            "_replay_finish_reason": {"type": "stop", "matched": 9},
        }
        records.extend((
            capture_record(
                raw, prompt, event="request_prompt",
                base_request_id=f"r{index - 1}" if index and width else None,
                common_prefix_tokens=width if index else 0,
            ),
            capture_record(raw, [7, 9], event="request_output"),
        ))
        for kind, key, count_value in (
            ("llm_submit", "prompt_tokens", len(prompt)),
            ("llm_result", "output_tokens", 2),
        ):
            native.append({
                **raw, "kind": kind,
                "attributes": {"request_id": request, key: count_value},
            })
    return records, native_request_expectations(native), prompts


def write_source(path, records):
    path.write_bytes(b"".join(
        json.dumps(record, separators=(",", ":")).encode() + b"\n"
        for record in records
    ))


@pytest.mark.parametrize("cache_bytes", (0, 1, 400_004, 1_000_000))
def test_arbitrary_ready_order_reconstructs_exact_long_tokens_with_bounded_cache(
    tmp_path, cache_bytes,
):
    records, expected, prompts = source()
    path = tmp_path / "tokens.jsonl"
    write_source(path, records)
    with CapturedTokenCorpus(path, expected, cache_bytes=cache_bytes) as corpus:
        for request in ("r3", "r1", "r0", "r2", "r3"):
            restored = corpus.get(request)
            assert list(restored.prompt_tokens) == prompts[request]
            assert list(restored.output_tokens) == [7, 9]
            assert restored.identity == ("w", "i", "c", int(request[1:]))
            assert restored.sampling_parameters["max_new_tokens"] == 32
            assert restored.sampling_parameters["min_new_tokens"] == 0
            assert restored.sampling_parameters["ignore_eos"] is False
            assert restored.native_finish_reason == {"type": "stop", "matched": 9}
            assert restored.session_id == "s"
            assert restored.session_generation == 1
            assert restored.extra_key == "ns"
            assert corpus.cache_size_bytes <= cache_bytes
        evidence = corpus.evidence
        assert evidence["requests"] == 4
        assert evidence["token_evidence_complete"]
        assert evidence["original_stopping_parameters_complete"]
        assert evidence["token_source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert not evidence["gpu_replay_qualified"]
        assert not evidence["performance_accepted"]


def test_deep_reference_chain_does_not_recurse_or_require_recorded_request_order(tmp_path):
    records, expected, _ = source(count=1100, width=1)
    path = tmp_path / "deep.jsonl"
    write_source(path, records)
    with CapturedTokenCorpus(path, expected, cache_bytes=0) as corpus:
        assert list(corpus.get("r1099").prompt_tokens) == [3, 1099]


def test_request_mutation_cannot_poison_cached_prefix_or_stopping_parameters(tmp_path):
    records, expected, _ = source(width=4)
    path = tmp_path / "tokens.jsonl"
    write_source(path, records)
    with CapturedTokenCorpus(path, expected) as corpus:
        restored = corpus.get("r0")
        restored.prompt_tokens[0] = 999
        restored.output_tokens[0] = 999
        restored.sampling_parameters["max_new_tokens"] = 2
        restored.native_finish_reason["type"] = "length"
        evidence = corpus.evidence
        evidence["token_evidence_complete"] = False
        again = corpus.get("r1")
        assert list(again.prompt_tokens) == [3, 3, 3, 3, 1]
        assert list(again.output_tokens) == [7, 9]
        assert again.sampling_parameters["max_new_tokens"] == 32
        assert again.native_finish_reason["type"] == "stop"
        assert corpus.evidence["token_evidence_complete"]


def test_concurrent_ready_requests_do_not_share_a_file_cursor(tmp_path):
    records, expected, prompts = source(count=20, width=1000)
    path = tmp_path / "tokens.jsonl"
    write_source(path, records)
    order = list(reversed(prompts))
    with CapturedTokenCorpus(path, expected, cache_bytes=16_000) as corpus:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(corpus.get, order))
        assert [list(row.prompt_tokens) for row in results] == [prompts[key] for key in order]
        assert corpus.cache_size_bytes <= 16_000


@pytest.mark.parametrize("change", (
    "append", "truncate", "overwrite", "replace", "unlink",
))
def test_source_mutation_after_indexing_never_returns_cached_tokens(tmp_path, change):
    records, expected, _ = source(width=4)
    path = tmp_path / "tokens.jsonl"
    write_source(path, records)
    with CapturedTokenCorpus(path, expected) as corpus:
        corpus.get("r0")
        if change == "append":
            with path.open("ab") as stream:
                stream.write(b"{}\n")
        elif change == "truncate":
            path.write_bytes(b"")
        elif change == "overwrite":
            path.write_bytes(path.read_bytes().replace(b'"max_new_tokens":32', b'"max_new_tokens":31'))
        elif change == "replace":
            replacement = tmp_path / "replacement"
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)
        else:
            path.unlink()
        with pytest.raises((ValueError, FileNotFoundError)):
            corpus.get("r0")


@pytest.mark.parametrize("change", (
    "missing_parameters", "old_parameters", "missing_reason", "abort",
    "invalid_budget", "invalid_minimum", "ignore_eos_type", "invalid_stop",
    "invalid_stop_regex", "invalid_stop_token", "incomplete_population",
    "changed_prefix", "partial_jsonl", "duplicate",
))
def test_incomplete_or_legacy_evidence_cannot_become_a_replay_corpus(tmp_path, change):
    records, expected, _ = source(width=4)
    if change == "missing_parameters":
        records[0]["sampling_parameters"] = None
    elif change == "old_parameters":
        del records[0]["sampling_parameters"]["stop_regex"]
    elif change == "missing_reason":
        records[1]["native_finish_reason"] = None
    elif change == "abort":
        records[1]["native_finish_reason"] = {"type": "abort"}
    elif change == "invalid_budget":
        records[0]["sampling_parameters"]["max_new_tokens"] = True
    elif change == "invalid_minimum":
        records[0]["sampling_parameters"]["min_new_tokens"] = 33
    elif change == "ignore_eos_type":
        records[0]["sampling_parameters"]["ignore_eos"] = 0
    elif change == "invalid_stop":
        records[0]["sampling_parameters"]["stop"] = ["ok", 7]
    elif change == "invalid_stop_regex":
        records[0]["sampling_parameters"]["stop_regex"] = "pattern"
    elif change == "invalid_stop_token":
        records[0]["sampling_parameters"]["stop_token_ids"] = [True]
    elif change == "incomplete_population":
        records.pop()
    elif change == "changed_prefix":
        records[2]["token_sha256"] = "0" * 64
    elif change == "duplicate":
        records.append(deepcopy(records[-1]))
    path = tmp_path / "bad.jsonl"
    write_source(path, records)
    if change == "partial_jsonl":
        path.write_bytes(path.read_bytes().rstrip(b"\n"))
    with pytest.raises((TypeError, ValueError)):
        CapturedTokenCorpus(path, expected)


@pytest.mark.parametrize("budget", (-1, True, 1.5))
def test_invalid_cache_budget_is_rejected_before_opening_source(tmp_path, budget):
    with pytest.raises(ValueError, match="cache budget"):
        CapturedTokenCorpus(tmp_path / "missing", {}, cache_bytes=budget)


def test_unknown_request_and_closed_corpus_fail_explicitly(tmp_path):
    records, expected, _ = source(width=4)
    path = tmp_path / "tokens.jsonl"
    write_source(path, records)
    corpus = CapturedTokenCorpus(path, expected)
    with pytest.raises(ValueError, match="absent"):
        corpus.get("unknown")
    corpus.close()
    with pytest.raises(ValueError, match="closed"):
        corpus.get("r0")
    assert corpus.cache_size_bytes == 0
