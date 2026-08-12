from __future__ import annotations

import pytest

from scripts.lock_h200_pilot_images import lock_image_requirements


def _inspect(image: str) -> dict[str, object]:
    repository = image.rsplit(":", 1)[0]
    suffix = image.rsplit("-", 1)[-1].split(":", 1)[0]
    digest = (suffix * 64)[:64]
    return {
        "Architecture": "amd64",
        "Created": "2026-08-12T00:00:00Z",
        "Id": f"sha256:{digest}",
        "Os": "linux",
        "RepoDigests": [f"{repository}@sha256:{digest}"],
        "Size": 1024,
    }


def test_lock_image_requirements_records_immutable_identity() -> None:
    payload = {
        "image_count": 2,
        "lock_state": "requirements_only",
        "images": [
            {"image": "swebench/example-111:latest", "status": "required_not_pulled"},
            {"image": "swebench/example-222:latest", "status": "required_not_pulled"},
        ],
    }

    locked = lock_image_requirements(
        payload,
        inspect_image=_inspect,
        locked_at="2026-08-12T01:02:03Z",
    )

    assert locked["lock_state"] == "frozen_local_images"
    assert locked["locked_at"] == "2026-08-12T01:02:03Z"
    assert {row["status"] for row in locked["images"]} == {"pulled_verified"}
    assert all(row["repo_digest"].startswith("swebench/") for row in locked["images"])
    assert all(row["operating_system"] == "linux" for row in locked["images"])
    assert all(row["architecture"] == "amd64" for row in locked["images"])


def test_lock_image_requirements_rejects_wrong_platform() -> None:
    payload = {
        "image_count": 1,
        "images": [{"image": "swebench/example-111:latest"}],
    }

    def wrong_platform(image: str) -> dict[str, object]:
        inspected = _inspect(image)
        inspected["Architecture"] = "arm64"
        return inspected

    with pytest.raises(ValueError, match="unexpected platform"):
        lock_image_requirements(
            payload,
            inspect_image=wrong_platform,
            locked_at="2026-08-12T01:02:03Z",
        )
