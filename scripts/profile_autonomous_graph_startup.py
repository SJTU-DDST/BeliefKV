#!/usr/bin/env python3
"""Count eager graph construction with a fake model and no tool execution."""

import argparse
import cProfile
import hashlib
import json
from pathlib import Path
import pstats
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def profile():
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from beliefkv.experiments import deepagents_swebench as runner

    with TemporaryDirectory(prefix="beliefkv-graph-profile-") as directory:
        root = Path(directory)
        config = runner.DeepAgentsExperimentConfig(
            mode="autonomous", base_url="http://localhost:1/v1", model="fixture",
            output_dir=root / "out", workload_manifest=root / "manifest.json",
            docker_image="fixture:latest",
            subagent_fanout_profile="native_in_graph_short_workstreams",
        )
        workload = runner.SweBenchWorkload(
            instance_id="fixture", repo="fixture/project", base_commit="deadbeef",
            problem_statement="Inspect one function.", difficulty="unknown",
        )
        audit = runner.JsonlAudit(root / "audit.jsonl")
        backend = runner.DockerWorkspaceBackend(
            root, image="fixture:latest", audit=audit, support_dir=None,
        )
        model = FakeMessagesListChatModel(responses=[AIMessage(content="done")])
        adapter = SimpleNamespace(record_call_censor=lambda _: None)
        controller = SimpleNamespace(register_model=lambda _: None, deadline=None)
        profiler = cProfile.Profile()
        try:
            with patch.object(runner, "_model", return_value=model):
                start = time.monotonic()
                profiler.runcall(
                    runner._build_autonomous_agent,
                    config, workload, backend, adapter, controller,
                )
                elapsed = time.monotonic() - start
        finally:
            audit.close()
    rows = []
    for (file, line, name), (_, calls, self_s, cumulative_s, _) in pstats.Stats(profiler).stats.items():
        if name not in ("create_agent", "create_sub_agent", "_build_task_tool", "_autonomous_subagents"):
            continue
        path = Path(file)
        rows.append({
            "file": file, "line": line, "function": name, "calls": calls,
            "self_cpu_s": self_s, "cumulative_cpu_s": cumulative_s,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    return {
        "schema_version": 1,
        "scope": "single_root_cpu_graph_construction_with_fake_model",
        "fanout_profile": config.subagent_fanout_profile,
        "elapsed_s": elapsed, "construction": rows,
        "model_requests": 0, "tool_executions": 0, "gpu_experiment": False,
        "historical_30s_gap_profiled": False,
        "concurrent_root_contention_measured": False,
        "performance_improvement_claimed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = profile()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
