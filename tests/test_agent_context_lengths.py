import hashlib
import json

import pytest

from scripts.audit_agent_context_lengths import (
    analyze, length_buckets, records, stratify_workflow_eligibility,
)


def created(name, parent=None, *, internal=False):
    return {
        "workflow_id": "workflow", "invocation_id": name,
        "context_id": f"context-{name}", "parent_invocation_id": parent,
        "relation_type": "root" if parent is None else "call" if internal else "spawn",
        "attributes": {"runtime_internal": internal},
    }


def request(name, prompt, *, output=10):
    start = {
        "workflow_id": "workflow", "invocation_id": name,
        "context_id": f"context-{name}", "context_epoch": 0,
        "attributes": {"prompt_tokens": prompt},
    }
    end = {**start, "attributes": {"output_tokens": output}}
    return start, end


def test_agent_weighting_and_internal_exclusion_differ_from_request_weighting():
    creates = {
        ("workflow", name): created(name, parent, internal=internal)
        for name, parent, internal in (
            ("root", None, False), ("child", "root", False),
            ("summary", "root", True),
        )
    }
    pairs = (
        request("root", 1000, output=100),
        request("root", 9000, output=200),
        request("child", 1000, output=50),
        request("summary", 200000, output=100),
    )
    report = analyze(
        creates, {str(i): p[0] for i, p in enumerate(pairs)},
        {str(i): p[1] for i, p in enumerate(pairs)}, 262144,
    )
    assert report["ordinary_agents"]["agents"] == 2
    assert report["ordinary_agents"]["mean_prompt_tokens"]["mean"] == 3000
    assert report["ordinary_requests"]["prompt_tokens"]["mean"] == pytest.approx(11000 / 3)
    assert report["by_role"]["root"]["peak_context_tokens"]["max"] == 9200
    assert report["by_role"]["internal"]["agents"] == 1
    assert report["by_role"]["internal"]["mean_prompt_tokens"]["mean"] == 200000


def test_nested_parentage_missing_output_and_unserved_agents_stay_explicit():
    creates = {
        ("workflow", name): created(name, parent)
        for name, parent in (
            ("root", None), ("child", "root"), ("nested", "child"),
            ("unknown", "absent"),
        )
    }
    nested, finish = request("nested", 65536, output=64)
    unknown, _ = request("unknown", 8192)
    report = analyze(creates, {"one": nested, "two": unknown}, {"one": finish}, 262144)
    assert report["declared_without_native_service"] == 2
    assert report["by_role"]["nested_child"]["agents"] == 1
    assert report["by_role"]["nested_child"]["peak_context_tokens"]["max"] == 65600
    assert report["by_role"]["unknown"]["agents"] == 1
    assert report["by_role"]["unknown"]["mean_request_end_tokens"]["count"] == 0
    assert report["unknown"] == {"no_native_result": 1}


def test_context_epoch_mismatch_cannot_be_paired():
    start, finish = request("root", 100)
    finish["context_epoch"] = 1
    with pytest.raises(ValueError, match="identity changed"):
        analyze({("workflow", "root"): created("root")}, {"one": start}, {"one": finish}, 262144)


def test_length_bucket_edges_include_the_model_limit_once():
    result = length_buckets([0, 16383, 16384, 32768, 65536, 131072, 262144, 262145], 262144)
    assert [row["count"] for row in result["buckets"]] == [2, 1, 1, 1, 2]
    assert result["out_of_range"] == 1
    assert sum(row["percent"] for row in result["buckets"]) == 87.5


def test_source_binding_excludes_an_incomplete_trailing_record(tmp_path):
    row = {"kind": "invocation_create", "invocation_id": "root"}
    prefix = (json.dumps(row) + "\n").encode()
    path = tmp_path / "events.jsonl"
    path.write_bytes(prefix + b'{"kind": "llm_submit"')
    bindings = {}
    assert list(records(path, bindings)) == [row]
    assert bindings[str(path)]["complete_prefix_bytes"] == len(prefix)
    assert bindings[str(path)]["sha256"] == hashlib.sha256(prefix).hexdigest()


def test_workflow_eligibility_keeps_excluded_unknown_and_internal_separate():
    rows = [
        {
            "workflow_id": workflow, "role": role, "requests": count,
            "mean_prompt_tokens": prompt, "mean_request_end_tokens": prompt + 100,
            "peak_context_tokens": prompt + 200,
        }
        for workflow, role, count, prompt in (
            ("eligible", "root", 1, 1000),
            ("eligible", "child", 3, 3000),
            ("excluded", "root", 5, 131072),
            ("excluded", "internal", 1, 200000),
            ("absent", "root", 1, 8000),
            ("invalid", "child", 1, 16000),
        )
    ]
    workflows = [
        {"workflow_id": "eligible", "native_agent_jct_eligible": True},
        {"workflow_id": "excluded", "native_agent_jct_eligible": False},
        {"workflow_id": "invalid", "native_agent_jct_eligible": "false"},
    ]
    result = stratify_workflow_eligibility(rows, workflows, 262144)
    assert result["eligible"]["agents"] == 2
    assert result["eligible"]["workflow_count"] == 1
    assert result["eligible"]["mean_prompt_tokens"]["mean"] == 2000
    assert result["eligible"]["request_weighted_mean_prompt_tokens"] == 2500
    assert result["excluded"]["agents"] == 1
    assert result["excluded"]["peak_context_tokens"]["max"] == 131272
    assert result["unknown"]["workflow_ids"] == ["absent", "invalid"]
    assert sum(group["agents"] for group in result.values()) == 5
    with pytest.raises(ValueError, match="duplicate workflow summary"):
        stratify_workflow_eligibility(rows, workflows + workflows[:1], 262144)
