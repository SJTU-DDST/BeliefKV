"""Bind all inherited CPU tests and the matched materialization configurations."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts.qualify_combined_cpu_candidate import checks
from scripts.run_causal_comparison import environment, preflight_runtime
from scripts.train_workflow_work import write


def bound_document(binding):
    path = Path(binding["path"])
    if sha256_file(path) != binding["sha256"]:
        raise ValueError("a bound predecessor input changed: " + str(path))
    return json.loads(path.read_bytes())


def qualify(inherited_path, prepared, output):
    if output.exists():
        raise FileExistsError("preserve previous qualification")
    if (
        os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19 or os.sched_getaffinity(0) != {0}
        or subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)
    ):
        raise ValueError("a clean candidate and CPU0/nice19/hidden CUDA are required")
    inherited = json.loads(inherited_path.read_bytes())
    if inherited.get("qualification_passed") is not True:
        raise ValueError("inherit the complete passing CPU qualification")
    previous_sources = bound_document(inherited["source_manifest"])
    paths = set(previous_sources) | {
        "beliefkv/runtime/subagent_materialization.py",
        "tests/test_subagent_materialization.py",
        "scripts/prepare_subagent_materialization.py",
        "scripts/benchmark_subagent_materialization_cpu.py",
        "scripts/qualify_subagent_materialization_cpu.py",
        "docs/design/subagent_materialization_pressure_20261116_zh.md",
    }
    expected_changes = {
        "beliefkv/runtime/reference_thunder_admission.py",
        "beliefkv/runtime/reference_free_continuation.py",
        "beliefkv/runtime/workflow_size_admission.py",
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/experiments/internal_comparison.py",
        "scripts/run_causal_comparison.py",
    }
    changed = {
        name for name, digest in previous_sources.items()
        if sha256_file(ROOT / name) != digest
    }
    if changed != expected_changes:
        raise ValueError("unexpected predecessor source changes: " + repr(changed))
    before = {name: sha256_file(ROOT / name) for name in sorted(paths)}
    external = {
        **inherited["external_inputs_sha256"], **inherited.get("prepared_inputs_sha256", {}),
    }
    if any(sha256_file(Path(name)) != digest for name, digest in external.items()):
        raise ValueError("an inherited external input changed")
    native_path = Path(inherited["environment"]["SGLANG_SOURCE_CHECKOUT"])
    native_before = git_source_state(native_path)
    if native_before["state_sha256"] != inherited["native_source"]["state_sha256"]:
        raise ValueError("native engine changed")
    report_path = Path(inherited["report"]["path"])
    if sha256_file(report_path) != inherited["report"]["sha256"]:
        raise ValueError("predecessor CPU report changed")
    prior, _ = checks(report_path)
    if len(prior) != inherited["unique_test_identities"] or any(
        value != "passed" for value in prior.values()
    ):
        raise ValueError("predecessor CPU identities are incomplete")
    command = [value for value in inherited["command"] if not value.startswith("--junitxml=")]
    command += ["tests/test_subagent_materialization.py", "--junitxml=" + str(output / "bound_cpu.xml")]
    env = {**os.environ, **inherited["environment"]}
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    output.mkdir(parents=True)
    write(output / "sources.json", before)
    write(output / "plan.json", {
        "candidate": str(ROOT), "commit": commit, "command": command,
        "inherited_evidence": str(inherited_path.resolve()),
        "inherited_sha256": sha256_file(inherited_path),
        "environment": inherited["environment"], "GPU_action": False,
    })
    constructors, prepared_pins = {}, {}
    with tempfile.TemporaryDirectory(prefix="bkv-materialization-CPU-") as directory:
        for index, label in enumerate(("control", "pressure")):
            path = prepared / (label + ".json")
            prepared_pins[str(path.resolve())] = sha256_file(path)
            config = json.loads(path.read_bytes())
            common = config["common_environment"]
            for binding in (
                {"path": common["WORKFLOW_WORK_MODEL"], "sha256": common["WORKFLOW_WORK_MODEL_SHA256"]},
                config["workflow_arrivals"], config["private_analysis_cohort"],
            ):
                target = Path(binding["path"]).resolve()
                if sha256_file(target) != binding["sha256"]:
                    raise ValueError("prepared model, arrivals or measurement membership changed")
                prepared_pins[str(target)] = binding["sha256"]
            settings, _, _ = environment(
                {"arms": {"full": common}}, "full", ROOT, native_path,
                Path(directory), 19990 + index, ROOT / "plugins",
                max_running_requests=int(common["MAX_RUNNING_REQUESTS"]),
                tool_restore_mode=common["TOOL_RESTORE_MODE"],
                execution_frontier_restore=common.get("EXECUTION_FRONTIER_RESTORE") == "1",
                frontier_cold_reclaim=common.get("FRONTIER_COLD_RECLAIM") == "1",
                sampling_mode="position",
            )
            model_path = inherited["environment"]["BELIEFKV_CONTEXT_TOKENIZER_TEST"]
            preflight = preflight_runtime(settings, model_path)
            code = """
import json,sys
from types import SimpleNamespace as NS
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
r = NativeAdmissionRuntime(event_socket_path=sys.argv[2], model_path=sys.argv[1])
try:
    r.attach_native_cache(NS(
        tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=805198),
        req_to_token_pool=NS(available_size=lambda: 24),
    ))
    a = r._agent_admission
    assert a._combination.strict_remaining_work
    assert a._combination.window_ms == 2000
    assert not r.enable_tool_prefetch and not r.layer_handoff_enabled
    assert not a._timeout_force_resume_allowed(None)
    assert r.subagent_materialization_enabled is (sys.argv[3] == "pressure")
    print(json.dumps({
        "runtime_constructed_and_attached": True,
        "subagent_materialization_enabled": r.subagent_materialization_enabled,
        "strict_remaining_work": True, "window_ms": 2000,
        "actual_GPU_or_allocator_qualification": False,
    }))
finally:
    r.close()
"""
            child_env = {**env, **settings, "PYTHONPATH": os.pathsep.join((
                str(ROOT), str(native_path / "python"), str(ROOT / "plugins"),
            )), "CUDA_VISIBLE_DEVICES": "99999", "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1"}
            for name in (
                "BELIEFKV_ADMISSION_OPPORTUNITY_DIR", "BELIEFKV_ADMISSION_TELEMETRY_DIR",
                "BELIEFKV_NATIVE_TELEMETRY_DIR",
            ):
                child_env.pop(name, None)
            process = subprocess.run(
                [sys.executable, "-c", code, model_path,
                 str(Path(directory) / (label + ".sock")), label],
                env=child_env, cwd=ROOT, capture_output=True, text=True, timeout=60,
            )
            write(output / (label + "_constructor.json"), {
                "returncode": process.returncode, "stdout": process.stdout, "stderr": process.stderr,
            })
            if process.returncode:
                raise ValueError("actual runtime constructor failed: " + process.stderr)
            constructors[label] = {
                "preflight": preflight, "attached": json.loads(process.stdout.splitlines()[-1]),
            }
    print(json.dumps({"phase": "inherited_and_new_CPU_tests", "commit": commit}), flush=True)
    with (output / "pytest.log").open("x") as log:
        process = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    current, totals = checks(output / "bound_cpu.xml")
    native_after = git_source_state(native_path)
    source_unchanged = before == {name: sha256_file(ROOT / name) for name in before}
    external_unchanged = all(sha256_file(Path(name)) == digest for name, digest in external.items())
    prepared_unchanged = all(sha256_file(Path(name)) == digest for name, digest in prepared_pins.items())
    missing = set(prior) - set(current)
    passed = (
        process.returncode == 0 and source_unchanged and external_unchanged and prepared_unchanged
        and native_before["state_sha256"] == native_after["state_sha256"] and not missing
        and all(value == "passed" for value in current.values())
        and all(totals[name] == 0 for name in ("failures", "errors", "skipped"))
        and not subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)
        and commit == subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip()
    )
    result = {
        "qualification_passed": passed, "candidate": str(ROOT), "candidate_commit": commit,
        "command": command, "environment": inherited["environment"],
        "unique_test_identities": len(current), "junit_totals_including_subtests": totals,
        "retained_prior_test_identities": len(set(prior) & set(current)),
        "new_test_identities": [list(item) for item in sorted(set(current) - set(prior))],
        "missing_prior_test_identities": [list(item) for item in sorted(missing)],
        "sources_unchanged": source_unchanged, "external_inputs_unchanged": external_unchanged,
        "prepared_inputs_unchanged": prepared_unchanged, "native_source": native_after,
        "source_manifest": {"path": str(output / "sources.json"),
                            "sha256": sha256_file(output / "sources.json")},
        "external_inputs_sha256": external, "prepared_inputs_sha256": prepared_pins,
        "constructors": constructors,
        "inherited_evidence": {"path": str(inherited_path.resolve()), "sha256": sha256_file(inherited_path)},
        "report": {"path": str(output / "bound_cpu.xml"),
                   "sha256": sha256_file(output / "bound_cpu.xml")},
        "GPU_action": False, "performance_accepted": False,
    }
    write(output / "evidence.json", result)
    print(json.dumps({
        "qualification_passed": passed, "tests": len(current), "totals": totals,
        "missing_prior_tests": len(missing),
    }), flush=True)
    if not passed:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inherited", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    qualify(args.inherited, args.prepared, args.output_dir.resolve())


if __name__ == "__main__":
    main()
