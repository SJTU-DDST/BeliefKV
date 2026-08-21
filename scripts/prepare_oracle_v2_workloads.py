#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
from collections import defaultdict, deque
import json
import math
from pathlib import Path
import random
import subprocess
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from beliefkv.experiments.p6_split import load_split_manifest, resolve_split
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.swebench_images import swebench_instance_image
from beliefkv.experiments.swebench_prompt import build_swebench_task_prompt


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preregister representative and KV-pressure Oracle v2 workloads."
    )
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument(
        "--exclude-collection-plan", type=Path, action="append", default=[]
    )
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--runtime-profile", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidate-count", type=int, default=64)
    parser.add_argument("--wave-size", type=int, default=16)
    return parser.parse_args()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_dataset(path: Path) -> tuple[list[dict[str, Any]], Path]:
    from datasets import load_from_disk

    path = path.expanduser().resolve()
    try:
        dataset = load_from_disk(str(path))
    except FileNotFoundError:
        path = path / "test"
        dataset = load_from_disk(str(path))
    if hasattr(dataset, "values"):
        values = list(dataset.values())
        if len(values) != 1:
            raise ValueError("dataset must contain exactly one split")
        dataset = values[0]
    return [dict(row) for row in dataset.to_list()], path


def _excluded_instances(paths: list[Path]) -> set[str]:
    excluded: set[str] = set()
    for path in paths:
        payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
        for batch in payload.get("batches", ()):
            excluded.update(str(value) for value in batch.get("instance_ids", ()))
    return excluded


def _round_robin(rows: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    grouped: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for row in sorted(rows, key=lambda item: (str(item["repo"]), str(item["instance_id"]))):
        grouped[str(row["repo"])].append(row)
    projects = sorted(grouped)
    selected: list[dict[str, Any]] = []
    while len(selected) < count and projects:
        remaining = []
        for project in projects:
            if grouped[project] and len(selected) < count:
                selected.append(grouped[project].popleft())
            if grouped[project]:
                remaining.append(project)
        projects = remaining
    if len(selected) != count:
        raise ValueError(f"only {len(selected)} eligible train tasks; {count} required")
    return selected


def _workload(row: dict[str, Any], source_root: Path) -> dict[str, Any]:
    project = str(row["repo"])
    source = source_root / project.replace("/", "__")
    if not (source / ".git").exists():
        raise ValueError(f"source repository is unavailable: {source}")
    return {
        "instance_id": str(row["instance_id"]),
        "repo": project,
        "base_commit": str(row["base_commit"]),
        "problem_statement": str(row["problem_statement"]),
        "difficulty": str(row.get("difficulty") or "unknown"),
        "version": str(row["version"]),
        "rollout_index": 0,
        "source_repo": str(source),
        "docker_image": swebench_instance_image(str(row["instance_id"])),
        "preflight_command": None,
    }


ORACLE_PRESSURE_CONTEXT_MARKER = (
    "\n\nFrozen repository context pack for this preregistered KV-pressure "
    "workload follows. It is read-only reference material; inspect the live "
    "workspace before editing.\n"
)


def _git_text(source: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=source,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout


def _freeze_context_pack(
    workload: dict[str, Any],
    *,
    target_tokens: int,
    seed: int,
    tokenizer: Any,
    destination: Path,
    source_file_limit: int = 256,
) -> dict[str, object]:
    source = Path(str(workload["source_repo"]))
    commit = str(workload["base_commit"])
    allowed_suffixes = {
        ".c", ".cc", ".cpp", ".go", ".h", ".hpp", ".java", ".js",
        ".md", ".py", ".rst", ".rs", ".toml", ".ts", ".yaml", ".yml",
    }
    names = [
        name
        for name in _git_text(source, "ls-tree", "-r", "--name-only", commit).splitlines()
        if Path(name).suffix.lower() in allowed_suffixes
    ]
    random.Random(seed).shuffle(names)
    chunks = []
    used_files = []
    character_budget = target_tokens * 8
    for name in names:
        result = subprocess.run(
            ["git", "show", f"{commit}:{name}"],
            cwd=source,
            check=False,
            capture_output=True,
        )
        if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
            continue
        try:
            content = result.stdout.decode("utf-8")
        except UnicodeDecodeError:
            continue
        chunks.append(f"\n--- repository file: {name} ---\n{content}")
        used_files.append(name)
        if len(used_files) >= source_file_limit or sum(map(len, chunks)) >= character_budget:
            break
    if not chunks:
        raise ValueError(f"no context source files for {workload['instance_id']}")

    prefix = build_swebench_task_prompt(
        instance_id=str(workload["instance_id"]),
        repo=str(workload["repo"]),
        base_commit=str(workload["base_commit"]),
        problem_statement=str(workload["problem_statement"]),
    ) + ORACLE_PRESSURE_CONTEXT_MARKER
    prefix_ids = tokenizer.encode(prefix, add_special_tokens=False)
    source_ids = tokenizer.encode("".join(chunks), add_special_tokens=False)
    if len(prefix_ids) + 1024 >= target_tokens or not source_ids:
        raise ValueError(f"invalid context token budget for {workload['instance_id']}")
    needed = target_tokens - len(prefix_ids)
    payload_ids = (source_ids * math.ceil(needed / len(source_ids)))[:needed]
    cursor = needed % len(source_ids)
    for _ in range(12):
        context = tokenizer.decode(
            payload_ids,
            skip_special_tokens=False,
            clean_up_tokenization_spaces=False,
        )
        actual = len(tokenizer.encode(prefix + context, add_special_tokens=False))
        delta = target_tokens - actual
        if 0 <= delta <= 16:
            break
        if delta < 0:
            payload_ids = payload_ids[: max(1, len(payload_ids) + delta - 4)]
        else:
            extension = [
                source_ids[(cursor + index) % len(source_ids)]
                for index in range(delta + 4)
            ]
            payload_ids.extend(extension)
            cursor = (cursor + len(extension)) % len(source_ids)
    else:
        raise ValueError(f"context token calibration did not converge: {workload['instance_id']}")
    context = tokenizer.decode(
        payload_ids,
        skip_special_tokens=False,
        clean_up_tokenization_spaces=False,
    )
    actual = len(tokenizer.encode(prefix + context, add_special_tokens=False))
    if not target_tokens - 128 <= actual <= target_tokens:
        raise ValueError(
            f"context token target missed for {workload['instance_id']}: {actual}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as stream:
            stream.write(context.encode("utf-8"))
    digest = hashlib.blake2b(digest_size=32)
    with destination.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "actual_parent_prompt_tokens": actual,
        "context_pack_path": str(destination),
        "context_pack_blake2b": digest.hexdigest(),
        "source_file_count": len(used_files),
    }


def main() -> int:
    args = _args()
    if args.candidate_count <= 0 or args.wave_size <= 0:
        raise ValueError("candidate count and wave size must be positive")
    if args.candidate_count % args.wave_size:
        raise ValueError("candidate count must be divisible by wave size")

    split_path = args.split_manifest.expanduser().resolve()
    split = load_split_manifest(split_path)
    rows, dataset_path = _load_dataset(args.dataset_dir)
    excluded = _excluded_instances(args.exclude_collection_plan)
    train_rows = [
        row
        for row in rows
        if str(row["instance_id"]) not in excluded
        and resolve_split(
            split,
            dataset=str(split["dataset"]),
            project=str(row["repo"]),
            instance_id=str(row["instance_id"]),
            base_commit=str(row["base_commit"]),
        )
        == "train"
    ]
    selected = _round_robin(train_rows, args.candidate_count)
    source_root = args.source_root.expanduser().resolve()
    workloads = [_workload(row, source_root) for row in selected]
    output_dir = args.output_dir.expanduser().resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")

    runtime_profile_path = args.runtime_profile.expanduser().resolve()
    runtime_profile = json.loads(runtime_profile_path.read_text(encoding="utf-8"))
    tokenizer_path = Path(runtime_profile["model"]["path"]).expanduser().resolve()
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        str(tokenizer_path),
        local_files_only=True,
        trust_remote_code=True,
    )
    model_context_tokens = int(runtime_profile["model"]["context_length"])

    batches = []
    for offset in range(0, len(workloads), args.wave_size):
        wave_index = offset // args.wave_size + 1
        wave = workloads[offset : offset + args.wave_size]
        batch_id = f"oracle-v2-natural-opportunity-wave-{wave_index:02d}"
        manifest_path = output_dir / "workload_manifests" / f"{batch_id}.json"
        _write_json(
            manifest_path,
            {
                "schema_version": 2,
                "dataset": split["dataset"],
                "dataset_revision": split["dataset_revision"],
                "split_manifest": str(split_path),
                "split": "train",
                "selection_policy": (
                    "lexical project-round-robin over unused train tasks; "
                    "frozen before execution; no outcome or Oracle filtering"
                ),
                "rollout_index": 0,
                "subagent_fanout_profile": "natural",
                "evidence_role": "natural_opportunity_prevalence_pool",
                "workloads": wave,
            },
        )
        batches.append(
            {
                "batch_id": batch_id,
                "split": "train",
                "workflow_count": len(wave),
                "instance_ids": [item["instance_id"] for item in wave],
                "projects": sorted({item["repo"] for item in wave}),
                "workload_manifest": str(manifest_path),
                "workload_manifest_sha256": sha256_file(manifest_path),
                "docker_images": sorted({item["docker_image"] for item in wave}),
                "concurrency": len(wave),
                "subagent_fanout_profile": "natural",
                "workflow_arrival_interval_ms": 0,
                "saturated_root_backlog": True,
                "predictive_actions": False,
                "policy": "frozen_p5_observed",
            }
        )

    natural_plan = {
        "schema_version": 2,
        "plan_id": "oracle-v2-natural-opportunity-pool-v2",
        "frozen": True,
        "evidence_role": "natural_opportunity_prevalence_not_representative_distribution",
        "dataset": split["dataset"],
        "dataset_revision": split["dataset_revision"],
        "dataset_path": str(dataset_path),
        "split_manifest": str(split_path),
        "source_root": str(source_root),
        "runtime_profile": str(runtime_profile_path),
        "selection_contract": {
            "split": "train",
            "ordering": "project_round_robin_then_instance_id",
            "excluded_instance_count": len(excluded),
            "candidate_count": len(workloads),
            "wave_size": args.wave_size,
            "model_output_filtering": False,
            "opportunity_prevalence_rule": (
                "all preregistered trajectories contribute complete local parked, "
                "reentry, and censor-safe opportunity intervals"
            ),
            "full_oracle_truth_rule": (
                "only clean complete trajectories contribute whole-run JCT truth"
            ),
            "replacement_rule": (
                "no outcome-based task replacement; censored runs remain prevalence "
                "evidence and are excluded only from whole-run truth"
            ),
        },
        "predictor_enabled": False,
        "predictive_actions_enabled": False,
        "runtime_policy": "frozen_p5_observed",
        "workflow_count": len(workloads),
        "batch_count": len(batches),
        "batches": batches,
    }
    natural_plan_path = output_dir / "natural_opportunity_collection_plan.json"
    _write_json(natural_plan_path, natural_plan)

    if len(workloads) < 32:
        raise ValueError("executable KV-pressure workload requires at least 32 tasks")
    targets = (65_536, 98_304, 131_072, 163_840)
    pressure_workloads = []
    for index, source in enumerate(workloads[:32]):
        workload = dict(source)
        workload["pressure_source_instance_id"] = source["instance_id"]
        target = targets[index % len(targets)]
        seed = 20_260_820 + index
        pack_path = (
            output_dir
            / "context_packs"
            / f"{source['instance_id']}-{target}.txt.gz"
        )
        pack = _freeze_context_pack(
            workload,
            target_tokens=target,
            seed=seed,
            tokenizer=tokenizer,
            destination=pack_path,
        )
        workload["oracle_kv_pressure"] = {
            "target_parent_prompt_tokens": target,
            "context_seed": seed,
            **pack,
            "model_context_tokens": model_context_tokens,
            "output_reserve_tokens": 4096,
            "runtime_overhead_reserve_tokens": 32768,
        }
        pressure_workloads.append(workload)

    pressure_batch_id = "oracle-v2-kv-pressure-32-parallel-r0"
    pressure_manifest_path = (
        output_dir / "workload_manifests" / f"{pressure_batch_id}.json"
    )
    _write_json(
        pressure_manifest_path,
        {
            "schema_version": 2,
            "dataset": split["dataset"],
            "dataset_revision": split["dataset_revision"],
            "split_manifest": str(split_path),
            "split": "train",
            "selection_policy": (
                "first 32 instances from the frozen natural opportunity pool; "
                "selected before execution without outcome filtering"
            ),
            "rollout_index": 0,
            "subagent_fanout_profile": "parallel_analysis_2to3",
            "evidence_role": "mechanism_stress_not_natural_prevalence",
            "workloads": pressure_workloads,
        },
    )
    pressure_batch = {
        "batch_id": pressure_batch_id,
        "split": "train",
        "workflow_count": len(pressure_workloads),
        "instance_ids": [item["instance_id"] for item in pressure_workloads],
        "projects": sorted({item["repo"] for item in pressure_workloads}),
        "workload_manifest": str(pressure_manifest_path),
        "workload_manifest_sha256": sha256_file(pressure_manifest_path),
        "docker_images": sorted(
            {item["docker_image"] for item in pressure_workloads}
        ),
        "concurrency": 32,
        "subagent_fanout_profile": "parallel_analysis_2to3",
        "workflow_arrival_interval_ms": 0,
        "workflow_arrival_batch_size": 16,
        "workflow_arrival_batch_interval_ms": 30000,
        "saturated_root_backlog": False,
        "predictive_actions": False,
        "policy": "frozen_p5_observed",
    }
    pressure = {
        "schema_version": 3,
        "plan_id": "oracle-v2-kv-pressure-executable-v3",
        "frozen": True,
        "executable": True,
        "evidence_role": "mechanism_stress_not_representative_prevalence",
        "source_contract": {
            "source_plan": str(natural_plan_path),
            "selection": "first_32_frozen_instances",
            "calibration_and_test_sealed": True,
            "context_construction": (
                "fixed-seed git-tracked repository text packed by the frozen "
                "Qwen tokenizer to each preregistered parent prompt target"
            ),
        },
        "runtime_profile": str(runtime_profile_path),
        "predictor_enabled": False,
        "predictive_actions_enabled": False,
        "runtime_policy": "frozen_p5_observed",
        "resource_contract": {
            "kv_pool_tokens": 850000,
            "host_pool_gib": 96,
            "max_running_requests": 32,
            "do_not_shrink_kv_pool": True,
            "model_context_tokens": model_context_tokens,
            "output_reserve_tokens": 4096,
            "required_reentry_total_context_lt": model_context_tokens - 4096,
        },
        "execution_contract": {
            "root_release_waves": [
                {"root_count": 16, "release_offset_ms": 0},
                {"root_count": 16, "release_offset_ms": 30000},
            ],
            "backlog_root_count": 32,
            "parent_prompt_targets": list(targets),
            "child_count_range": [2, 3],
            "fanout_profile": "parallel_analysis_2to3",
            "wait_source": (
                "real child LLM and Docker tool execution; no synthetic sleep or "
                "post-hoc duration replacement"
            ),
            "required_states": ["WAIT_TOOL", "WAIT_JOIN"],
            "future_parent_reentry_required": True,
        },
        "failure_contract": {
            "fixed_task_replacement": "none",
            "guard_or_timeout": (
                "retain complete local parked/reentry intervals with explicit censor; "
                "exclude the episode from whole-run Oracle/JCT truth"
            ),
            "context_overflow": (
                "mark input invalid and do not replace it; never truncate after "
                "observing Oracle outcomes"
            ),
        },
        "joint_opportunity_definition": {
            "eviction": (
                "migratable parked victim and HBM-blocked ready beneficiary and "
                "causal slack exceeds D2H plus commit guard"
            ),
            "stall_free_round_trip": (
                "eviction opportunity and causal slack exceeds D2H plus H2D plus "
                "both commit guards"
            ),
            "net_positive": (
                "beneficiary unlock gain exceeds predicted restore stall"
            ),
        },
        "qualification_gate": {
            "source_row": "C0 NOMINAL measured-fastpath",
            "minimum_stall_free_round_trip_windows": 10,
            "minimum_unique_victim_byte_ms": 1,
            "minimum_blocked_beneficiary_work_ms": 1,
            "peak_hbm_alone_is_sufficient": False,
        },
        "stopping_rule": (
            "run C0 first; if the fixed batch fails the exact joint gate, report "
            "insufficient opportunity and do not evaluate C1-C3 or replace tasks"
        ),
        "batch_count": 1,
        "batches": [pressure_batch],
    }
    pressure_plan_path = output_dir / "kv_pressure_execution_plan.json"
    _write_json(pressure_plan_path, pressure)
    print(
        json.dumps(
            {
                "natural_opportunity_plan": str(natural_plan_path),
                "pressure_plan": str(pressure_plan_path),
                "pressure_workload_manifest": str(pressure_manifest_path),
                "candidate_count": len(workloads),
                "pressure_workflow_count": len(pressure_workloads),
                "wave_count": len(batches),
                "projects": sorted({item["repo"] for item in workloads}),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
