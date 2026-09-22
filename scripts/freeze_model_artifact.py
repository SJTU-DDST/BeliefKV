#!/usr/bin/env python3
"""Hash a downloaded model artifact without storing its weights in Git."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    model = args.model.resolve()
    index = model / "model.safetensors.index.json"
    if not index.is_file():
        raise SystemExit("model.safetensors.index.json is missing")
    shards = sorted(set(json.loads(index.read_text())["weight_map"].values()))
    required = ["config.json", "tokenizer_config.json", index.name, *shards]
    for name in required:
        if not (model / name).is_file():
            raise SystemExit(f"model file is missing: {name}")
    record = {
        "schema_version": 1,
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "source": args.source,
        "model_path": str(model),
        "files": {
            name: {
                "size_bytes": (model / name).stat().st_size,
                "sha256": sha256(model / name),
            }
            for name in required
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + "\n")
    print(f"hashed {len(required)} files: {args.output}")


if __name__ == "__main__":
    main()
