"""Estimate reclaimable memory for the selected Host pool NUMA policy."""

from __future__ import annotations

import argparse
from pathlib import Path


GB = 1_000_000_000  # SGLang --hicache-size is total decimal GB, split across pools.
GIB = 1024**3
MAX_POOL_GB = 200
RESERVE_GIB = 8


def _node_capacity(node: int, node_root: Path) -> tuple[int, int]:
    path = node_root / f"node{node}" / "meminfo"
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        raise ValueError(f"NUMA node {node} meminfo unavailable: {exc}") from exc
    metrics: dict[str, int] = {}
    for line in lines:
        fields = line.split()
        if len(fields) == 5 and fields[:2] == ["Node", str(node)] and fields[4] == "kB":
            if fields[2] in (
                "MemTotal:", "MemFree:", "Inactive(file):", "Active(file):",
                "SReclaimable:", "FilePages:", "Shmem:", "Dirty:", "Writeback:",
            ):
                metrics[fields[2]] = int(fields[3]) * 1024
    if not {"MemTotal:", "MemFree:"}.issubset(metrics):
        raise ValueError(f"NUMA node {node} has incomplete meminfo")
    free = metrics["MemFree:"]
    # Avoid double-counting file pages: clean inactive file cache is the
    # primary reclaim source; active cache and slab receive conservative credit.
    clean_file = max(
        0,
        metrics.get("FilePages:", 0)
        - metrics.get("Shmem:", 0)
        - metrics.get("Dirty:", 0)
        - metrics.get("Writeback:", 0),
    )
    inactive_file = min(metrics.get("Inactive(file):", 0), clean_file)
    active_file = min(
        metrics.get("Active(file):", 0), clean_file - inactive_file
    )
    available_estimate = (
        free + inactive_file
        + active_file // 4
        + metrics.get("SReclaimable:", 0) // 2
    )
    return metrics["MemTotal:"], available_estimate


def check_host_pool(
    node: int, size_gb: int, *,
    node_root: Path = Path("/sys/devices/system/node"),
    memory_policy: str = "bind",
) -> tuple[int, int]:
    if type(node) is not int or node < 0:
        raise ValueError("HOST_NUMA_NODE must be a nonnegative integer")
    if type(size_gb) is not int or not 0 < size_gb <= MAX_POOL_GB:
        raise ValueError("HICACHE_SIZE_GB must be between 1 and 200 in total")
    if memory_policy not in ("bind", "preferred"):
        raise ValueError("HOST_NUMA_MEMORY_POLICY must be bind or preferred")
    total, available_estimate = _node_capacity(node, node_root)
    required = size_gb * GB + RESERVE_GIB * GIB
    if memory_policy == "preferred":
        for directory in node_root.glob("node[0-9]*"):
            suffix = directory.name[4:]
            if suffix.isdigit() and int(suffix) != node:
                other_total, other_available = _node_capacity(int(suffix), node_root)
                total += other_total
                available_estimate += other_available
    if total < required or available_estimate < required:
        raise ValueError(
            f"NUMA node {node} needs at least {required / GIB:.2f} GiB available "
            f"for {size_gb} GB total Host pools plus {RESERVE_GIB} GiB reserve; "
            f"currently {available_estimate / GIB:.2f} GiB estimated reclaimable "
            f"with memory policy {memory_policy}"
        )
    return size_gb * GB, available_estimate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", type=int, required=True)
    parser.add_argument("--size-gb", type=int, required=True)
    parser.add_argument("--memory-policy", choices=("bind", "preferred"), default="bind")
    args = parser.parse_args()
    try:
        pool_bytes, available = check_host_pool(
            args.node, args.size_gb, memory_policy=args.memory_policy,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(
        f"NUMA node {args.node} policy={args.memory_policy}: "
        f"combined FULL+MAMBA host pool budget "
        f"{pool_bytes / GIB:.2f} GiB, estimated available "
        f"{available / GIB:.2f} GiB "
        f"({'actual page placement must be observed' if args.memory_policy == 'preferred' else 'NUMA-local allocation still required'})"
    )


if __name__ == "__main__":
    main()
