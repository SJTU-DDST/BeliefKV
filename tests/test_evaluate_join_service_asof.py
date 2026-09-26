from scripts.evaluate_join_service_asof import (
    asof_rows, evaluate, online_residual_predictions,
)


def _episode(project="alpha", rid="final", *, notice=150):
    return {
        "project": project,
        "task_id": f"{project}__issue",
        "notice_ms": notice,
        "join_last": True,
        "features": [500, 1, 2],
        "post_notice": {
            "final_request_id": rid,
            "llm_submit_to_result_ms": 2500,
        },
    }


def test_submit_features_use_only_pre_submit_metrics_and_prompt():
    episodes = [_episode()]
    submits = {"final": {
        "ts_ms": 200, "attributes": {
            "prompt_chars": 2048, "message_count": 8,
        },
    }}
    metrics = [
        {"monotonic_ts_ms": 100, "num_running_reqs": 48,
         "num_queue_reqs": 3},
        {"monotonic_ts_ms": 300, "num_running_reqs": 1,
         "num_queue_reqs": 999},
    ]
    rows, excluded = asof_rows(episodes, submits, metrics)
    assert excluded == {}
    assert rows[0]["features"] == [500, 1, 2]
    assert rows[0]["load_features"] == [500, 1, 2, 48, 3]
    assert rows[0]["load_shape_features"] == [
        500, 1, 2, 48, 3, 2048, 8,
    ]
    assert rows[0]["duration_ms"] == 2500
    assert rows[0]["metric_age_ms"] == 100


def test_stale_or_missing_submit_is_counted_not_imputed():
    episodes = [
        _episode("alpha", "stale", notice=3000),
        _episode("beta", "missing"),
    ]
    submits = {"stale": {
        "ts_ms": 5000, "attributes": {
            "prompt_chars": 100, "message_count": 2,
        },
    }}
    rows, excluded = asof_rows(episodes, submits, [
        {"monotonic_ts_ms": 100, "num_running_reqs": 48,
         "num_queue_reqs": 200},
    ])
    assert rows == []
    assert excluded == {
        "missing_final_submit": 1,
        "missing_recent_load": 1,
    }


def test_project_folds_do_not_train_on_their_target_project():
    rows = []
    for project, target in (("alpha", 1100), ("beta", 2400), ("gamma", 3600)):
        for index in range(5):
            row, _ = asof_rows([_episode(project, f"{project}-{index}")], {
                f"{project}-{index}": {
                    "ts_ms": 200, "attributes": {
                        "prompt_chars": 2000, "message_count": 5,
                    },
                },
            }, [{"monotonic_ts_ms": 100, "num_running_reqs": 20,
                 "num_queue_reqs": 10}])
            rows.append({
                **row[0], "workflow": f"{project}__{index}",
                "duration_ms": target,
            })
    report = evaluate(rows)
    assert report["projects"] == ["alpha", "beta", "gamma"]
    assert report["folds"]["alpha"]["methods"]["train_median"]["all"][
        "median_absolute_error_ms"
    ] == 1900
    assert report["folds"]["alpha"]["workflows"] == 5


def test_online_residual_waits_for_completed_other_workflows():
    rows = [
        {"workflow": "a", "submit_ts_ms": 100, "completion_ts_ms": 150,
         "duration_ms": 50},
        {"workflow": "b", "submit_ts_ms": 110, "completion_ts_ms": 190,
         "duration_ms": 80},
        {"workflow": "c", "submit_ts_ms": 175, "completion_ts_ms": 275,
         "duration_ms": 100},
        {"workflow": "d", "submit_ts_ms": 201, "completion_ts_ms": 301,
         "duration_ms": 100},
    ]
    predictions, supported = online_residual_predictions(
        rows, [100.] * len(rows),
        minimum_support=2, minimum_workflows=2,
    )
    assert supported == [3]
    assert predictions == [100., 100., 100., 82.5]
