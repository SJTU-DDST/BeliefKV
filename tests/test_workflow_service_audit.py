import pytest

from scripts.audit_workflow_service import merge, service_metrics, subtract


def test_sibling_overlap_counts_once_and_blocked_only_time_is_not_a_service_gap():
    result = service_metrics(
        [(10., 100.), (20., 100.), (500., 600.)],
        [(20., 50.), (40., 80.), (550., 580.)],
        arrival=0., terminal=600., first_launch=20.,
    )
    assert result["first_submit_to_first_launch_s"] == .01
    assert result["arrival_to_first_launch_s"] == .02
    assert result["submitted_ready_s"] == .19
    assert result["max_post_start_ready_service_gap_s"] == .05
    assert result["submitted_ready_stall_fraction"] == pytest.approx(100. / 190.)


def test_initial_service_wait_is_separate_from_post_start_ready_gaps():
    result = service_metrics(
        [(0., 1000.)], [(800., 900.), (950., 1000.)],
        arrival=0., terminal=1000., first_launch=800.,
    )
    assert result["first_submit_to_first_launch_s"] == .8
    assert result["max_post_start_ready_service_gap_s"] == .05
    assert result["post_start_ready_gap_s"]["count"] == 1


def test_only_prefill_touch_does_not_hide_later_workflow_starvation():
    result = service_metrics(
        [(0., 10000.)], [(1., 2.), (9990., 10000.)],
        arrival=0., terminal=10000., first_launch=1.,
    )
    assert result["first_submit_to_first_launch_s"] == .001
    assert result["max_post_start_ready_service_gap_s"] == 9.988


@pytest.mark.parametrize("invalid", [[(2., 1.)], [(float("nan"), 3.)]])
def test_invalid_service_intervals_are_rejected(invalid):
    with pytest.raises(ValueError, match="invalid"):
        merge(invalid)


def test_service_subtraction_handles_multiple_readiness_episodes():
    assert subtract(
        merge([(0., 5.), (8., 10.), (15., 20.)]),
        merge([(1., 4.), (9., 16.), (18., 30.)]),
    ) == [[0., 1.], [4., 5.], [8., 9.], [16., 18.]]
