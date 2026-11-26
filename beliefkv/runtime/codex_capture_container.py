"""Run Codex in the task's container with only a local model connection."""

from __future__ import annotations

import contextlib
from pathlib import Path
import os
import re
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlsplit
from uuid import uuid4


class Relay(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            upstream = self.server.connect()
        except OSError:
            return
        with upstream:
            other = threading.Thread(target=pump, args=(upstream, self.request))
            other.start()
            try:
                pump(self.request, upstream)
            finally:
                other.join()


def pump(source, destination):
    try:
        while block := source.recv(64 * 1024):
            destination.sendall(block)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            destination.shutdown(socket.SHUT_WR)


class UnixRelay(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


class TCPRelay(socketserver.ThreadingTCPServer):
    daemon_threads = True


def connect_unix(path):
    stream = socket.socket(socket.AF_UNIX)
    try:
        stream.connect(path)
    except OSError:
        stream.close()
        raise
    return stream


@contextlib.contextmanager
def model_socket(url):
    upstream = urlsplit(url)
    if upstream.scheme != "http" or upstream.hostname != "127.0.0.1" or not upstream.port:
        raise ValueError("Codex task container requires an explicit host loopback model bridge")
    with tempfile.TemporaryDirectory(prefix="beliefkv-model-") as directory:
        path = Path(directory) / "provider.sock"
        with UnixRelay(str(path), Relay) as server:
            def connect():
                stream = socket.create_connection(("127.0.0.1", upstream.port), timeout=5.)
                stream.settimeout(None)
                return stream

            server.connect = connect
            thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": .05}, daemon=True,
            )
            thread.start()
            try:
                yield path
            finally:
                server.shutdown()
                thread.join()


@contextlib.contextmanager
def codex_container(command, *, bridge_url, workload, image, python, workspace_write):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("Codex task container requires a local immutable image ID")
    workspace = Path(workload["worktree"]).resolve()
    git_dir = subprocess.check_output(
        ["git", "-C", str(workspace), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        text=True, timeout=10,
    ).strip()
    name = "beliefkv-codex-" + uuid4().hex
    with model_socket(bridge_url) as path:
        def mount(source, target, *, readonly=True):
            if "," in str(source) or "," in str(target):
                raise ValueError("Docker mount paths cannot contain commas")
            suffix = ",readonly" if readonly else ""
            return ["--mount", f"type=bind,src={source},dst={target}{suffix}"]

        args = [
            "docker", "run", "--rm", "-i", "--init", "--name", name,
            "--pull=never", "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--tmpfs", "/tmp:rw,exec,nosuid,size=2g,mode=1777",
            "--workdir", str(workspace),
            "--env", "CODEX_HOME=/tmp/codex",
            "--env", "GIT_OPTIONAL_LOCKS=0",
            *mount(command[0], "/usr/local/bin/codex"),
            *mount(Path(__file__).resolve(), "/opt/beliefkv/container_entry.py"),
            *mount(path, "/opt/beliefkv/provider.sock"),
            *mount(workspace, workspace, readonly=not workspace_write),
            *mount(workspace, "/workspace", readonly=not workspace_write),
            *mount(git_dir, git_dir),
        ]
        for key in ("PYTHONPATH", "QT_QPA_PLATFORM", "QT_OPENGL", "MPLBACKEND", "AWS_EC2_METADATA_DISABLED"):
            value = (workload.get("sandbox_environment") or {}).get(key)
            if isinstance(value, str):
                args.extend(("--env", f"{key}={value}"))
        try:
            yield [
                *args, "--entrypoint", python, image,
                "/opt/beliefkv/container_entry.py", str(urlsplit(bridge_url).port),
                "/usr/local/bin/codex", *command[1:],
            ]
        finally:
            subprocess.run(
                ["docker", "container", "rm", "-f", name],
                check=False, capture_output=True, timeout=15,
            )


def container_entry(port, command):
    Path("/tmp/codex").mkdir(mode=0o700, exist_ok=True)
    with TCPRelay(("127.0.0.1", port), Relay) as server:
        server.connect = lambda: connect_unix("/opt/beliefkv/provider.sock")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            return subprocess.call(command)
        finally:
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    raise SystemExit(container_entry(int(sys.argv[1]), sys.argv[2:]))
