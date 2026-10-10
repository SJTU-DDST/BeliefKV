"""HTTP boundaries remain observable without changing request or SSE content."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import httpx
import pytest

from beliefkv.experiments.deepagents_swebench import _stream_diagnostic_http_clients


@pytest.fixture
def http_server():
    received = []
    content = b'data: {"choices":[{"delta":{"content":"final report"}}]}\n\ndata: [DONE]\n\n'

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received, content
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_request_timing_over_real_http_preserves_body_and_response(
    tmp_path, http_server, asynchronous,
):
    url, received, content = http_server
    rows = []
    sync, async_client = _stream_diagnostic_http_clients(tmp_path, rows.append)
    payload = {"rid": "request-1", "messages": [{"role": "user", "content": "task"}]}

    async def send():
        return await async_client.post(url + "/v1/chat/completions", json=payload)

    try:
        response = asyncio.run(send()) if asynchronous else sync.post(
            url + "/v1/chat/completions", json=payload,
        )
        assert response.content == content
        assert received == [payload]
        [timing] = [row for row in rows if row["event"] == "llm_request_http_transport"]
        assert timing["request_id"] == "request-1"
        assert timing["status_code"] == 200
        assert (
            timing["http_request_start_ts_ms"] <= timing["http_body_send_start_ts_ms"]
            <= timing["http_body_sent_ts_ms"] <= timing["http_response_headers_ts_ms"]
        )
        assert "messages" not in timing
    finally:
        sync.close()
        asyncio.run(async_client.aclose())


def test_mock_transport_does_not_fabricate_body_send_receipt(tmp_path):
    rows = []
    sync, async_client = _stream_diagnostic_http_clients(tmp_path, rows.append)
    try:
        sync._transport.close()
        sync._transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"report"),
        )
        assert sync.post(
            "http://127.0.0.1/v1/chat/completions", json={"rid": "request-1"},
        ).content == b"report"
        [timing] = rows
        assert timing["http_request_start_ts_ms"] is not None
        assert timing["http_body_sent_ts_ms"] is None
        assert timing["http_response_headers_ts_ms"] is None
    finally:
        sync.close()
        asyncio.run(async_client.aclose())
