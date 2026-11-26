#!/usr/bin/env python3
"""Record one OpenCode workflow, including the OpenAI-compatible provider stream."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import subprocess
import threading
import time
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from beliefkv.core.events import RuntimeEventKind
from beliefkv.experiments.causal_replay import CausalReplayError, evaluate_causal_plan
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from beliefkv.runtime.event_channel import JsonlRuntimeEventSink
from beliefkv.runtime.opencode_capture import (
    OpenCodeActivations, OpenCodeCaptureError, build_opencode_capture, compile_opencode_replay,
    notification, notification_hash, request_metadata, sse, task_binding,
)


VERSION = "1.19.6"
REVISION = "f4e363c8295fa90a03dddc451882fffe8fbce357"
SOURCE_TREE = "53420ce9bb3f9651291e91a5f6d5a520a0f8b140"
ARCHIVE_SHA256 = "8443113b3f3dfebf8b0fa91c4377e5519a62b9231732d9344a5df001e4a852b3"
BINARY_SHA256 = "aab0a58cd7fc5a1f17495b76c08e79989d331653d8b63156aca2f6e9e36d1173"
PROVIDER = "opencode-beliefkv"
LOOPBACK = "127.0.0.1"


class Evidence:
    def __init__(self, stream):
        self.stream = stream
        self.rows = []
        self.changed = threading.Condition()

    def add(self, kind, **fields):
        with self.changed:
            row = {"kind": kind, "ts_ms": time.monotonic_ns() / 1e6, **fields}
            self.rows.append(row)
            self.stream.write(json.dumps(row, allow_nan=False) + "\n")
            self.stream.flush()
            self.changed.notify_all()
            return row

    def wait(self, predicate, timeout, description):
        with self.changed:
            def ready():
                for row in self.rows:
                    if row["kind"] in {"event_stream_error", "provider_error"}:
                        raise RuntimeError(f"{row['kind']}: {row['error']}")
                return predicate(self.rows)

            result = self.changed.wait_for(ready, timeout)
            if not result:
                raise TimeoutError(f"timed out waiting for {description}")
            return result


@contextmanager
def http_server(handler):
    class Server(ThreadingHTTPServer):
        daemon_threads = False

    with Server((LOOPBACK, 0), handler) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join()


def event_payload(line, data):
    """Assemble SSE data fields. Blank lines delimit events."""
    if line in (b"\n", b"\r\n"):
        if data:
            value = b"\n".join(data)
            data.clear()
            return value
    elif line.startswith(b"data:"):
        value = line[5:].rstrip(b"\r\n")
        data.append(value[1:] if value.startswith(b" ") else value)
    return None


@contextmanager
def watch_downstream(downstream, upstream, disconnected):
    stopping = threading.Event()

    def monitor():
        while not stopping.is_set():
            readable, _, _ = select.select([downstream], [], [], 0.05)
            if not readable:
                continue
            try:
                if downstream.recv(1, socket.MSG_PEEK):
                    return
            except OSError:
                pass
            disconnected.set()
            try:
                upstream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            return

    thread = threading.Thread(target=monitor)
    thread.start()
    try:
        yield
    finally:
        stopping.set()
        thread.join()


def task_result_inputs(body):
    """Record digests of delivered Task results without storing provider prompts."""
    hashes = []
    for message in body["messages"]:
        if message["role"] != "user":
            continue
        content = message.get("content") or []
        texts = [content] if isinstance(content, str) else [
            part["text"] for part in content if part["type"] == "text"
        ]
        for text in texts:
            if digest := notification_hash(text):
                hashes.append(digest)
    return sorted(hashes)


def wait_for_provider_dependencies(evidence, session, body, task_results, timeout):
    tool_results = Counter(
        message["tool_call_id"] for message in body["messages"]
        if message["role"] == "tool" and message.get("tool_call_id")
    )
    if not tool_results and not task_results:
        return

    def satisfied(rows):
        settled_tools, settled_notices = {}, set()
        for row in rows:
            props = sse(row, "message.part.updated")
            if props is None:
                continue
            part = props["part"]
            if part["sessionID"] != session:
                continue
            if part["type"] == "tool" and part["state"]["status"] in {"completed", "error"}:
                settled_tools[part["id"]] = part["callID"]
            elif notification(part):
                settled_notices.add(notification_hash(part["text"]))
        observed_tools = Counter(settled_tools.values())
        return (
            all(observed_tools[call_id] >= count for call_id, count in tool_results.items())
            and settled_notices.issuperset(task_results)
        )

    evidence.wait(satisfied, timeout, "tool results consumed by the provider request")


def proxy_handler(evidence, upstream_url, timeout, native_metadata=None):
    upstream = urlsplit(upstream_url)
    if (
        upstream.scheme not in {"http", "https"} or not upstream.hostname
        or upstream.username or upstream.password or upstream.query or upstream.fragment
    ):
        raise ValueError("upstream must be an HTTP(S) API base URL")
    connection_type = http.client.HTTPSConnection if upstream.scheme == "https" else http.client.HTTPConnection
    key = os.environ.get("OPENCODE_CAPTURE_UPSTREAM_KEY")

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            rid = f"opencode-{uuid4().hex}"
            attempted = False
            disconnected = threading.Event()
            receipt = None
            connection = connection_type(upstream.hostname, upstream.port, timeout=timeout)
            try:
                size = int(self.headers["Content-Length"])
                if not 0 < size <= 32 * 1024 * 1024:
                    raise ValueError("invalid provider request size")
                body = json.loads(self.rfile.read(size))
                session = self.headers["x-opencode-session-id"]
                parent = self.headers["x-opencode-parent-session-id"]
                message = self.headers["x-opencode-request"]
                if not session or not message or body.get("stream") is not True:
                    raise ValueError("missing OpenCode session/user identity or streaming request")
                arrival = evidence.add("provider_arrival", request_id=rid, session_id=session)
                body["stream_options"] = {**(body.get("stream_options") or {}), "include_usage": True}
                if native_metadata:
                    body["stream_options"]["continuous_usage_stats"] = True
                task_results = task_result_inputs(body)
                wait_for_provider_dependencies(evidence, session, body, task_results, timeout)
                with evidence.changed:
                    metadata = native_metadata(session, parent) if native_metadata else None
                    if metadata is not None:
                        if "rid" in body or "beliefkv_metadata" in body:
                            raise ValueError("conflicting native identity in OpenCode provider request")
                        body.update(rid=rid, beliefkv_metadata=metadata)
                    evidence.add(
                        "provider_request", request_id=rid, session_id=session, parent_session_id=parent,
                        user_message_id=message, model=body["model"], native_metadata=metadata,
                        task_result_inputs=task_results, arrival_monotonic_ms=arrival["ts_ms"],
                    )
                attempted = True
                headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
                headers.update({
                    "x-opencode-session-id": session,
                    "x-opencode-request": message,
                })
                if parent:
                    headers["x-opencode-parent-session-id"] = parent
                if key:
                    headers["Authorization"] = f"Bearer {key}"
                connection.connect()
                with watch_downstream(self.connection, connection.sock, disconnected):
                    connection.request(
                        "POST", upstream.path.rstrip("/") + "/chat/completions",
                        json.dumps(body).encode(), headers,
                    )
                    with connection.getresponse() as response:
                        if response.status != 200 or response.headers.get_content_type() != "text/event-stream":
                            raise ValueError(f"upstream streaming chat returned HTTP {response.status}")
                        try:
                            self.send_response(200)
                            self.send_header("Content-Type", "text/event-stream")
                            self.end_headers()
                        except (BrokenPipeError, ConnectionResetError):
                            disconnected.set()
                            return
                        evidence.add(
                            "provider_stream_open", request_id=rid, session_id=session, parent_session_id=parent,
                        )
                        receipt = self.copy_stream(response, rid, disconnected)
            except Exception as error:
                if not disconnected.is_set():
                    evidence.add("provider_error", request_id=rid, error=str(error))
            finally:
                connection.close()
                if attempted and receipt is None:
                    evidence.add(
                        "provider_incomplete", request_id=rid,
                        reason="downstream_disconnected" if disconnected.is_set() else "upstream_eof_or_error",
                    )

        def copy_stream(self, response, rid, disconnected):
            data, usage, finish, response_id = [], None, None, None
            tool_calls = {}
            while line := response.readline():
                payload = event_payload(line, data)
                if payload == b"[DONE]":
                    receipt = evidence.add(
                        "provider_done", request_id=rid, usage=usage, finish_reason=finish,
                        response_id=response_id, tool_call_ids=list(tool_calls),
                    )
                    try:
                        self.wfile.write(line)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return receipt
                if payload is not None:
                    frame = json.loads(payload)
                    response_id = frame.get("id") or response_id
                    usage = frame.get("usage") or usage
                    for choice in frame.get("choices", []):
                        if choice["index"] == 0:
                            finish = choice.get("finish_reason") or finish
                            calls = (choice.get("delta") or {}).get("tool_calls") or ()
                            if calls:
                                # The blank line below publishes this frame to OpenCode.
                                evidence.add(
                                    "provider_tool_delta", request_id=rid, response_id=response_id,
                                    calls=calls, usage=frame.get("usage"),
                                )
                            for call in calls:
                                if call.get("id"):
                                    tool_calls[call["id"]] = None
                try:
                    self.wfile.write(line)
                except (BrokenPipeError, ConnectionResetError):
                    disconnected.set()
                    return None
            return None

    return Proxy


class OpenCodeAPI:
    def __init__(self, port, workspace, timeout):
        self.port = port
        self.query = "?" + urlencode({"directory": str(workspace)})
        self.timeout = timeout

    def request(self, method, path, payload=None):
        connection = http.client.HTTPConnection(LOOPBACK, self.port, timeout=self.timeout)
        try:
            connection.request(
                method, path + self.query, None if payload is None else json.dumps(payload),
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            data = response.read()
            if not 200 <= response.status < 300:
                raise RuntimeError(f"OpenCode {method} {path}: HTTP {response.status}: {data[:500]!r}")
            return json.loads(data) if data else None
        finally:
            connection.close()

    @contextmanager
    def events(self, evidence):
        connection = http.client.HTTPConnection(LOOPBACK, self.port, timeout=self.timeout)
        connection.request("GET", "/event" + self.query, headers={"Accept": "text/event-stream"})
        try:
            response = connection.getresponse()
            if response.status != 200 or response.headers.get_content_type() != "text/event-stream":
                raise RuntimeError("OpenCode /event failed to open an SSE stream")
            connection.sock.settimeout(None)
            stopping = threading.Event()

            def read():
                data = []
                try:
                    while line := response.readline():
                        payload = event_payload(line, data)
                        if payload is not None:
                            event = json.loads(payload)
                            if event["type"] not in {"message.part.delta", "server.heartbeat"}:
                                evidence.add("opencode_sse", event=event)
                    if not stopping.is_set():
                        raise RuntimeError("OpenCode /event reached EOF")
                except Exception as error:
                    if not stopping.is_set():
                        evidence.add("event_stream_error", error=str(error))

            thread = threading.Thread(target=read)
            thread.start()
            try:
                evidence.wait(
                    lambda rows: any(sse(row, "server.connected") is not None for row in rows),
                    self.timeout, "the /event subscription",
                )
                yield
            finally:
                stopping.set()
                try:
                    connection.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                thread.join(timeout=self.timeout)
                response.close()
        finally:
            connection.close()


class NativeIdentity:
    def __init__(self, evidence, timeout):
        self.evidence = evidence
        self.timeout = timeout
        self.epochs = Counter()
        self.activations = OpenCodeActivations()
        self.processed = 0

    def __call__(self, session_id, parent_id):
        def resolve(rows):
            for row in rows[self.processed:]:
                self.activations.observe(row)
            self.processed = len(rows)
            activation = self.activations.current(session_id)
            if activation is None:
                return None
            if activation.session.get("parentID") != parent_id:
                raise OpenCodeCaptureError("provider session/parent headers disagree with Task binding")
            return activation, rows[-1]

        activation, row = self.evidence.wait(resolve, self.timeout, "Task provider identity")
        epoch = self.epochs[session_id]
        self.epochs[session_id] += 1
        return request_metadata(self.activations.root, activation, epoch, row).to_wire()


def verify_binary(binary):
    digest = hashlib.sha256()
    with binary.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != BINARY_SHA256:
        raise ValueError("expected the pinned official Linux x64 baseline opencode executable")
    version = subprocess.check_output(
        [str(binary), "--version"], env={"PATH": os.defpath, "OPENCODE_DISABLE_MODELS_FETCH": "1"},
        text=True, timeout=10,
    ).strip()
    if version != VERSION:
        raise ValueError(f"expected OpenCode {VERSION}, got {version}")


@contextmanager
def opencode_server(args, output, workspace, proxy_port, config):
    binary, model, timeout = args.binary.resolve(), args.model, args.timeout
    state = output / "opencode-state"
    state.mkdir()
    env = {
        **{k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL") if k in os.environ},
        "OPENCODE_TEST_HOME": str(state),
        **{f"XDG_{key}_HOME": str(state / key.lower()) for key in ("CACHE", "DATA", "CONFIG", "STATE")},
        "OPENCODE_DISABLE_MODELS_FETCH": "1",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
        "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
        "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
        "OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS": "1",
        "OPENCODE_CONFIG_CONTENT": json.dumps({
            **config,
            "share": "disabled",
            "autoupdate": False,
            "enabled_providers": [PROVIDER],
            "provider": {PROVIDER: {
                "npm": "@ai-sdk/openai-compatible",
                "options": {"baseURL": f"http://{LOOPBACK}:{proxy_port}/v1", "apiKey": "opencode-local"},
                "models": {model: {
                    "name": model, "tool_call": True,
                    "limit": {"context": args.context_limit, "output": args.output_limit},
                }},
            }},
            "model": f"{PROVIDER}/{model}",
            "small_model": f"{PROVIDER}/{model}",
        }),
    }
    log_path = output / "opencode-server.log"
    with log_path.open("x", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(binary), "serve", "--hostname", LOOPBACK, "--port", "0"],
            cwd=workspace, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and process.poll() is None:
                match = re.search(r"opencode server listening on http://127\.0\.0\.1:(\d+)", log_path.read_text())
                if match:
                    yield OpenCodeAPI(int(match[1]), workspace, timeout)
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError(f"OpenCode server did not start; see {log_path}")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)


def workflow_drained(rows, root):
    """Keep the root open while detached tasks or delivered prompts are active."""
    sessions = {root}
    requests = set()
    active_tools = set()
    busy, idle, assistant_done = {}, {}, {}
    user_messages, answered_users = {}, {}
    seen_users = set()
    provider_closed = set()
    detached, notified, aborted = {}, {}, set()
    for index, row in enumerate(rows):
        if props := sse(row, "session.created"):
            info = props["info"]
            if info.get("parentID") in sessions:
                sessions.add(info["id"])
        if props := sse(row, "session.status"):
            session = props["sessionID"]
            if props["status"]["type"] == "busy":
                busy[session] = index
            elif props["status"]["type"] == "idle":
                idle[session] = index
        if props := sse(row, "message.updated"):
            info = props["info"]
            session = info["sessionID"]
            if info["role"] == "user" and info["id"] not in seen_users:
                seen_users.add(info["id"])
                user_messages[session] = info["id"]
            elif (
                info["role"] == "assistant" and info["time"].get("completed") is not None
                and (info.get("error") or info.get("finish") not in {None, "unknown", "tool-calls"})
            ):
                assistant_done[session] = index
                answered_users[session] = info["parentID"]
        if props := sse(row, "session.error"):
            if props["error"]["name"] == "MessageAbortedError":
                aborted.add(props["sessionID"])
        if props := sse(row, "message.part.updated"):
            part = props["part"]
            if part["type"] == "tool" and part["sessionID"] in sessions:
                if part["state"]["status"] == "running":
                    active_tools.add(part["id"])
                elif part["state"]["status"] in {"completed", "error"}:
                    active_tools.discard(part["id"])
                binding = task_binding(part)
                if binding and binding[1]:
                    detached[binding[0]] = part["sessionID"]
            elif delivery := notification(part):
                notified[delivery[0]] = part["sessionID"]
        if row["kind"] == "provider_request":
            requests.add(row["request_id"])
        elif row["kind"] in {"provider_done", "provider_incomplete"}:
            provider_closed.add(row["request_id"])
    return (
        bool(requests) and requests == provider_closed and not active_tools
        and all(notified.get(child) == parent or child in aborted for child, parent in detached.items())
        and all(
            idle.get(session, -1) > busy.get(session, -1)
            and session in assistant_done
            and answered_users.get(session) == user_messages.get(session)
            for session in sessions
        )
    )


def capture(args, upstream_url, *, fake_mode=None):
    workspace = args.workspace.resolve()
    output = args.output.resolve()
    config = json.loads(args.config.read_text()) if args.config else {}
    if fake_mode:
        config = {
            "compaction": {"auto": False},
            "permission": {"*": "deny", "task": "allow", "read": "allow"},
        }
    output.mkdir(mode=0o700)
    with (output / "opencode-protocol.jsonl").open("x", encoding="utf-8") as stream:
        evidence = Evidence(stream)
        native_identity = NativeIdentity(evidence, args.timeout) if args.native_metadata or fake_mode else None
        with http_server(proxy_handler(evidence, upstream_url, args.timeout, native_identity)) as port:
            with opencode_server(args, output, workspace, port, config) as api:
                with api.events(evidence):
                    root = api.request("POST", "/session", {"title": "BeliefKV capture", "agent": args.agent})
                    session = root["id"]
                    evidence.add("root_session", session=root)
                    text = f"beliefkv-{fake_mode}" if fake_mode else args.prompt.read_bytes().decode("utf-8")
                    api.request("POST", f"/session/{session}/prompt_async", {
                        "agent": args.agent,
                        "model": {"providerID": PROVIDER, "modelID": args.model},
                        "parts": [{"type": "text", "text": text}],
                    })
                    if fake_mode in {"cancel", "cancel-task", "promote"}:
                        wait_for_child = fake_mode in {"cancel-task", "promote"}
                        evidence.wait(
                            lambda rows: any(
                                row["kind"] == "provider_stream_open"
                                and bool(row["parent_session_id"]) == wait_for_child
                                for row in rows
                            ), args.timeout, "an active upstream model stream",
                        )
                    if fake_mode in {"cancel", "cancel-task"}:
                        evidence.add("abort_requested", session_id=session)
                        if api.request("POST", f"/session/{session}/abort") is not True:
                            raise RuntimeError("OpenCode did not acknowledge /abort")
                        evidence.add("abort_ack", session_id=session)
                    if fake_mode == "promote":
                        if api.request("POST", f"/experimental/session/{session}/background") is not True:
                            raise RuntimeError("OpenCode did not promote the running Task")
                        evidence.add("task_promotion_ack", session_id=session)
                    evidence.wait(
                        lambda rows: workflow_drained(rows, session),
                        args.timeout, "OpenCode workflow drain",
                    )
                if not workflow_drained(evidence.rows, session):
                    raise RuntimeError("OpenCode published new work while the capture was closing")
                evidence.add("capture_end", session_id=session)
    return evidence.rows


def export_capture(output, rows, fake_mode):
    clock_domain = local_monotonic_clock_domain()
    capture = build_opencode_capture(rows, clock_domain)
    with JsonlRuntimeEventSink(output / "opencode-events.jsonl") as sink:
        sink.emit_batch(capture.events)
    with (output / "opencode-provider-boundaries.jsonl").open("x", encoding="utf-8") as stream:
        for row in capture.provider_boundaries:
            stream.write(json.dumps(row, allow_nan=False) + "\n")
    summary = {
        "version": VERSION, "revision": REVISION, "source_tree": SOURCE_TREE,
        "archive_sha256": ARCHIVE_SHA256, "binary_sha256": BINARY_SHA256,
        "scenario": fake_mode, "monotonic_clock_domain": clock_domain,
        "protocol_records": dict(Counter(row["kind"] for row in rows)),
        "runtime_events": dict(Counter(event.kind.value for event in capture.events)),
        "provider_boundaries": len(capture.provider_boundaries),
        "max_provider_arrival_to_dispatch_ms": max(
            row["ts_ms"] - row["arrival_monotonic_ms"] for row in rows if row["kind"] == "provider_request"
        ),
        "native_token_evidence": False,
    }
    if fake_mode and not any(event.kind is RuntimeEventKind.CALL_CENSORED for event in capture.events):
        try:
            plan = compile_opencode_replay(capture.events, capture.provider_boundaries)
            fixture = evaluate_causal_plan(
                plan, {rid: 10. for rid in plan.request_usage},
                request_progress_ms={
                    (rid, count): 5. for rid, counts in plan.output_boundaries().items() for count in counts
                },
            )
            summary["cpu_replay"] = {
                **plan.summary(), "native_completion_boundaries_verified": False,
                "completion_evidence": "fake_model_provider_done_at_bridge",
                "scope": "CPU dependency replay using scripted provider counts and a 10 ms service fixture.",
                "workflow_elapsed_ms": fixture["workflow_elapsed_ms"],
            }
        except CausalReplayError as error:
            summary["cpu_replay_error"] = str(error)
    elif any(event.kind is RuntimeEventKind.CALL_CENSORED for event in capture.events):
        summary["replay_status"] = "censored_provider_request"
    with (output / "opencode-summary.json").open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", default="beliefkv-cpu")
    parser.add_argument("--context-limit", type=int, default=32768)
    parser.add_argument("--output-limit", type=int, default=1024)
    parser.add_argument("--agent", default="build")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--prompt", type=Path)
    parser.add_argument("--native-metadata", action="store_true", help="forward SGLang rid and beliefkv_metadata")
    parser.add_argument("--timeout", type=float, default=30)
    endpoint = parser.add_mutually_exclusive_group(required=True)
    endpoint.add_argument("--upstream", help="OpenAI-compatible API base URL, including /v1 when required")
    endpoint.add_argument("--fake", choices=[
        "foreground", "background", "mixed", "promote", "cancel", "cancel-task", "reuse", "stream-tool",
    ])
    args = parser.parse_args()
    if args.upstream and args.prompt is None:
        parser.error("--upstream requires --prompt")
    if not args.workspace.is_dir() or not 0 < args.timeout < float("inf"):
        parser.error("--workspace must be a directory and --timeout must be finite and positive")
    if not 0 < args.output_limit <= args.context_limit:
        parser.error("require 0 < --output-limit <= --context-limit")
    verify_binary(args.binary.resolve())
    if args.fake:
        from scripts.opencode_fake_model import fake_handler

        with http_server(fake_handler(args.fake)) as port:
            rows = capture(args, f"http://{LOOPBACK}:{port}/v1", fake_mode=args.fake)
    else:
        rows = capture(args, args.upstream)
    summary = export_capture(args.output, rows, args.fake)
    print(json.dumps(summary, indent=2, allow_nan=False))
    if "cpu_replay_error" in summary:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
