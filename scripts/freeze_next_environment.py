#!/usr/bin/env python3
"""Freeze the isolated SGLang migration environment without recording secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
ENV = Path("/home/longhao/miniconda3/envs/beliefkv-next")
CHECKOUT = ROOT / "third_party/sglang-v0.5.20"
MODEL_MANIFEST = ROOT / "configs/migration/2026-09-22_qwen35_model_artifact.json"


def output(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    python = ENV / "bin/python"
    cuda = ENV / "lib/python3.11/site-packages/nvidia/cu13/bin/nvcc"
    if not python.is_file() or not cuda.is_file() or not MODEL_MANIFEST.is_file():
        parser.error("migration environment, CUDA 13 nvcc or model manifest missing")

    record = {
        "schema_version": 1,
        "environment": str(ENV),
        "python_version": output(str(python), "--version"),
        "conda_packages": json.loads(
            output("/home/longhao/miniconda3/bin/conda", "list", "-n", "beliefkv-next", "--json")
        ),
        "pip_packages": json.loads(
            output(str(python), "-m", "pip", "list", "--format=json")
        ),
        "nvcc": output(str(cuda), "--version"),
        "sglang": {
            "installed_version": output(
                str(python), "-c",
                "import importlib.metadata as m; print(m.version('sglang'))",
            ),
            "source_commit": output("git", "rev-parse", "HEAD", cwd=CHECKOUT),
            "source_status": output("git", "status", "--short", cwd=CHECKOUT),
            "source_is_active": False,
        },
        "model_manifest_path": str(MODEL_MANIFEST.relative_to(ROOT)),
        "model_manifest_sha256": sha256(MODEL_MANIFEST),
        "staging_patch_sha256": sha256(
            ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch"
        ),
        "environment_declaration_sha256": sha256(ROOT / "environment-next.yml"),
        "project_dependencies_sha256": sha256(ROOT / "pyproject.toml"),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, ensure_ascii=True) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
