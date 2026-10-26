"""Opt-in CPU qualification of first-party calls during actual pytest tests."""

import json
from pathlib import Path
import sys
import threading

import pytest


def pytest_addoption(parser):
    group = parser.getgroup("beliefkv-production-probe")
    group.addoption("--beliefkv-production-report")
    group.addoption("--beliefkv-production-root", default="/workspace")


class ProductionProbe:
    def __init__(self, root):
        self.root = str(Path(root).resolve()).rstrip("/") + "/"
        self.case = None
        self.calls = 0
        self.paths = set()
        self.cases = set()
        self.functions = set()
        self.code_paths = {}
        self.previous = None
        self.previous_thread = None

    def _production_path(self, code):
        if code in self.code_paths:
            return self.code_paths[code]
        filename = code.co_filename
        relative = filename[len(self.root):] if filename.startswith(self.root) else None
        if relative:
            parts = Path(relative).parts
            name = parts[-1]
            if (
                not filename.endswith(".py") or name == "conftest.py"
                or name.startswith(("test_", "tests_")) or name.endswith("_test.py")
                or any(part in (
                    "test", "tests", "testing", ".venv", "venv", "site-packages",
                ) for part in parts[:-1])
            ):
                relative = None
        self.code_paths[code] = relative
        return relative

    def observe(self, frame, event, arg):
        if event != "call" or self.case is None:
            return
        relative = self._production_path(frame.f_code)
        if relative is None:
            return
        self.calls += 1
        self.paths.add(relative)
        self.cases.add(self.case)
        self.functions.add((relative, frame.f_code.co_firstlineno, frame.f_code.co_name))

    def start(self):
        self.previous = sys.getprofile()
        self.previous_thread = (
            threading.getprofile() if hasattr(threading, "getprofile")
            else getattr(threading, "_profile_hook", None)
        )
        if self.previous is not None or self.previous_thread is not None:
            raise pytest.UsageError("production probe requires an isolated profiling process")
        sys.setprofile(self.observe)
        threading.setprofile(self.observe)

    def stop(self):
        self.case = None
        sys.setprofile(self.previous)
        threading.setprofile(self.previous_thread)

    def report(self):
        return {
            "schema_version": 1,
            "observation": "first_party_python_calls_during_pytest_call_phase",
            "production_root": self.root.rstrip("/"),
            "call_events": self.calls,
            "production_files": sorted(self.paths),
            "production_functions": [
                {"path": path, "line": line, "name": name}
                for path, line, name in sorted(self.functions)[:256]
            ],
            "distinct_production_functions": len(self.functions),
            "test_cases_reaching_production": sorted(self.cases)[:256],
            "test_cases_reaching_production_count": len(self.cases),
            "duration_is_not_normal_tool_timing": True,
            "main_and_future_thread_profile_restored": True,
        }


def pytest_configure(config):
    output = config.getoption("--beliefkv-production-report")
    if output:
        config._beliefkv_production_probe = ProductionProbe(
            config.getoption("--beliefkv-production-root"),
        )


def pytest_sessionstart(session):
    probe = getattr(session.config, "_beliefkv_production_probe", None)
    if probe is not None:
        probe.start()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    probe = getattr(item.config, "_beliefkv_production_probe", None)
    if probe is None:
        yield
        return
    probe.case = item.nodeid
    try:
        yield
    finally:
        probe.case = None


def pytest_sessionfinish(session, exitstatus):
    probe = getattr(session.config, "_beliefkv_production_probe", None)
    if probe is None:
        return
    probe.stop()
    output = Path(session.config.getoption("--beliefkv-production-report"))
    with output.open("x") as stream:
        json.dump(probe.report(), stream, indent=2, allow_nan=False)
        stream.write("\n")
