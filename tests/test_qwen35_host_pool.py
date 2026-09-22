"""CPU-only NUMA host-pool sizing and native launch checks."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from scripts.preflight_qwen35_host_pool import GB, GIB, check_host_pool


ROOT = Path(__file__).resolve().parents[1]
LAUNCH = ROOT / "scripts/launch_qwen35_native_v0520.sh"


def _node(
    tmp_path: Path, *, total_kb: int, free_kb: int,
    inactive_file_kb: int = 0, active_file_kb: int = 0,
    reclaimable_kb: int = 0,
) -> Path:
    node = tmp_path / "node1"
    node.mkdir()
    (node / "meminfo").write_text(
        f"Node 1 MemTotal: {total_kb} kB\nNode 1 MemFree: {free_kb} kB\n"
        f"Node 1 Inactive(file): {inactive_file_kb} kB\n"
        f"Node 1 Active(file): {active_file_kb} kB\n"
        f"Node 1 FilePages: {inactive_file_kb + active_file_kb} kB\n"
        f"Node 1 SReclaimable: {reclaimable_kb} kB\n"
    )
    return tmp_path


def test_two_decimal_gb_pools_fit_one_node_with_reserve(tmp_path: Path) -> None:
    required = 200 * GB + 8 * GIB
    root = _node(tmp_path, total_kb=required // 1024 + 1, free_kb=required // 1024 + 1)
    budget, free = check_host_pool(1, 200, node_root=root)
    assert budget == 200 * GB
    assert budget / GIB == pytest.approx(186.2645, rel=1e-5)
    assert free > required


@pytest.mark.parametrize(
    "total,free", [(250 * GIB, 10 * GIB), (180 * GIB, 180 * GIB)]
)
def test_node_must_have_total_and_available_memory(tmp_path: Path, total: int, free: int) -> None:
    root = _node(tmp_path, total_kb=total // 1024, free_kb=free // 1024)
    with pytest.raises(ValueError, match="NUMA node 1 needs"):
        check_host_pool(1, 200, node_root=root)


def test_clean_inactive_file_cache_can_make_pool_fit(tmp_path: Path) -> None:
    root = _node(
        tmp_path, total_kb=250 * GIB // 1024,
        free_kb=9 * GIB // 1024,
        inactive_file_kb=188 * GIB // 1024,
        active_file_kb=35 * GIB // 1024,
        reclaimable_kb=4 * GIB // 1024,
    )
    _, estimate = check_host_pool(1, 200, node_root=root)
    assert estimate > 190 * GIB


def test_dirty_and_shared_pages_cannot_count_as_reclaimable(tmp_path: Path) -> None:
    root = _node(
        tmp_path, total_kb=250 * GIB // 1024,
        free_kb=9 * GIB // 1024,
        inactive_file_kb=198 * GIB // 1024,
    )
    path = root / "node1/meminfo"
    with path.open("a") as stream:
        stream.write(
            f"Node 1 Shmem: {100 * GIB // 1024} kB\n"
            f"Node 1 Dirty: {100 * GIB // 1024} kB\n"
        )
    with pytest.raises(ValueError, match="NUMA node 1 needs"):
        check_host_pool(1, 200, node_root=root)


def test_dirty_active_file_cache_cannot_receive_reclaim_credit(tmp_path: Path) -> None:
    root = _node(
        tmp_path, total_kb=250 * GIB // 1024,
        free_kb=9 * GIB // 1024,
        inactive_file_kb=180 * GIB // 1024,
        active_file_kb=56 * GIB // 1024,
    )
    with (root / "node1/meminfo").open("a") as stream:
        stream.write(f"Node 1 Dirty: {56 * GIB // 1024} kB\n")
    with pytest.raises(ValueError, match="NUMA node 1 needs"):
        check_host_pool(1, 200, node_root=root)

@pytest.mark.parametrize("node,size", [(-1, 200), (1, 0), (1, 201), (1, True)])
def test_invalid_node_or_size_fails_closed(tmp_path: Path, node: int, size: int) -> None:
    with pytest.raises(ValueError):
        check_host_pool(node, size, node_root=tmp_path)


def test_missing_node_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unavailable"):
        check_host_pool(1, 200, node_root=tmp_path)


def _stub(tmp_path: Path) -> dict[str, str]:
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}")
    cuda = tmp_path / "cuda"
    (cuda / "bin").mkdir(parents=True)
    (cuda / "lib").mkdir()
    (cuda / "bin/nvcc").write_text("")
    (cuda / "bin/nvcc").chmod(0o755)
    (cuda / "lib/libcudart.so.13").write_text("")
    (cuda / "lib/libcudart.so").symlink_to("libcudart.so.13")
    python = tmp_path / "fake-python"
    python.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  */preflight_qwen35_host_pool.py)\n"
        "    printf '%s\\n' \"$@\" > \"$PREFLIGHT_ARGS_LOG\"\n"
        "    if [ \"${BLOCK_PREFLIGHT:-0}\" = '1' ]; then exit 2; fi\n"
        "    exit 0 ;;\n"
        "esac\n"
        "if [ \"$1\" != '-c' ]; then\n"
        "  printf '%s\\n' \"$@\" > \"$PYTHON_ARGS_LOG\"\n"
        "fi\n"
    )
    python.chmod(0o755)
    numactl = tmp_path / "numactl"
    numactl.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$@\" > \"$NUMACTL_ARGS_LOG\"\n"
        "printf '%s' \"${BELIEFKV_NATIVE_TELEMETRY_DIR:-}\" > \"$TELEMETRY_LOG\"\n"
    )
    numactl.chmod(0o755)
    git = tmp_path / "git"
    git.write_text(
        "#!/bin/sh\n"
        "if [ \"$3\" = 'rev-parse' ]; then\n"
        "  printf '%s\\n' '94602c9c2b7cbdb8efd5c52802dac6a1c180089e'\n"
        "fi\n"
    )
    git.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "MODEL_PATH": str(model),
        "PYTHON": str(python),
        "CUDA_HOME": str(cuda),
        "PYTHON_ARGS_LOG": str(tmp_path / "python-args"),
        "PREFLIGHT_ARGS_LOG": str(tmp_path / "preflight-args"),
        "NUMACTL_ARGS_LOG": str(tmp_path / "numactl-args"),
        "TELEMETRY_LOG": str(tmp_path / "telemetry"),
        "HICACHE_SIZE_GB": "200",
        "HOST_NUMA_NODE": "1",
        "SGLANG_SOURCE_CHECKOUT": "",
    }


def test_launch_binds_one_node_and_passes_telemetry_dir(tmp_path: Path) -> None:
    env = _stub(tmp_path)
    checkout = tmp_path / "sglang"
    scheduler = checkout / "python/sglang/srt/managers/scheduler.py"
    scheduler.parent.mkdir(parents=True)
    scheduler.write_text("")
    env["SGLANG_SOURCE_CHECKOUT"] = str(checkout)
    telemetry = tmp_path / "telemetry-dir"
    telemetry.mkdir()
    env["BELIEFKV_NATIVE_TELEMETRY_DIR"] = str(telemetry)
    result = subprocess.run(["bash", str(LAUNCH)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    args = (tmp_path / "numactl-args").read_text().splitlines()
    assert args[:2] == ["--cpunodebind=1", "--membind=1"]
    assert args[2:5] == [env["PYTHON"], "-m", "sglang.launch_server"]
    assert args[args.index("--hicache-size") + 1] == "200"
    assert (tmp_path / "preflight-args").read_text().splitlines()[-4:] == [
        "--node", "1", "--size-gb", "200",
    ]
    assert (tmp_path / "telemetry").read_text() == str(telemetry)


def test_launch_stops_when_numa_preflight_fails(tmp_path: Path) -> None:
    env = {**_stub(tmp_path), "BLOCK_PREFLIGHT": "1"}
    result = subprocess.run(["bash", str(LAUNCH)], env=env, capture_output=True)
    assert result.returncode == 2
    assert (tmp_path / "preflight-args").exists()
    assert not (tmp_path / "numactl-args").exists()


def test_no_host_smoke_does_not_bind_or_preflight(tmp_path: Path) -> None:
    env = {**_stub(tmp_path), "HICACHE_SIZE_GB": "0"}
    result = subprocess.run(["bash", str(LAUNCH)], env=env, capture_output=True)
    assert result.returncode == 0
    assert not (tmp_path / "preflight-args").exists()
    assert not (tmp_path / "numactl-args").exists()
    assert "--hicache-size" not in (tmp_path / "python-args").read_text()


@pytest.mark.parametrize(
    "env_override,extra_args,error",
    [
        ({"HICACHE_SIZE_GB": "201"}, [], "HICACHE_SIZE_GB"),
        ({"HICACHE_SIZE_GB": "99999999999999999999"}, [], "HICACHE_SIZE_GB"),
        ({}, ["--hicache-size", "150"], "capacity overrides"),
        ({"HICACHE_SIZE_GB": "0"}, ["--hicache-size=100"], "capacity overrides"),
        ({"HICACHE_SIZE_GB": "0"}, ["--enable-hierarchical-cache"], "NUMA binding"),
        ({"BELIEFKV_NATIVE_TELEMETRY_DIR": "/nonexistent/beliefkv-telemetry"}, [], "SGLANG_SOURCE_CHECKOUT"),
        ({"BELIEFKV_NATIVE_TELEMETRY_DIR": ""}, [], "SGLANG_SOURCE_CHECKOUT"),
        (
            {
                "SGLANG_SOURCE_CHECKOUT": "/nonexistent/sglang",
                "BELIEFKV_NATIVE_TELEMETRY_DIR": "/nonexistent/beliefkv-telemetry",
            },
            [],
            "Telemetry directory",
        ),
    ],
)
def test_launch_rejects_unsafe_configuration(
    tmp_path: Path, env_override: dict[str, str], extra_args: list[str], error: str
) -> None:
    env = {**_stub(tmp_path), **env_override}
    result = subprocess.run(
        ["bash", str(LAUNCH), *extra_args], env=env, capture_output=True, text=True
    )
    assert result.returncode == 2
    assert error in result.stderr
    assert not (tmp_path / "numactl-args").exists()
