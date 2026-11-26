"""Bind an external harness's observed lifecycle to the native token corpus."""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind, is_unobserved_join_event
from beliefkv.experiments.causal_replay import compile_causal_replay
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.runtime.replay_token_capture import native_request_expectations


MANIFEST = "harness_source.json"
HARNESS_NAMES = ("codex", "opencode", "openhands")
NATIVE_EVENTS = "server/runtime_events.sglang.jsonl"
NATIVE_TOKENS = "server/request_tokens.jsonl"
NATIVE_STATUS = "server/native_telemetry_status.json"


def capture_file(arm: Path, name: str) -> Path:
    if type(name) is not str or Path(name).is_absolute():
        raise ValueError("harness capture files require paths relative to the arm")
    path = (arm / name).resolve()
    if not path.is_relative_to(arm.resolve()) or not path.is_file():
        raise ValueError(f"missing or external harness capture artifact: {name}")
    return path


def read_jsonl(path):
    rows, digest = [], sha256()
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                raise ValueError(f"incomplete capture record: {path}")
            digest.update(line)
            if line.strip():
                rows.append(json.loads(line))
    if not rows:
        raise ValueError(f"empty capture artifact: {path}")
    return rows, digest.hexdigest()


def observed_lifecycle(events):
    kinds = Counter(event.kind.value for event in events)
    modes, owners = Counter(), defaultdict(list)
    for event in events:
        owners[event.workflow_id, event.invocation_id].append(event)
        if event.kind is Kind.JOIN_CREATE and not is_unobserved_join_event(event):
            modes[event.attributes.get("mode", "all")] += 1
    continued = 0
    for stream in owners.values():
        spawned = False
        for event in stream:
            if event.kind is Kind.SPAWN:
                spawned = True
            elif event.kind is Kind.JOIN_WAIT:
                spawned = False
            elif event.kind is Kind.LLM_SUBMIT and spawned:
                continued += 1
                spawned = False
    return {
        "event_counts": dict(kinds), "observed_join_modes": dict(modes),
        "parent_model_calls_observed_after_spawn_before_wait": continued,
    }


def compile_harness_plan(harness, events, native_events):
    if harness == "openhands":
        from beliefkv.runtime.openhands_capture import compile_openhands_capture

        return compile_openhands_capture(events, native_events)
    if harness == "opencode":
        from beliefkv.runtime.opencode_capture import compile_opencode_replay

        return compile_opencode_replay(events, native_events)
    return compile_causal_replay(events, native_events)


def _compile(arm, manifest):
    if manifest.get("schema_version") != 1 or manifest.get("harness") not in HARNESS_NAMES:
        raise ValueError("unknown harness capture manifest")
    for field in ("harness_revision", "dataset", "dataset_revision"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise ValueError(f"missing source provenance: {field}")
    sources = manifest.get("client_event_files")
    tasks = manifest.get("workflow_task_ids")
    if (
        not isinstance(sources, list) or not sources or any(type(x) is not str for x in sources)
        or len(sources) != len(set(sources)) or not isinstance(tasks, dict)
        or any(not isinstance(v, str) or not v for v in tasks.values())
    ):
        raise ValueError("require client event files and explicit workload-to-task binding")
    events, artifacts = [], {}
    native, artifacts[NATIVE_EVENTS] = read_jsonl(capture_file(arm, NATIVE_EVENTS))
    for name in sources:
        rows, artifacts[name] = read_jsonl(capture_file(arm, name))
        client_events = tuple(RuntimeEvent.from_dict(row) for row in rows)
        if manifest["harness"] == "opencode" and any(
            event.attributes.get("model_output_boundary") is not None
            or event.kind is Kind.INVOCATION_CREATE and event.attributes.get("resumed") is True
            for event in client_events
        ):
            from beliefkv.runtime.opencode_capture import build_opencode_capture

            protocol = str(Path(name).with_name("opencode-protocol.jsonl"))
            raw_protocol, artifacts[protocol] = read_jsonl(capture_file(arm, protocol))
            rebuilt = build_opencode_capture(raw_protocol, "registration")
            if client_events != rebuilt.events:
                raise ValueError("OpenCode replay events disagree with their original protocol evidence")
        events.extend(client_events)
    workflow_ids = {event.workflow_id for event in events if event.kind is Kind.WORKFLOW_START}
    if workflow_ids != set(tasks):
        raise ValueError("dataset task binding differs from the observed workflow population")
    raw = capture_file(arm, NATIVE_STATUS).read_bytes()
    status = json.loads(raw)
    artifacts[NATIVE_STATUS] = sha256(raw).hexdigest()
    if (
        status.get("replay_token_capture") is not True
        or status.get("writer_error") is not None
        or any(status.get(key) != 0 for key in (
            "failed_records", "dropped_records", "writer_queue_depth", "pending_batch_count",
        ))
    ):
        raise ValueError("require enabled and drained native token capture")
    plan = compile_harness_plan(manifest["harness"], events, native)
    return plan, native_request_expectations(native), artifacts, observed_lifecycle(events)


def register_harness_source(arm: Path, *, harness, harness_revision, dataset,
                            dataset_revision, client_event_files, workflow_task_ids):
    arm = arm.resolve()
    output = arm / MANIFEST
    if output.exists():
        raise FileExistsError("preserve the frozen harness source manifest")
    manifest = {
        "schema_version": 1, "harness": harness, "harness_revision": harness_revision,
        "dataset": dataset, "dataset_revision": dataset_revision,
        "client_event_files": list(client_event_files), "workflow_task_ids": workflow_task_ids,
    }
    plan, expected, artifacts, lifecycle = _compile(arm, manifest)
    with CapturedTokenCorpus(capture_file(arm, NATIVE_TOKENS), expected) as corpus:
        tokens = corpus.evidence
    artifacts[NATIVE_TOKENS] = tokens["token_source_sha256"]
    manifest.update(artifacts_sha256=artifacts, lifecycle=lifecycle, replay_graph=plan.summary())
    with output.open("x", encoding="ascii") as stream:
        json.dump(manifest, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return manifest


def load_harness_source(arm: Path):
    arm = arm.resolve()
    body = capture_file(arm, MANIFEST).read_bytes()
    manifest = json.loads(body)
    plan, expected, artifacts, coverage = _compile(arm, manifest)
    bound = manifest["artifacts_sha256"]
    token_hash = bound.get(NATIVE_TOKENS)
    if (
        not isinstance(token_hash, str) or len(token_hash) != 64
        or {**artifacts, NATIVE_TOKENS: token_hash} != bound
        or coverage != manifest.get("lifecycle")
    ):
        raise ValueError("harness evidence changed after registration")
    return plan, expected, {
        "source_arm": str(arm), "source_kind": "observed_external_harness",
        "manifest_sha256": sha256(body).hexdigest(), "token_source_sha256": token_hash,
        **{key: manifest[key] for key in ("harness", "harness_revision", "dataset", "dataset_revision")},
        "workflow_task_ids": manifest["workflow_task_ids"], "lifecycle": coverage,
        "independent_task_correctness_verified": False,
    }
