import pytest

from scripts.audit_native_host_demand import host_demand, repeated_context_demand


def event(request="r", queued=10., served=20., tokens=5, **extra):
    return {
        "kind": "llm_submit", "workflow_id": "wf", "invocation_id": "root",
        "context_id": "ctx", "context_epoch": 0, "ts_ms": queued,
        "attributes": {
            "request_id": request, "cached_tokens_host": tokens,
            "cached_tokens_device": 3, "prompt_tokens": 10,
            "native_host_hit_match_path": {"observed_ts_ms": served},
        }, **extra,
    }


def test_window_uses_service_observation_instead_of_queue_time():
    report = host_demand([
        event("before", queued=1., served=9.),
        event("in", queued=1., served=20.),
        event("after", queued=20., served=30.),
    ], {("wf", "root"): {"instance_id": "task", "role": "root"}}, 10., 30., 10)
    assert [row["request_id"] for row in report["rows"]] == ["in"]
    assert report["summary"]["reported_host_full_bytes"] == 50
    assert report["by_role"]["root"]["host_hit_requests"] == 1


def test_repeated_context_demand_is_summed_without_becoming_unique_storage():
    report = host_demand([event("a"), event("b")], {}, 0., 30., 10)
    assert report["summary"]["reported_host_full_bytes"] == 100
    assert report["summary"]["contexts"] == 1
    assert report["by_role"]["unknown"]["host_hit_requests"] == 2


def test_missing_first_service_timing_is_not_assigned_by_queue_time():
    missing = event()
    del missing["attributes"]["native_host_hit_match_path"]
    report = host_demand([missing], {}, 0., 30., 10)
    assert report["summary"]["host_hit_requests"] == 0
    assert report["unknown_timing_or_size_across_full_recorded_prefix"][
        "positive_host_hit_without_first_service_timestamp_in_prefix"
    ] == 1


def test_invocation_identity_is_scoped_by_workflow():
    report = host_demand([event(workflow_id="other")], {
        ("wf", "root"): {"instance_id": "task", "role": "child"},
    }, 0., 30., 10)
    assert report["rows"][0]["role"] == "unknown"
    assert report["rows"][0]["instance_id"] is None


def test_duplicate_native_request_and_unordered_times_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        host_demand([event(), event()], {}, 0., 30., 10)
    with pytest.raises(ValueError, match="precedes"):
        host_demand([event(queued=20., served=10.)], {}, 0., 30., 10)


@pytest.mark.parametrize("tokens", [-1, 1.5, True])
def test_invalid_host_demand_is_rejected(tokens):
    with pytest.raises(ValueError, match="invalid"):
        host_demand([event(tokens=tokens)], {}, 0., 30., 10)


def test_repeated_positive_observations_use_service_order_and_keep_singletons():
    report = host_demand([
        event("later", served=25., tokens=7),
        event("first", served=20., tokens=3),
        event("singleton", served=26., tokens=2, context_id="other"),
    ], {}, 0., 30., 10)["repeated_context_demand"]
    assert report["known_identity_contexts"] == 2
    assert report["contexts_with_multiple_positive_observations"] == 1
    assert report["first_positive_observations_in_window"] == 2
    assert report["later_positive_observations_in_same_context"] == 1
    assert report["first_reported_host_full_bytes"] == 50
    assert report["later_reported_host_full_bytes"] == 70
    assert report["later_fraction_of_known_reported_host_full_bytes"] == 70 / 120
    assert report["interval_between_positive_observations_ms"]["p50"] == 5.


def test_repeated_demand_does_not_merge_context_names_across_workflows():
    report = host_demand([
        event("one"), event("two", workflow_id="other"),
    ], {}, 0., 30., 10)["repeated_context_demand"]
    assert report["known_identity_contexts"] == 2
    assert report["later_positive_observations_in_same_context"] == 0
    assert report["later_fraction_of_known_reported_host_full_bytes"] == 0.
    assert report["interval_between_positive_observations_ms"]["count"] == 0


def test_missing_context_identity_is_unknown_instead_of_a_shared_context():
    report = host_demand([
        event("one", context_id=None), event("two", context_id=None),
    ], {}, 0., 30., 10)["repeated_context_demand"]
    assert report["unknown_context_identity_rows"] == 2
    assert report["known_identity_contexts"] == 0
    assert report["later_positive_observations_in_same_context"] == 0
    assert report["later_fraction_of_known_reported_host_full_bytes"] is None


def test_empty_repeated_demand_keeps_unknown_fraction():
    report = repeated_context_demand([])
    assert report["first_reported_host_full_bytes"] == report["later_reported_host_full_bytes"] == 0
    assert report["later_fraction_of_known_reported_host_full_bytes"] is None
