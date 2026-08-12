from __future__ import annotations

import re


_INSTANCE_ID = re.compile(
    r"^(?P<organization>[A-Za-z0-9_.-]+)__(?P<repository>[A-Za-z0-9_.-]+)-"
    r"(?P<issue>[A-Za-z0-9_.-]+)$"
)


def swebench_instance_image(
    instance_id: str,
    *,
    namespace: str = "swebench",
    architecture: str = "x86_64",
    tag: str = "latest",
) -> str:
    """Return the canonical SWE-bench per-instance evaluation image key."""

    match = _INSTANCE_ID.fullmatch(instance_id)
    if match is None:
        raise ValueError(f"invalid SWE-bench instance ID: {instance_id!r}")
    organization = match.group("organization").lower()
    repository = match.group("repository").lower()
    issue = match.group("issue").lower()
    return (
        f"{namespace}/sweb.eval.{architecture}.{organization}_1776_"
        f"{repository}-{issue}:{tag}"
    )
