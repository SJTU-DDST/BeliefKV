import json
from pathlib import Path
import sys

import pytest

from beliefkv.experiments.replay_arrivals import poisson_arrival_plan
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import declare_cohort, summarize_cohort
from scripts import compare_reported_workflow_cohorts as comparison
from scripts.audit_sealed_wait_prefetch import SealedRecords


def write_json(path, data):
    path.write_text(json.dumps(data) + "\n")


def make_arm(root, label, *, stop=False, censored=(), origin=0., window_end=4., measured=18):
    ids = [f"w{i:02d}" for i in range(22)]
    arrivals = poisson_arrival_plan(ids, rate_per_second=.5, seed=31, initial_workflows=2)
    cohort = declare_cohort(
        arrivals, warmup=2, measured=measured, throughput_start_s=1., throughput_end_s=window_end,
        stop_after_measured=stop,
    )
    directory = root / label
    arm = directory / "raw/full"
    for subdir in ("replay", "server"):
        (arm / subdir).mkdir(parents=True)
    registration, report_path, declaration, arrivals_path = (
        directory / name for name in ("replay.registration.json", "cohort.json", "declared.json", "arrivals.json")
    )
    write_json(declaration, cohort)
    write_json(arrivals_path, arrivals)
    write_json(registration, {
        "workflow_arrivals_path": str(arrivals_path), "workflow_arrivals": arrivals,
        "workflow_ids": ids, "run_root": str(arm.parent), "order": ["full"],
        "token_source_sha256": "same-source", "source_arm": str(directory),
    })

    events, ends = [], {}
    for key, row in zip(ids, arrivals["arrival_schedule"]):
        start = origin + row["offset_seconds"] * 1000
        events.append({"kind": "workflow_start", "workflow_id": key, "ts_ms": start})
        if key not in censored:
            duration = {"w00": 5., "w01": 70.}.get(key, 40.)
            ends[key] = start + duration * 1000
            events.append({"kind": "workflow_end", "workflow_id": key, "ts_ms": ends[key]})

    batches = [
        (.5, "prefill", {"w00": 7}),
        (1., "prefill", {"w00": 1}),
        (2., "prefill", {"w02": 2}),
        (3., "decode", {"w01": 3, "w02": 4}),
        (window_end, "decode", {"w02": 8}),
    ]
    if window_end > 33.9:
        batches.append((33.9, "prefill", {"w20": 5}))
    samples = [{
        "event": "gpu_service_sample", "forward_iter": index, "phase": phase,
        "launch_monotonic_ms": origin + when * 1000 - .5,
        "complete_monotonic_ms": origin + when * 1000,
        "monotonic_clock_domain": "test-clock", "batch_size": len(tokens),
        "request_samples": [{
            "workflow_id": key, "token_delta": 2048, "output_token_delta": count,
        } for key, count in tokens.items()],
    } for index, (when, phase, tokens) in enumerate(batches)]
    reader = SealedRecords()
    report = summarize_cohort(cohort, arrivals, events, samples, bin_seconds=1.25)
    receipts = [{
        "workflow_id": key, "output_tokens": count,
    } for key, count in report["all_service_output_tokens_by_workflow"].items() if key not in censored]
    for name, rows in zip(comparison.RAW_FILES, (events, samples, receipts)):
        path = arm / name
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        list(reader(path))
    report.update(
        input_jsonl_prefixes=reader.prefixes,
        inputs_sha256={str(path): sha256_file(path) for path in (arrivals_path, declaration)},
        service_output_matches_complete_receipts=not stop,
        exact_receipt_requests=len(receipts),
    )
    if stop:
        expected_path = directory / "expected.json"
        write_json(expected_path, {
            "warmup-request": {"llm_submit": [["w00"]], "llm_result": [None, 8]},
        })
        report["inputs_sha256"][str(expected_path)] = sha256_file(expected_path)
        certificate_path = directory / "completion.json"
        write_json(certificate_path, {
            "cohort_completion_monotonic_ms": max(ends[key] for key in cohort["measured_workflow_ids"]),
            "stop_observed_monotonic_ms": max(ends.values()) + 1000.,
            "measured_JCT_ms": report["measured_workflow_JCT_ms"],
            "input_jsonl_prefixes": {str(arm / comparison.RAW_FILES[2]):
                                   reader.prefixes[str(arm / comparison.RAW_FILES[2])]},
        })
        report.update(
            measured_service_output_matches_complete_receipts=True,
            measured_exact_receipt_requests=measured,
        )
    else:
        # The oldest complete auditor had no separate all_measured_completed flag.
        report.pop("all_measured_completed")
    write_json(report_path, report)
    return label, registration, report_path


def write_native_host_usage(arm_spec, *caps):
    _, registration_path, _ = arm_spec
    registration = comparison.load(registration_path)
    arm = Path(registration["run_root"]) / registration["order"][0]
    path = arm / comparison.HOST_POOL_TELEMETRY
    rows = [{"event": "host_pool_transfer_ack", "ts_ms": 1795325894572.1042}]
    for index, cap in enumerate(caps):
        rows.append({
            "event": "host_pool_usage",
            "ts_ms": 1795325894572.1091 + index * 1000,
            "sample_semantics": "scheduler_safe_point_sampled_high_water",
            "write_back_batch": {
                "max_nodes": cap,
                "counts": {"attempts": index, "batches": index if cap == 32 else 0},
            },
        })
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return arm, path


def test_cli_accepts_legacy_complete_reports_and_reconciles_fixed_window(tmp_path, monkeypatch):
    first = make_arm(tmp_path, "reference", origin=1000., window_end=34.)
    second = make_arm(tmp_path, "trial", origin=2000., window_end=34.)
    output = tmp_path / "comparison.json"
    monkeypatch.setattr(sys, "argv", [
        "compare_reported_workflow_cohorts.py", "--arm", *map(str, first),
        "--arm", *map(str, second), "--output", str(output),
    ])
    comparison.main()
    result = comparison.load(output)
    assert result["write_back_cap_comparison"] == {"status": "unknown", "matched_max_nodes": None}
    fixed, union = (result["groups"][key] for key in ("fixed_18", "fixed_measured_plus_warmup"))
    assert fixed["member_count"] == fixed["completed_in_all_arms_count"] == 18
    assert union["member_count"] == union["completed_in_all_arms_count"] == 20
    assert union["paired_completion_counts"] == [{
        "arms": ["reference", "trial"], "both_completed": 20,
        "first_only_completed": 0, "second_only_completed": 0, "neither_completed": 0,
    }]
    assert fixed["arms"]["reference"]["natural_complete_JCT_s"] == pytest.approx({
        "mean": 40., "p50": 40., "p95": 40.,
    })
    assert union["arms"]["trial"]["natural_complete_JCT_s"] == pytest.approx({
        "mean": 39.75, "p50": 40., "p95": 41.5,
    })
    assert union["restriction"] is None
    assert result["throughput_window"]["offset_seconds"] == [1., 34.]
    assert result["throughput_window"]["timestamp_field"] == "complete_monotonic_ms"
    for label in ("reference", "trial"):
        assert result["arms"][label]["write_back_batch"]["max_nodes"] is None
        assert fixed["arms"][label]["window_output_tokens"] == 6
        assert union["arms"][label]["window_output_tokens"] == 10
        assert union["arms"][label]["window_output_tokens_per_second"] == pytest.approx(10 / 33)
        assert result["arms"][label]["partition_window_output"]["background"]["output_tokens"] == 5
        assert result["arms"][label]["system_window_output"] == {
            "output_tokens": 15, "output_tokens_per_second": pytest.approx(15 / 33),
        }


def test_cli_rejects_native_32_vs_1_except_for_explicit_ablation(tmp_path, monkeypatch, capsys):
    full = make_arm(tmp_path, "full")
    thunder = make_arm(tmp_path, "thunderagent")
    write_native_host_usage(full, 32, 32)
    write_native_host_usage(thunder, 1, 1)
    output = tmp_path / "comparison.json"
    argv = [
        "compare_reported_workflow_cohorts.py", "--arm", *map(str, full),
        "--arm", *map(str, thunder), "--output", str(output),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as error:
        comparison.main()
    assert error.value.code == 2
    assert "different effective native write-back caps (max_nodes): full=32, thunderagent=1" in (
        capsys.readouterr().err
    )
    assert not output.exists()

    monkeypatch.setattr(sys, "argv", [*argv, "--write-back-cap-ablation"])
    comparison.main()
    result = comparison.load(output)
    assert result["write_back_cap_comparison"] == {"status": "ablation", "matched_max_nodes": None}
    assert result["arms"]["full"]["write_back_batch"]["max_nodes"] == 32
    assert result["arms"]["thunderagent"]["write_back_batch"]["max_nodes"] == 1


def test_strict_cli_records_equal_effective_caps_and_evidence(tmp_path, monkeypatch):
    full = make_arm(tmp_path, "full")
    thunder = make_arm(tmp_path, "thunderagent")
    _, full_path = write_native_host_usage(full, 32, 32)
    _, thunder_path = write_native_host_usage(thunder, 32)
    output = tmp_path / "comparison.json"
    monkeypatch.setattr(sys, "argv", [
        "compare_reported_workflow_cohorts.py", "--require-verified-write-back-cap",
        "--arm", *map(str, full), "--arm", *map(str, thunder), "--output", str(output),
    ])
    comparison.main()
    result = comparison.load(output)
    assert result["write_back_cap_comparison"] == {"status": "matched", "matched_max_nodes": 32}
    for spec, path in ((full, full_path), (thunder, thunder_path)):
        assert result["arms"][spec[0]]["write_back_batch"] == {
            "max_nodes": 32,
            "telemetry_path": str(path),
            "telemetry_seal": {
                "complete_prefix_bytes": path.stat().st_size,
                "first_read_limit_bytes": path.stat().st_size,
                "complete_prefix_sha256": sha256_file(path),
            },
        }
    with pytest.raises(ValueError, match="ablation requires different verified effective caps"):
        comparison.compare_arms([full, thunder], write_back_cap_ablation=True)


def test_requested_cap_without_native_observation_is_unknown(tmp_path):
    full = make_arm(tmp_path, "full")
    thunder = make_arm(tmp_path, "thunderagent")
    write_native_host_usage(full, 32)
    write_native_host_usage(thunder, None)
    registration = comparison.load(thunder[1])
    registration["reference_environment"] = {"SGLANG_HICACHE_WRITE_BACK_BATCH_NODES": "32"}
    write_json(thunder[1], registration)

    result = comparison.compare_arms([full, thunder])
    assert result["write_back_cap_comparison"] == {"status": "unknown", "matched_max_nodes": None}
    assert result["arms"]["thunderagent"]["write_back_batch"]["max_nodes"] is None
    for option in ("require_verified_write_back_cap", "write_back_cap_ablation"):
        with pytest.raises(ValueError, match="native write-back cap is unknown for: thunderagent"):
            comparison.compare_arms([full, thunder], **{option: True})


@pytest.mark.parametrize("caps, message", [
    ((32, 1), "max_nodes changed from 32 to 1"),
    ((True,), "invalid native write_back_batch.max_nodes"),
    (("32",), "invalid native write_back_batch.max_nodes"),
    ((0,), "invalid native write_back_batch.max_nodes"),
])
def test_rejects_inconsistent_or_invalid_native_caps(tmp_path, caps, message):
    arm, _ = write_native_host_usage(make_arm(tmp_path, "full"), *caps)
    with pytest.raises(ValueError, match=message):
        comparison.read_write_back_batch(arm)


def test_write_back_batch_honors_an_existing_telemetry_seal(tmp_path):
    arm, path = write_native_host_usage(make_arm(tmp_path, "full"), 32)
    batch = comparison.read_write_back_batch(arm)
    sealed_prefixes = {str(path): batch["telemetry_seal"]}
    assert comparison.read_write_back_batch(arm, sealed_prefixes=sealed_prefixes) == batch

    path.write_text(path.read_text().replace('"max_nodes": 32', '"max_nodes": 16'))
    with pytest.raises(ValueError, match="changed sealed prefix"):
        comparison.read_write_back_batch(arm, sealed_prefixes=sealed_prefixes)


def test_reports_the_declared_measured_group_size(tmp_path):
    first, second = (make_arm(tmp_path, label, measured=8) for label in ("first", "second"))
    groups = comparison.compare_arms([first, second])["groups"]
    assert groups["fixed_8"]["member_count"] == 8
    assert groups["fixed_measured_plus_warmup"]["member_count"] == 10


def test_censored_union_uses_fixed_18_stop_and_one_followup_for_all_members(tmp_path, monkeypatch):
    complete = make_arm(tmp_path, "complete")
    stopped = make_arm(tmp_path, "stopped", stop=True, censored=("w01",), origin=2000.)
    plan = tmp_path / "plan.json"
    write_json(plan, {"cohort": "stopped/declared.json"})
    verified = []

    def verify(registration, declaration):
        verified.append((registration, declaration))
        certificate = stopped[1].with_name("completion.json")
        return {
            "certificate": str(certificate), "certificate_sha256": sha256_file(certificate),
            "measured_requests": 18,
            "completed_workflow_ids": [f"w{i:02d}" for i in range(22) if i != 1],
            "completed_receipt_requests": ["warmup-request"],
        }

    monkeypatch.setattr(comparison, "verify_measured_execution", verify)
    result = comparison.compare_arms([complete, stopped], plan)
    assert verified == [(stopped[1], stopped[1].with_name("declared.json"))]
    fixed = result["groups"]["fixed_18"]
    union = result["groups"]["fixed_measured_plus_warmup"]
    assert fixed["completed_in_all_arms_count"] == 18
    assert fixed["restriction"] is None
    assert union["completed_in_all_arms_count"] == 19
    assert union["paired_completion_counts"][0]["first_only_completed"] == 1
    assert union["arms"]["stopped"]["right_censored_workflow_ids"] == ["w01"]
    assert union["arms"]["stopped"]["natural_complete_JCT_s"] is None
    assert union["restriction"]["common_followup_s"] == pytest.approx(40.)
    assert union["restriction"]["common_wall_cutoff_offset_s"] == pytest.approx(68.1903711814)
    for label in ("complete", "stopped"):
        assert union["arms"][label]["restricted_mean_JCT_s"] == pytest.approx(38.25)
        assert union["arms"][label]["window_output_tokens"] == 10


def test_rejects_warmup_completion_without_its_exact_receipt(tmp_path, monkeypatch):
    complete = make_arm(tmp_path, "complete")
    stopped = make_arm(tmp_path, "stopped", stop=True, censored=("w01",))
    certificate = stopped[1].with_name("completion.json")
    monkeypatch.setattr(comparison, "verify_measured_execution", lambda *_: {
        "certificate": str(certificate), "certificate_sha256": sha256_file(certificate),
        "measured_requests": 18,
        "completed_workflow_ids": [f"w{i:02d}" for i in range(22) if i != 1],
        "completed_receipt_requests": [],
    })
    with pytest.raises(ValueError, match="completed warmup workflow lacks an exact receipt"):
        comparison.compare_arms([complete, stopped])


@pytest.mark.parametrize("unarrived", [False, True])
def test_restricted_mean_requires_positive_followup_for_every_member(unarrived):
    observations = {
        "complete": {
            "natural_jct_s": {"w0": 2., "w1": 4.}, "arrival_offsets_s": {"w0": 0., "w1": 2.},
            "observation_end_offset_s": 6., "partition_tokens": {"measured": 10},
        },
        "censored": {
            "natural_jct_s": {"w0": 2.}, "arrival_offsets_s": {"w0": 0.} if unarrived else {"w0": 0., "w1": 2.},
            "observation_end_offset_s": 2., "partition_tokens": {"measured": 8},
        },
    }
    result = comparison.report_group(observations, ["w0", "w1"], ["measured"], 1.)
    assert result["restriction"]["common_followup_s"] is None
    assert result["arms"]["censored"]["unarrived_workflow_ids"] == (["w1"] if unarrived else [])
    assert all(arm["restricted_mean_JCT_s"] is None for arm in result["arms"].values())


@pytest.mark.parametrize("change, error", [
    ("window", "identical frozen"),
    ("overlapping_members", "identical frozen"),
    ("system_rate", "differs from the sealed"),
    ("bins", "do not reconcile"),
    ("raw_seal", "changed sealed prefix"),
    ("tool_timing", "same tool timing overlay"),
])
def test_rejects_nonmatching_or_changed_sealed_inputs(tmp_path, change, error):
    first, second = (make_arm(tmp_path, label) for label in ("first", "second"))
    report = comparison.load(second[2])
    if change == "window":
        report["cohort"]["throughput_window_offset_seconds"][0] += .5
    elif change == "overlapping_members":
        cohort = report["cohort"]
        cohort["warmup_workflow_ids"][0] = cohort["measured_workflow_ids"][0]
    elif change == "system_rate":
        report["common_window_output_tokens_per_second"] += 1
    elif change == "bins":
        report["common_window_throughput_bins"][0]["output_tokens"] += 1
    elif change == "tool_timing":
        registration = comparison.load(second[1])
        registration["tool_timing_overlay"] = {"sha256": "different-overlay"}
        write_json(second[1], registration)
    else:
        path = Path(next(name for name in report["input_jsonl_prefixes"] if name.endswith("runtime_audit.jsonl")))
        path.write_text(path.read_text().replace("2048", "2049"))
    write_json(second[2], report)
    with pytest.raises(ValueError, match=error):
        comparison.compare_arms([first, second])
