import random

import pytest

from beliefkv.runtime.workflow_work_oracle import (
    COST_KIND, WorkflowWorkOracle, WorkflowWorkOracleArtifact,
    cache_remaining_paths, remaining_model_seconds, scan_remaining_paths,
)
from scripts import run_causal_comparison as comparison
from tests.test_workflow_work_oracle import node, oracle_file, source, configured


def calibrated():
    raw = source()
    raw.update(
        kind=COST_KIND, model_prefill_seconds_per_token=.00008,
        model_prefill_progress="first_observed_output",
    )
    return raw


def test_legacy_cost_keeps_the_exact_original_float_expression():
    item = {"input_increment": 701, "output_tokens": 37}
    for generated in (0, 1, 19, 37):
        assert remaining_model_seconds(item, generated, .020962694137437794) == (
            (701 + 100 * (37 - generated)) / 100. * .020962694137437794
        )


def test_cost_components_do_not_rewrite_intrinsic_work_or_source_input_counts(tmp_path):
    raw = calibrated()
    legacy = source()
    oracle = WorkflowWorkOracle(*oracle_file(tmp_path, raw))
    first = oracle.prediction("wf")
    assert first.remaining_work_units == legacy["workflows"]["wf"]["total_work_units"]
    assert first.remaining_work == 47.
    assert first.remaining_path_s == pytest.approx(700 * .00008 + 40 * .02)
    assert oracle.artifact.requests == legacy["requests"]
    oracle.submit("wf", "started", "one", "child", "ctx", range(500))
    oracle.submit("wf", "generated", "one", 1)
    after = oracle.prediction("wf")
    assert after.remaining_work == 46.
    assert after.remaining_path_s == pytest.approx(200 * .00008 + 39 * .02)
    assert oracle.status()["model_path_cost_separate_from_intrinsic_work"]
    assert oracle.status()["kind"] == COST_KIND
    assert oracle.status()["input_progress"] == "completion_only_like_learned_labels"


def test_zero_observed_output_cannot_claim_prefill_has_finished(tmp_path):
    oracle = WorkflowWorkOracle(*oracle_file(tmp_path, calibrated()))
    before = oracle.prediction("wf").remaining_path_s
    oracle.submit("wf", "started", "one", "child", "ctx", range(500))
    oracle.submit("wf", "generated", "one", 0)
    assert oracle.prediction("wf").remaining_path_s == before


def test_request_completion_mode_keeps_input_cost_until_a_real_completion(tmp_path):
    raw = calibrated()
    raw["model_prefill_progress"] = "request_completion"
    oracle = WorkflowWorkOracle(*oracle_file(tmp_path, raw))
    oracle.submit("wf", "started", "one", "child", "ctx", range(500))
    oracle.submit("wf", "generated", "one", 1)
    assert oracle.prediction("wf").remaining_path_s == pytest.approx(700 * .00008 + 39 * .02)
    oracle.submit("wf", "completed", "one", "child", "ctx", range(500), range(30))
    assert oracle.prediction("wf").remaining_path_s == pytest.approx(200 * .00008 + 10 * .02)


def test_all_join_critical_branch_switches_with_calibrated_prefill_cost():
    requests = {
        "large_input": {"input_increment": 1000, "output_tokens": 1},
        "output_work": {"input_increment": 0, "output_tokens": 5},
    }
    graph = [
        node("a", request_id="large_input"), node("b", request_id="output_work"),
        node("join", (0, 1), event_id="join", kind="join_satisfied"),
    ]
    legacy = scan_remaining_paths(graph, requests, {}, {}, .02, 0.)
    calibrated_value = scan_remaining_paths(
        graph, requests, {}, {}, .02, 0., prefill_seconds_per_token=.00001,
    )
    assert legacy[0] == pytest.approx(.22)
    assert calibrated_value[0] == pytest.approx(.10)
    assert cache_remaining_paths(
        graph, requests, {}, {}, .02, prefill_seconds_per_token=.00001,
    ).at(0.) == pytest.approx(calibrated_value)


def test_cached_cost_paths_match_literal_dag_evaluation_with_partial_output_and_elapsed_tools():
    rng = random.Random(31)
    for _case in range(80):
        graph, requests, done, active = [], {}, {}, {}
        for index in range(30):
            deps = tuple(sorted(rng.sample(range(index), min(index, rng.randrange(4)))))
            if index % 3 == 0:
                request = str(index)
                requests[request] = {
                    "input_increment": rng.randrange(10000),
                    "output_tokens": rng.randrange(1, 200),
                }
                active[request] = rng.randrange(requests[request]["output_tokens"])
                graph.append(node("r" + request, deps, request_id=request))
            else:
                graph.append(node(
                    "e" + str(index), deps, event_id=str(index), kind="tool_end",
                    delay=rng.random() * 20,
                ))
            if rng.random() < .3:
                done[graph[-1]["node_id"]] = 100. + rng.random() * 10.
        for prefill_done in (False, True):
            kwargs = {
                "prefill_seconds_per_token": .0000832252503024952,
                "prefill_done_after_output": prefill_done,
            }
            cached = cache_remaining_paths(graph, requests, done, active, .023478, **kwargs)
            for now in (111., 115., 140., 200.):
                assert cached.at(now) == pytest.approx(
                    scan_remaining_paths(graph, requests, done, active, .023478, now, **kwargs),
                    abs=1e-7,
                )


@pytest.mark.parametrize("value", (-.1, float("inf"), float("nan"), True, None, "fast"))
def test_invalid_prefill_cost_is_rejected(value):
    raw = calibrated()
    raw["model_prefill_seconds_per_token"] = value
    with pytest.raises(ValueError, match="v2 oracle"):
        WorkflowWorkOracleArtifact(raw)


@pytest.mark.parametrize("field", ("model_prefill_seconds_per_token", "model_prefill_progress"))
def test_v2_cost_and_progress_semantics_must_both_be_explicit(field):
    raw = calibrated()
    raw.pop(field)
    with pytest.raises(ValueError, match="v2 oracle"):
        WorkflowWorkOracleArtifact(raw)
    legacy = source()
    legacy[field] = calibrated()[field]
    with pytest.raises(ValueError, match="separate v2"):
        WorkflowWorkOracleArtifact(legacy)


def test_unknown_prefill_progress_semantics_are_rejected():
    raw = calibrated()
    raw["model_prefill_progress"] = "assume_at_registration"
    with pytest.raises(ValueError, match="v2 oracle"):
        WorkflowWorkOracleArtifact(raw)


def test_complete_work_conservation_is_identical_after_partial_progress(tmp_path):
    oracle = WorkflowWorkOracle(*oracle_file(tmp_path, calibrated()))
    for name, prompt, output in (("one", 500, 30), ("two", 730, 10)):
        oracle.submit("wf", "started", name, "child", "ctx", range(prompt))
        oracle.submit("wf", "generated", name, output // 2)
        oracle.submit("wf", "completed", name, "child", "ctx", range(prompt), range(output))
    assert oracle.prediction("wf").remaining_work_units == 0
    assert oracle.prediction("wf").remaining_path_s == 0
    assert oracle.status()["counts"]["observed_input_tokens"] == 700
    assert oracle.status()["counts"]["observed_output_tokens"] == 40


def test_real_constructor_reads_v2_artifact_without_loading_a_learned_model(tmp_path):
    values = configured(tmp_path)
    path, digest = oracle_file(tmp_path, calibrated())
    values.update(WORKFLOW_WORK_MODEL=path, WORKFLOW_WORK_MODEL_SHA256=digest)
    from beliefkv.experiments.internal_comparison import arm_environment
    result = comparison.preflight_runtime(arm_environment(values, "full"), str(tmp_path))
    assert result["verified_configuration"]["workflow_work_source"] == "oracle"
