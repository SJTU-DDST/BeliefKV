from scripts.audit_join_alternative_frontiers import own_frontiers


def group():
    return {"j": {
        "members": ("a", "b"), "wait_ms": 10., "satisfied_ms": 200.,
        "workflow": "w",
    }}


def test_early_returning_competitor_has_no_direct_isolated_join_advance():
    result = own_frontiers("a", 20., group(), {"a": 100., "b": 150.}, set())
    assert not result[0]["competitor_was_actual_last_return"]
    assert result[0]["isolated_advance_ceiling_s"] == 0.
    last = own_frontiers("b", 20., group(), {"a": 100., "b": 150.}, set())
    assert last[0]["competitor_was_actual_last_return"]
    assert last[0]["isolated_advance_ceiling_s"] == .05


def test_censored_peer_and_unobserved_wait_do_not_become_known_last_returns():
    result = own_frontiers("a", 20., group(), {"a": 100.}, set())
    assert result[0]["status"] == "unclosed_JOIN_unknown"
    assert "isolated_advance_ceiling_s" not in result[0]
    assert own_frontiers("a", 5., group(), {"a": 100., "b": 150.}, set()) == [
        {"status": "no_current_observed_JOIN"},
    ]
    assert own_frontiers("root", 20., group(), {}, {"root"}) == [
        {"status": "root_continuation"},
    ]
