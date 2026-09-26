import ast
import hashlib
import json
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import threading
import time

import numpy as np
import pytest

from beliefkv.experiments.child_hidden_live_shadow import (
    FRAME_BYTES, HEADER, audit_snapshot, decode_snapshot, receive,
)
from scripts.audit_child_hidden_live_delivery import audit


def _packet(rid="beliefkv:child", tokens=256, sent_ns=None):
    if sent_ns is None:
        sent_ns = time.monotonic_ns()
    return HEADER.pack(b"BKVH", 1, rid.encode(), tokens, sent_ns) + (
        b"\x00\x3c" * 2048
    )


def test_live_shadow_receives_without_persisting_vectors_or_plain_request_id(
    tmp_path,
):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    output = tmp_path / "audit.jsonl"
    path = private / "hidden.sock"
    result = []
    observed = []
    receiver = threading.Thread(
        target=lambda: result.append(receive(
            path, output, max_events=1,
            on_snapshot=lambda snapshot, received_ns: observed.append((
                snapshot.request_id, snapshot.token_count,
                len(snapshot.vector), struct.unpack_from(
                    "<e", snapshot.vector,
                )[0], received_ns >= snapshot.sent_ns,
            )),
        )),
    )
    receiver.start()
    try:
        for _ in range(100):
            if path.exists():
                break
            time.sleep(.01)
        assert path.exists()
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
            sent = sender.sendto(_packet(), str(path))
        assert sent == FRAME_BYTES
        receiver.join(timeout=2)
        assert not receiver.is_alive()
        assert result == [1]
        assert observed == [("beliefkv:child", 256, 4096, 1.0, True)]
        row = json.loads(output.read_text(encoding="utf-8"))
        assert row["request_sha256"] == hashlib.sha256(
            b"beliefkv:child"
        ).hexdigest()
        assert row["token_count"] == 256
        assert row["received_monotonic_ns"] >= 0
        assert 0 <= row["transport_age_ms"] < 1000
        assert "beliefkv:child" not in output.read_text(encoding="utf-8")
        assert not path.exists()
    finally:
        if receiver.is_alive():
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
                sender.sendto(_packet(), str(path))
            receiver.join(timeout=2)


def test_live_shadow_rejects_invalid_or_future_frames(tmp_path):
    packet = _packet()
    assert decode_snapshot(packet).token_count == 256
    for invalid in (
        packet[:-1], b"WRNG" + packet[4:], _packet(tokens=255),
        _packet(rid="nul\x00bad"),
    ):
        with pytest.raises(ValueError):
            decode_snapshot(invalid)
    with pytest.raises(ValueError, match="monotonic clocks"):
        audit_snapshot(
            _packet(sent_ns=time.monotonic_ns() + 1_000_000_000),
            received_ns=time.monotonic_ns(),
        )
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="private owned directory"):
        receive(public / "hidden.sock", tmp_path / "unused.jsonl", max_events=1)
    assert not (tmp_path / "unused.jsonl").exists()


def test_sglang_shadow_sender_patch_matches_receiver_protocol(tmp_path):
    root = Path(__file__).resolve().parents[1]
    original = (
        root / "experiments/shadow/sglang-hidden-stage-20260926/python/sglang"
        / "srt/entrypoints/openai/serving_chat.py"
    )
    if not original.is_file():
        pytest.skip("optional patched SGLang checkout is unavailable")
    target = (
        tmp_path / "python/sglang/srt/entrypoints/openai/serving_chat.py"
    )
    target.parent.mkdir(parents=True)
    shutil.copyfile(original, target)
    patch = root / "patches/sglang-v0.5.20-child-hidden-live-shadow.patch"
    if "def _send_child_hidden_live(" in target.read_text(encoding="utf-8"):
        installed = target.read_text(encoding="utf-8").replace(
            'logger.debug("Child hidden live sent=',
            'logger.info("Child hidden live sent=',
        )
        target.write_text(installed, encoding="utf-8")
        subprocess.run(
            ["git", "apply", "--reverse", str(patch)], cwd=tmp_path,
            check=True,
        )
    subprocess.run(["git", "apply", str(patch)], cwd=tmp_path, check=True)
    syntax = ast.parse(target.read_text(encoding="utf-8"))
    sender = next(
        node for node in syntax.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_send_child_hidden_live"
    )
    namespace = {"np": np, "struct": struct, "socket": socket}
    exec(compile(ast.Module(body=[sender], type_ignores=[]), str(target), "exec"),
         namespace)

    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    socket_path = private / "hidden.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind(str(socket_path))
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender_socket:
            sender_socket.setblocking(False)
            sent_ns = time.monotonic_ns()
            assert namespace["_send_child_hidden_live"](
                sender_socket, str(socket_path), "beliefkv:child", 256, sent_ns,
                [1.] * 2048,
            )
            packet = receiver.recv(FRAME_BYTES + 1)
            sample = decode_snapshot(packet)
            assert (sample.request_id, sample.token_count, sample.sent_ns) == (
                "beliefkv:child", 256, sent_ns,
            )
            assert struct.unpack_from("<e", sample.vector)[0] == 1.
            assert not namespace["_send_child_hidden_live"](
                sender_socket, str(private / "absent.sock"), "beliefkv:child",
                288, time.monotonic_ns(), [1.] * 2048,
            )


def test_live_delivery_audit_uses_receiver_clock_and_strict_join_identity(
    tmp_path,
):
    traces = tmp_path / "traces"
    traces.mkdir()
    workflows = tmp_path / "workflows" / "one"
    workflows.mkdir(parents=True)
    rid = "beliefkv:child"
    digest = hashlib.sha256(rid.encode()).hexdigest()
    np.savez(
        traces / f"{digest}.npz",
        rid=rid, token_counts=np.asarray([32, 64]),
        arrival_ns=np.asarray([1_000_000_000, 1_100_000_000]),
        finish_ns=np.int64(1_800_000_000),
    )
    other_rid = "beliefkv:other-batch"
    np.savez(
        traces / f"{hashlib.sha256(other_rid.encode()).hexdigest()}.npz",
        rid=other_rid, token_counts=np.asarray([32]),
        arrival_ns=np.asarray([1_000_000_000]),
        finish_ns=np.int64(1_800_000_000),
    )
    delivery = tmp_path / "live.jsonl"
    delivery.write_text(json.dumps({
        "request_sha256": digest,
        "token_count": 32,
        "received_monotonic_ns": 1_000_250_000,
        "transport_age_ms": 0.25,
    }) + "\n", encoding="utf-8")
    events = [
        {"kind": "spawn", "target_invocation_id": "child"},
        {
            "kind": "llm_result", "invocation_id": "child", "ts_ms": 1800,
            "attributes": {
                "request_id": rid, "finish_reason": "stop", "output_chars": 1,
            },
        },
        {"kind": "return", "invocation_id": "child", "ts_ms": 2000},
        {
            "kind": "join_create", "join_id": "join",
            "member_invocation_ids": ["child"],
        },
        {"kind": "join_satisfied", "join_id": "join", "ts_ms": 2000},
    ]
    (workflows / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    report = audit(delivery, traces, workflows.parent)
    assert report["matched_retained_samples"] == 1
    assert report["retained_samples_missing_delivery"] == 1
    assert report["receipts_with_explicit_timestamp"] == 1
    assert report["child_return_first_delivery_lead_ms"]["p50"] == 999.75
    assert report["join_last_child_last_delivery_lead_ms"]["p50"] == 999.75
