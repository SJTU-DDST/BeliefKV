"""Allow CUDA registration metadata to fall back without moving local Host KV."""

from contextlib import contextmanager
import ctypes
import ctypes.util
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import threading
import time


NODE_ENV = "BELIEFKV_HOST_REGISTER_NUMA_FALLBACK_NODE"
TARGET = "sglang.srt.mem_cache.pool_host.common._cuda_host_register"
AUDIT_NAME = "host_registration_numa.jsonl"
MPOL_PREFERRED = 1
MPOL_BIND = 2


def configured_node():
    value = os.environ.get(NODE_ENV, "")
    if value == "":
        return None
    if not value.isascii() or not value.isdigit() or str(int(value)) != value:
        raise ValueError("Host registration fallback requires one canonical NUMA node")
    return int(value)


class LinuxMemoryPolicy:
    def __init__(self):
        name = ctypes.util.find_library("numa")
        if name is None:
            raise RuntimeError("libnuma is required for Host registration fallback")
        self.library = ctypes.CDLL(name, use_errno=True)
        self.library.numa_max_possible_node.restype = ctypes.c_int
        maximum = self.library.numa_max_possible_node()
        if maximum < 0:
            raise RuntimeError("NUMA topology is unavailable")
        bits = ctypes.sizeof(ctypes.c_ulong) * 8
        self.mask_type = ctypes.c_ulong * (maximum // bits + 1)
        self.maxnode = ctypes.sizeof(self.mask_type) * 8
        self.bits = bits
        self.library.get_mempolicy.argtypes = [
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
            ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
        ]
        self.library.get_mempolicy.restype = ctypes.c_int
        self.library.set_mempolicy.argtypes = [
            ctypes.c_int, ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong,
        ]
        self.library.set_mempolicy.restype = ctypes.c_int

    @staticmethod
    def _check(result, operation):
        if result != 0:
            error = ctypes.get_errno()
            raise OSError(error, operation + ": " + os.strerror(error))

    def read(self):
        mode, mask = ctypes.c_int(), self.mask_type()
        self._check(self.library.get_mempolicy(
            ctypes.byref(mode), mask, self.maxnode, None, 0,
        ), "get_mempolicy")
        nodes = [
            node for node in range(self.maxnode)
            if mask[node // self.bits] & (1 << (node % self.bits))
        ]
        return {"mode": mode.value, "nodes": nodes}

    def set(self, policy):
        mask = self.mask_type()
        for node in policy["nodes"]:
            if type(node) is not int or not 0 <= node < self.maxnode:
                raise ValueError("NUMA policy node exceeds the actual topology")
            mask[node // self.bits] |= 1 << (node % self.bits)
        self._check(self.library.set_mempolicy(
            policy["mode"], mask, self.maxnode,
        ), "set_mempolicy")


@contextmanager
def registration_policy(node):
    policy = LinuxMemoryPolicy()
    before = policy.read()
    if before != {"mode": MPOL_BIND, "nodes": [node]}:
        raise ValueError("Host registration fallback requires the existing exact local bind")
    state = {"before_policy": before}
    try:
        policy.set({"mode": MPOL_PREFERRED, "nodes": [node]})
        state["during_policy"] = policy.read()
        if state["during_policy"] != {"mode": MPOL_PREFERRED, "nodes": [node]}:
            raise ValueError("Host registration preferred policy was not applied")
        yield state
    finally:
        policy.set(before)
        state["after_policy"] = policy.read()
        if state["after_policy"] != before:
            raise RuntimeError("the original Host NUMA bind was not restored")


@lru_cache(maxsize=1)
def implementation_sha256():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def host_register(original, buffer, *args, **kwargs):
    node = configured_node()
    if node is None:
        return original(buffer, *args, **kwargs)
    directory = Path(os.environ["BELIEFKV_NATIVE_TELEMETRY_DIR"])
    if not directory.is_dir():
        raise ValueError("Host registration fallback requires its startup audit directory")
    started = time.monotonic() * 1000.
    record = {
        "pid": os.getpid(), "thread_id": threading.get_native_id(),
        "worker_start_ticks": Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19],
        "buffer_address": buffer.data_ptr(),
        "buffer_bytes": buffer.numel() * buffer.element_size(),
        "node": node, "implementation_sha256": implementation_sha256(),
        "native_callback": TARGET, "started_monotonic_ms": started,
        "status": "failed",
    }
    state = {}
    try:
        with registration_policy(node) as state:
            result = original(buffer, *args, **kwargs)
        record["status"] = "completed"
        return result
    finally:
        record.update(state, completed_monotonic_ms=time.monotonic() * 1000.)
        with (directory / AUDIT_NAME).open("a") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")


def register():
    if configured_node() is None:
        return
    from sglang.srt.plugins.hook_registry import HookRegistry, HookType

    HookRegistry.register(TARGET, host_register, HookType.AROUND)
