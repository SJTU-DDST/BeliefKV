import hashlib
import pytest

from scripts.recover_repetition_service_audits import frozen_bindings, require_stopped


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_frozen_recovery_maps_only_exact_source_bytes_and_preserves_data_bindings(tmp_path):
    development, frozen = tmp_path / "main", tmp_path / "frozen"
    source = development / "beliefkv" / "policy.py"
    expected = write(frozen / "beliefkv" / "policy.py", "original policy")
    write(source, "new development policy")
    data = development / "experiments" / "cohort.json"
    data_digest = write(data, "fixed cohort")
    pins = {str(source): expected, str(data): data_digest}
    mapped, bindings = frozen_bindings(pins, development, frozen)
    assert pins == {str(source): expected, str(data): data_digest}
    assert mapped == {str(frozen / "beliefkv" / "policy.py"): expected, str(data): data_digest}
    assert bindings == [{
        "original": str(source), "frozen": str(frozen / "beliefkv" / "policy.py"),
        "sha256": expected, "byte_identical_to_prior_registered_reader": True,
    }]


@pytest.mark.parametrize("change", ("frozen_source", "data", "missing"))
def test_recovery_never_relaxes_a_source_or_data_fingerprint(tmp_path, change):
    development, frozen = tmp_path / "main", tmp_path / "frozen"
    source = frozen / "scripts" / "audit.py"
    data = development / "cohort.json"
    pins = {
        str(development / "scripts" / "audit.py"): write(source, "exact reader"),
        str(data): write(data, "exact data"),
    }
    if change == "frozen_source":
        source.write_text("changed reader")
    elif change == "data":
        data.write_text("changed data")
    else:
        source.unlink()
    with pytest.raises(ValueError, match="exact prior"):
        frozen_bindings(pins, development, frozen)


@pytest.mark.parametrize("change", ("live", "no_process", "wrong_failure", "partially_audited"))
def test_recovery_requires_actual_old_owner_exit_and_the_declared_failure(change):
    state = {
        "status": "stopped_preserve_repeated_service_audits", "process": {"pid": 1},
        "error": "ValueError('campaign input changed: /main/policy.py')", "completed_methods": [],
    }
    require_stopped(state, lambda _process: False)
    if change == "no_process":
        state.pop("process")
    elif change == "wrong_failure":
        state["error"] = "unknown failure"
    elif change == "partially_audited":
        state["completed_methods"] = ["oracle"]
    with pytest.raises(ValueError, match="actually exited"):
        require_stopped(state, lambda _process: change == "live")
