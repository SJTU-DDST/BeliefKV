#!/usr/bin/env python3
"""Record a reproducible, non-secret inventory before a runtime migration."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CONDA = Path("/home/longhao/miniconda3/bin/conda")


def run(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--profile",
        type=Path,
        default=ROOT / "configs/p6/h200_bf16_v10/frozen_runtime_profile.json",
    )
    args = parser.parse_args()
    profile_path = args.profile.resolve()
    profile = json.loads(profile_path.read_text())
    model = Path(profile["model"]["path"])
    sglang = ROOT / "third_party/sglang"
    envs: dict[str, object] = {}
    for name in ("beliefkv", "beliefkv-agents"):
        python = Path(f"/home/longhao/miniconda3/envs/{name}/bin/python")
        envs[name] = {
            "python": run(str(python), "--version"),
            "conda_packages": json.loads(
                run(str(CONDA), "list", "-n", name, "--json")
            ),
            "pip_packages": json.loads(
                run(str(python), "-m", "pip", "list", "--format=json")
            ),
        }
    model_files = ("config.json", "tokenizer_config.json", "model.safetensors.index.json")
    record = {
        "schema_version": 1,
        "git": {
            "beliefkv_commit": run("git", "rev-parse", "HEAD"),
            "beliefkv_status": run("git", "status", "--porcelain"),
            "sglang_commit": run("git", "rev-parse", "HEAD", cwd=sglang),
            "sglang_patch_sha256": digest(
                ROOT / profile["source_contract"]["canonical_sglang_patch"]
            ),
            "sglang_status": run("git", "status", "--short", cwd=sglang),
        },
        "hardware": {
            "gpu": run(
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version,pci.bus_id",
                "--format=csv,noheader",
            ),
            "nvcc": run(
                "/home/longhao/miniconda3/envs/beliefkv/bin/nvcc",
                "--version",
            ),
        },
        "profile": {
            "path": str(profile_path),
            "sha256": digest(profile_path),
            "model": profile["model"],
            "runtime": profile["runtime"],
            "capacity": profile["capacity"],
        },
        "model_files": {
            filename: digest(model / filename)
            for filename in model_files
            if (model / filename).is_file()
        },
        "model_weight_shards": sorted(
            (
                {"name": item.name, "size_bytes": item.stat().st_size}
                for item in model.glob("*.safetensors")
            ),
            key=lambda item: item["name"],
        ),
        "environments": envs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(record, indent=2, ensure_ascii=True) + "\n"
    )
    print(args.output)


if __name__ == "__main__":
    sys.exit(main())
