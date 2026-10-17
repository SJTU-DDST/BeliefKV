#!/usr/bin/env python3
"""Observe an owned scheduler for any supported experiment arm."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--arm", choices=("native", "predictive_h2d", "reactive"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def resident_pages(text, page_bytes):
    all_pages, large_pages, large_maps = Counter(), Counter(), []
    for line in text.splitlines():
        pages = {}
        for field in line.split():
            name, separator, value = field.partition("=")
            if separator and name.startswith("N") and name[1:].isdigit():
                pages[name] = int(value)
        all_pages.update(pages)
        size = sum(pages.values()) * page_bytes
        if size >= 256 * 1024**2:
            large_pages.update(pages)
            large_maps.append({
                "raw": line, "observed_resident_bytes": size,
                "bytes_by_node": {key: count * page_bytes for key, count in pages.items()},
            })
    return {
        "observed_resident_bytes_by_node": {
            key: count * page_bytes for key, count in all_pages.items()
        },
        "large_map_threshold_bytes": 256 * 1024**2,
        "large_maps": large_maps,
        "large_map_resident_bytes_by_node": {
            key: count * page_bytes for key, count in large_pages.items()
        },
    }


def main(argv=None):
    args = arguments(argv)
    if args.output.exists():
        raise FileExistsError("preserve an existing placement observation")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ops"))
    import server_reservation as reservation

    run = args.run_root.resolve()
    ready_path = run / args.arm / "server/native_telemetry_ready.json"
    census = json.loads(ready_path.with_name("native_capacity_census.json").read_bytes())
    pid = json.loads(ready_path.read_bytes())["scheduler_pid"]
    identity = reservation.process(pid)
    if (
        not identity or not reservation.alive(identity)
        or census["scheduler_pid"] != pid
        or Path(f"/proc/{pid}").stat().st_uid != os.getuid()
    ):
        raise ValueError("owned scheduler identity cannot be verified")
    text = Path(f"/proc/{pid}/numa_maps").read_text()
    status = Path(f"/proc/{pid}/status").read_text()
    if not reservation.alive(identity):
        raise ValueError("scheduler changed during observation")
    record = {
        "status": "live_owned_scheduler_numa_observation",
        "observed_at_local": reservation.now().isoformat(),
        "run_root": str(run), "scheduler_identity": identity, "arm": args.arm,
        "declared_memory_policy": json.loads(
            (run / "ab_plan.json").read_bytes()
        )["host_numa_memory_policy"],
        "capacity_census": census, "numa_maps_raw": text,
        "numa_maps_sha256": hashlib.sha256(text.encode()).hexdigest(),
        **resident_pages(text, os.sysconf("SC_PAGE_SIZE")),
        "selected_process_status": [
            line for line in status.splitlines() if line.startswith((
                "Name:", "Uid:", "VmRSS:", "VmLck:", "Cpus_allowed_list:", "Mems_allowed_list:",
            ))
        ],
        "operational_helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": (
            "Resident-page snapshot, not bandwidth or pinned-page proof. Large maps "
            "are selected by resident size and are not exact pool identities."
        ),
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(record, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "scheduler_pid": pid,
        "large_map_resident_bytes_by_node": record["large_map_resident_bytes_by_node"],
    }))


if __name__ == "__main__":
    main()
