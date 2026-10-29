"""Prepare declared common arrivals and prefill profiles without scheduling GPU."""

from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.replay_arrivals import poisson_arrival_plan
from beliefkv.experiments.source_provenance import sha256_file

HERE = Path(__file__).resolve().parent
BASE = ROOT / "configs/migration/predictive_demand_prefill_pipeline_20261028.json"
REGISTRATION = ROOT.parent / "ops/demand_prefill_ack_retry_20261028_r1.registration.json"


def write(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def main():
    base = json.loads(BASE.read_bytes())
    registration = json.loads(REGISTRATION.read_bytes())
    for rate in (.2, .35, .5):
        path = HERE / f"poisson_{rate:g}_seed21.json"
        arrivals = poisson_arrival_plan(
            registration["workflow_ids"], rate_per_second=rate, seed=21,
        )
        write(path, arrivals)
        print(json.dumps({
            "arrivals": str(path), "rate": rate,
            "first_arrival_s": arrivals["arrival_schedule"][0]["offset_seconds"],
            "last_arrival_s": arrivals["arrival_schedule"][-1]["offset_seconds"],
            "sha256": sha256_file(path),
        }))
    for name, chunk, mixed in (
        ("chunk2048", "2048", "0"), ("chunk1024", "1024", "0"),
        ("chunk2048_mixed", "2048", "1"),
    ):
        candidate = deepcopy(base)
        candidate["status"] = "prepared_cpu_qualification_and_fresh_model_checks_required"
        candidate["common_environment"].update(
            CHUNKED_PREFILL_SIZE=chunk, ENABLE_MIXED_CHUNK=mixed,
        )
        candidate["comparison_contract"].update(
            native_demand_pipeline="selected_initial_chunk_or_unchunked_batch;no_existing_request_row",
            all_mode_output_accounting="observed_output_ids_delta_all_forward_modes_v1",
            mixed_worker_interval="shared_whole_forward;not_additive_to_prefill",
        )
        candidate["native_reference"] = {
            "repeat_full_native_replay": False,
            "burst_source_jct_is_not_poisson_control": True,
        }
        candidate["gpu_execution"] = {"registered": False}
        candidate["workflow_arrivals"] = {
            "path": str(HERE / "poisson_0.35_seed21.json"),
            "sha256": sha256_file(HERE / "poisson_0.35_seed21.json"),
        }
        candidate["parent_configuration"] = {
            "path": str(BASE), "sha256": sha256_file(BASE),
        }
        write(HERE / (name + ".json"), candidate)


if __name__ == "__main__":
    main()
