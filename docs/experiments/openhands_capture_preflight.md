# OpenHands Capture Preflight

The capture uses official OpenHands SDK and tools v1.14.0, tag commit
`ab83d054ee88a3e19eea2af7fc3149a0d743804f`. Its local Agent setup follows the
text-only chat, terminal, file editor, and task tracker patterns in the MARS
OpenHands runner at `d2e34d007ad2013b4a2513efdb9b2f9b606369f6`. The capture root
also registers the SDK's `DelegateTool`.

Use Python 3.12 with `venv` available. On this host, the interpreter at
`/home/longhao/miniconda3/envs/mars/bin/python3.12` has it.

```bash
"${OPENHANDS_PREFLIGHT_PYTHON:-python3.12}" -m venv /tmp/openhands-preflight-venv
/tmp/openhands-preflight-venv/bin/python -m pip install -r configs/openhands_capture_requirements.txt pytest
/tmp/openhands-preflight-venv/bin/python scripts/run_openhands_capture.py smoke --output /tmp/openhands-preflight
/tmp/openhands-preflight-venv/bin/python -m pytest -q tests/test_openhands_capture.py
```

The smoke uses an ephemeral loopback HTTP server with scripted chat completions.
It exercises root streaming, child non-streaming, ordinary root and child tools,
idle child creation, concurrent child task threads, a blocking ALL wait, and
reuse of a child conversation. The SDK rejects `command="wait"` because
`DelegateAction` in this release exposes `spawn` and `delegate`. The observed
wait starts at the SDK executor's first `Thread.join` and ends when its last join
returns. Each `delegate` activation has a distinct invocation ID, and a resumed
activation retains the SDK conversation ID as its context ID.

## Source Handoff

Use `capture` with a trusted prompt and an existing local workspace. The SDK
subprocess terminal executes commands on the host with Bash startup files
disabled. The command runs a single conversation and writes
`<output>/runtime_events.openhands.jsonl` and a receipt.

```bash
/tmp/openhands-preflight-venv/bin/python scripts/run_openhands_capture.py capture \
  --workspace /path/to/workspace --prompt-file /path/to/prompt.txt \
  --base-url http://127.0.0.1:30000/v1 --model openai/MODEL \
  --workflow-id WORKFLOW --task-id TASK --output /tmp/openhands-source
```

`OpenHandsCapture(..., publish=...)` publishes each `RuntimeEvent` in trace order.
The SDK chat transport sends `rid` and `beliefkv_metadata` on every attempt.
HTTP response IDs, finish reasons, and usage are recorded when available.
The native server log binds the request ID and causal identity to its completion
clock and usage. A matching `request_tokens.jsonl` supplies exact token and stop
evidence. The localhost model emits explicitly labeled fixture receipts and
supplies no token or EOS evidence.

```bash
python scripts/run_openhands_capture.py compile \
  --events /tmp/openhands-source/runtime_events.openhands.jsonl \
  --server-events /path/to/runtime_events.sglang.jsonl \
  --request-tokens /path/to/request_tokens.jsonl
```

For a sealed source loader, call `compile_openhands_capture(events, server_events)`
with normalized `RuntimeEvent` objects. It calls the common compiler and adds
the observed serial tool dependencies and the delegate tool's release
dependency on its ALL join. This preserves the SDK's tool order and lets
replayed child service determine the delegation span. The capture command
uses local chat completions and the SDK's foreground delegation API.
