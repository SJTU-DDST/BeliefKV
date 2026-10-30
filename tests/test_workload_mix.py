import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import compile_causal_replay
from beliefkv.experiments.replay_arrivals import validate_arrival_plan
from beliefkv.experiments.tool_timing_overlay import fingerprint
from beliefkv.experiments.workload_mix import (
    burst_arrivals, calibrated_arrivals, capacity_competition, context_crossing,
    population_fingerprint, protocol, quantile, shuffled_workflows, stratify,
)
from tests.test_causal_replay import mixed_trace
from scripts.prepare_workflow_mix_candidates import mixed_scope_manifest


def rows():
    return [
        {
            "workflow_id": str(index), "project": "project" + str(index),
            "protocol_sha256": "same", "eligible": True,
            "generated_tokens": (index + 1) ** 2, "necessary_input_tokens": (index + 1) ** 2,
            "requests": index + 1, "initial_prompt_tokens": 10000 - index * 100,
            "max_context_tokens": 20000 - index * 100,
            "source_capture": "/source", "event_sha256": fingerprint(["event", index]),
            "token_source_sha256": "c" * 64,
        }
        for index in range(18)
    ]


class WorkloadMixTest(unittest.TestCase):
    def test_protocol_excludes_arrival_and_pool_but_includes_budgets(self):
        manifest = {"config": {"model": "same", "concurrency": 18, "pool_tokens": 123}}
        first = protocol(manifest)
        manifest["config"].update(concurrency=6, pool_tokens=999)
        self.assertEqual(first, protocol(manifest))
        manifest["config"]["child_work_budget_target_calls"] = 8
        self.assertNotEqual(first, protocol(manifest))

    def test_strata_use_whole_workflow_three_measure_rank_and_ignore_tools(self):
        population = rows()
        population[0]["tool_total_s"] = 1e12
        result = stratify(list(reversed(population)))
        self.assertEqual([row["workflow_id"] for row in result], [str(i) for i in range(18)])
        self.assertEqual([row["size_tier"] for row in result], ["short"] * 6 + ["medium"] * 6 + ["long"] * 6)

    def test_same_project_or_protocol_or_bad_return_rejected(self):
        for field, value in (("project", "project0"), ("protocol_sha256", "other"), ("eligible", False)):
            population = rows()
            population[1][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                stratify(population)

    def test_context_crossing_identifies_short_large_context(self):
        result = context_crossing(stratify(rows()))
        self.assertEqual(result["short_has_larger_initial_prompt_pairs"], 36)
        self.assertEqual(result["short_has_larger_maximum_context_pairs"], 36)

    def test_seed_permutation_preserves_population(self):
        self.assertEqual(shuffled_workflows(rows(), seed=21), shuffled_workflows(list(reversed(rows())), seed=21))
        self.assertNotEqual(shuffled_workflows(rows(), seed=21), shuffled_workflows(rows(), seed=22))
        self.assertEqual(len({row["project"] for row in shuffled_workflows(rows(), seed=21)}), 18)

    def test_burst_has_no_arrival_size_correlation(self):
        plan = burst_arrivals([row["workflow_id"] for row in shuffled_workflows(rows(), seed=21)])
        self.assertEqual({row["offset_seconds"] for row in plan["arrival_schedule"]}, {0.})
        self.assertEqual(validate_arrival_plan(plan, [row["workflow_id"] for row in rows()]), plan)

    def test_calibration_requires_complete_same_population_burst(self):
        population = rows()
        control = {
            "workflow_ids": [row["workflow_id"] for row in population], "completed_workflows": 18,
            "makespan_seconds": 1800., "all_work_complete": True,
            "source_population_sha256": population_fingerprint(population),
            "registration_sha256": "a" * 64, "mode": "simultaneous",
        }
        value = calibrated_arrivals(
            population, completed_control=control, permutation_seed=21, arrival_seed=22,
        )
        self.assertAlmostEqual(value["arrivals"]["rate_per_second"], .009)
        self.assertEqual([row["offset_seconds"] for row in value["arrivals"]["arrival_schedule"][:5]], [0.] * 5)
        self.assertEqual(
            validate_arrival_plan(value["arrivals"], sorted(control["workflow_ids"])),
            value["arrivals"],
        )
        for field, bad in (("all_work_complete", False), ("completed_workflows", 17), ("mode", "poisson")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                calibrated_arrivals(
                    population, completed_control={**control, field: bad}, permutation_seed=21, arrival_seed=22,
                )
        modified = rows()
        modified[0]["event_sha256"] = "d" * 64
        with self.assertRaises(ValueError):
            calibrated_arrivals(
                modified, completed_control=control, permutation_seed=21, arrival_seed=22,
            )

    def test_competition_individual_fit_not_jointly_ready_same_category(self):
        candidates = [
            {"owner": str(i), "workflow_id": str(i), "category": 1, "tokens": 60,
             "sequence": i, "ready_paused": True, "size_tier": tier}
            for i, tier in enumerate(("short", "long"))
        ]
        result = capacity_competition(candidates, capacity=100)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["aggregate_tokens"], 120)
        self.assertEqual(capacity_competition(candidates, capacity=120), [])
        self.assertEqual(capacity_competition(candidates, capacity=50), [])
        candidates[1]["ready_paused"] = False
        self.assertEqual(capacity_competition(candidates, capacity=100), [])
        candidates[1].update(ready_paused=True, category=2)
        self.assertEqual(capacity_competition(candidates, capacity=100), [])

    def test_higher_categories_consume_reference_capacity_and_buffer(self):
        candidates = [
            {"owner": str(i), "workflow_id": str(i), "category": category, "tokens": tokens,
             "sequence": i, "ready_paused": True, "size_tier": tier}
            for i, (category, tokens, tier) in enumerate(((0, 50, "medium"), (1, 30, "short"), (1, 30, "long")))
        ]
        result = capacity_competition(candidates, capacity=110, buffer_tokens=1)
        self.assertEqual(result[0]["remaining_tokens"], 59)
        self.assertEqual(result[0]["category"], 1)
        candidates[2]["workflow_id"] = candidates[1]["workflow_id"]
        self.assertEqual(capacity_competition(candidates, capacity=110, buffer_tokens=1), [])

    def test_quantiles_do_not_turn_max_min_into_p90_p10(self):
        self.assertEqual(quantile([1, 2, 3, 100], .5), 2.5)
        self.assertLess(quantile([1, 2, 3, 100], .9) / quantile([1, 2, 3, 100], .1), 100)


if __name__ == "__main__":
    unittest.main()


def test_inventory_binds_join_parent_from_wait_and_preserves_parallel_paths(tmp_path):
    from beliefkv.experiments.workload_mix import inventory_capture

    trace = mixed_trace()
    plan = compile_causal_replay(trace.ordered(), trace.native)
    client = tmp_path / "client_1"
    directory = client / "workflows/task"
    directory.mkdir(parents=True)
    (client / "manifest.json").write_text(json.dumps({"config": {"model": "test"}}))
    (directory / "trajectory.json").write_text(json.dumps([{
        "type": "human", "content": "Project: original-project\nTask requirements:\nInvestigate.",
    }]))
    (directory / "result.json").write_text(json.dumps({"natural_terminal": True}))
    (directory / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event.to_dict()) + "\n" for event in trace.ordered()),
    )
    (tmp_path / "server").mkdir()
    (tmp_path / "server/request_tokens.jsonl").write_text("original bound token stand-in\n")

    class Corpus:
        evidence = {"fixture_only": True, "token_source_sha256": "c" * 64}
        def __init__(self, *_args):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def get(self, _rid):
            return SimpleNamespace(prompt_tokens=range(10), output_tokens=range(4))

    with patch("beliefkv.experiments.workload_mix.CapturedTokenCorpus", Corpus):
        result = inventory_capture(
            tmp_path, seconds_per_model_unit=.02, plan_loader=lambda _path: (plan, {}, {}),
        )
    row = result["workflows"][0]
    assert row["eligible"] is True
    assert row["joins"] == 2
    assert row["root_join_rounds"] == 1
    assert row["nested_joins"] == 1
    assert row["completed_parallel_root_joins"] == 1
    assert row["tool_total_s"] == .065
    assert row["critical_tool_only_s"] == .055
    assert row["requests"] == 8
    assert row["generated_tokens"] == 32
    assert row["project"] == "original-project"


def test_original_scope_candidates_cross_context_and_never_expose_reference_answers():
    base = {"workloads": [], "candidates": []}
    scenarios = {}
    for index in range(18):
        project = f"p{index:02}"
        base["workloads"].append({
            "repo": "locobench/" + project, "instance_id": "previous-" + project,
            "frozen_repository_context": {"tokens": 1000 + index * 100},
            "dataset_provenance": {}, "source_repo": "/original/" + project,
            "base_commit": "unchanged", "sandbox_environment": {"PYTHONPATH": "same"},
        })
        paths = []
        for category in (
            "bug_investigation", "feature_implementation", "cross_file_refactoring",
            "multi_session_development",
        ):
            path = f"data/{project}_{category}_01.json"
            paths.append(path)
            scenarios[path] = json.dumps({
                "id": project + category, "task_category": category, "difficulty": "easy",
                "title": "Original task", "description": "Do the original work.",
                "task_prompt": "Investigate the repository.", "ground_truth": "PRIVATE ANSWER",
                "expected_approach": "PRIVATE APPROACH", "evaluation_criteria": "PRIVATE CHECK",
            }).encode()
        base["candidates"].append({"project_id": project, "scenario_paths": paths})
    manifest, declaration, _references = mixed_scope_manifest(
        base, SimpleNamespace(read=scenarios.__getitem__),
    )
    assert len({row["repo"] for row in manifest["workloads"]}) == 18
    assert declaration["scope_counts"] == {"investigation": 6, "implementation": 6, "multi_stage": 6}
    assert declaration["observed_size_tiers"] is None
    assert declaration["online_size_hints_injected"] is False
    assert declaration["post_capture_acceptance"]["p80_p20_diagnostic_minimum"] == 3
    assert declaration["post_capture_acceptance"]["p80_p20_stronger_diagnostic"] == 4
    assert declaration["post_capture_acceptance"]["p90_p10_is_reference_only"]
    assignment = declaration["assignments"]
    for band in range(6):
        assert {row["scope_hypothesis"] for row in assignment if row["context_band"] == band} == {
            "investigation", "implementation", "multi_stage",
        }
    for new in manifest["workloads"]:
        old = next(row for row in base["workloads"] if row["repo"] == new["repo"])
        assert "PRIVATE" not in new["problem_statement"]
        for field in ("source_repo", "base_commit", "sandbox_environment", "frozen_repository_context"):
            assert new[field] == old[field]
        assert "size_tier" not in new and "scope_hypothesis" not in new


def test_scope_registration_uses_a_unique_native_capture_label(monkeypatch):
    from experiments.reports.workflow_mix_20261029 import register_capture as scope
    import sys

    command = []
    monkeypatch.setattr(sys, "argv", ["register", "--cpu-evidence", "/bound/evidence.json"])
    monkeypatch.setattr(scope.register_capture, "main", lambda: command.extend(sys.argv))
    scope.main()
    run = command[command.index("--run-root") + 1]
    assert run.rsplit("_", 1)[-1] == "scope18"
    assert command[command.index("--port") + 1] == "18903"
