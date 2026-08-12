#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Any, Callable


InspectImage = Callable[[str], dict[str, Any]]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze locally pulled Docker identities for an H200 pilot."
    )
    parser.add_argument("--requirements", type=Path, required=True)
    return parser.parse_args()


def _inspect_image(image: str) -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = json.loads(result.stdout)
    if len(rows) != 1:
        raise ValueError(f"expected one Docker inspect row for {image!r}")
    return dict(rows[0])


def _matching_repo_digest(image: str, repo_digests: list[str]) -> str:
    repository = image.rsplit(":", 1)[0]
    matches = [
        digest for digest in repo_digests if digest.startswith(f"{repository}@sha256:")
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one immutable RepoDigest for {image!r}, found {matches!r}"
        )
    return matches[0]


def lock_image_requirements(
    payload: dict[str, Any],
    *,
    inspect_image: InspectImage,
    locked_at: str,
) -> dict[str, Any]:
    images = payload.get("images")
    if not isinstance(images, list) or not images:
        raise ValueError("image requirements contain no images")
    if int(payload.get("image_count", -1)) != len(images):
        raise ValueError("image_count does not match the requirement rows")

    locked_rows: list[dict[str, Any]] = []
    seen_images: set[str] = set()
    seen_digests: set[str] = set()
    for raw in images:
        row = dict(raw)
        image = str(row["image"])
        if image in seen_images:
            raise ValueError(f"duplicate image requirement: {image}")
        seen_images.add(image)
        inspected = inspect_image(image)
        architecture = str(inspected.get("Architecture") or "")
        operating_system = str(inspected.get("Os") or "")
        if (operating_system, architecture) != ("linux", "amd64"):
            raise ValueError(
                f"unexpected platform for {image}: {operating_system}/{architecture}"
            )
        image_id = str(inspected.get("Id") or "")
        if not image_id.startswith("sha256:"):
            raise ValueError(f"invalid image ID for {image}: {image_id!r}")
        repo_digest = _matching_repo_digest(
            image, [str(value) for value in inspected.get("RepoDigests") or ()]
        )
        if repo_digest in seen_digests:
            raise ValueError(f"duplicate RepoDigest across pilot images: {repo_digest}")
        seen_digests.add(repo_digest)
        size_bytes = int(inspected.get("Size") or 0)
        if size_bytes <= 0:
            raise ValueError(f"invalid image size for {image}: {size_bytes}")
        row.update(
            {
                "architecture": architecture,
                "created": str(inspected.get("Created") or ""),
                "image_id": image_id,
                "operating_system": operating_system,
                "repo_digest": repo_digest,
                "size_bytes": size_bytes,
                "status": "pulled_verified",
            }
        )
        locked_rows.append(row)

    output = dict(payload)
    output.update(
        {
            "lock_state": "frozen_local_images",
            "locked_at": locked_at,
            "images": locked_rows,
        }
    )
    return output


def _write_json_atomic(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    args = _parse_args()
    path = args.requirements.expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    locked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    locked = lock_image_requirements(
        payload,
        inspect_image=_inspect_image,
        locked_at=locked_at,
    )
    _write_json_atomic(path, locked)
    print(
        json.dumps(
            {
                "requirements": str(path),
                "lock_state": locked["lock_state"],
                "locked_at": locked_at,
                "image_count": len(locked["images"]),
                "total_size_bytes": sum(
                    int(row["size_bytes"]) for row in locked["images"]
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
