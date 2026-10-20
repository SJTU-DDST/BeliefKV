import json
from pathlib import Path
import subprocess
import sys

import pytest

from beliefkv.experiments.delegation_policy import NESTED_IN_GRAPH_PROFILE, delegation_contract
from scripts.audit_delegation_workload import audit


def write_workflow(client, name, fanouts=(2, 2), nested=True):
    directory = client / "workflows" / name
    directory.mkdir(parents=True)
    rows = [{
        "kind": "invocation_create", "invocation_id": "root",
        "relation_type": "root",
    }]
    for index, size in enumerate(fanouts):
        members = [f"child-{index}-{offset}" for offset in range(size)]
        rows.extend([
            {"kind": "join_create", "join_id": f"join-{index}",
             "member_invocation_ids": members},
            {"kind": "join_wait", "join_id": f"join-{index}", "invocation_id": "root"},
            *[{"kind": "spawn", "invocation_id": "root", "target_invocation_id": child}
              for child in members],
        ])
    if nested and fanouts and fanouts[0]:
        rows.extend({
            "kind": "spawn", "invocation_id": "child-0-0",
            "target_invocation_id": f"grandchild-{index}",
        } for index in range(2))
    children = sum(fanouts) + (2 if nested and fanouts and fanouts[0] else 0)
    (directory / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )
    (directory / "result.json").write_text(json.dumps({
        "outcome": "completed",
        "trace": {
            "root_delegation_round_count": len(fanouts),
            "dynamic_subagent_count": children,
            "nested_spawn_count": children - sum(fanouts),
            "maximum_spawn_depth": 2 if children > sum(fanouts) else 1 if children else 0,
        },
    }))
    return directory


@pytest.fixture
def run(tmp_path):
    contract = delegation_contract(NESTED_IN_GRAPH_PROFILE)
    (tmp_path / "ab_plan.json").write_text(json.dumps({
        "root_count": 2, "workload_instance_ids_in_manifest_order": ["a", "b"],
        "code_commit": "fixture", "fanout_profile": NESTED_IN_GRAPH_PROFILE,
        "delegation_contract": contract,
    }))
    client = tmp_path / "predictive_h2d" / "client_2"
    client.mkdir(parents=True)
    (client / "manifest.json").write_text(json.dumps({
        "config": {"subagent_fanout_profile": NESTED_IN_GRAPH_PROFILE},
        "delegation_contract": contract,
    }))
    write_workflow(client, "a")
    write_workflow(client, "b", fanouts=(3,), nested=False)
    return tmp_path, client


def test_observed_workload_qualifies_without_forcing_four_rounds_per_root(run):
    root, _ = run
    result = audit(root)
    assert result["status"] == "qualified"
    assert result["roots_with_delegation"] == 2
    assert result["roots_with_multiple_rounds"] == 1
    assert result["nested_spawns"] == 2
    assert result["dynamic_children"] == 9


@pytest.mark.parametrize("mutation,check", [
    ("missing_result", "terminal_results_complete"),
    ("missing_trace", "all_expected_workflows_observed"),
    ("no_spawn", "every_root_has_delegated"),
    ("one_initial_child", "initial_root_fanout_in_2_to_4"),
    ("summary_disagrees", "valid_trace_identities_and_summary_counts"),
    ("wrong_profile", "restored_profile_and_contract"),
    ("unknown_parent", "valid_trace_identities_and_summary_counts"),
])
def test_missing_or_inconsistent_evidence_cannot_qualify_native(run, mutation, check):
    root, client = run
    directory = client / "workflows" / "b"
    if mutation == "missing_result":
        (directory / "result.json").unlink()
    elif mutation == "missing_trace":
        (directory / "runtime_events.deepagents.jsonl").unlink()
    elif mutation == "wrong_profile":
        manifest = client / "manifest.json"
        content = json.loads(manifest.read_text())
        content["config"]["subagent_fanout_profile"] = "native_in_graph_dynamic_nested"
        manifest.write_text(json.dumps(content))
    elif mutation == "summary_disagrees":
        result = directory / "result.json"
        content = json.loads(result.read_text())
        content["trace"]["dynamic_subagent_count"] += 1
        result.write_text(json.dumps(content))
    elif mutation == "unknown_parent":
        with (directory / "runtime_events.deepagents.jsonl").open("a") as stream:
            stream.write(json.dumps({
                "kind": "spawn", "invocation_id": "unknown",
                "target_invocation_id": "orphan",
            }) + "\n")
    else:
        for path in directory.iterdir():
            path.unlink()
        directory.rmdir()
        write_workflow(
            client, "b", fanouts=() if mutation == "no_spawn" else (1,), nested=False,
        )
    result = audit(root)
    assert result["status"] == "not_qualified"
    assert result["checks"][check] is False


@pytest.mark.parametrize("nested,fanouts,failed_check", [
    (False, (2, 2), "nested_spawn_observed"),
    (True, (2,), "multiple_root_rounds_observed"),
])
def test_missing_multiround_or_nested_behavior_is_not_accepted(
    run, nested, fanouts, failed_check,
):
    root, client = run
    directory = client / "workflows" / "a"
    for path in directory.iterdir():
        path.unlink()
    directory.rmdir()
    write_workflow(client, "a", fanouts=fanouts, nested=nested)
    result = audit(root)
    assert not result["checks"][failed_check]
    assert result["status"] == "not_qualified"


@pytest.mark.parametrize("qualified", [False, True])
def test_command_prevents_native_on_unqualified_workload_without_modifying_trace(
    run, qualified,
):
    root, client = run
    if not qualified:
        (client / "workflows" / "a" / "result.json").unlink()
    sources = {
        path: path.read_bytes()
        for path in client.rglob("*") if path.is_file()
    }
    output = root / "qualification.json"
    command = subprocess.run([
        sys.executable, str(Path(__file__).resolve().parents[1]
                            / "scripts/audit_delegation_workload.py"),
        "--run-root", str(root), "--require-qualified", "--output", str(output),
    ], text=True, capture_output=True)
    assert command.returncode == (0 if qualified else 2), command.stderr
    assert json.loads(output.read_text())["status"] == (
        "qualified" if qualified else "not_qualified"
    )
    assert all(path.read_bytes() == raw for path, raw in sources.items())
