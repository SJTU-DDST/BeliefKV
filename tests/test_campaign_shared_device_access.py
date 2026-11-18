from datetime import datetime, timezone
import json
import pytest

from scripts.run_continuous_matched_campaign import check_device_access


def plan(tmp_path, *, mode=None, status="released", heartbeat=0.):
    state = tmp_path / "device.json"
    guard = {"pid": 100, "start_ticks": 20}
    anchor = {"pid": 101, "start_ticks": 21}
    state.write_text(json.dumps({
        "status": status, "guard": guard, "owner": anchor,
        "checked_at": datetime.fromtimestamp(heartbeat, timezone.utc).isoformat(),
    }))
    result = {"device_state": str(state), "held_reservation_anchor": anchor}
    if mode is not None:
        result["device_access_mode"] = mode
    return result


def test_shared_mode_accepts_released_protection_without_a_heartbeat_or_live_guard(tmp_path):
    value = plan(tmp_path, mode="shared")
    assert check_device_access(value, lambda _owner: False, now=1_000_000.) == "shared"


@pytest.mark.parametrize("status", ("held", "restoring", "failed", "unknown"))
def test_shared_mode_never_treats_live_or_unrestored_protection_as_released(tmp_path, status):
    value = plan(tmp_path, mode="shared", status=status)
    with pytest.raises(RuntimeError):
        check_device_access(value, lambda _owner: False)


def test_shared_mode_refuses_a_released_file_whose_guard_is_still_live(tmp_path):
    with pytest.raises(RuntimeError):
        check_device_access(plan(tmp_path, mode="shared"), lambda _owner: True)


@pytest.mark.parametrize("mode", (None, "protected"))
def test_default_protected_mode_retains_guard_heartbeat_and_anchor_requirements(tmp_path, mode):
    value = plan(tmp_path, mode=mode, status="held", heartbeat=100.)
    assert check_device_access(value, lambda _owner: True, now=110.) == "protected"
    with pytest.raises(RuntimeError, match="stale"):
        check_device_access(value, lambda _owner: True, now=116.)
    with pytest.raises(RuntimeError, match="live"):
        check_device_access(value, lambda _owner: False, now=110.)
    value["held_reservation_anchor"]["start_ticks"] += 1
    with pytest.raises(RuntimeError, match="anchor"):
        check_device_access(value, lambda _owner: True, now=110.)


def test_released_lease_does_not_silently_change_default_access_mode(tmp_path):
    with pytest.raises(RuntimeError):
        check_device_access(plan(tmp_path), lambda _owner: False)


@pytest.mark.parametrize("mode", (False, None, "", "shared-ish", 0))
def test_unknown_explicit_access_modes_are_rejected(tmp_path, mode):
    value = plan(tmp_path)
    value["device_access_mode"] = mode
    with pytest.raises(ValueError):
        check_device_access(value, lambda _owner: False)
