import pytest

from scripts.benchmark_execution_frontier_cpu import measure


@pytest.mark.parametrize("depth", [4, 16])
def test_positive_benchmark_releases_the_previous_selected_consumer(depth):
    result = measure(depth, "positive", 2)
    assert result["control_payload_signature"][:2] == (depth, depth)
    assert len(result["cpu_samples_us"]) == 2


def test_selected_pending_benchmark_checks_the_fast_path_without_native_reads():
    result = measure(4, "selected_pending", 2)
    assert not any(result["control_payload_signature"])
    assert not any(result["native_reads_last_iteration"].values())
    assert result["selected_pending_checks_last_iteration"] == 1


def test_disabled_benchmark_does_not_install_or_inspect_a_selected_consumer():
    result = measure(4, "selected_pending", 2, enabled=False)
    assert not any(result["control_payload_signature"])
    assert not any(result["native_reads_last_iteration"].values())
    assert result["selected_pending_checks_last_iteration"] == 0
