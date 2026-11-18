"""Retain unchanged service tests and bind the tested preparation and queue changes."""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.qualify_combined_cpu_candidate import checks
from scripts.run_materialization_campaign import matching_preparations
from scripts.train_workflow_work import write


def bind(inherited_path, protocol_path, queue_report, output):
    if output.exists() or subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT):
        raise ValueError("preserve previous qualifications and freeze a clean candidate")
    if (
        os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19 or os.sched_getaffinity(0) != {0}
    ):
        raise ValueError("use CPU0, nice19 and hidden CUDA")
    previous = json.loads(inherited_path.read_bytes())
    protocol = json.loads(protocol_path.read_bytes())
    if not previous["qualification_passed"] or not protocol["protocol_qualification_passed"]:
        raise ValueError("require complete inherited service and latest protocol evidence")
    manifest = Path(previous["source_manifest"]["path"])
    if sha256_file(manifest) != previous["source_manifest"]["sha256"]:
        raise ValueError("the inherited source manifest changed")
    before = json.loads(manifest.read_bytes())
    changed = {name for name, digest in before.items() if sha256_file(ROOT / name) != digest}
    if changed != {
        "scripts/prepare_subagent_materialization.py",
        "docs/design/subagent_materialization_pressure_20261116_zh.md",
    }:
        raise ValueError("service changed beyond the tested preparation repair: " + repr(changed))
    for name, digest in protocol["inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("latest production protocol proof changed")
    prepared = Path(protocol["prepared"])
    matching_preparations({
        label: json.loads((prepared / (label + ".json")).read_bytes())
        for label in ("control", "pressure", "thunderagent_acting_decay", "native_matched")
    })
    report = Path(previous["report"]["path"])
    if sha256_file(report) != previous["report"]["sha256"]:
        raise ValueError("inherited CPU report changed")
    reports = [
        report,
        prepared.parent / "native_cohort_contract_after_r1.xml",
        prepared.parent / "native_cohort_stop_related_r1.xml",
        queue_report,
    ]
    cases, provenance = {}, {}
    for path in reports:
        statuses, totals = checks(path)
        if not statuses or any(value != "passed" for value in statuses.values()) or any(
            totals[name] for name in ("failures", "errors", "skipped")
        ):
            raise ValueError("CPU report has failed or skipped work: " + str(path))
        for row in ET.parse(path).getroot().iter("testcase"):
            identity = row.get("classname"), row.get("name")
            cases[identity] = deepcopy(row)
            provenance[identity] = str(path.resolve())
    prior, _ = checks(report)
    if not prior.keys() <= cases.keys():
        raise ValueError("inherited test identities were lost")
    additions = {
        "scripts/run_materialization_campaign.py", "scripts/review_materialization_campaign.py",
        "scripts/supervise_materialization_stage.py", "scripts/bind_materialization_campaign_cpu.py",
        "tests/test_materialization_campaign.py", "scripts/qualify_materialization_protocol.py",
        "scripts/analyze_materialization_ready_supply.py", "scripts/analyze_materialization_workload.py",
        "tests/test_materialization_workload_screen.py",
    }
    source_paths = set(before) | {name for name in additions if (ROOT / name).is_file()}
    sources = {name: sha256_file(ROOT / name) for name in sorted(source_paths)}
    output.mkdir(parents=True)
    write(output / "sources.json", sources)
    tree = ET.Element("testsuites")
    suite = ET.SubElement(tree, "testsuite", {
        "name": "inherited_service_and_targeted_protocol_and_queue",
        "tests": str(len(cases)), "failures": "0", "errors": "0", "skipped": "0",
    })
    for identity in sorted(cases):
        suite.append(cases[identity])
    ET.ElementTree(tree).write(output / "bound_cpu.xml", encoding="utf-8", xml_declaration=True)
    inputs = {
        str(path.resolve()): sha256_file(path)
        for path in (inherited_path, protocol_path, manifest, Path(__file__), *reports)
    }
    for name, digest in sources.items():
        if sha256_file(ROOT / name) != digest:
            raise ValueError("source changed while binding CPU evidence")
    result = {
        "qualification_passed": True,
        "candidate_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        "source_manifest": {
            "path": str(output / "sources.json"), "sha256": sha256_file(output / "sources.json"),
        },
        "report": {
            "path": str(output / "bound_cpu.xml"), "sha256": sha256_file(output / "bound_cpu.xml"),
        },
        "unique_test_identities": len(cases), "retained_prior_test_identities": len(prior),
        "new_test_identities": len(cases.keys() - prior.keys()),
        "only_preparation_and_documentation_changed_in_inherited_sources": sorted(changed),
        "actual_runtime_policy_control_sources_unchanged": True,
        "assembled_existing_passing_reports_not_a_repeated_full_suite": True,
        "inherited_executed_subtests": 23,
        "reports_sha256": {str(path.resolve()): sha256_file(path) for path in reports},
        "latest_actual_report_for_identity": [
            {"classname": key[0], "name": key[1], "report": provenance[key]} for key in sorted(cases)
        ],
        "inputs_sha256": inputs, "GPU_action": False, "performance_accepted": False,
    }
    write(output / "evidence.json", result)
    print(json.dumps({
        "qualification_passed": True, "retained": len(prior), "unique": len(cases),
        "repeated_full_suite": False, "GPU_action": False,
    }))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("inherited", "protocol", "queue-report", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    bind(args.inherited.resolve(), args.protocol.resolve(), args.queue_report.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
