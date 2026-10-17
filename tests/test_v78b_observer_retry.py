from copy import deepcopy

import pytest

from scripts.observe_owned_scheduler_numa import arguments, resident_pages
from scripts.start_v78b_native_order_retry_20261016 import (
    LEGACY_OBSERVER, OBSERVER, observer_command, retry_environment,
)


@pytest.mark.parametrize("arm", ["native", "predictive_h2d", "reactive"])
def test_observer_accepts_supported_arm_without_contacting_scheduler(arm):
    args = arguments(["--run-root", "/tmp/run", "--arm", arm, "--output", "/tmp/out"])
    assert args.arm == arm


def test_observer_separates_large_maps_and_keeps_all_node_bytes():
    result = resident_pages(
        "a prefer:1 N0=1 N1=65536 kernelpagesize_kB=4\nb default N0=2\nc default\n", 4096,
    )
    assert result["observed_resident_bytes_by_node"] == {"N0": 3 * 4096, "N1": 65536 * 4096}
    assert result["large_map_resident_bytes_by_node"] == {"N0": 4096, "N1": 65536 * 4096}
    assert len(result["large_maps"]) == 1


def test_only_legacy_placement_command_is_redirected():
    command = ["python", str(LEGACY_OBSERVER), "--arm", "reactive", "--output", "evidence"]
    before = deepcopy(command)
    assert observer_command(command) == ["python", str(OBSERVER), *command[2:]]
    assert command == before
    other = ["python", "serving.py", "--arm", "reactive"]
    assert observer_command(other) is other


def test_only_retry_output_directory_can_change():
    original = {"RUN_ROOT": "/original", "PORT": "18482", "ROOT_COUNT": "156"}
    assert retry_environment(original, {**original, "RUN_ROOT": "/retry"}, "/retry")
    with pytest.raises(ValueError):
        retry_environment(original, {**original, "RUN_ROOT": "/retry", "ROOT_COUNT": "108"}, "/retry")
    with pytest.raises(ValueError):
        retry_environment(original, {**original, "RUN_ROOT": "/retry", "PORT": "18483"}, "/retry")
