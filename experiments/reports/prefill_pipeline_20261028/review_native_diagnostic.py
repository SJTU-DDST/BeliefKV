"""Compare the original failed numerical fixture with its native-demand control."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.qualify_demand_prefill_model import DOCUMENT, LABELS, RESIDENT_OUTPUT, output_diagnostics
from scripts.qualify_full_native_model import response_record


def load(path, seals):
    digest = sha256_file(path)
    value = json.loads(path.read_bytes())
    if sha256_file(path) != digest:
        raise ValueError("diagnostic input changed during reading")
    seals[str(path)] = digest
    return value


def inspect(path, seals):
    report = load(path / "report.json", seals)
    cleanup = load(path / "owned_process_cleanup.json", seals)
    if cleanup["remaining_processes"] or Path(f"/proc/{report['server']['pid']}").exists():
        raise ValueError("diagnostic server has not actually exited")
    requests, rows = {}, {}
    for label in LABELS:
        request = load(path / f"request_{label}.json", seals)
        response = load(path / f"response_{label}.json", seals)
        document = {
            **DOCUMENT, "generated_tokens": RESIDENT_OUTPUT if label.startswith("a_r") else 8,
        }
        rows[label] = response_record(
            response, request=request["rid"], prompt_tokens=len(request["input_ids"]),
            document=document,
        )
        requests[label] = request
    return report, requests, rows


def main():
    output = HERE / "native_diagnostic_review.json"
    if output.exists():
        raise FileExistsError("preserve earlier numerical diagnosis")
    paths = {
        "pipeline": ROOT / "experiments/raw/demand_prefill_model_20261028_r1",
        "native": ROOT / "experiments/raw/demand_prefill_native_diagnostic_20261028_r1",
    }
    seals = {}
    arms = {name: inspect(path, seals) for name, path in paths.items()}
    original, control = (arms[name][0] for name in ("pipeline", "native"))
    if (
        original["status"] != "failed"
        or control["status"] != "native_demand_model_diagnostic_collected"
        or control.get("diagnostic_control_only") is not True
        or control["model"].get("model_qualified") is not False
        or control["model"].get("pipeline_installed") is not False
        or original["source_requests"] != control["source_requests"]
        or original["native_engine"] != control["native_engine"]
        or original["native_tree"] != control["native_tree"]
        or original["fixture"]["device_tokens"] != control["fixture"]["device_tokens"]
        or original["fixture"]["host_gb"] != control["fixture"]["host_gb"]
    ):
        raise ValueError("native diagnostic does not match the failed numerical fixture")
    identities = []
    for label in LABELS:
        left, right = (arms[name][1][label] for name in ("pipeline", "native"))
        # Each server owns its own real session namespace.
        if {key: value for key, value in left.items() if key != "session_id"} != {
            key: value for key, value in right.items() if key != "session_id"
        }:
            raise ValueError("diagnostic changed actual model input or sampler: " + label)
        identities.append({
            "request_id": left["rid"], "prompt_tokens": len(left["input_ids"]),
            "output_budget": left["sampling_params"]["max_new_tokens"],
            "same_body_except_real_session_namespace": True,
        })
    changed = subprocess.check_output([
        "git", "diff", "--name-only",
        original["application"]["commit"], control["application"]["commit"],
    ], cwd=ROOT, text=True).splitlines()
    if any(path.startswith(("beliefkv/", "patches/")) for path in changed):
        raise ValueError("runtime or native patch changed across the numerical diagnosis")
    diagnostics = {name: output_diagnostics(arm[2]) for name, arm in arms.items()}
    violations = {
        name: [
            pair for pair, details in values.items()
            if not details["output_token_ids_equal"] or details["maximum_logprob_delta"] > .05
        ] for name, values in diagnostics.items()
    }
    for path, digest in seals.items():
        if sha256_file(Path(path)) != digest:
            raise ValueError("numerical inputs changed before publication")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Bounded unforced numerical diagnosis, not model or performance qualification.",
        "source_requests": original["source_requests"], "native_tree": original["native_tree"],
        "same_actual_request_bodies_except_sessions": identities,
        "application_commits": {name: arm[0]["application"]["commit"] for name, arm in arms.items()},
        "changed_application_paths": changed,
        "runtime_and_native_patch_unchanged": True, "output_diagnostics": diagnostics,
        "numerical_violations": violations,
        "native_without_pipeline_also_violates_numerical_gate": bool(violations["native"]),
        "all_seven_calls_completed_per_arm": True, "both_owned_servers_exited": True,
        "original_failure_preserved": True, "model_qualified": False, "performance_accepted": False,
        "inputs_sha256": seals, "operator_sha256": sha256_file(Path(__file__)),
        "limitations": [
            "One execution per condition; no isolated performance or JCT estimate.",
            "Same requests do not imply identical actual batch membership or floating-point kernels.",
            "After token divergence, sampled-token logprob deltas are not same-token logit errors.",
            "A numerical violation without the pipeline cannot alone exclude additional pipeline defects.",
            "Independent references currently build full continuation input by chunked prefill.",
        ],
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "numerical_violations": violations,
        "output_diagnostics": diagnostics,
        "model_qualified": False,
    }, indent=2))


if __name__ == "__main__":
    main()
