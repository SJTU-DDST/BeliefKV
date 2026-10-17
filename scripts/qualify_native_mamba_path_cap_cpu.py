#!/usr/bin/env python3
"""Run existing native path-cap CPU cases with the observer fixture initialized."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument(
        "--canonical-patch", type=Path,
        default=ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )
    args = parser.parse_args()
    if args.output.exists() or args.receipt.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "999":
        parser.error("fresh outputs and CUDA_VISIBLE_DEVICES=999 are required")
    engine = args.engine.resolve()
    source = git_source_state(engine, canonical_patch=args.canonical_patch)
    if not source["canonical_patch"]["matches_worktree"]:
        raise ValueError("canonical patch does not describe the actual engine")
    test_file = engine / "test/registered/unit/mem_cache/test_mamba_path_state_cap.py"
    sys.path[:0] = [str(engine / "python"), str(test_file.parent)]
    module = importlib.import_module("test_mamba_path_state_cap")
    original = module._FakeTreeCore.__init__

    def initialize_observer(self):
        original(self)
        self._beliefkv_host_eviction_observer = None

    module._FakeTreeCore.__init__ = initialize_observer
    import pytest

    code = pytest.main([
        str(test_file) + "::TestMambaPathStateCap", "-q",
        "--junitxml=" + str(args.output),
    ])
    cases = list(ET.parse(args.output).getroot().iter("testcase"))
    complete = bool(cases) and not any(
        case.find(kind) is not None
        for case in cases for kind in ("failure", "error", "skipped")
    )
    paths = [
        Path(__file__).resolve(), test_file,
        engine / "python/sglang/srt/mem_cache/unified_cache/components/mamba.py",
        engine / "python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py",
        engine / "python/sglang/srt/arg_groups/fields/exec_.py",
    ]
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "status": "cpu_existing_path_cap_qualified" if code == 0 and complete else "failed",
        "engine": str(engine),
        "engine_head_tree": subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=engine, text=True,
        ).strip(),
        "engine_actual_tree": source["canonical_patch"]["actual_tree"],
        "engine_source": source,
        "selected_suite": "TestMambaPathStateCap",
        "xml_cases": len(cases), "pytest_exit_code": int(code),
        "cpu_receipt": {
            "path": str(args.output),
            "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        },
        "sources": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
        },
        "fixture_adapter": "Initialize only the fake tree's observer to the production constructor default None; native source files and eviction conditions are unchanged.",
        "scope": "Existing CPU policy/CLI cases only; CUDA fixtures are not selected. No GPU, traffic, JCT or quality improvement is established.",
    }
    if git_source_state(engine, canonical_patch=args.canonical_patch) != source:
        raise ValueError("engine source changed during qualification")
    with args.receipt.open("x") as stream:
        stream.write(json.dumps(record, indent=2, allow_nan=False) + "\n")
    print(json.dumps(record, indent=2))
    return int(code) if code else int(not complete)


if __name__ == "__main__":
    raise SystemExit(main())
