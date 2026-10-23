#!/usr/bin/env python3
"""Replay the retained CPU qualification and add reusable-session coverage."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reference-xml", type=Path,
        default=ROOT / "experiments/reports/v3112_private_prepare_final_qualified_cpu_20261023.xml",
    )
    parser.add_argument("--junitxml", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise RuntimeError("CPU qualification requires CUDA_VISIBLE_DEVICES=99999")
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    modules = {
        "tests/test_reusable_subagents.py", "tests/test_subagent_retention.py",
        "tests/test_causal_replay.py", "tests/test_causal_replay_backend.py",
        "tests/test_causal_source_capture.py", "tests/test_causal_comparison.py",
        "tests/test_tool_prefetch_host_retention.py", "tests/test_qwen35_native_regime_probe.py",
        "tests/test_dormant_retention_geometry.py",
        "tests/test_causal_jct_paths.py",
    }
    nodes = []
    for case in ET.parse(args.reference_xml).getroot().iter("testcase"):
        parts = case.attrib["classname"].split(".")
        classes = []
        path = ROOT.joinpath(*parts).with_suffix(".py")
        while not path.is_file() and len(parts) > 1:
            classes.insert(0, parts.pop())
            path = ROOT.joinpath(*parts).with_suffix(".py")
        if not path.is_file():
            raise RuntimeError(f"reference test module missing: {path}")
        relative = str(path.relative_to(ROOT))
        if relative not in modules:
            nodes.append("::".join([relative, *classes, case.attrib["name"]]))
    nodes = list(dict.fromkeys(nodes))
    nodes.extend(sorted(modules))
    return pytest.main(["-q", *nodes, f"--junitxml={args.junitxml}"])


if __name__ == "__main__":
    raise SystemExit(main())
