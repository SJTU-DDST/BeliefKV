"""Local-only, read-only transport audit for opt-in child hidden snapshots.

The receive path intentionally discards vectors. It cannot authorize actions or
train a model; it measures whether a live signal reaches the host in time.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import struct
import time
from typing import Callable


HEADER = struct.Struct("!4sB64sIQ")
MAGIC = b"BKVH"
VERSION = 1
VECTOR_BYTES = 2048 * 2
FRAME_BYTES = HEADER.size + VECTOR_BYTES


@dataclass(frozen=True)
class Snapshot:
    request_id: str
    token_count: int
    sent_ns: int
    vector: memoryview = field(repr=False, compare=False)


def decode_snapshot(packet: bytes) -> Snapshot:
    if len(packet) != FRAME_BYTES:
        raise ValueError("invalid child hidden frame length")
    magic, version, raw_id, tokens, sent_ns = HEADER.unpack_from(packet)
    if magic != MAGIC or version != VERSION:
        raise ValueError("invalid child hidden frame version")
    rid_bytes = raw_id.rstrip(b"\0")
    if not rid_bytes or b"\0" in rid_bytes:
        raise ValueError("invalid child hidden request identity")
    try:
        rid = rid_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid child hidden request identity") from exc
    if tokens < 32 or tokens % 32 or sent_ns <= 0:
        raise ValueError("invalid child hidden sample boundary")
    return Snapshot(rid, tokens, sent_ns, memoryview(packet)[HEADER.size:])


def audit_snapshot(packet: bytes, *, received_ns: int) -> dict:
    sample = decode_snapshot(packet)
    latency_ms = (received_ns - sample.sent_ns) / 1e6
    if latency_ms < 0:
        raise ValueError("hidden sample crossed incompatible monotonic clocks")
    return {
        "request_sha256": hashlib.sha256(
            sample.request_id.encode("ascii")
        ).hexdigest(),
        "token_count": sample.token_count,
        "received_monotonic_ns": received_ns,
        "transport_age_ms": latency_ms,
    }


def receive(
    socket_path: Path, output: Path, *, max_events: int | None = None,
    on_snapshot: Callable[[Snapshot, int], None] | None = None,
) -> int:
    if max_events is not None and max_events < 1:
        raise ValueError("max_events must be positive")
    parent = socket_path.parent
    parent_stat = parent.stat()
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != os.getuid()
        or parent_stat.st_mode & 0o077
    ):
        raise ValueError("hidden live socket requires a private owned directory")
    if socket_path.exists():
        raise FileExistsError(socket_path)
    old_umask = os.umask(0o077)
    try:
        stream = output.open("x", encoding="utf-8")
    finally:
        os.umask(old_umask)
    accepted = 0
    with stream, socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        receiver.bind(str(socket_path))
        own_inode = socket_path.stat().st_ino
        try:
            while max_events is None or accepted < max_events:
                packet, _, flags, _ = receiver.recvmsg(FRAME_BYTES + 1)
                received_ns = time.monotonic_ns()
                if flags & socket.MSG_TRUNC:
                    continue
                try:
                    row = audit_snapshot(packet, received_ns=received_ns)
                except ValueError:
                    continue
                if on_snapshot is not None:
                    on_snapshot(decode_snapshot(packet), received_ns)
                stream.write(json.dumps(row, separators=(",", ":")) + "\n")
                stream.flush()
                accepted += 1
        finally:
            if socket_path.exists() and socket_path.stat().st_ino == own_inode:
                socket_path.unlink()
    return accepted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-events", type=int)
    args = parser.parse_args()
    print(receive(args.socket_path, args.output, max_events=args.max_events))


if __name__ == "__main__":
    main()
