"""Opt-in, content-free test progress frames for sandbox timing experiments."""

import json
import os


_session = None


def _emit(phase, completed, total):
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return
    payload = json.dumps(
        {"phase": phase, "completed": completed, "total": total},
        separators=(",", ":"),
    ).encode("ascii")
    os.write(2, b"\x1eBKVP:" + payload + b"\x1f")


def pytest_collection_finish(session):
    session._beliefkv_progress_total = len(session.items)
    session._beliefkv_progress_completed = 0
    _emit("collection", 0, len(session.items))


def pytest_runtest_logreport(report):
    if (report.when != "teardown" or os.environ.get("PYTEST_XDIST_WORKER")
        or _session is None
        or not getattr(_session, "_beliefkv_progress_total", 0)):
        return
    _session._beliefkv_progress_completed += 1
    _emit(
        "test_done",
        _session._beliefkv_progress_completed,
        _session._beliefkv_progress_total,
    )


def pytest_sessionstart(session):
    global _session
    _session = session


def pytest_sessionfinish(session, exitstatus):
    _emit(
        "session_finish",
        getattr(session, "_beliefkv_progress_completed", 0),
        getattr(session, "_beliefkv_progress_total", 0),
    )
    global _session
    _session = None
