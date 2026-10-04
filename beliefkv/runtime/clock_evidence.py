"""Identify a shared Linux monotonic clock without assuming a common host."""

from functools import lru_cache
import hashlib
import os
from pathlib import Path
import time
from uuid import UUID


SEMANTIC_FRAME_INTERVAL_MS = 100.


@lru_cache(maxsize=1)
def local_monotonic_clock_domain() -> str | None:
    implementation = time.get_clock_info("monotonic").implementation
    if implementation != "clock_gettime(CLOCK_MONOTONIC)":
        return None
    try:
        boot = str(UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip()))
        namespace = os.readlink("/proc/self/ns/time")
    except (OSError, ValueError):
        return None
    return hashlib.sha256(f"{boot}:{namespace}:{implementation}".encode()).hexdigest()
