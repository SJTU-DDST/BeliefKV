"""Estimate reclaimable memory for the selected Host pool NUMA policy."""

from __future__ import annotations

import argparse
from pathlib import Path


GB = 1_000_000_000  # SGLang --hicache-size is total decimal GB, split across pools.
GIB = 1024**3
MAX_POOL_GB = 200
MAX_INTERLEAVED_POOL_GB = 400
RESERVE_GIB = 8


def interleave_nodes(value: str, node: int) -> tuple[int, ...]:
    if type(value) is not str or not value:
        raise ValueError("use bind or preferred unless explicit canonical interleave NUMA nodes are declared")
    parts = value.split(",")
    if any(not part.isascii() or not part.isdigit() or str(int(part)) != part for part in parts):
        raise ValueError("interleave requires explicit canonical NUMA nodes")
    nodes = tuple(map(int, parts))
    if len(nodes) < 2 or tuple(sorted(set(nodes))) != nodes or node not in nodes:
        raise ValueError("interleave nodes must be distinct, sorted and include the CPU node")
    return nodes


def _node_capacity(
    node: int, node_root: Path, *, reclaim_policy: str = "conservative",
) -> tuple[int, int]:
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
    # primary reclaim source. Explicit clean_file also prices clean active pages;
    # strict allocation and actual residency verification remain required.
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
        + (active_file if reclaim_policy == "clean_file" else active_file // 4)
        + metrics.get("SReclaimable:", 0) // 2
    )
    return metrics["MemTotal:"], available_estimate


def check_host_pool(
    node: int, size_gb: int, *,
    node_root: Path = Path("/sys/devices/system/node"),
    memory_policy: str = "bind",
    reclaim_policy: str = "conservative",
    memory_nodes: str = "",
    placement_check: str = "strict",
) -> tuple[int, int]:
    if type(node) is not int or node < 0:
        raise ValueError("HOST_NUMA_NODE must be a nonnegative integer")
    maximum = MAX_INTERLEAVED_POOL_GB if memory_policy == "interleave" else MAX_POOL_GB
    if type(size_gb) is not int or not 0 < size_gb <= maximum:
        if memory_policy == "interleave":
            raise ValueError("interleaved HICACHE_SIZE_GB must be between 1 and 400 in total")
        raise ValueError("HICACHE_SIZE_GB must be between 1 and 200 in total")
    if memory_policy not in ("bind", "preferred", "interleave"):
        raise ValueError("HOST_NUMA_MEMORY_POLICY must be bind or preferred, or explicit interleave")
    if placement_check not in ("strict", "diagnostic") or (
        placement_check == "diagnostic" and memory_policy != "interleave"
    ):
        raise ValueError("diagnostic placement requires explicit interleave")
    if memory_policy == "interleave":
        nodes = interleave_nodes(memory_nodes, node)
    elif memory_nodes:
        raise ValueError("explicit NUMA memory nodes require interleave")
    if reclaim_policy not in ("conservative", "clean_file") or (
        reclaim_policy == "clean_file" and memory_policy not in ("bind", "interleave")
    ):
        raise ValueError("clean_file reclaim estimate requires strict bind or explicit interleave allocation")
    required = size_gb * GB + RESERVE_GIB * GIB
    if memory_policy == "interleave":
        required_per_node = (required + len(nodes) - 1) // len(nodes)
        available = 0
        for owner in nodes:
            total, estimate = _node_capacity(owner, node_root, reclaim_policy=reclaim_policy)
            if placement_check == "strict" and min(total, estimate) < required_per_node:
                raise ValueError(
                    f"interleave NUMA node {owner} needs {required_per_node / GIB:.2f} GiB; "
                    f"only {estimate / GIB:.2f} GiB estimated reclaimable"
                )
            available += min(total, estimate)
        if available < required:
            raise ValueError("the selected NUMA nodes lack the total Host pool budget and reserve")
        return size_gb * GB, available
    total, available_estimate = _node_capacity(node, node_root, reclaim_policy=reclaim_policy)
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
            f"with memory policy {memory_policy} and reclaim estimate {reclaim_policy}"
        )
    return size_gb * GB, available_estimate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", type=int, required=True)
    parser.add_argument("--size-gb", type=int, required=True)
    parser.add_argument("--memory-policy", choices=("bind", "preferred", "interleave"), default="bind")
    parser.add_argument("--memory-nodes", default="")
    parser.add_argument("--reclaim-policy", choices=("conservative", "clean_file"), default="conservative")
    parser.add_argument("--placement-check", choices=("strict", "diagnostic"), default="strict")
    args = parser.parse_args()
    try:
        pool_bytes, available = check_host_pool(
            args.node, args.size_gb, memory_policy=args.memory_policy,
            reclaim_policy=args.reclaim_policy, memory_nodes=args.memory_nodes,
            placement_check=args.placement_check,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(
        f"NUMA node {args.node} policy={args.memory_policy}: "
        f"combined FULL+MAMBA host pool budget "
        f"{pool_bytes / GIB:.2f} GiB, estimated available "
        f"{available / GIB:.2f} GiB "
        f"({'actual page placement must be observed' if args.memory_policy in ('preferred', 'interleave') else 'NUMA-local allocation still required'})"
    )


if __name__ == "__main__":
    main()
