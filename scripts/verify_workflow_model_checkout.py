"""Compare complete causal prediction inputs across an existing frozen serving tree."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts.train_workflow_work import write


PROBE = r'''
import hashlib
import json
from pathlib import Path
import struct
import sys

settings = json.loads(sys.argv[1])
sys.path.insert(0, settings["checkout"])
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from beliefkv.predictor.workflow_work_distribution import support_status
model = WorkflowWorkModel.load(settings["model"], settings["model_sha256"])
cached, digest, rows, workflows = {}, hashlib.sha256(), 0, set()
for name in settings["datasets"]:
    dataset = json.loads(Path(name).read_bytes())
    for row in dataset["rows"]:
        text = row.get("task_text", row["text"])
        key = row["workflow"], text
        if key not in cached:
            cached[key] = model.task_encoding(text)
        projection, coverage = cached[key]
        if projection is None:
            raise ValueError("a complete compatibility input lacks task projection")
        predicted = model.predict(projection, row["features"])
        support = support_status(model, row["features"], coverage)
        digest.update(struct.pack("!dddd", *predicted, coverage))
        digest.update(support.encode() + b"\n")
        rows += 1
        workflows.add(row["workflow"])
print(json.dumps({"prediction_and_support_sha256": digest.hexdigest(),
                  "rows": rows, "workflows": len(workflows), "task_encodings": len(cached)}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, action="append", required=True)
    parser.add_argument("--frozen-checkout", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve earlier frozen-checkout compatibility results")
    files = [args.model, *args.dataset, Path(__file__)]
    inputs = {str(path.resolve()): sha256_file(path) for path in files}
    model_raw = json.loads(args.model.read_bytes())
    if any(model_raw.get(name) not in (None, False) for name in (
        "task_encoder", "total_work_prior", "task_text_encoding", "observed_delegation_inputs",
        "task_only_before_observed_work",
    )):
        raise ValueError("this compatibility declaration covers only the selected plain R5 MLP")
    frozen = git_source_state(args.frozen_checkout)
    if frozen["dirty"]:
        raise ValueError("the existing serving checkout is not frozen and clean")
    settings = {
        "model": str(args.model.resolve()), "model_sha256": inputs[str(args.model.resolve())],
        "datasets": [str(path.resolve()) for path in args.dataset],
    }
    probes = {}
    for label, checkout in (("current", ROOT), ("frozen", args.frozen_checkout.resolve())):
        result = subprocess.run(
            [sys.executable, "-c", PROBE, json.dumps({**settings, "checkout": str(checkout)})],
            cwd=checkout, text=True, capture_output=True, check=True,
        )
        probes[label] = json.loads(result.stdout)
    if probes["current"] != probes["frozen"]:
        raise ValueError("current and frozen prediction probabilities, support, or input coverage differ")
    modules = (
        "beliefkv/runtime/workflow_combination.py",
        "beliefkv/policy/workflow_combination.py",
        "beliefkv/runtime/deferred_free_admission.py",
        "beliefkv/runtime/reference_thunder_admission.py",
        "beliefkv/runtime/agent_working_set.py",
        "beliefkv/runtime/workflow_size_admission.py",
    )
    exact = {}
    for name in modules:
        current_path, frozen_path = ROOT / name, args.frozen_checkout / name
        if not current_path.is_file() or not frozen_path.is_file():
            raise ValueError("a required selection source is missing: " + name)
        exact[name] = {
            "current_sha256": sha256_file(current_path),
            "frozen_sha256": sha256_file(frozen_path),
        }
        if exact[name]["current_sha256"] != exact[name]["frozen_sha256"]:
            raise ValueError("the shared R5 selection implementation differs: " + name)
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("a compatibility input changed")
    if git_source_state(args.frozen_checkout) != frozen:
        raise ValueError("the existing serving tree changed during CPU verification")
    write(args.output, {
        "frozen_serving_source": frozen, "probes": probes,
        "candidate_model_sha256": inputs[str(args.model.resolve())],
        "exact_selection_source_modules": exact, "inputs_sha256": inputs,
        "all_exported_prediction_probabilities_and_support_match": True,
        "selected_model_requires_no_new_optional_prediction_features": True,
        "other_predictor_features_or_worker_timing_equivalence_claimed": False,
        "new_GPU_or_position_or_sampler_qualification": False,
        "GPU_action": False,
    })
    print(json.dumps({"commit": frozen["commit"], **probes["frozen"], "GPU_action": False}), flush=True)


if __name__ == "__main__":
    main()
