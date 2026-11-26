from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, TextIO


_SENSITIVE_ENVIRONMENT_NAMES = frozenset(
    {
        "ALL_PROXY",
        "ANTHROPIC_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "CODEX_API_KEY",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GOOGLE_API_KEY",
        "HF_TOKEN",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "HUGGING_FACE_HUB_TOKEN",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "PROXY_AUTHORIZATION",
        "SSLKEYLOGFILE",
    }
)
_SENSITIVE_ENVIRONMENT_SUFFIXES = (
    "_API_KEY",
    "_ACCESS_KEY",
    "_ACCESS_TOKEN",
    "_AUTH_TOKEN",
    "_CREDENTIAL",
    "_PASSWORD",
    "_SECRET",
    "_TOKEN",
)


def sanitized_codex_environment(
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build a child environment without inherited credentials or proxies."""

    environment = os.environ.copy()
    environment.update(overrides or {})
    for name in tuple(environment):
        normalized = name.upper()
        if normalized in _SENSITIVE_ENVIRONMENT_NAMES or normalized.endswith(
            _SENSITIVE_ENVIRONMENT_SUFFIXES
        ):
            environment.pop(name, None)
    return environment


class CodexAppServerError(RuntimeError):
    pass


CODEX_CAPTURE_VERSION = "codex-cli 0.171.0-alpha.13.2"
CODEX_CAPTURE_SOURCE_COMMIT = "ad9d659a8491fc5169a00dcf7b7e6ca8a87adf69"
# Official rust-v0.171.0-alpha.13.2 x86_64-unknown-linux-musl release asset.
CODEX_CAPTURE_BINARY_SHA256 = "13f4960291147ba63dca2508920962f97dccf8710e11cb931125048b5436e9b2"
CODEX_CAPTURE_SCHEMA_SHA256 = "c6be2081b4fba1a524f4b13d9113f891803a90c5b188bfe1161d3e6c0cb35972"


def verify_codex_capture_binary(command: str) -> dict[str, str]:
    """Bind runs to the binary and experimental v2 schema used in the wire smoke."""

    executable = shutil.which(command)
    if executable is None:
        raise CodexAppServerError(f"Codex executable not found: {command}")
    binary = Path(executable).resolve()
    digest = hashlib.sha256()
    with binary.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    binary_sha256 = digest.hexdigest()
    if binary_sha256 != CODEX_CAPTURE_BINARY_SHA256:
        raise CodexAppServerError(f"unpinned Codex capture binary: {binary} ({binary_sha256})")
    env = sanitized_codex_environment()
    with tempfile.TemporaryDirectory(prefix="beliefkv-codex-schema-") as directory:
        env["CODEX_HOME"] = directory
        version = subprocess.run(
            [str(binary), "--version"], check=True, capture_output=True,
            text=True, env=env, timeout=10,
        ).stdout.strip()
        subprocess.run(
            [str(binary), "app-server", "generate-json-schema", "--experimental", "--out", directory],
            check=True, capture_output=True, env=env, timeout=10,
        )
        schema = Path(directory, "codex_app_server_protocol.v2.schemas.json")
        schema_sha256 = hashlib.sha256(schema.read_bytes()).hexdigest()
    if version != CODEX_CAPTURE_VERSION or schema_sha256 != CODEX_CAPTURE_SCHEMA_SHA256:
        raise CodexAppServerError(f"unpinned Codex protocol: {version} ({schema_sha256})")
    return {
        "path": str(binary),
        "version": version,
        "binary_sha256": binary_sha256,
        "experimental_v2_schema_sha256": schema_sha256,
        "source_commit": CODEX_CAPTURE_SOURCE_COMMIT,
    }


class CodexAppServerClient:
    """Minimal JSON-RPC client for one Codex app-server stdio process."""

    def __init__(
        self,
        command: list[str],
        *,
        codex_home: str | Path,
        notification_handler: Callable[[dict[str, Any]], None] | None = None,
        stderr_path: str | Path | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        self.command = list(command)
        self.codex_home = Path(codex_home).expanduser().resolve()
        self.codex_home.mkdir(parents=True, exist_ok=True)
        self.notification_handler = notification_handler
        self.stderr_path = (
            Path(stderr_path).expanduser().resolve() if stderr_path else None
        )
        self.environment = dict(environment or {})
        self._process: subprocess.Popen[str] | None = None
        self._stderr_stream: TextIO | None = None
        self._reader: threading.Thread | None = None
        self._next_id = 0
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._reader_error: BaseException | None = None
        self._closing = False

    def start(self, timeout_s: float = 30.0) -> None:
        if self._process is not None:
            raise CodexAppServerError("Codex app-server is already running")
        self._closing = False
        self._reader_error = None
        stderr: int | TextIO = subprocess.DEVNULL
        if self.stderr_path is not None:
            self.stderr_path.parent.mkdir(parents=True, exist_ok=True)
            self._stderr_stream = self.stderr_path.open("x", encoding="utf-8")
            stderr = self._stderr_stream
        env = sanitized_codex_environment(self.environment)
        env["CODEX_HOME"] = str(self.codex_home)
        try:
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=env,
            )
            self._reader = threading.Thread(
                target=self._read_loop,
                args=(self._process,),
                name="codex-app-server-reader",
                daemon=True,
            )
            self._reader.start()
            self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "beliefkv-benchmark",
                        "title": "BeliefKV Codex Runtime",
                        "version": "0.1.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
                timeout_s=timeout_s,
            )
            self.notify("initialized", {})
        except BaseException:
            self.close()
            raise

    def request(
        self, method: str, params: dict[str, Any], *, timeout_s: float = 60.0
    ) -> dict[str, Any]:
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[request_id] = response_queue
        try:
            self._send({"id": request_id, "method": method, "params": params})
            response = response_queue.get(timeout=timeout_s)
        except queue.Empty as error:
            self._raise_if_reader_failed()
            raise TimeoutError(f"Codex request timed out: {method}") from error
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
        self._raise_if_reader_failed()
        if "error" in response:
            raise CodexAppServerError(
                f"Codex request {method} failed: {json.dumps(response['error'])}"
            )
        result = response.get("result")
        return result if isinstance(result, dict) else {"value": result}

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"method": method, "params": params})

    def raise_if_reader_failed(self) -> None:
        """Surface asynchronous protocol or notification-handler failures."""

        self._raise_if_reader_failed()

    def interrupt(self, thread_id: str, turn_id: str, *, timeout_s: float = 5.0) -> None:
        self.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id},
                     timeout_s=timeout_s)

    def close(self) -> None:
        self._closing = True
        process = self._process
        if process is not None:
            if process.stdin is not None:
                with self._write_lock:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5.0)
            if self._reader is not None:
                self._reader.join(timeout=5.0)
                if self._reader.is_alive():
                    raise CodexAppServerError("Codex notification reader did not stop")
                self._reader = None
            if process.stdout is not None:
                process.stdout.close()
            self._process = None
        if self._stderr_stream is not None:
            self._stderr_stream.close()
            self._stderr_stream = None

    def __enter__(self) -> "CodexAppServerClient":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise CodexAppServerError("Codex app-server is not running")
        self._raise_if_reader_failed()
        if process.poll() is not None:
            raise CodexAppServerError(
                f"Codex app-server exited with status {process.returncode}"
            )
        encoded = json.dumps(message, separators=(",", ":"), allow_nan=False)
        try:
            with self._write_lock:
                process.stdin.write(encoded + "\n")
                process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise CodexAppServerError("failed to write to Codex app-server") from error

    def _read_loop(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                message = json.loads(line)
                if not isinstance(message, dict):
                    continue
                request_id = message.get("id")
                if isinstance(request_id, int) and (
                    "result" in message or "error" in message
                ):
                    with self._lock:
                        response_queue = self._pending.pop(request_id, None)
                    if response_queue is not None:
                        response_queue.put(message)
                elif "method" in message:
                    if request_id is not None:
                        raise CodexAppServerError(
                            f"unexpected Codex server request: {message['method']}"
                        )
                    if self.notification_handler is not None:
                        self.notification_handler(message)
            if not self._closing:
                raise CodexAppServerError("Codex app-server exited before capture closed")
        except BaseException as error:
            self._reader_error = error
        finally:
            failure = {
                "error": {
                    "code": -32000,
                    "message": "Codex app-server stream closed",
                }
            }
            with self._lock:
                pending = tuple(self._pending.values())
                self._pending.clear()
            for response_queue in pending:
                try:
                    response_queue.put_nowait(failure)
                except queue.Full:
                    pass

    def _raise_if_reader_failed(self) -> None:
        if self._reader_error is not None:
            raise CodexAppServerError("Codex app-server reader failed") from self._reader_error
