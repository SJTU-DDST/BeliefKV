"""Verify checkpoint instructions in exact completed native prompts, without GPU."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tokenizers import Tokenizer

from beliefkv.core.events import RelationType, RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workload_mix import summary
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--declaration", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve original prompt-delivery audits")
    capture = args.capture.resolve()
    plan, expected, source_evidence = source_plan(capture)
    clients = list(capture.glob("client_*/manifest.json"))
    if len(clients) != 1:
        raise ValueError("require one completed original capture")
    manifest = json.loads(clients[0].read_bytes())
    workload_path = Path(manifest["config"]["workload_manifest"])
    if sha256_file(workload_path) != manifest["workload_manifest_sha256"]:
        raise ValueError("original workload declaration changed")
    workloads = {
        row["instance_id"]: row for row in json.loads(workload_path.read_bytes())["workloads"]
    }
    declaration = json.loads(args.declaration.read_bytes())
    if manifest["delegation_contract"] != declaration["delegation_contract"]:
        raise ValueError("audit the exact declared checkpoint protocol")
    config = manifest["config"]
    warning, target = (
        config["child_work_budget_warning_calls"], config["child_work_budget_target_calls"],
    )
    tokenizer = Tokenizer.from_file(str(args.tokenizer))
    requests, creates = defaultdict(list), {}
    events = sorted(
        (node.event for node in plan.nodes if node.event is not None),
        key=lambda event: event.ts_ms,
    )
    for event in events:
        identity = event.workflow_id, event.invocation_id
        if event.kind is Kind.INVOCATION_CREATE:
            creates[identity] = event
        elif event.kind is Kind.LLM_RESULT and not event.attributes.get("runtime_internal"):
            requests[identity].append(event.attributes["request_id"])
    children = []
    with CapturedTokenCorpus(capture / "server/request_tokens.jsonl", expected) as corpus:
        for identity, event in sorted(creates.items()):
            if event.relation_type is RelationType.ROOT or event.attributes.get("runtime_internal"):
                continue
            calls = requests[identity]
            if not calls:
                raise ValueError("checkpoint child has no completed model call")
            observations = []
            for index in sorted({0, warning, target, len(calls) - 1}):
                if index >= len(calls):
                    continue
                request = corpus.get(calls[index])
                text = tokenizer.decode(request.prompt_tokens, skip_special_tokens=False)
                observations.append({
                    "model_call": index + 1, "request_id": request.request_id,
                    "prompt_sha256": request.prompt_sha256,
                    "checkpoint_contract_present": "CHECKPOINT RETURN CONTRACT" in text,
                    "soft_target_instruction_present": f"within about {target} model turns" in text,
                    "warning_notice_present": (
                        f"DELEGATED WORK BUDGET: {warning} model turns have completed." in text
                    ),
                    "target_notice_present": (
                        f"DELEGATED WORK BUDGET: {target} model turns have completed." in text
                    ),
                })
            children.append({
                "workflow_id": identity[0], "invocation_id": identity[1],
                "role": event.agent_definition_id, "model_calls": len(calls),
                "observations": observations,
            })
        token_evidence = corpus.evidence
    patches = []
    assignments = {row["instance_id"]: row for row in declaration["assignments"]}
    for directory in sorted(clients[0].parent.joinpath("workflows").iterdir()):
        if directory.name not in assignments:
            raise ValueError("completed task differs from the checkpoint declaration")
        patch = directory / "model.patch"
        modified = []
        patch_error, raw_diff_digest, stripped_equivalent = None, None, None
        if patch.exists() and patch.stat().st_size:
            parsed = subprocess.run(
                ["git", "apply", "--numstat", "-z", str(patch.resolve())],
                capture_output=True, timeout=10.,
            )
            data = parsed.stdout
            if parsed.returncode:
                patch_error = parsed.stderr.decode("utf-8")
                raw = subprocess.run(
                    ["git", "diff", "--binary", workloads[directory.name]["base_commit"], "--"],
                    cwd=directory / "workspace", check=True, capture_output=True, timeout=10.,
                ).stdout
                raw_diff_digest = hashlib.sha256(raw).hexdigest()
                stripped_equivalent = patch.read_bytes().strip() == raw.strip()
                if not stripped_equivalent:
                    raise ValueError("retained workspace differs from the original captured diff")
                data = subprocess.run(
                    ["git", "apply", "--numstat", "-z"],
                    input=raw, check=True, capture_output=True, timeout=10.,
                ).stdout
            modified = [
                row.decode("utf-8").split("\t", 2)[2] for row in data.split(b"\0") if row
            ]
        patches.append({
            "task_id": directory.name,
            "scope_hypothesis": assignments[directory.name]["scope_hypothesis"],
            "patch_sha256": sha256_file(patch) if patch.exists() else None,
            "original_patch_parse_error": patch_error,
            "unstripped_workspace_diff_sha256": raw_diff_digest,
            "original_patch_and_workspace_diff_match_after_stripping": stripped_equivalent,
            "changed_paths": modified,
            "read_only_deliverable_has_changes": bool(
                modified and assignments[directory.name]["scope_hypothesis"] == "investigation"
            ),
        })
    after_target = [row for row in children if row["model_calls"] > target]
    next_prompt = [
        item for row in after_target for item in row["observations"]
        if item["model_call"] == target + 1
    ]
    result = {
        "source": str(capture), "source_evidence": source_evidence,
        "token_evidence": token_evidence, "children": children, "patches": patches,
        "summary": {
            "children": len(children), "child_calls": summary([row["model_calls"] for row in children]),
            "roles": dict(Counter(row["role"] for row in children)),
            "children_beyond_soft_target": len(after_target),
            "post_target_prompts_observed": len(next_prompt),
            "post_target_prompts_with_target_notice": sum(
                row["target_notice_present"] for row in next_prompt
            ),
            "every_sampled_child_prompt_has_checkpoint_contract": all(
                item["checkpoint_contract_present"]
                for row in children for item in row["observations"]
            ),
            "read_only_deliverables_with_changes": sum(
                row["read_only_deliverable_has_changes"] for row in patches
            ),
            "patches_with_original_parse_errors": sum(
                row["original_patch_parse_error"] is not None for row in patches
            ),
        },
        "scope": {
            "actual_prompt_token_hashes_verified": True,
            "sampled_call_boundaries_not_every_prompt": True,
            "soft_target_not_a_hard_limit": True,
            "prompt_presence_does_not_prove_obedience": True,
            "changed_paths_do_not_establish_task_success": True,
            "full_source_and_negative_results_retained": True,
        },
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (
                args.declaration, args.tokenizer, Path(__file__), clients[0], workload_path,
            )
        },
        "GPU_action": False, "performance_accepted": False,
    }
    write(args.output, result)
    print(result["summary"])


if __name__ == "__main__":
    main()
