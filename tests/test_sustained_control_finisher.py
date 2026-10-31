from unittest.mock import patch

import pytest

from scripts.finish_sustained_control import registered_control_complete


def test_published_registration_without_its_initial_state_remains_pending(tmp_path):
    registration = tmp_path / "control.registration.json"
    with patch("scripts.finish_sustained_control.complete_work") as complete:
        assert not registered_control_complete(registration)
        registration.write_text("{}")
        assert not registered_control_complete(registration)
        complete.assert_not_called()


def test_existing_state_still_requires_complete_work_drain_and_actual_exit(tmp_path):
    registration = tmp_path / "control.registration.json"
    registration.write_text("{}")
    registration.with_suffix(".state.json").write_text("{}")
    with patch("scripts.finish_sustained_control.complete_work", return_value=None):
        assert not registered_control_complete(registration)
    with patch("scripts.finish_sustained_control.complete_work", return_value={"request": ()}):
        assert registered_control_complete(registration)


def test_missing_required_completion_artifacts_are_not_silently_ignored(tmp_path):
    registration = tmp_path / "control.registration.json"
    registration.write_text("{}")
    registration.with_suffix(".state.json").write_text("{}")
    with patch("scripts.finish_sustained_control.complete_work", side_effect=FileNotFoundError):
        with pytest.raises(FileNotFoundError):
            registered_control_complete(registration)
