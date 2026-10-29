"""Recheck raw model outputs, native identity, fresh drain and actual owned exit."""

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.agentix_engine import verify_native_hooks
from beliefkv.experiments.agentix_model_probe import FIXTURES, compare_unforced_outputs
from beliefkv.experiments.native_engine_variant import AGENTIX_TREE
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import qualify_full_native_model as numerical
from scripts import run_causal_comparison as controls
from scripts.qualify_agentix_runtime import validate_runtime


def review(run):
    inputs = {}

    def load(path):
        inputs[str(path.resolve())] = sha256_file(path)
        return controls.load(path)

    plan = load(run / "registration.json")
    summary = load(run / "summary.json")
    policy = load(run / "qualification_policy.json")
    cleanup = load(run / "owned_process_cleanup.json")
    drain = load(run / "telemetry_drain.json")
    cpu = load(Path(plan["cpu_evidence"]))
    components = load(Path(plan["components"]))
    fixture = FIXTURES[plan["fixture"]]
    if summary["status"] != "agentix_unforced_model_qualified":
        raise ValueError("model attempt did not complete successfully")
    validate_runtime(summary["runtime"], policy)
    application, engine = Path(plan["application"]), Path(plan["engine"])
    manifest = load(Path(cpu["source_manifest"]["path"]))
    if (
        inputs[str(Path(cpu["source_manifest"]["path"]).resolve())] != cpu["source_manifest"]["sha256"]
        or sha256_file(Path(cpu["report"]["path"])) != cpu["report"]["sha256"]
        or cpu.get("all_prior_identities_retained") is not True
        or any(cpu["totals"][name] for name in ("errors", "failures", "skipped"))
    ):
        raise ValueError("source-bound common-engine CPU evidence changed")
    controls.comparison.verify_cpu_snapshot(application, manifest)
    native = git_source_state(
        engine, canonical_patch=application / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )
    if (
        native["canonical_patch"]["actual_tree"] != AGENTIX_TREE
        or components["engine_tree"] != AGENTIX_TREE
        or components["registered_frozen_application"] != str(application)
        or components["engine_checkout"] != str(engine)
        or components["status"] != "four_native_consumer_gpu_checks_qualified"
    ):
        raise ValueError("actual models and physical components use different native/application sources")
    for scene in components["reports"].values():
        path = Path(scene["path"])
        load(path)
        if (
            inputs[str(path.resolve())] != scene["sha256"]
            or scene.get("actual_exit_verified") is not True
        ):
            raise ValueError("physical component evidence changed after actual exit")
    component_cleanup = load(Path(plan["components"]).parent / "owned_process_cleanup.json")
    if component_cleanup["remaining_processes"] or any(
        controls.reservation.alive(row) for row in component_cleanup["identities"]
    ):
        raise ValueError("a physical component still owns live processes")
    hooks = verify_native_hooks(engine)
    records, bodies = {}, {}
    for phase in ("actual", "reference"):
        for index in range(3):
            label = f"{phase}-{index}"
            body = load(run / f"request_{label}.json")
            response = load(run / f"response_{label}.json")
            if (
                len(body["input_ids"]) != fixture.prompt_tokens
                or "custom_logit_processor" in body
                or body["sampling_params"] != {
                    "temperature": 0., "top_k": 1,
                    "max_new_tokens": fixture.generated_tokens,
                    "min_new_tokens": fixture.generated_tokens,
                    "ignore_eos": True, "sampling_seed": 21,
                }
            ):
                raise ValueError("raw model call changed its explicit unforced numerical fixture")
            bodies[label] = body
            records[label] = numerical.response_record(
                response, request=body["rid"], prompt_tokens=fixture.prompt_tokens,
                document={"generated_tokens": fixture.generated_tokens, "sampling_seed": 21},
            )
    for index in range(3):
        if bodies[f"actual-{index}"]["input_ids"] != bodies[f"reference-{index}"]["input_ids"]:
            raise ValueError("preempted model call and independent reference use different tokens")
    metadata = [body["beliefkv_metadata"] for body in bodies.values()]
    if (
        len({bodies[f"actual-{index}"]["beliefkv_metadata"]["root_workflow_id"] for index in range(3)}) != 1
        or len({row["root_workflow_id"] for row in metadata}) != 4
        or len({body["session_id"] for body in bodies.values()}) != 6
        or len({body["extra_key"] for body in bodies.values()}) != 6
        or len({body["cache_salt"] for body in bodies.values()}) != 6
        or any(row.get("full_prompt_replay_guaranteed") is not True for row in metadata)
    ):
        raise ValueError("actual/reference program and session ownership are not independent")
    equality = compare_unforced_outputs(records)
    idle = drain["drained_status"]
    if (
        not drain["previous_zero_snapshot_ts_ms"] < idle["snapshot_ts_ms"]
        or any(idle.get(name) != 0 for name in (
            "pending_request_count", "pending_batch_count", "writer_queue_depth",
            "failed_records", "dropped_records",
        ))
        or idle.get("writer_error") is not None
        or cleanup["remaining_processes"] or not cleanup.get("finished_at")
        or any(controls.reservation.alive(row) for row in (
            summary["supervisor"], *cleanup["identities"],
        ))
    ):
        raise ValueError("actual native model drain or owned exit is incomplete")
    paper = summary["runtime"]["paper_baseline"]
    counts = paper["swap"]["counts"]
    if counts["paused_requests"] != counts["resumed_requests"]:
        raise ValueError("actual pause and native resumption populations differ")
    for path, digest in inputs.items():
        if sha256_file(Path(path)) != digest:
            raise ValueError("raw model evidence changed during review")
    return {
        "status": "common_engine_unforced_model_reviewed", "native_tree": AGENTIX_TREE,
        "fixture": plan["fixture"], "requests": len(records), **equality,
        "checkpoint_pauses": counts["paused_requests"],
        "actual_native_resumptions": counts["resumed_requests"],
        "backing_drain_control_rounds": paper["backing"]["counts"].get("drain_rounds", 0),
        "drain_rounds_are_not_independent_host_exhaustions": True,
        "actual_owned_exit_verified": True, "native_hooks": hooks,
        "full_formal_workload_qualified": False, "performance_accepted": False,
        "inputs_sha256": inputs,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve every raw-model review")
    evidence = review(args.run.resolve())
    controls.reservation.write_json(args.output, evidence)
    print(json.dumps({key: value for key, value in evidence.items() if key not in (
        "inputs_sha256", "native_hooks",
    )}, indent=2))
