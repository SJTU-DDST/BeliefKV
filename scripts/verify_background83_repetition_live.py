"""Reuse the original live readers with the exact repeated control and owners."""

import argparse
from pathlib import Path
import sys
from types import SimpleNamespace

OPS = Path(__file__).resolve().parents[2] / "ops"
sys.path.insert(0, str(OPS))

import run_background83_frozen_replication_20261107 as repetition


def control_reference(document):
    aliases = {
        "actual_runtime": "actual_retained_runtime",
        "actual_agent": "actual_retained_agent",
    }
    if document.get("status") != "live_common_engine_and_original_receipts_verified":
        raise ValueError("require the actual repeated Full live verification")
    normalized = dict(document)
    for destination, source in aliases.items():
        if not isinstance(document.get(source), dict) or not document[source]:
            raise ValueError("repeated Full verification lacks actual policy evidence")
        if destination in document and document[destination] != document[source]:
            raise ValueError("conflicting live verification schema fields")
        normalized[destination] = document[source]
    if (
        normalized["actual_runtime"].get("internal_comparison_mode") != "full"
        or normalized["actual_agent"].get("workflow_combination_admission") is not False
        or normalized["actual_agent"].get("workflow_oracle_future_work_used_for_decisions") is not False
        or normalized["actual_agent"].get("workflow_oracle_remaining_work_ranking_enabled") is not False
    ):
        raise ValueError("the repeated control is not the retained nonranking Full")
    return normalized


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", choices=("oracle", "learned"), required=True)
    args = parser.parse_args()
    if args.method == "oracle":
        import verify_background83_oracle_live_20261107 as reader
    else:
        import verify_background83_learned_live_20261107 as reader

    plan = repetition.load(repetition.PLAN)
    repetition.campaign.verify_pins(plan["inputs_sha256"])
    reference_path = repetition.HERE / "live_full_verified.json"
    control_reference(repetition.load(reference_path))
    original_load, original_write = repetition.load, repetition.write

    def load(path):
        document = original_load(path)
        if Path(path).resolve() == reference_path.resolve():
            return control_reference(document)
        return document

    def write(path, document):
        original_write(path, {
            **document,
            "verification_scope": "same_frozen_repetition_with_its_own_nonranking_control",
            "repetition_method": args.method,
            "control_live_schema_aliases": {
                "actual_runtime": "actual_retained_runtime",
                "actual_agent": "actual_retained_agent",
            },
            "control_schema_aliases_preserve_actual_values_and_original_file": True,
            "reused_live_reader": str(Path(reader.__file__).resolve()),
            "inputs_sha256": {
                **document["inputs_sha256"],
                str(Path(__file__).resolve()): repetition.campaign.sha256_file(Path(__file__)),
            },
        })

    facade = SimpleNamespace(**vars(reader.campaign))
    facade.HERE, facade.PLAN = repetition.HERE, repetition.PLAN
    facade.registration = lambda method: Path(plan["stages"][method]["registration"])
    facade.load, facade.write = load, write
    reader.campaign = facade
    if args.method == "oracle":
        reader.load, reader.write = load, write
    reader.main()


if __name__ == "__main__":
    main()
