from __future__ import annotations

import json
import gzip
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import PrivateAttr


pytest.importorskip("deepagents")

from deepagents.backends.protocol import ExecuteResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.tools import tool
from langchain.agents import create_agent
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain.agents.structured_output import ToolStrategy

from beliefkv.experiments.agent_protocol import (
    ActivationDeadline,
    ActivationDeadlineExceeded,
    AgentLoopGuardMiddleware,
    ChildCompletion,
    LoopGuardPolicy,
    TerminalProtocolError,
    WorkflowCompletion,
    analyze_agent_history,
    classify_tool_outcome,
    require_structured_completion,
)
from beliefkv.experiments.deepagents_swebench import (
    AUTONOMOUS_NATURAL_SUBAGENT_PROMPT,
    AUTONOMOUS_SYSTEM_PROMPT,
    DELEGATED_TASK_FOCUS_INSTRUCTION,
    DynamicInitialDelegationPlan,
    EmptyReasoningRecoveryMiddleware,
    NATIVE_DYNAMIC_1TO4_PROMPT,
    NATIVE_DYNAMIC_INITIAL_PLANNER_PROMPT,
    NATIVE_SUBAGENT_2TO3_PROMPT,
    DeepAgentsExperimentConfig,
    DockerWorkspaceBackend,
    DelegationPlan,
    JsonlAudit,
    ParallelAnalysisPlan,
    PartialAgentRunError,
    OracleKVPressureContext,
    SANDBOX_PATH_CONTRACT,
    SYMPY_SANDBOX_PREFLIGHT,
    SweBenchWorkload,
    WorkflowDeadlineController,
    _execute_saturated_root_pool,
    capture_append_offset,
    classify_workflow_measurement,
    collect_workspace_artifacts,
    copy_append_window,
    load_workload_bundle,
    prepare_workspace,
    prometheus_gauge_sum,
    _demand_load,
    repository_sandbox_contract,
    summarize_agent_control,
    validate_workflow_completion,
    _trace_summary,
    _demand_load_from_metrics,
    SGLangMetricsMonitor,
    _invoke_with_partial_state,
    _autonomous_fanout_prompt,
    _filesystem_middleware,
    _autonomous_subagents,
    _dynamic_initial_delegation_tasks,
    _planned_child_completion,
    _run_planned_child,
    _workflow_terminal,
    _run_autonomous,
    _task_prompt,
    _blake2b_file,
    _planned_child_loop_guard_policy,
    _parallel_analysis_tasks,
    _workspace_patch_tool,
)
from beliefkv.runtime.langchain_tool_safety import (
    ToolCircuitBreakerMiddleware,
    ToolObservationBudgetMiddleware,
    ToolObservationBudgetPolicy,
    ToolOutcomeStatusMiddleware,
)
from beliefkv.runtime.context_lifecycle import (
    CompletionBudgetMiddleware,
    ContextLifecycleMiddleware,
    ContextLifecyclePolicy,
)
from langchain.agents.middleware.types import ToolCallRequest


def test_prometheus_gauge_sum_aggregates_labeled_series() -> None:
    payload = """\
sglang:num_used_tokens{tp_rank="0"} 123
sglang:num_used_tokens{tp_rank="1"} 45
sglang:num_running_reqs{tp_rank="0"} 2
"""
    assert prometheus_gauge_sum(payload, "sglang:num_used_tokens") == 168
    assert prometheus_gauge_sum(payload, "missing") is None


def test_sglang_load_monitor_accepts_native_dp_loads() -> None:
    assert _demand_load([{"dp_rank": 0, "num_reqs": 2},
                         {"dp_rank": 1, "num_reqs": 3}]) == 5
    assert _demand_load({"load": 4}) == 4
    with pytest.raises(ValueError, match="unsupported"):
        _demand_load([{"num_reqs": "3"}])


def test_sglang_demand_load_uses_the_single_metrics_snapshot() -> None:
    assert _demand_load_from_metrics(12.0, 7.0) == 19
    assert _demand_load_from_metrics(12.0, None) is None


def test_sglang_metrics_monitor_uses_one_metrics_request_per_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from beliefkv.experiments import deepagents_swebench

    requests: list[str] = []
    payload = (
        "sglang:num_used_tokens 100\n"
        "sglang:num_running_reqs 4\n"
        "sglang:num_queue_reqs 3\n"
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return payload.encode()

    def fake_urlopen(url: str, timeout: float):
        requests.append(url)
        return Response()

    monkeypatch.setattr(deepagents_swebench.urllib.request, "urlopen", fake_urlopen)
    monitor = SGLangMetricsMonitor(
        "http://server/v1", tmp_path / "metrics.jsonl",
        pool_tokens=200, poll_interval_s=60,
    )
    monitor.start()
    deadline = time.monotonic() + 2
    while not monitor.samples and time.monotonic() < deadline:
        time.sleep(0.005)
    summary = monitor.close()

    assert requests == ["http://server/metrics"]
    assert monitor.samples[0]["demand_load"] == 7
    assert monitor.samples[0]["resident_pressure"] == 0.5
    assert summary["error_count"] == 0
    assert summary["metrics_request_count"] == 1


def test_sglang_metrics_monitor_backs_off_after_scrape_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from beliefkv.experiments import deepagents_swebench

    requests = 0
    payload = "sglang:num_used_tokens 100\n"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return payload.encode()

    def fake_urlopen(url: str, timeout: float):
        nonlocal requests
        requests += 1
        if requests == 1:
            raise TimeoutError("metrics endpoint busy")
        return Response()

    monkeypatch.setattr(deepagents_swebench.urllib.request, "urlopen", fake_urlopen)
    monitor = SGLangMetricsMonitor(
        "http://server/v1",
        tmp_path / "metrics.jsonl",
        pool_tokens=200,
        poll_interval_s=0.01,
        max_error_backoff_s=0.02,
    )
    monitor.start()
    deadline = time.monotonic() + 2
    while requests < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    summary = monitor.close()

    records = [
        json.loads(line)
        for line in (tmp_path / "metrics.jsonl").read_text().splitlines()
    ]
    assert requests == 2
    assert summary["error_count"] == 1
    assert summary["error_counts"] == {"TimeoutError": 1}
    assert records[0]["error"] == "TimeoutError: metrics endpoint busy"
    assert records[0]["request_duration_ms"] >= 0
    assert records[1]["num_used_tokens"] == 100
    assert summary["max_request_duration_ms"] >= 0


def test_agent_control_summary_separates_protocol_and_guard_outcomes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "audit.jsonl"
    records = [
        {"event": "agent_semantic_completion", "forced": False},
        {
            "event": "agent_semantic_completion",
            "forced": False,
            "protocol_repaired": True,
        },
        {
            "event": "agent_semantic_completion",
            "forced": False,
            "guard_intervened": True,
        },
        {"event": "agent_protocol_repair_failed"},
        {"event": "agent_tool_duplicate_suppressed"},
    ]
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in records),
        encoding="utf-8",
    )

    summary = summarize_agent_control(path)

    assert summary["semantic_completions"] == 3
    assert summary["natural_semantic_completions"] == 1
    assert summary["protocol_repaired_completions"] == 1
    assert summary["guard_intervened_completions"] == 1
    assert summary["protocol_repair_failures"] == 1
    assert summary["duplicate_tool_calls_suppressed"] == 1


def test_native_child_natural_language_is_a_terminal_return(
    tmp_path: Path,
) -> None:
    audit_path = tmp_path / "native-child-audit.jsonl"
    audit = JsonlAudit(audit_path)
    model = FakeMessagesListChatModel(
        responses=[AIMessage(content="Found the failing ordering path and test.")],
    )
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(),
        completion_schema=ChildCompletion,
        completion_instruction="Return the result to the parent.",
        audit=audit,
        scope="native-child-test",
        accept_natural_completion=True,
    )
    agent = create_agent(model=model, tools=[], middleware=[guard])

    result = agent.invoke(
        {"messages": [HumanMessage(content="Inspect the assigned issue.")]},
        config={"recursion_limit": 16},
    )
    audit.close()

    assert result["messages"][-1].content == (
        "Found the failing ordering path and test."
    )
    summary = summarize_agent_control(audit_path)
    assert summary["natural_language_return_count"] == 1
    assert summary["protocol_repaired_completions"] == 0
    assert summary["event_counts"].get("agent_unstructured_stop_detected", 0) == 0
    assert summary["event_counts"].get("agent_protocol_repair_attempt", 0) == 0


def test_direct_runtime_trace_reports_pairing_and_subagent_lifecycle(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    records = [
        {"kind": "workflow_start"},
        {
            "kind": "invocation_create",
            "invocation_id": "child",
            "parent_invocation_id": "root",
        },
        {"kind": "spawn", "target_invocation_id": "child"},
        {"kind": "join_create"},
        {"kind": "llm_submit", "invocation_id": "child"},
        {"kind": "llm_result", "invocation_id": "child"},
        {
            "kind": "tool_start",
            "attributes": {"tool_name": "edit_file"},
        },
        {
            "kind": "tool_end",
            "attributes": {
                "tool_name": "edit_file",
                "status": "success",
                "workspace_digest_before": "before",
                "workspace_digest_after": "after",
            },
        },
        {"kind": "return", "invocation_id": "child"},
        {"kind": "join_satisfied"},
        {"kind": "workflow_end"},
    ]
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in records),
        encoding="utf-8",
    )

    summary = _trace_summary(path)

    assert summary["llm_pairing_valid"]
    assert summary["tool_pairing_valid"]
    assert summary["tool_status_coverage"] == 1.0
    assert summary["workspace_digest_coverage"] == 1.0
    assert summary["all_subagents_returned"]
    assert summary["all_joins_satisfied"]
    assert summary["workflow_lifecycle_valid"]


def test_system_jct_allows_recovered_protocol_but_native_jct_does_not() -> None:
    result = classify_workflow_measurement(
        outcome="completed",
        error=None,
        semantic_completion={"status": "blocked"},
        agent_control={
            "protocol_repair_failures": 0,
            "protocol_repaired_completions": 1,
            "protocol_normalized_completions": 0,
            "guard_intervened_completions": 0,
            "forced_semantic_completions": 0,
            "stuck_reasons": {},
        },
        control_delivery={"degraded": False},
        trace={
            "workflow_lifecycle_valid": True,
            "llm_pairing_valid": True,
            "tool_pairing_valid": True,
            "tool_status_coverage": 1.0,
            "workspace_digest_coverage": 1.0,
            "dynamic_subagent_count": 1,
            "all_subagents_returned": True,
            "all_joins_satisfied": True,
        },
    )

    assert result["system_jct_eligible"]
    assert not result["native_agent_jct_eligible"]
    assert "protocol_repaired_completion" in result[
        "native_agent_jct_exclusion_reasons"
    ]


def test_natural_workflow_return_preserves_system_measurement() -> None:
    result = classify_workflow_measurement(
        outcome="completed",
        error=None,
        semantic_completion=None,
        agent_control={},
        control_delivery={"degraded": False},
        trace={
            "workflow_lifecycle_valid": True,
            "llm_pairing_valid": True,
            "tool_pairing_valid": True,
            "tool_status_coverage": 1.0,
            "workspace_digest_coverage": 1.0,
            "dynamic_subagent_count": 0,
        },
    )
    assert result["system_jct_eligible"]
    assert not result["native_agent_jct_eligible"]
    assert result["native_agent_jct_exclusion_reasons"] == [
        "missing_semantic_completion"
    ]


def test_missing_child_final_is_not_eligible_after_blocked_join() -> None:
    result = classify_workflow_measurement(
        outcome="completed",
        error=None,
        semantic_completion=None,
        agent_control={},
        control_delivery={"degraded": False},
        trace={
            "workflow_lifecycle_valid": True,
            "llm_pairing_valid": True,
            "tool_pairing_valid": True,
            "tool_status_coverage": 1.0,
            "workspace_digest_coverage": 1.0,
            "dynamic_subagent_count": 1,
            "all_subagents_returned": True,
            "all_joins_satisfied": True,
        },
        child_reports=[{
            "semantic_completion": {
                "status": "blocked", "unresolved": ["no_natural_final_text"],
            },
        }],
    )
    assert not result["system_jct_eligible"]
    assert "child_missing_natural_final" in result["system_jct_exclusion_reasons"]


def test_copy_append_window_freezes_only_new_bytes(tmp_path: Path) -> None:
    source = tmp_path / "server.jsonl"
    source.write_bytes(b'{"old":1}\n')
    offset = capture_append_offset(source)
    with source.open("ab") as stream:
        stream.write(b'{"new":2}\n')
    destination = tmp_path / "run" / "server.jsonl"

    metadata = copy_append_window(
        source,
        destination,
        start_offset=offset,
    )

    assert destination.read_bytes() == b'{"new":2}\n'
    assert metadata["start_offset"] == len(b'{"old":1}\n')
    assert metadata["end_offset"] == source.stat().st_size
    assert metadata["byte_count"] == len(b'{"new":2}\n')
    assert metadata["sha256"]


def test_load_bundle_and_prepare_exact_commit(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=source, check=True)
    (source / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "module.py"], cwd=source, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=BeliefKV Test",
            "-c",
            "user.email=beliefkv@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "test fixture",
        ],
        cwd=source,
        check=True,
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    manifest = tmp_path / "workloads.json"
    manifest.write_text(
        json.dumps(
            {
                "dataset": "fixture",
                "dataset_revision": "r1",
                "source_repo": str(source),
                "workloads": [
                    {
                        "instance_id": "fixture-1",
                        "repo": "fixture/repo",
                        "base_commit": commit,
                        "problem_statement": "Fix VALUE.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    bundle = load_workload_bundle(manifest)
    destination = tmp_path / "workspace"
    metadata = prepare_workspace(source, bundle.workloads[0], destination)
    assert metadata["initial_head"] == commit
    assert metadata["isolation"] == "per-workflow-independent-local-clone"
    assert not (destination / ".git" / "objects" / "info" / "alternates").exists()
    assert (destination / "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    (destination / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    (destination / ".git" / "HEAD").write_text(
        "ref: refs/heads/missing\n", encoding="utf-8"
    )
    patch, status, collection = collect_workspace_artifacts(
        destination,
        source_repo=source,
        base_commit=commit,
    )
    assert "+VALUE = 2" in patch
    assert status == "M\tmodule.py"
    assert collection["mode"] == "temporary_git_metadata"
    assert len(collection["errors"]) == 1


def test_docker_backend_hashes_commands_and_truncates_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    backend = DockerWorkspaceBackend(
        tmp_path,
        image="fixture:latest",
        audit=audit,
        max_output_chars=4,
    )
    backend._started = True

    invocations = []

    def fake_run(*args, **kwargs):
        invocations.append((args, kwargs))
        return subprocess.CompletedProcess([], 0, stdout="abcdef", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    response = backend.execute("printf abcdef")
    backend.close()
    audit.close()
    assert response.truncated
    records = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    execute = next(item for item in records if item["event"] == "sandbox_execute")
    assert execute["command_chars"] == len("printf abcdef")
    assert execute["container_name"] == backend._container_name
    assert execute["lock_wait_ms"] >= 0
    assert execute["execute_elapsed_ms"] >= 0
    assert execute["lock_wait_ms"] + execute["execute_elapsed_ms"] == pytest.approx(
        execute["duration_ms"]
    )
    assert "command" not in execute
    execute_argv = invocations[0][0][0]
    assert execute_argv[:4] == ["docker", "exec", "--workdir", "/workspace"]
    assert "PATH=/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:" in " ".join(
        execute_argv
    )
    assert execute_argv[-3:-1] == ["/bin/sh", "-c"]


def test_docker_backend_opt_in_stdout_timing_keeps_command_body_out_of_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    backend = DockerWorkspaceBackend(
        tmp_path, image="fixture:latest", audit=audit,
        output_timing_shadow=True,
    )
    backend._started = True
    monkeypatch.setattr(backend, "_docker_exec_argv", lambda _: [
        "/bin/sh", "-c", "printf first; sleep .05; printf last",
    ])
    response = backend.execute("private command text")
    backend._started = False
    backend.close()
    audit.close()
    assert response.output == "firstlast"
    assert response.exit_code == 0
    record = next(
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text().splitlines()
        if json.loads(line)["event"] == "sandbox_execute"
    )
    assert record["output_timing_shadow"] is True
    assert record["observed_output_bytes"] == len(b"firstlast")
    assert (0 <= record["first_output_after_execute_ms"]
            < record["last_output_after_execute_ms"]
            <= record["execute_elapsed_ms"])
    assert "private command text" not in str(record)
    assert "firstlast" not in str(record)


def test_unbuffered_python_output_is_only_allowed_in_explicit_shadow(
    tmp_path: Path,
) -> None:
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    with pytest.raises(ValueError, match="requires sandbox output timing shadow"):
        DockerWorkspaceBackend(
            tmp_path, image="fixture:latest", audit=audit,
            unbuffered_output_shadow=True,
        )
    plain = DockerWorkspaceBackend(tmp_path, image="fixture:latest", audit=audit)
    assert "PYTHONUNBUFFERED=1" not in plain._docker_environment_args()
    opt_in = DockerWorkspaceBackend(
        tmp_path, image="fixture:latest", audit=audit,
        output_timing_shadow=True, unbuffered_output_shadow=True,
    )
    assert "PYTHONUNBUFFERED=1" in opt_in._docker_environment_args()
    audit.close()


def test_test_progress_shadow_requires_support_and_stdout_shadow(
    tmp_path: Path,
) -> None:
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    with pytest.raises(ValueError, match="requires sandbox output timing"):
        DockerWorkspaceBackend(
            tmp_path, image="fixture:latest", audit=audit,
            test_progress_shadow=True,
        )
    with pytest.raises(ValueError, match="requires sandbox support"):
        DockerWorkspaceBackend(
            tmp_path, image="fixture:latest", audit=audit,
            support_dir=None, output_timing_shadow=True,
            test_progress_shadow=True,
        )
    backend = DockerWorkspaceBackend(
        tmp_path, image="fixture:latest", audit=audit,
        output_timing_shadow=True, test_progress_shadow=True,
    )
    assert "PYTEST_PLUGINS=beliefkv_pytest_progress" in (
        backend._docker_environment_args()
    )
    audit.close()


@pytest.mark.skipif(
    os.environ.get("BELIEFKV_TEST_SANDBOX_DOCKER") != "1",
    reason="requires explicitly selected cached SWE-bench Docker image",
)
def test_test_progress_shadow_in_real_sandbox(tmp_path: Path) -> None:
    image = os.environ["BELIEFKV_TEST_SANDBOX_IMAGE"]
    workspace = tmp_path / "workspace"
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    (workspace / "test_progress_example.py").write_text(
        "import time\n"
        "def test_first():\n    time.sleep(.02)\n"
        "def test_second():\n    time.sleep(.2)\n"
    )
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    backend = DockerWorkspaceBackend(
        workspace, image=image, audit=audit,
        output_timing_shadow=True, test_progress_shadow=True,
    )
    try:
        backend.start()
        result = backend.execute(
            "python -m pytest -q -c /dev/null /workspace/test_progress_example.py",
            timeout=30,
        )
        assert result.exit_code == 0, result.output
        assert "2 passed" in result.output
        assert "BKVP" not in result.output
    finally:
        backend.close()
        audit.close()
    records = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text().splitlines()
    ]
    execution = next(row for row in records if row["event"] == "sandbox_execute")
    assert execution["total_test_progress_events"] == 4
    assert execution["recent_test_progress_events"][1][1:] == [
        "test_done", 1, 2,
    ]
    assert [row[1] for row in execution["first_test_progress_stages"]] == [
        "collection", "all_tests_done",
    ]
    assert (
        execution["execute_elapsed_ms"]
        - execution["recent_test_progress_events"][1][0]
    ) >= 100


def test_planned_child_inherits_unbuffered_output_shadow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    backend = DockerWorkspaceBackend(
        tmp_path / "workspace", image="fixture:latest", audit=audit,
        support_dir=None, output_timing_shadow=True,
        unbuffered_output_shadow=True,
    )
    monkeypatch.setattr(
        "beliefkv.experiments.deepagents_swebench.prepare_workspace",
        lambda *_: None,
    )
    child_backends = []

    def check_child(child: DockerWorkspaceBackend) -> None:
        assert child.output_timing_shadow is True
        assert child.unbuffered_output_shadow is True
        assert "PYTHONUNBUFFERED=1" in child._docker_environment_args()
        raise RuntimeError("checked child backend")

    monkeypatch.setattr(DockerWorkspaceBackend, "start", check_child)
    monkeypatch.setattr(DockerWorkspaceBackend, "close", lambda _: None)
    controller = SimpleNamespace(
        register_backend=child_backends.append,
        unregister_backend=child_backends.remove,
    )
    with pytest.raises(RuntimeError, match="checked child backend"):
        _run_planned_child(
            None, None, backend, None,
            SimpleNamespace(invocation_id="child-1"),
            SimpleNamespace(role="analysis"),
            controller,
        )
    assert not child_backends
    audit.close()


def test_docker_cleanup_timeout_is_audited_without_losing_workflow_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    backend = DockerWorkspaceBackend(tmp_path, image="fixture:latest", audit=audit)
    backend._started = True

    def timed_out(argv: list[str], **kwargs: Any) -> Any:
        del kwargs
        raise subprocess.TimeoutExpired(argv, 30.0)

    monkeypatch.setattr(subprocess, "run", timed_out)
    backend.close()
    audit.close()

    records = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert backend.cleanup_status == "failed"
    assert records[-1]["event"] == "sandbox_stop"
    assert records[-1]["status"] == "failed"
    assert records[-1]["container_name"] == backend.id
    assert "TimeoutExpired" in records[-1]["error"]


def test_tool_observation_budget_bounds_parallel_turn_deterministically(
    tmp_path: Path,
) -> None:
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    policy = ToolObservationBudgetPolicy(
        total_chars_per_turn=65_536,
        max_chars_per_result=16_384,
    )
    middleware = ToolObservationBudgetMiddleware(
        policy=policy,
        audit=audit,
        scope="observation-budget-test",
    )
    calls = [
        {"name": "execute", "args": {"command": f"probe {index}"}, "id": f"c{index}"}
        for index in range(50)
    ]
    state = {"messages": [AIMessage(content="", tool_calls=calls)]}
    visible: list[str] = []
    for call in calls:
        request = ToolCallRequest(
            tool_call=call,
            tool=None,
            state=state,
            runtime=SimpleNamespace(config={}),
        )
        result = middleware.wrap_tool_call(
            request,
            lambda current: ToolMessage(
                content=("head-" + "x" * 70_000 + "-tail"),
                tool_call_id=str(current.tool_call["id"]),
                name="execute",
                status="success",
            ),
        )
        assert isinstance(result, ToolMessage)
        assert result.status == "success"
        assert result.additional_kwargs["beliefkv_observation_truncated"] is True
        visible.append(str(result.content))
    audit.close()

    assert sum(map(len, visible)) <= policy.total_chars_per_turn
    assert all(len(item) == policy.total_chars_per_turn // len(calls) for item in visible)
    assert all("BeliefKV observation truncated" in item for item in visible)
    assert all(item.endswith("-tail") for item in visible)
    records = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == len(calls)
    assert {item["turn_tool_fanout"] for item in records} == {len(calls)}
    assert {item["per_result_budget_chars"] for item in records} == {1310}


def test_docker_backend_deadline_cancel_stops_workflow_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    backend = DockerWorkspaceBackend(tmp_path, image="fixture:latest", audit=audit)
    backend._started = True
    invocations: list[list[str]] = []

    def fake_run(argv, **kwargs):
        del kwargs
        invocations.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="fixture", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert backend.cancel_active_commands(reason="workflow_timeout") == 1
    assert backend.cancel_active_commands(reason="workflow_timeout") == 0
    audit.close()

    assert invocations == [["docker", "kill", backend.id]]
    records = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    cancel = next(item for item in records if item["event"] == "sandbox_cancel")
    assert cancel["reason"] == "workflow_timeout"
    assert cancel["returncode"] == 0


def test_workspace_digest_detects_mutating_tool_changes(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    source = tmp_path / "module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "module.py"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=BeliefKV Test",
            "-c",
            "user.email=beliefkv@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
        cwd=tmp_path,
        check=True,
    )
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    backend = DockerWorkspaceBackend(tmp_path, image="fixture:latest", audit=audit)

    before = backend.tool_state_digest("edit_file", {"path": "module.py"})
    source.write_text("VALUE = 2\n", encoding="utf-8")
    after = backend.tool_state_digest("edit_file", {"path": "module.py"})

    assert before is not None
    assert after is not None
    assert before != after
    assert backend.tool_state_digest("read_file", {"path": "module.py"}) is None
    audit.close()


def test_workspace_epoch_advances_only_after_successful_mutations(
    tmp_path: Path,
) -> None:
    source = tmp_path / "module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    backend = DockerWorkspaceBackend(tmp_path, image="fixture:latest", audit=audit)

    assert backend.workspace_epoch() == 0
    edited = backend.edit("/workspace/module.py", "VALUE = 1", "VALUE = 2")
    assert getattr(edited, "error", None) is None
    assert backend.workspace_epoch() == 1

    failed = backend.edit("/workspace/module.py", "MISSING", "VALUE = 3")
    assert getattr(failed, "error", None) is not None
    assert backend.workspace_epoch() == 1

    written = backend.write("/workspace/new.py", "NEW = True\n")
    assert getattr(written, "error", None) is None
    assert backend.workspace_epoch() == 2
    audit.close()


def test_docker_backend_preflights_test_environment_before_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    backend = DockerWorkspaceBackend(
        tmp_path,
        image="fixture:latest",
        audit=audit,
        preflight_command=SYMPY_SANDBOX_PREFLIGHT,
    )
    invocations: list[list[str]] = []

    def fake_run(argv, **kwargs):
        del kwargs
        invocations.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    backend.start()
    backend.close()
    audit.close()

    assert invocations[0][:2] == ["docker", "run"]
    git_mount = (
        f"type=bind,source={backend.workspace / '.git'},"
        "target=/workspace/.git,readonly"
    )
    assert git_mount in invocations[0]
    assert invocations[1][:4] == ["docker", "exec", "--workdir", "/workspace"]
    assert SYMPY_SANDBOX_PREFLIGHT in invocations[1][-1]
    assert invocations[2][:3] == ["docker", "rm", "--force"]
    records = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    preflight = next(item for item in records if item["event"] == "sandbox_preflight")
    assert preflight["returncode"] == 0
    assert preflight["expected_python"] == "/opt/miniconda3/envs/testbed/bin/python"


def test_docker_backend_prefers_mounted_src_tree_over_installed_packages(
    tmp_path: Path,
) -> None:
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    backend = DockerWorkspaceBackend(
        tmp_path,
        image="fixture:latest",
        audit=audit,
        support_dir=None,
    )

    environment_args = backend._docker_environment_args()
    environment = {
        environment_args[index + 1].split("=", 1)[0]:
        environment_args[index + 1].split("=", 1)[1]
        for index, value in enumerate(environment_args[:-1])
        if value == "--env"
    }

    assert environment["PYTHONPATH"] == "/workspace/src:/workspace"
    assert environment["DJANGO_TEST_PROCESSES"] == "2"
    audit.close()


def test_django_test_workers_respect_sandbox_cpu_quota(tmp_path: Path) -> None:
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    backend = DockerWorkspaceBackend(
        tmp_path, image="fixture:latest", audit=audit, cpus=0.5,
        support_dir=None,
    )
    environment = backend._docker_environment_args()
    assert "DJANGO_TEST_PROCESSES=1" in environment
    audit.close()


def test_workload_cli_does_not_apply_sympy_preflight_globally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.run_deepagents_swebench import parse_args

    monkeypatch.setattr(
        "sys.argv",
        ["run_deepagents_swebench.py", "--mode", "autonomous"],
    )

    args = parse_args()

    assert args.sandbox_preflight_command is None


def test_workload_cli_defaults_model_context_independent_of_lifecycle_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.run_deepagents_swebench import parse_args

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_deepagents_swebench.py",
            "--mode",
            "autonomous",
            "--context-window-tokens",
            "32768",
        ],
    )

    args = parse_args()

    assert args.model_context_tokens == 262_144


def test_workload_cli_can_disable_activation_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.run_deepagents_swebench import parse_args

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_deepagents_swebench.py",
            "--mode",
            "autonomous",
            "--disable-activation-deadline",
        ],
    )

    args = parse_args()

    assert args.disable_activation_deadline is True


def test_docker_backend_recovers_when_timed_out_run_is_already_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    backend = DockerWorkspaceBackend(tmp_path, image="fixture:latest", audit=audit)
    invocations: list[list[str]] = []

    def fake_run(argv, **kwargs):
        del kwargs
        invocations.append(argv)
        if argv[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(argv, 120.0)
        if argv[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(argv, 0, stdout="true\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    backend.start()
    backend.close()
    audit.close()

    assert [item[:2] for item in invocations] == [
        ["docker", "run"],
        ["docker", "inspect"],
        ["docker", "exec"],
        ["docker", "rm"],
    ]
    records = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    start = next(item for item in records if item["event"] == "sandbox_start")
    assert start["status"] == "timeout_recovered_running"
    assert start["attempt"] == 1


def test_docker_backend_uses_workspace_paths_for_file_and_shell_tools(
    tmp_path: Path,
) -> None:
    (tmp_path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    backend = DockerWorkspaceBackend(tmp_path, image="fixture:latest", audit=audit)

    listing = backend.ls("/")
    assert listing.entries is not None
    paths = [item["path"] for item in listing.entries]
    assert "/workspace/module.py" in paths
    read_result = backend.read("/workspace/module.py")
    assert read_result.file_data is not None
    assert read_result.file_data["content"] == "VALUE = 1\n"
    assert backend._resolve_path("/workspace/module.py") == tmp_path / "module.py"
    audit.close()


def test_workspace_patch_tool_checks_then_applies_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    backend = DockerWorkspaceBackend(tmp_path, image="fixture:latest", audit=audit)
    commands: list[str] = []

    def fake_execute(command: str, *, timeout: int | None = None):
        del timeout
        commands.append(command)
        patch_files = list(tmp_path.glob(".beliefkv-patch-*.diff"))
        assert len(patch_files) == 1
        normalized = patch_files[0].read_text(encoding="utf-8")
        assert "diff --git a/module.py b/module.py" in normalized
        assert normalized.endswith("\n")
        return ExecuteResponse(output="", exit_code=0, truncated=False)

    monkeypatch.setattr(backend, "execute", fake_execute)
    patch_tool = _workspace_patch_tool(backend)
    result = patch_tool.invoke(
        {
            "patch": (
                "diff --git a/module.py b/module.py\n"
                "--- a/module.py\n"
                "+++ b/module.py\n"
                "@@ -1 +1 @@\n"
                "-OLD\n"
                "+NEW"
            )
        }
    )

    assert result == "Patch applied successfully."
    assert len(commands) == 2
    assert "git apply --check" in commands[0]
    assert not list(tmp_path.glob(".beliefkv-patch-*.diff"))
    audit.close()


def test_patch_only_filesystem_middleware_removes_direct_edit_tools(
    tmp_path: Path,
) -> None:
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    backend = DockerWorkspaceBackend(tmp_path, image="fixture:latest", audit=audit)
    middleware = _filesystem_middleware(backend, allow_direct_edits=False)

    tool_names = {item.name for item in middleware.tools}
    assert {"ls", "read_file", "glob", "grep", "execute"} <= tool_names
    assert "write_file" not in tool_names
    assert "edit_file" not in tool_names
    audit.close()


def test_writable_filesystem_middleware_exposes_sandboxed_edit_tools(
    tmp_path: Path,
) -> None:
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    backend = DockerWorkspaceBackend(tmp_path, image="fixture:latest", audit=audit)
    middleware = _filesystem_middleware(backend, allow_direct_edits=True)

    tool_names = {item.name for item in middleware.tools}
    assert {"write_file", "edit_file", "execute"} <= tool_names
    audit.close()


def test_workflow_completion_gate_does_not_require_a_test_command() -> None:
    completion = WorkflowCompletion(
        status="patched_and_tested",
        summary="Fixed the issue",
        files_changed=["sympy/core/basic.py"],
        tests=[],
        unresolved=[],
    )
    accepted = validate_workflow_completion(
        completion,
        patch="diff --git a/sympy/core/basic.py b/sympy/core/basic.py",
    )
    assert accepted["passed"]

    unresolved = validate_workflow_completion(
        completion.model_copy(update={"unresolved": ["second requirement missing"]}),
        patch="diff --git a/sympy/core/basic.py b/sympy/core/basic.py",
    )
    assert not unresolved["passed"]
    assert "completion_has_unresolved_items" in unresolved["errors"]

    self_declared_incomplete = validate_workflow_completion(
        completion.model_copy(
            update={
                "summary": "The second requirement requires additional implementation."
            }
        ),
        patch="diff --git a/sympy/core/basic.py b/sympy/core/basic.py",
    )
    assert not self_declared_incomplete["passed"]
    assert "completion_summary_declares_incomplete_work" in self_declared_incomplete[
        "errors"
    ]

    rejected = validate_workflow_completion(
        completion.model_copy(update={"status": "blocked"}),
        patch="",
    )
    assert not rejected["passed"]
    assert "terminal_status:blocked" in rejected["errors"]
    assert "workspace_has_no_patch" in rejected["errors"]


def test_native_root_natural_completion_and_length_exhaustion() -> None:
    natural = {"messages": [AIMessage(content="Implemented the change.")]}
    completion, outcome = _workflow_terminal(natural, require_schema=False)
    assert completion is None
    assert outcome == "completed"
    with pytest.raises(RuntimeError, match="WorkflowCompletion"):
        _workflow_terminal(natural, require_schema=True)

    exhausted = {
        "messages": [
            AIMessage(
                content="",
                response_metadata={
                    "finish_reason": "length",
                    "token_usage": {"completion_tokens": 4096, "reasoning_tokens": 4096},
                },
            )
        ]
    }
    assert _workflow_terminal(exhausted, require_schema=False) == (
        None, "incomplete"
    )
    interrupted_after_tool = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{"name": "execute", "args": {"command": "pwd"}, "id": "t"}],
            ),
            ToolMessage(content="/workspace", tool_call_id="t", name="execute"),
        ]
    }
    assert _workflow_terminal(interrupted_after_tool, require_schema=False) == (
        None, "incomplete"
    )


def test_experiment_config_rejects_unsupported_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mode"):
        DeepAgentsExperimentConfig(
            mode="hybrid",
            base_url="http://localhost:18000/v1",
            model="model",
            output_dir=tmp_path,
            workload_manifest=tmp_path / "workloads.json",
            docker_image="fixture:latest",
        )


def test_experiment_config_uses_hard_fuse_as_langgraph_limit(tmp_path: Path) -> None:
    config = DeepAgentsExperimentConfig(
        mode="planned",
        base_url="http://localhost:18000/v1",
        model="model",
        output_dir=tmp_path,
        workload_manifest=tmp_path / "workloads.json",
        docker_image="fixture:latest",
    )
    assert config.recursion_limit == 2048
    assert config.sampling_seed is None
    assert config.child_finish_chunk_shadow is False
    assert config.workflow_arrival_interval_ms == 0.0
    assert config.workflow_arrival_batch_size == 0
    assert config.workflow_arrival_batch_interval_ms == 0.0
    assert config.saturated_root_backlog is False
    assert config.loop_guard.enabled
    assert config.completion_gate_enabled is True
    assert config.completion_repair_attempts == 2
    assert config.runtime_event_ack_timeout_s == 10.0
    assert config.runtime_event_ack_retries == 3
    assert config.context_lifecycle.window_tokens == 32_768
    assert config.context_lifecycle.keep_tokens == 8_192
    assert config.sandbox_preflight_command is None
    assert "/workspace" in SANDBOX_PATH_CONTRACT
    assert "sympy/core/basic.py" not in SANDBOX_PATH_CONTRACT


def test_final_chunk_shadow_requires_streaming_and_has_explicit_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    required = {
        "mode": "autonomous",
        "base_url": "http://localhost:18000/v1",
        "model": "model",
        "output_dir": tmp_path,
        "workload_manifest": tmp_path / "workloads.json",
        "docker_image": "fixture:latest",
        "child_finish_chunk_shadow": True,
    }
    with pytest.raises(ValueError, match="requires streamed completion"):
        DeepAgentsExperimentConfig(**required)
    configured = DeepAgentsExperimentConfig(
        **required, stream_completion_shadow=True,
    )
    assert configured.child_finish_chunk_shadow is True

    from scripts.run_deepagents_swebench import parse_args

    monkeypatch.setattr("sys.argv", [
        "run_deepagents_swebench.py", "--mode", "autonomous",
        "--stream-completion-shadow", "--child-finish-chunk-shadow",
    ])
    parsed = parse_args()
    assert parsed.stream_completion_shadow is True
    assert parsed.child_finish_chunk_shadow is True


def test_saturated_root_pool_submits_all_roots_before_any_completion() -> None:
    workloads = tuple(
        SweBenchWorkload(
            instance_id=f"root-{index}",
            repo="owner/repo",
            base_commit="base",
            problem_statement="inspect",
            difficulty="medium",
        )
        for index in range(4)
    )
    all_started = threading.Event()
    started: set[str] = set()
    lock = threading.Lock()

    def run_one(workload: SweBenchWorkload) -> str:
        with lock:
            started.add(workload.instance_id)
            if len(started) == len(workloads):
                all_started.set()
        assert all_started.wait(timeout=2.0)
        return workload.instance_id

    completed = _execute_saturated_root_pool(
        workloads,
        concurrency=len(workloads),
        run_one=run_one,
    )

    assert started == {item.instance_id for item in workloads}
    assert {future.result() for future, _workload in completed} == started


def test_saturated_root_pool_rejects_hidden_client_backlog() -> None:
    workloads = tuple(
        SweBenchWorkload(
            instance_id=f"root-{index}",
            repo="owner/repo",
            base_commit="base",
            problem_statement="inspect",
            difficulty="medium",
        )
        for index in range(4)
    )

    with pytest.raises(ValueError, match="concurrency >= frozen root count"):
        _execute_saturated_root_pool(
            workloads,
            concurrency=3,
            run_one=lambda workload: workload.instance_id,
        )


def test_experiment_config_rejects_negative_sampling_seed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="sampling_seed"):
        DeepAgentsExperimentConfig(
            mode="planned",
            base_url="http://localhost:18000/v1",
            model="model",
            output_dir=tmp_path,
            workload_manifest=tmp_path / "workloads.json",
            docker_image="fixture:latest",
            sampling_seed=-1,
        )


def test_experiment_config_rejects_negative_arrival_interval(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="arrival interval"):
        DeepAgentsExperimentConfig(
            mode="planned",
            base_url="http://localhost:18000/v1",
            model="model",
            output_dir=tmp_path,
            workload_manifest=tmp_path / "workloads.json",
            docker_image="fixture:latest",
            workflow_arrival_interval_ms=-1,
        )


def test_experiment_config_rejects_overlapping_arrival_waves(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="final intra-batch"):
        DeepAgentsExperimentConfig(
            mode="planned",
            base_url="http://localhost:18000/v1",
            model="model",
            output_dir=tmp_path,
            workload_manifest=tmp_path / "workloads.json",
            docker_image="fixture:latest",
            workflow_arrival_interval_ms=500,
            workflow_arrival_batch_size=8,
            workflow_arrival_batch_interval_ms=3000,
        )


def test_parallel_analysis_profile_builds_three_read_only_orthogonal_roles(
    tmp_path: Path,
) -> None:
    config = DeepAgentsExperimentConfig(
        mode="autonomous",
        base_url="http://localhost:18000/v1",
        model="model",
        output_dir=tmp_path / "output",
        workload_manifest=tmp_path / "workloads.json",
        docker_image="fixture:latest",
        subagent_fanout_profile="parallel_analysis_2to3",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    backend = DockerWorkspaceBackend(
        workspace,
        image="fixture:latest",
        audit=audit,
        support_dir=None,
    )
    model = FakeMessagesListChatModel(responses=[AIMessage(content="done")])
    try:
        specs = _autonomous_subagents(
            config,
            SweBenchWorkload(
                instance_id="pydata__xarray-1",
                repo="pydata/xarray",
                base_commit="deadbeef",
                problem_statement="Fix an invariant.",
                difficulty="unknown",
            ),
            backend,
            SimpleNamespace(record_call_censor=lambda _record: None),
            model,
            model,
        )
    finally:
        audit.close()
    assert [item["name"] for item in specs] == [
        "repository-explorer",
        "test-analyst",
        "compatibility-analyst",
    ]
    assert all(item["tools"] == [] for item in specs)
    assert all(
        "do not modify files" in item["system_prompt"].lower()
        for item in specs
    )
    assert all("response_format" not in item for item in specs)
    assert all("valid child return" in item["system_prompt"] for item in specs)
    child_guards = [
        middleware
        for item in specs
        for middleware in item["middleware"]
        if isinstance(middleware, AgentLoopGuardMiddleware)
    ]
    assert len(child_guards) == len(specs)
    assert all(item.accept_natural_completion for item in child_guards)


def test_native_subagent_prompt_excludes_natural_fanout_policy() -> None:
    natural_policy = "no required or preconfigured count"
    assert natural_policy in AUTONOMOUS_NATURAL_SUBAGENT_PROMPT
    assert natural_policy not in AUTONOMOUS_SYSTEM_PROMPT
    assert natural_policy not in NATIVE_SUBAGENT_2TO3_PROMPT
    assert "A one-task message is invalid" in NATIVE_SUBAGENT_2TO3_PROMPT
    assert "you may start another round" in NATIVE_SUBAGENT_2TO3_PROMPT
    assert "Do not force a second round" in NATIVE_SUBAGENT_2TO3_PROMPT


def test_native_dynamic_prompt_requires_initial_and_allows_multiround_fanout() -> None:
    prompt = NATIVE_DYNAMIC_1TO4_PROMPT
    normalized = " ".join(prompt.split())

    assert "runtime has already launched and joined" in normalized
    assert "Integrate those reports" in normalized
    assert "Choose one child for a localized issue" in normalized
    assert "specific evidence or test deliverable" in normalized
    assert "A JOIN does not end delegation" in normalized
    assert "may start another one-to-four-task round" in normalized
    assert "retain native DeepAgents repository tools" in normalized
    assert "Avoid overlapping write assignments" in normalized
    assert "exactly these two mandatory" not in normalized
    assert "spawning subagents is never required" not in normalized


def test_dynamic_initial_plan_accepts_model_selected_fanout_from_one_to_four() -> None:
    for count in range(1, 5):
        plan = DynamicInitialDelegationPlan(
            rationale="Independent evidence streams",
            tasks=[
                {
                    "role": f"analyst-{index}",
                    "description": f"Inspect independent area {index} and report evidence.",
                }
                for index in range(count)
            ],
        )
        assert len(_dynamic_initial_delegation_tasks(plan)) == count
    assert "one to four" in " ".join(
        NATIVE_DYNAMIC_INITIAL_PLANNER_PROMPT.split()
    )


def test_dynamic_initial_plan_decodes_json_array_in_tasks_field() -> None:
    plan = DynamicInitialDelegationPlan.model_validate(
        {
            "rationale": "Independent source and test analysis",
            "tasks": '[{"role":"source","description":"Inspect source and report."}]',
        }
    )

    assert [task.role for task in _dynamic_initial_delegation_tasks(plan)] == [
        "source"
    ]
    with pytest.raises(ValueError):
        DynamicInitialDelegationPlan.model_validate(
            {"rationale": "Invalid", "tasks": '{"role":"source"}'}
        )
    with pytest.raises(ValueError):
        DynamicInitialDelegationPlan.model_validate(
            {"rationale": "Invalid", "tasks": "not JSON"}
        )


def test_dynamic_initial_plan_repairs_duplicate_roles_and_tasks() -> None:
    duplicate_roles = DynamicInitialDelegationPlan(
        rationale="Independent evidence streams",
        tasks=[
            {"role": "analyst", "description": "Inspect source and report evidence."},
            {"role": "ANALYST", "description": "Inspect tests and report evidence."},
        ],
    )
    duplicate_tasks = DynamicInitialDelegationPlan(
        rationale="Independent evidence streams",
        tasks=[
            {"role": "source", "description": "Inspect source and report evidence."},
            {"role": "tests", "description": "Inspect source and report evidence."},
        ],
    )
    empty_task = DynamicInitialDelegationPlan(
        rationale="Independent evidence streams",
        tasks=[
            {"role": "source", "description": "  "},
            {"role": "", "description": "Inspect a valid independent area."},
        ],
    )

    role_tasks = _dynamic_initial_delegation_tasks(duplicate_roles)
    assert [item.role for item in role_tasks] == ["analyst", "ANALYST-2"]
    assert [item.source_role for item in role_tasks] == ["analyst", "ANALYST"]

    distinct_tasks = _dynamic_initial_delegation_tasks(duplicate_tasks)
    assert len(distinct_tasks) == 1
    assert distinct_tasks[0].role == "source"

    nonempty_tasks = _dynamic_initial_delegation_tasks(empty_task)
    assert len(nonempty_tasks) == 1
    assert nonempty_tasks[0].role == "analyst-2"
    assert nonempty_tasks[0].source_role is None


def test_planned_child_accepts_natural_text_and_marks_guarded_return_blocked() -> None:
    natural = _planned_child_completion(
        {"messages": [AIMessage(content="The failing invariant is in QuerySet._fetch_all.")]}
    )
    assert natural.status == "complete"
    assert "QuerySet._fetch_all" in natural.summary
    assert natural.confidence == "low"

    explicitly_blocked = _planned_child_completion(
        {"messages": [AIMessage(content="I could not complete the assigned test analysis.")]}
    )
    assert explicitly_blocked.status == "blocked"

    guarded = _planned_child_completion(
        {
            "messages": [AIMessage(content="Partial evidence found before the loop guard.")],
            "guard_ever_intervened": True,
            "guard_reason": "repeated_tool_call",
        }
    )
    assert guarded.status == "blocked"
    assert "repeated_tool_call" in guarded.unresolved
    empty = _planned_child_completion({"messages": [AIMessage(content="")]})
    assert empty.status == "blocked"
    assert empty.unresolved == ["no_natural_final_text"]


def test_autonomous_dynamic_profile_runs_planned_initial_children_then_native_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = DynamicInitialDelegationPlan(
        rationale="Separate implementation and regression evidence.",
        tasks=[
            {
                "role": "source-review",
                "description": "Trace the implementation and report its invariant.",
            },
            {
                "role": "test-review",
                "description": "Inspect the regression tests and report a focused test.",
            },
        ],
    )

    class FakePlanner:
        def with_structured_output(self, *_args: object, **_kwargs: object) -> FakePlanner:
            return self

        def invoke(self, *_args: object, **_kwargs: object) -> DynamicInitialDelegationPlan:
            return plan

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "beliefkv.experiments.deepagents_swebench._task_prompt",
        lambda *_args, **_kwargs: "Fix the reported issue.",
    )
    monkeypatch.setattr(
        "beliefkv.experiments.deepagents_swebench._model",
        lambda *_args, **_kwargs: FakePlanner(),
    )

    def run_children(_config: object, _workload: object, _backend: object,
                     _adapter: object, tasks: list[object], _artifact_dir: Path,
                     *, group_id: str, deadline_controller: object) -> list[dict[str, str]]:
        del deadline_controller
        captured["tasks"] = tasks
        captured["group_id"] = group_id
        return [
            {
                "role": "source-review",
                "report": "source evidence",
            },
            {
                "role": "test-review",
                "report": "test evidence",
            },
        ]

    monkeypatch.setattr(
        "beliefkv.experiments.deepagents_swebench._run_declared_analysis_children",
        run_children,
    )

    def build_root(*_args: object, delegation_enabled: bool) -> str:
        captured["delegation_enabled"] = delegation_enabled
        return "root-agent"

    monkeypatch.setattr(
        "beliefkv.experiments.deepagents_swebench._build_autonomous_agent",
        build_root,
    )

    def invoke(agent: str, inputs: dict[str, object], _config: dict[str, object]) -> dict[str, bool]:
        captured["agent"] = agent
        captured["root_prompt"] = inputs["messages"][0]["content"]  # type: ignore[index]
        return {"ok": True}

    monkeypatch.setattr(
        "beliefkv.experiments.deepagents_swebench._invoke_with_partial_state",
        invoke,
    )
    config = DeepAgentsExperimentConfig(
        mode="autonomous",
        base_url="http://localhost:18000/v1",
        model="model",
        output_dir=tmp_path / "output",
        workload_manifest=tmp_path / "manifest.json",
        docker_image="fixture:latest",
        subagent_fanout_profile="native_dynamic_1to4",
    )
    workload = SweBenchWorkload(
        instance_id="django__django-1",
        repo="django/django",
        base_commit="deadbeef",
        problem_statement="Fix the reported issue.",
        difficulty="unknown",
    )

    result, plan_payload, reports = _run_autonomous(
        config,
        workload,
        SimpleNamespace(workspace=tmp_path),
        SimpleNamespace(),
        tmp_path,
        SimpleNamespace(),
    )

    assert result == {"ok": True}
    assert plan_payload is not None and len(plan_payload["tasks"]) == 2
    assert plan_payload["dispatch_tasks"] == [
        {
            "source_role": "source-review",
            "dispatch_role": "source-review",
            "description": (
                "Trace the implementation and report its invariant."
            ),
        },
        {
            "source_role": "test-review",
            "dispatch_role": "test-review",
            "description": (
                "Inspect the regression tests and report a focused test."
            ),
        },
    ]
    assert len(reports) == 2
    assert len(captured["tasks"]) == 2  # type: ignore[arg-type]
    assert captured["group_id"] == "native-initial:django__django-1"
    assert captured["delegation_enabled"] is True
    assert captured["agent"] == "root-agent"
    assert "source evidence" in str(captured["root_prompt"])
    assert "test evidence" in str(captured["root_prompt"])


def test_autonomous_tool_prompt_requires_a_strategy_change_after_repeat() -> None:
    normalized = " ".join(AUTONOMOUS_SYSTEM_PROMPT.split())

    assert "Do not repeat the same tool with the same arguments" in normalized
    assert "After one unchanged repeat" in normalized
    assert "switch to a materially different action" in normalized
    assert "A new call ID or slightly altered probe" in normalized


def test_native_dynamic_profile_selects_dynamic_supervisor_prompt(
    tmp_path: Path,
) -> None:
    config = DeepAgentsExperimentConfig(
        mode="autonomous",
        base_url="http://localhost:18000/v1",
        model="model",
        output_dir=tmp_path / "output",
        workload_manifest=tmp_path / "workloads.json",
        docker_image="fixture:latest",
        subagent_fanout_profile="native_dynamic_1to4",
    )

    assert _autonomous_fanout_prompt(
        config,
        delegation_enabled=True,
    ) == NATIVE_DYNAMIC_1TO4_PROMPT
    assert _autonomous_fanout_prompt(
        config,
        delegation_enabled=False,
    ) == ""


def test_second_native_delegation_round_keeps_root_call_budget() -> None:
    policy = LoopGuardPolicy(
        enforce_call_budgets=True,
        max_tool_calls_without_completion=4,
        max_model_calls_without_completion=20,
    )
    messages: list[BaseMessage] = []
    for round_index in range(2):
        call_ids = [
            f"round-{round_index}-child-{child_index}"
            for child_index in range(2)
        ]
        messages.append(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": call_id},
                        "id": call_id,
                    }
                    for call_id in call_ids
                ],
            )
        )
        messages.extend(
            ToolMessage(
                content=f"JOIN_ALL result for {call_id}",
                name="task",
                tool_call_id=call_id,
            )
            for call_id in call_ids
        )
        snapshot = analyze_agent_history(messages, policy)
        assert snapshot.model_calls == round_index + 1
        assert snapshot.tool_calls == (round_index + 1) * 2
        assert snapshot.completed_tool_calls == snapshot.tool_calls
        assert snapshot.reason == (
            "tool_call_budget_exhausted" if round_index else None
        )


def test_native_subagent_profile_builds_read_only_children(tmp_path: Path) -> None:
    config = DeepAgentsExperimentConfig(
        mode="autonomous",
        base_url="http://localhost:18000/v1",
        model="model",
        output_dir=tmp_path / "output",
        workload_manifest=tmp_path / "workloads.json",
        docker_image="fixture:latest",
        subagent_fanout_profile="native_subagent_2to3",
        stop_after_first_native_join=True,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    backend = DockerWorkspaceBackend(
        workspace,
        image="fixture:latest",
        audit=audit,
        support_dir=None,
    )
    model = FakeMessagesListChatModel(responses=[AIMessage(content="done")])
    try:
        specs = _autonomous_subagents(
            config,
            SweBenchWorkload(
                instance_id="pydata__xarray-1",
                repo="pydata/xarray",
                base_commit="deadbeef",
                problem_statement="Fix an invariant.",
                difficulty="unknown",
            ),
            backend,
            SimpleNamespace(record_call_censor=lambda _record: None),
            model,
            model,
        )
    finally:
        audit.close()
    assert [item["name"] for item in specs] == [
        "repository-explorer",
        "test-analyst",
        "compatibility-analyst",
    ]
    assert all(item["tools"] == [] for item in specs)


def test_native_dynamic_profile_keeps_native_child_tools(tmp_path: Path) -> None:
    config = DeepAgentsExperimentConfig(
        mode="autonomous",
        base_url="http://localhost:18000/v1",
        model="model",
        output_dir=tmp_path / "output",
        workload_manifest=tmp_path / "workloads.json",
        docker_image="fixture:latest",
        subagent_fanout_profile="native_dynamic_1to4",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    backend = DockerWorkspaceBackend(
        workspace,
        image="fixture:latest",
        audit=audit,
        support_dir=None,
    )
    model = FakeMessagesListChatModel(responses=[AIMessage(content="done")])
    try:
        specs = _autonomous_subagents(
            config,
            SweBenchWorkload(
                instance_id="pydata__xarray-1",
                repo="pydata/xarray",
                base_commit="deadbeef",
                problem_statement="Fix an invariant.",
                difficulty="unknown",
            ),
            backend,
            SimpleNamespace(record_call_censor=lambda _record: None),
            model,
            model,
        )
    finally:
        audit.close()

    assert [item["name"] for item in specs] == [
        "repository-explorer",
        "test-analyst",
        "implementation-agent",
        "general-purpose",
    ]
    assert all(item["tools"] for item in specs)
    assert any(
        "implement" in item["system_prompt"].lower()
        for item in specs
    )


def test_semantic_gate_rejects_non_native_profile(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="native_subagent_2to3"):
        DeepAgentsExperimentConfig(
            mode="autonomous",
            base_url="http://localhost:18000/v1",
            model="model",
            output_dir=tmp_path / "output",
            workload_manifest=tmp_path / "workloads.json",
            docker_image="fixture:latest",
            stop_after_first_native_join=True,
        )


def test_parallel_analysis_plan_always_materializes_two_orthogonal_children() -> None:
    tasks = _parallel_analysis_tasks(
        ParallelAnalysisPlan(
            rationale="The code path and failure evidence are independent.",
            repository_analysis="Trace index construction and its invariants.",
            test_analysis="Reproduce the repr failure and identify regression tests.",
        )
    )

    assert [item.role for item in tasks] == [
        "repository-explorer",
        "test-analyst",
    ]


def test_parallel_analysis_plan_materializes_optional_compatibility_child() -> None:
    tasks = _parallel_analysis_tasks(
        ParallelAnalysisPlan(
            rationale="Serialization behavior is independent.",
            repository_analysis="Trace the implementation.",
            test_analysis="Reproduce the failure.",
            compatibility_analysis="Check the serialization compatibility contract.",
        )
    )

    assert [item.role for item in tasks] == [
        "repository-explorer",
        "test-analyst",
        "compatibility-analyst",
    ]


def test_parallel_analysis_plan_rejects_empty_mandatory_task() -> None:
    with pytest.raises(ValueError, match="two non-empty"):
        _parallel_analysis_tasks(
            ParallelAnalysisPlan(
                rationale="invalid",
                repository_analysis=" ",
                test_analysis="Reproduce the failure.",
            )
        )


def test_trace_summary_reports_parallel_fanout_shape(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    records = [
        {"kind": "workflow_start", "ts_ms": 0},
        {
            "kind": "invocation_create",
            "invocation_id": "p",
            "parent_invocation_id": None,
            "relation_type": "root",
            "ts_ms": 0,
        },
        {
            "kind": "invocation_create",
            "invocation_id": "a",
            "parent_invocation_id": "p",
            "agent_definition_id": "explorer",
            "ts_ms": 10,
        },
        {
            "kind": "spawn",
            "invocation_id": "p",
            "target_invocation_id": "a",
            "ts_ms": 10,
        },
        {
            "kind": "invocation_create",
            "invocation_id": "b",
            "parent_invocation_id": "p",
            "agent_definition_id": "tester",
            "ts_ms": 10,
        },
        {
            "kind": "spawn",
            "invocation_id": "p",
            "target_invocation_id": "b",
            "ts_ms": 10,
        },
        {"kind": "join_create", "ts_ms": 10, "attributes": {"mode": "all"}},
        {"kind": "return", "invocation_id": "a", "ts_ms": 30},
        {"kind": "return", "invocation_id": "b", "ts_ms": 50},
        {"kind": "join_satisfied", "ts_ms": 50},
        {"kind": "return", "invocation_id": "p", "ts_ms": 55},
        {"kind": "workflow_end", "ts_ms": 60},
    ]
    path.write_text("".join(json.dumps(item) + "\n" for item in records))
    summary = _trace_summary(path)
    assert summary["fanout_by_parent"] == {"p": 2}
    assert summary["peak_concurrent_children"] == 2
    assert summary["join_type_counts"] == {"all": 1}
    assert summary["natural_child_return_count"] == 2
    assert summary["root_return_count"] == 1
    assert summary["child_cancel_count"] == 0
    assert summary["child_return_rate_by_role"] == {
        "explorer": {
            "spawned": 1,
            "returned": 1,
            "cancelled": 0,
            "return_rate": 1.0,
        },
        "tester": {
            "spawned": 1,
            "returned": 1,
            "cancelled": 0,
            "return_rate": 1.0,
        },
    }
    assert summary["join_satisfied_count"] == 1
    assert summary["join_timeout_count"] == 0
    assert summary["child_return_span_ms"] == 20


def test_trace_summary_reports_post_join_delegation_round(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    rounds = (
        ("j1", 10, ("a", "b")),
        ("j2", 75, ("c", "d", "e")),
    )
    records = [{"kind": "workflow_start", "ts_ms": 0}]
    records.extend(
        {
            "kind": "invocation_create",
            "invocation_id": child,
            "parent_invocation_id": "p",
            "join_id": join_id,
            "ts_ms": ts_ms,
        }
        for join_id, ts_ms, children in rounds
        for child in children
    )
    records.extend(
        {
            "kind": "spawn",
            "invocation_id": "p",
            "target_invocation_id": child,
            "ts_ms": ts_ms,
        }
        for _join_id, ts_ms, children in rounds
        for child in children
    )
    records.extend(
        [
            {
                "kind": "join_create",
                "join_id": "j1",
                "ts_ms": 10,
                "attributes": {"mode": "all"},
            },
            {"kind": "return", "invocation_id": "a", "ts_ms": 30},
            {"kind": "return", "invocation_id": "b", "ts_ms": 40},
            {"kind": "join_satisfied", "join_id": "j1", "ts_ms": 40},
            {
                "kind": "join_create",
                "join_id": "j2",
                "ts_ms": 75,
                "attributes": {"mode": "all"},
            },
            {"kind": "return", "invocation_id": "c", "ts_ms": 90},
            {"kind": "return", "invocation_id": "d", "ts_ms": 100},
            {"kind": "invocation_cancel", "invocation_id": "e", "ts_ms": 105},
            {"kind": "join_timeout", "join_id": "j2", "ts_ms": 110},
            {"kind": "workflow_end", "ts_ms": 120},
        ]
    )
    path.write_text("".join(json.dumps(item) + "\n" for item in records))

    summary = _trace_summary(path)

    assert summary["delegation_round_count"] == 2
    assert summary["fanout_per_round"] == [2, 3]
    assert summary["children_per_parent"] == {"p": 5}
    assert summary["post_join_spawn_count"] == 3
    assert summary["join_to_next_spawn_ms"] == [35.0]
    assert summary["natural_child_return_count"] == 4
    assert summary["root_return_count"] == 0
    assert summary["child_cancel_count"] == 1
    assert summary["join_satisfied_count"] == 1
    assert summary["join_timeout_count"] == 1
    assert summary["all_subagents_returned"] is False
    assert summary["all_joins_satisfied"] is False


def test_workflow_deadline_cancels_requests_tasks_and_commands_in_order() -> None:
    now = [100.0]
    deadline = ActivationDeadline(clock=lambda: now[0])
    events: list[tuple[str, dict[str, object]]] = []

    class Audit:
        def emit(self, event: str, **fields: object) -> None:
            events.append((event, fields))

    class Adapter:
        def cancel_pending_tasks(self, *, reason: str) -> int:
            assert "deadline" in reason
            return 2

    class Backend:
        def cancel_active_commands(self, *, reason: str) -> int:
            assert "deadline" in reason
            return 1

    class Model:
        active = 1

        def cancel_active_requests(self) -> int:
            self.active = 0
            return 1

        def active_request_count(self) -> int:
            return self.active

    controller = WorkflowDeadlineController(
        deadline=deadline,
        adapter=Adapter(),
        backend=Backend(),
        audit=Audit(),
    )
    controller.register_model(Model())
    deadline.start(5.0)
    now[0] = 105.0

    assert controller.cancel_if_expired()
    summary = controller.close()

    assert [
        event for event, _fields in events if event.startswith("workflow_deadline_")
    ] == [
        "workflow_deadline_expired",
        "workflow_deadline_abort_sent",
        "workflow_deadline_server_terminal",
        "workflow_deadline_cleanup_complete",
    ]
    assert summary["abort_requested_count"] == 1
    assert summary["server_terminal"] is True
    assert 0.0 <= summary["server_terminal_latency_ms"] <= 5000.0
    assert summary["pending_task_cancel_count"] == 2
    assert summary["active_command_cancel_count"] == 1
    assert summary["cleanup_complete"] is True


def test_workflow_deadline_controller_can_be_disabled() -> None:
    events: list[str] = []

    class Audit:
        def emit(self, event: str, **_fields: object) -> None:
            events.append(event)

    class Adapter:
        def cancel_pending_tasks(self, *, reason: str) -> int:
            raise AssertionError(reason)

    class Backend:
        def cancel_active_commands(self, *, reason: str) -> int:
            raise AssertionError(reason)

    controller = WorkflowDeadlineController(
        deadline=ActivationDeadline(),
        adapter=Adapter(),
        backend=Backend(),
        audit=Audit(),
    )

    controller.start(None)
    summary = controller.close()

    assert events == ["workflow_deadline_disabled"]
    assert summary["enabled"] is False
    assert summary["expired"] is False
    assert summary["cleanup_complete"] is True


def test_workflow_deadline_reports_server_terminal_before_slow_cleanup() -> None:
    now = [100.0]
    deadline = ActivationDeadline(clock=lambda: now[0])
    events: list[tuple[str, dict[str, object]]] = []

    class Audit:
        def emit(self, event: str, **fields: object) -> None:
            events.append((event, fields))

    class Adapter:
        def cancel_pending_tasks(self, *, reason: str) -> int:
            assert "deadline" in reason
            time.sleep(0.15)
            return 2

    class Backend:
        def cancel_active_commands(self, *, reason: str) -> int:
            assert "deadline" in reason
            time.sleep(0.15)
            return 1

    class Model:
        active = 1

        def cancel_active_requests(self) -> int:
            self.active = 0
            time.sleep(0.15)
            return 1

        def active_request_count(self) -> int:
            return self.active

    controller = WorkflowDeadlineController(
        deadline=deadline,
        adapter=Adapter(),
        backend=Backend(),
        audit=Audit(),
    )
    controller.server_terminal_timeout_s = 0.5
    controller.register_model(Model())
    deadline.start(5.0)
    now[0] = 105.0

    assert controller.cancel_if_expired()
    summary = controller.close()

    terminal = next(
        fields
        for event, fields in events
        if event == "workflow_deadline_server_terminal"
    )
    cleanup = next(
        fields
        for event, fields in events
        if event == "workflow_deadline_cleanup_complete"
    )
    assert terminal["server_terminal"] is True
    assert terminal["latency_ms"] < 100.0
    assert cleanup["cleanup_latency_ms"] >= 100.0
    assert summary["cleanup_complete"] is True
    assert summary["cleanup_errors"] == []


def test_repository_contract_does_not_duplicate_django_checkout_root() -> None:
    workload = SweBenchWorkload(
        instance_id="django__django-11138",
        repo="django/django",
        base_commit="deadbeef",
        problem_statement="Fix the database backend.",
        difficulty="unknown",
    )

    contract = repository_sandbox_contract(workload)

    assert "checkout root is still exactly\n  `/workspace`" in contract
    assert "/workspace/django/db/backends/base/base.py" in contract
    assert "/workspace/django/django/db/backends/base/base.py" in contract
    assert "not\n  `/workspace/django/django" in contract
    assert "python tests/runtests.py" in contract


def test_autonomous_subagents_have_independent_context_lifecycles(
    tmp_path: Path,
) -> None:
    config = DeepAgentsExperimentConfig(
        mode="autonomous",
        base_url="http://localhost:18000/v1",
        model="model",
        output_dir=tmp_path / "output",
        workload_manifest=tmp_path / "workloads.json",
        docker_image="fixture:latest",
        max_completion_tokens=4_096,
        context_lifecycle=ContextLifecyclePolicy(
            window_tokens=32_768,
            keep_tokens=8_192,
            intermediate_output_tokens=4_096,
            summary_output_tokens=2_048,
        ),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    audit = JsonlAudit(tmp_path / "sandbox.jsonl")
    backend = DockerWorkspaceBackend(
        workspace,
        image="fixture:latest",
        audit=audit,
        support_dir=None,
    )
    adapter = SimpleNamespace(record_call_censor=lambda _record: None)
    model = FakeMessagesListChatModel(responses=[AIMessage(content="done")])
    try:
        subagents = _autonomous_subagents(
            config,
            SweBenchWorkload(
                instance_id="django__django-11138",
                repo="django/django",
                base_commit="deadbeef",
                problem_statement="Fix the database backend.",
                difficulty="unknown",
            ),
            backend,
            adapter,
            model,
            model,
        )
    finally:
        audit.close()

    lifecycles = [
        next(
            item
            for item in spec["middleware"]
            if isinstance(item, ContextLifecycleMiddleware)
        )
        for spec in subagents
    ]
    assert len(lifecycles) == 4
    assert len({id(item) for item in lifecycles}) == len(lifecycles)
    assert all(not item.persist_cursor_across_invocations for item in lifecycles)
    assert all(
        "/workspace/django/db/backends/base/base.py" in spec["system_prompt"]
        for spec in subagents
    )
    assert all(
        DELEGATED_TASK_FOCUS_INSTRUCTION in spec["system_prompt"]
        for spec in subagents
    )
    assert all(item.policy.window_tokens == 32_768 for item in lifecycles)
    assert all(
        any(
            isinstance(item, CompletionBudgetMiddleware)
            and item.intermediate_tokens == 4_096
            and item.final_tokens == 4_096
            for item in spec["middleware"]
        )
        for spec in subagents
    )
    assert all(
        any(
            isinstance(item, EmptyReasoningRecoveryMiddleware)
            for item in spec["middleware"]
        )
        for spec in subagents
    )


@pytest.mark.parametrize("finish_reason", ["stop", "length"])
def test_empty_reasoning_terminal_retries_once_without_thinking(
    tmp_path: Path, finish_reason: str
) -> None:
    audit = JsonlAudit(tmp_path / "retry.jsonl")
    middleware = EmptyReasoningRecoveryMiddleware(audit=audit, scope="child")
    request = ModelRequest(
        model=FakeMessagesListChatModel(responses=[AIMessage(content="unused")]),
        messages=[HumanMessage(content="inspect")],
        model_settings={
            "max_tokens": 8192,
            "extra_body": {"chat_template_kwargs": {"some_setting": 1}},
        },
    )
    first = ModelResponse(
        result=[
            AIMessage(
                content="",
                additional_kwargs={"reasoning_content": "I should report findings."},
                response_metadata={
                    "finish_reason": finish_reason,
                    "token_usage": {"reasoning_tokens": 16},
                },
            )
        ]
    )
    second = ModelResponse(result=[AIMessage(content="A useful report.")])
    calls: list[ModelRequest] = []

    def handler(attempt: ModelRequest) -> ModelResponse:
        calls.append(attempt)
        return first if len(calls) == 1 else second

    try:
        assert middleware.wrap_model_call(request, handler) is second
    finally:
        audit.close()
    assert len(calls) == 2
    assert calls[0].model_settings == request.model_settings
    assert calls[1].model_settings == {
        "max_tokens": 8192,
        "extra_body": {
            "chat_template_kwargs": {
                "some_setting": 1,
                "enable_thinking": False,
            }
        },
    }
    assert request.model_settings["extra_body"]["chat_template_kwargs"] == {
        "some_setting": 1
    }
    events = [
        json.loads(line)
        for line in (tmp_path / "retry.jsonl").read_text().splitlines()
    ]
    assert [item["event"] for item in events] == [
        "agent_empty_reasoning_retry",
        "agent_empty_reasoning_retry_result",
    ]
    assert events[0]["finish_reason"] == finish_reason
    assert events[-1]["recovered"] is True


@pytest.mark.parametrize(
    "message",
    [
        AIMessage(content="A natural report.", response_metadata={"finish_reason": "stop"}),
        AIMessage(
            content="",
            tool_calls=[{"name": "read_file", "args": {}, "id": "call-1"}],
            response_metadata={
                "finish_reason": "tool_calls",
                "token_usage": {"reasoning_tokens": 16},
            },
        ),
    ],
)
def test_empty_reasoning_recovery_does_not_retry_other_outputs(
    tmp_path: Path, message: AIMessage
) -> None:
    audit = JsonlAudit(tmp_path / "retry.jsonl")
    middleware = EmptyReasoningRecoveryMiddleware(audit=audit, scope="child")
    request = ModelRequest(
        model=FakeMessagesListChatModel(responses=[AIMessage(content="unused")]),
        messages=[HumanMessage(content="inspect")],
    )
    response = ModelResponse(result=[message])
    calls = 0

    def handler(_request: ModelRequest) -> ModelResponse:
        nonlocal calls
        calls += 1
        return response

    try:
        assert middleware.wrap_model_call(request, handler) is response
    finally:
        audit.close()
    assert calls == 1


@pytest.mark.parametrize("finish_reason", ["stop", "length"])
def test_empty_stream_terminal_without_reasoning_metadata_retries_once(
    tmp_path: Path, finish_reason: str
) -> None:
    audit = JsonlAudit(tmp_path / "retry.jsonl")
    middleware = EmptyReasoningRecoveryMiddleware(audit=audit, scope="child")
    request = ModelRequest(
        model=FakeMessagesListChatModel(responses=[AIMessage(content="unused")]),
        messages=[HumanMessage(content="inspect")],
    )
    empty = ModelResponse(result=[
        AIMessage(content="", response_metadata={"finish_reason": finish_reason})
    ])
    complete = ModelResponse(result=[
        AIMessage(content="Child finished.", response_metadata={"finish_reason": "stop"})
    ])
    calls = 0

    def handler(_request: ModelRequest) -> ModelResponse:
        nonlocal calls
        calls += 1
        return empty if calls == 1 else complete

    try:
        assert middleware.wrap_model_call(request, handler) is complete
    finally:
        audit.close()
    assert calls == 2


def test_empty_reasoning_recovery_never_forges_a_child_result(
    tmp_path: Path,
) -> None:
    audit = JsonlAudit(tmp_path / "retry.jsonl")
    middleware = EmptyReasoningRecoveryMiddleware(audit=audit, scope="child")
    request = ModelRequest(
        model=FakeMessagesListChatModel(responses=[AIMessage(content="unused")]),
        messages=[HumanMessage(content="inspect")],
    )
    blank = ModelResponse(
        result=[
            AIMessage(
                content="",
                response_metadata={
                    "finish_reason": "stop",
                    "token_usage": {"reasoning_tokens": 20},
                },
            )
        ]
    )
    calls = 0

    def handler(_request: ModelRequest) -> ModelResponse:
        nonlocal calls
        calls += 1
        return blank

    try:
        assert middleware.wrap_model_call(request, handler) is blank
    finally:
        audit.close()
    assert calls == 2
    events = [
        json.loads(line)
        for line in (tmp_path / "retry.jsonl").read_text().splitlines()
    ]
    assert events[-1]["recovered"] is False


@pytest.mark.parametrize(
    ("ack_timeout_s", "ack_retries"),
    [(0.0, 3), (10.0, 0)],
)
def test_experiment_config_rejects_invalid_runtime_event_ack_policy(
    tmp_path: Path,
    ack_timeout_s: float,
    ack_retries: int,
) -> None:
    with pytest.raises(ValueError, match="runtime-event ACK"):
        DeepAgentsExperimentConfig(
            mode="planned",
            base_url="http://localhost:18000/v1",
            model="model",
            output_dir=tmp_path,
            workload_manifest=tmp_path / "workloads.json",
            docker_image="fixture:latest",
            runtime_event_ack_timeout_s=ack_timeout_s,
            runtime_event_ack_retries=ack_retries,
        )


def test_planner_schema_uses_qwen_compatible_flat_tasks() -> None:
    plan = DelegationPlan.model_validate(
        {"rationale": "Parallel repository evidence", "tasks": ["Inspect parser"]}
    )
    assert plan.tasks == ["Inspect parser"]

    with pytest.raises(ValueError):
        DelegationPlan.model_validate(
            {
                "rationale": "Too many overlapping tasks",
                "tasks": ["one", "two", "three"],
            }
        )


def test_planned_children_use_a_shorter_analysis_budget(tmp_path: Path) -> None:
    config = DeepAgentsExperimentConfig(
        mode="planned",
        base_url="http://localhost:18000/v1",
        model="model",
        output_dir=tmp_path,
        workload_manifest=tmp_path / "workloads.json",
        docker_image="fixture:latest",
        loop_guard=LoopGuardPolicy(
            repeated_call_limit=5,
            max_model_calls_without_completion=32,
            max_tool_calls_without_completion=64,
        ),
    )

    policy = _planned_child_loop_guard_policy(config)
    assert policy.repeated_call_limit == 3
    assert policy.max_model_calls_without_completion == 12
    assert policy.max_tool_calls_without_completion == 16


def test_agent_stream_failure_preserves_latest_state() -> None:
    class FailingAgent:
        def stream(self, inputs, *, config, stream_mode):
            assert inputs == {"messages": []}
            assert config == {"recursion_limit": 2}
            assert stream_mode == "values"
            yield {"messages": ["partial"]}
            raise RuntimeError("limit")

    with pytest.raises(PartialAgentRunError) as caught:
        _invoke_with_partial_state(
            FailingAgent(), {"messages": []}, {"recursion_limit": 2}
        )
    assert caught.value.partial_result == {"messages": ["partial"]}


def _tool_exchange(
    name: str,
    args: dict[str, object],
    call_id: str,
    output: str,
) -> list[object]:
    return [
        AIMessage(
            content="",
            tool_calls=[{"name": name, "args": args, "id": call_id}],
        ),
        ToolMessage(content=output, tool_call_id=call_id, name=name),
    ]


def test_loop_guard_detects_repeated_and_alternating_calls() -> None:
    policy = LoopGuardPolicy()
    repeated = []
    for index in range(4):
        repeated.extend(_tool_exchange("ls", {"path": "/src"}, str(index), "same"))
    assert analyze_agent_history(repeated, policy).reason == "repeated_tool_call"

    alternating = []
    for index, path in enumerate(("/a", "/b", "/a", "/b", "/a", "/b", "/a", "/b")):
        alternating.extend(_tool_exchange("read_file", {"path": path}, str(index), path))
    assert analyze_agent_history(alternating, policy).reason == "alternating_tool_cycle"


def test_repeated_tool_signature_with_new_output_counts_as_progress() -> None:
    messages = []
    for index in range(4):
        messages.extend(
            _tool_exchange(
                "read_file",
                {"path": "/src/module.py"},
                str(index),
                f"new evidence {index}",
            )
        )

    snapshot = analyze_agent_history(messages, LoopGuardPolicy())

    assert snapshot.reason is None
    assert snapshot.consecutive_no_progress == 0


def test_loop_guard_resets_after_a_persistent_thread_completion() -> None:
    old_activation = []
    for index in range(4):
        old_activation.extend(
            _tool_exchange("read_file", {"path": f"/old-{index}"}, str(index), "old")
        )
    old_activation.extend(
        _tool_exchange(
            "AgenticPeerDecision",
            {"complete": False, "next_role": "reviewer"},
            "peer-decision",
            "accepted",
        )
    )
    current_activation = _tool_exchange(
        "read_file", {"path": "/current"}, "current", "new evidence"
    )

    snapshot = analyze_agent_history(
        [*old_activation, HumanMessage(content="Resume"), *current_activation],
        LoopGuardPolicy(max_model_calls_without_completion=4),
        completion_tool_names=frozenset({"AgenticPeerDecision"}),
    )

    assert snapshot.model_calls == 1
    assert snapshot.tool_calls == 1
    assert snapshot.reason is None


def test_loop_guard_detects_errors_no_progress_and_completion_budget() -> None:
    errors = []
    for index in range(3):
        errors.extend(
            _tool_exchange(
                "read_file",
                {"path": f"/missing-{index}"},
                str(index),
                "Error: path_not_found",
            )
        )
    assert analyze_agent_history(errors, LoopGuardPolicy()).reason == (
        "consecutive_tool_errors"
    )

    source_reads = []
    for index in range(3):
        source_reads.extend(
            _tool_exchange(
                "read_file",
                {"path": f"/source-{index}"},
                str(index),
                "def timeout_handler():\n    return 'error: documented value'",
            )
        )
    source_snapshot = analyze_agent_history(source_reads, LoopGuardPolicy())
    assert source_snapshot.consecutive_errors == 0

    no_progress_policy = LoopGuardPolicy(
        repeated_call_limit=99,
        consecutive_no_progress_limit=3,
    )
    no_progress = []
    for index in range(4):
        no_progress.extend(
            _tool_exchange("grep", {"pattern": "same"}, str(index), "same output")
        )
    assert analyze_agent_history(no_progress, no_progress_policy).reason == (
        "no_observable_progress"
    )

    budget_policy = LoopGuardPolicy(
        max_model_calls_without_completion=4,
        enforce_call_budgets=True,
    )
    exploring = []
    for index in range(4):
        exploring.extend(
            _tool_exchange(
                "read_file",
                {"path": f"/new-{index}"},
                str(index),
                f"unique output {index}",
            )
        )
    assert analyze_agent_history(exploring, budget_policy).reason == (
        "completion_budget_exhausted"
    )

    tool_budget_policy = LoopGuardPolicy(
        max_model_calls_without_completion=99,
        max_tool_calls_without_completion=4,
        enforce_call_budgets=True,
    )
    multi_tool = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "probe",
                    "args": {"path": f"/{index}"},
                    "id": f"probe-{index}",
                }
                for index in range(4)
            ],
        )
    ]
    assert analyze_agent_history(multi_tool, tool_budget_policy).reason == (
        "tool_call_budget_exhausted"
    )

    assert analyze_agent_history(
        exploring,
        LoopGuardPolicy(max_model_calls_without_completion=4),
    ).reason is None


def test_structured_tool_error_triggers_failed_call_circuit_breaker() -> None:
    messages = []
    for index in range(2):
        call_id = f"edit-{index}"
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "edit_file",
                            "args": {
                                "file_path": "/workspace/module.py",
                                "old_string": "old",
                                "new_string": "new",
                            },
                            "id": call_id,
                        }
                    ],
                ),
                ToolMessage(
                    content=[
                        {
                            "type": "text",
                            "text": "Error: String not found in file: 'old'",
                        }
                    ],
                    tool_call_id=call_id,
                    name="edit_file",
                    status="error",
                ),
            ]
        )

    outcome = classify_tool_outcome(messages[-1], tool_name="edit_file")
    snapshot = analyze_agent_history(messages, LoopGuardPolicy())

    assert outcome.status == "error"
    assert outcome.error_class == "string_not_found"
    assert snapshot.repeated_failed_calls == 2
    assert snapshot.reason == "repeated_failed_tool_call"


def test_suppressed_retry_is_not_counted_as_second_physical_failure() -> None:
    args = {"pattern": 401, "path": "/workspace"}
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"name": "grep", "args": args, "id": "grep-1"}],
        ),
        ToolMessage(
            content="Input should be a valid string",
            tool_call_id="grep-1",
            name="grep",
            status="error",
            additional_kwargs={
                "beliefkv_error_class": "validation_error",
                "beliefkv_physical_execution": True,
                "beliefkv_suppressed_repeat_intent": False,
                "beliefkv_failure_episode_id": "failure-1",
            },
        ),
        AIMessage(
            content="",
            tool_calls=[{"name": "grep", "args": args, "id": "grep-2"}],
        ),
        ToolMessage(
            content="duplicate_suppressed",
            tool_call_id="grep-2",
            name="grep",
            status="error",
            additional_kwargs={
                "beliefkv_error_class": "duplicate_suppressed",
                "beliefkv_physical_execution": False,
                "beliefkv_suppressed_repeat_intent": True,
                "beliefkv_failure_episode_id": "failure-1",
            },
        ),
    ]

    snapshot = analyze_agent_history(
        messages,
        LoopGuardPolicy(
            repeated_call_limit=99,
            consecutive_error_limit=99,
            consecutive_no_progress_limit=99,
        ),
    )

    assert snapshot.physical_failure_count == 1
    assert snapshot.suppressed_repeat_intent_count == 1
    assert snapshot.repeated_failed_calls == 1
    assert snapshot.reason is None


def test_tool_status_middleware_preserves_content_and_marks_semantic_error() -> None:
    original = ToolMessage(
        content="output\n[Command failed with exit code 1]",
        tool_call_id="execute-1",
        name="execute",
        status="success",
    )
    request = type(
        "FixtureRequest",
        (),
        {"tool_call": {"name": "execute", "id": "execute-1"}},
    )()

    normalized = ToolOutcomeStatusMiddleware().wrap_tool_call(
        request,
        lambda unused: original,
    )

    assert isinstance(normalized, ToolMessage)
    assert normalized.status == "error"
    assert normalized.content == original.content
    assert original.status == "success"


def _circuit_request(
    *,
    call_id: str,
    command: str,
    conversation: str = "conversation-1",
    tool_name: str = "execute",
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={
            "name": tool_name,
            "args": {"command": command},
            "id": call_id,
        },
        tool=None,
        state={"messages": [HumanMessage(content=conversation, id=conversation)]},
        runtime=SimpleNamespace(config={}),
    )


def test_tool_circuit_does_not_suppress_successful_repeated_calls() -> None:
    epoch = [0]
    executed: list[str] = []
    censored: list[dict[str, Any]] = []
    circuit = ToolCircuitBreakerMiddleware(
        state_epoch=lambda: epoch[0],
        audit=None,
        scope="circuit-test",
        censor_observer=lambda fields: censored.append(dict(fields)),
    )

    def handler(request: ToolCallRequest) -> ToolMessage:
        executed.append(str(request.tool_call["id"]))
        return ToolMessage(
            content="1 passed",
            tool_call_id=str(request.tool_call["id"]),
            name="execute",
        )

    first = circuit.wrap_tool_call(
        _circuit_request(call_id="call-1", command="pytest test_a.py"),
        handler,
    )
    confirmation = circuit.wrap_tool_call(
        _circuit_request(call_id="call-2", command="pytest test_a.py"),
        handler,
    )
    repeated = circuit.wrap_tool_call(
        _circuit_request(call_id="call-3", command="pytest test_a.py"),
        handler,
    )
    assert first.status == "success"
    assert confirmation.status == "success"
    assert repeated.status == "success"
    assert first.additional_kwargs["beliefkv_physical_execution"] is True
    assert repeated.additional_kwargs["beliefkv_physical_execution"] is True
    assert executed == ["call-1", "call-2", "call-3"]
    assert censored == []

    epoch[0] += 1
    rerun = circuit.wrap_tool_call(
        _circuit_request(call_id="call-4", command="pytest test_a.py"),
        handler,
    )
    assert rerun.status == "success"
    assert executed == ["call-1", "call-2", "call-3", "call-4"]


def test_tool_circuit_suppresses_repeated_success_without_effect() -> None:
    epoch = [17]
    executed: list[str] = []
    censored: list[dict[str, Any]] = []
    circuit = ToolCircuitBreakerMiddleware(
        state_epoch=lambda: epoch[0],
        audit=None,
        scope="successful-no-effect-test",
        censor_observer=lambda fields: censored.append(dict(fields)),
    )

    def handler(request: ToolCallRequest) -> ToolMessage:
        executed.append(str(request.tool_call["id"]))
        return ToolMessage(
            content="\n[Command succeeded with exit code 0]",
            tool_call_id=str(request.tool_call["id"]),
            name="execute",
        )

    results = [
        circuit.wrap_tool_call(
            _circuit_request(
                call_id=f"no-effect-{index}",
                command="python -c '# only a comment'",
            ),
            handler,
        )
        for index in range(3)
    ]

    assert executed == ["no-effect-0", "no-effect-1"]
    assert [result.status for result in results] == ["success", "success", "error"]
    assert results[0].additional_kwargs["beliefkv_no_observable_effect"] is True
    assert results[2].additional_kwargs["beliefkv_physical_execution"] is False
    assert results[2].additional_kwargs["beliefkv_duplicate_reason"] == (
        "successful_no_effect"
    )
    assert len(censored) == 1

    epoch[0] += 1
    rerun = circuit.wrap_tool_call(
        _circuit_request(
            call_id="no-effect-new-epoch",
            command="python -c '# only a comment'",
        ),
        handler,
    )
    assert rerun.status == "success"
    assert executed[-1] == "no-effect-new-epoch"


def test_tool_circuit_does_not_share_observation_across_conversations() -> None:
    executed: list[str] = []
    circuit = ToolCircuitBreakerMiddleware(
        state_epoch=lambda: 0,
        audit=None,
        scope="conversation-isolation-test",
    )

    def handler(request: ToolCallRequest) -> ToolMessage:
        executed.append(str(request.tool_call["id"]))
        return ToolMessage(
            content="result",
            tool_call_id=str(request.tool_call["id"]),
            name="execute",
        )

    for call_id, conversation in (("call-a", "a"), ("call-b", "b")):
        circuit.wrap_tool_call(
            _circuit_request(
                call_id=call_id,
                command="pytest test_a.py",
                conversation=conversation,
            ),
            handler,
        )
    assert executed == ["call-a", "call-b"]


def test_tool_circuit_does_not_suppress_concurrent_successful_calls() -> None:
    entered = threading.Event()
    release = threading.Event()
    executed: list[str] = []
    circuit = ToolCircuitBreakerMiddleware(
        state_epoch=lambda: 0,
        audit=None,
        scope="concurrent-circuit-test",
    )

    def handler(request: ToolCallRequest) -> ToolMessage:
        executed.append(str(request.tool_call["id"]))
        entered.set()
        assert release.wait(timeout=5.0)
        return ToolMessage(
            content="1 passed",
            tool_call_id=str(request.tool_call["id"]),
            name="execute",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        first = executor.submit(
            circuit.wrap_tool_call,
            _circuit_request(call_id="call-0", command="pytest test_a.py"),
            handler,
        )
        assert entered.wait(timeout=5.0)
        duplicates = [
            executor.submit(
                circuit.wrap_tool_call,
                _circuit_request(
                    call_id=f"call-{index}",
                    command="pytest test_a.py",
                ),
                handler,
            )
            for index in range(1, 8)
        ]
        release.set()
        duplicate_results = [future.result(timeout=5.0) for future in duplicates]
        first_result = first.result(timeout=5.0)

    assert first_result.status == "success"
    assert sorted(executed) == [f"call-{index}" for index in range(8)]
    assert all(result.status == "success" for result in duplicate_results)


def test_tool_circuit_allows_one_transient_retry_then_suppresses() -> None:
    executed: list[str] = []
    circuit = ToolCircuitBreakerMiddleware(
        state_epoch=lambda: 0,
        audit=None,
        scope="transient-retry-test",
        transient_retry_limit=1,
    )

    def handler(request: ToolCallRequest) -> ToolMessage:
        executed.append(str(request.tool_call["id"]))
        return ToolMessage(
            content="Error: command timed out",
            tool_call_id=str(request.tool_call["id"]),
            name="execute",
            status="error",
        )

    for index in range(3):
        result = circuit.wrap_tool_call(
            _circuit_request(
                call_id=f"timeout-{index}",
                command="pytest test_slow.py",
            ),
            handler,
        )
    assert executed == ["timeout-0", "timeout-1"]
    assert result.status == "error"
    assert "duplicate_suppressed" in str(result.content)


def test_tool_circuit_suppresses_deterministic_failure_without_retry() -> None:
    executed: list[str] = []
    circuit = ToolCircuitBreakerMiddleware(
        state_epoch=lambda: 0,
        audit=None,
        scope="deterministic-failure-test",
    )

    def handler(request: ToolCallRequest) -> ToolMessage:
        executed.append(str(request.tool_call["id"]))
        return ToolMessage(
            content="Error: String not found in file: old text",
            tool_call_id=str(request.tool_call["id"]),
            name="edit_file",
            status="error",
        )

    for index in range(2):
        result = circuit.wrap_tool_call(
            _circuit_request(
                call_id=f"edit-{index}",
                command="replace old text",
                tool_name="edit_file",
            ),
            handler,
        )
    assert executed == ["edit-0"]
    assert result.status == "error"
    assert "duplicate_suppressed" in str(result.content)


def test_loop_guard_counts_distinct_structured_tool_outputs_as_progress() -> None:
    messages = [
        ToolMessage(
            content=[{"type": "json", "value": index}],
            tool_call_id=f"unmatched-{index}",
            name="execute",
        )
        for index in range(8)
    ]

    snapshot = analyze_agent_history(
        messages,
        LoopGuardPolicy(
            repeated_call_limit=99,
            consecutive_no_progress_limit=3,
        ),
    )

    assert snapshot.reason is None
    assert snapshot.consecutive_no_progress == 1


def test_loop_guard_counts_parallel_failures_as_one_decision_batch() -> None:
    calls = [
        {
            "name": "read_file",
            "args": {"path": f"/missing-{index}"},
            "id": f"parallel-{index}",
        }
        for index in range(44)
    ]
    messages = [AIMessage(content="", tool_calls=calls)]
    messages.extend(
        ToolMessage(
            content="Error: path_not_found",
            tool_call_id=str(call["id"]),
            name="read_file",
        )
        for call in calls
    )

    snapshot = analyze_agent_history(
        messages,
        LoopGuardPolicy(
            repeated_call_limit=6,
            consecutive_error_limit=6,
            consecutive_no_progress_limit=8,
        ),
    )

    assert snapshot.reason is None
    assert snapshot.tool_calls == 44
    assert snapshot.completed_tool_calls == 44
    assert snapshot.consecutive_errors == 1
    assert snapshot.consecutive_no_progress == 0


def test_loop_guard_does_not_treat_distinct_probe_strings_as_progress() -> None:
    messages = []
    for index in range(8):
        messages.extend(
            _tool_exchange(
                "execute",
                {"command": f'python -c "print({index})"'},
                str(index),
                f"diagnostic result {index}",
            )
        )

    snapshot = analyze_agent_history(messages, LoopGuardPolicy())
    assert snapshot.reason == "no_observable_progress"
    assert snapshot.consecutive_no_progress == 8


def test_loop_guard_counts_novel_substantive_execute_output_as_progress() -> None:
    messages = []
    for index in range(8):
        messages.extend(
            _tool_exchange(
                "execute",
                {"command": f"python inspect_state_{index}.py"},
                str(index),
                f"diagnostic report {index}\n" + ("new repository evidence " * 8),
            )
        )

    snapshot = analyze_agent_history(messages, LoopGuardPolicy())

    assert snapshot.reason is None
    assert snapshot.consecutive_no_progress == 0


def test_loop_guard_still_detects_repeated_execute_output() -> None:
    messages = []
    for index in range(6):
        messages.extend(
            _tool_exchange(
                "execute",
                {"command": "python inspect_state.py"},
                str(index),
                "same diagnostic output with enough text to be substantive",
            )
        )

    snapshot = analyze_agent_history(
        messages,
        LoopGuardPolicy(repeated_call_limit=99),
    )

    assert snapshot.reason == "no_observable_progress"
    assert snapshot.consecutive_no_progress == 5


def test_loop_guard_observes_semantic_patterns_without_intervening() -> None:
    messages = []
    for index in range(3):
        messages.extend(_tool_exchange("ls", {"path": "/src"}, str(index), "same"))
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(enforce_semantic_guard=False),
        completion_schema=ChildCompletion,
        completion_instruction="Return ChildCompletion.",
        audit=None,
        scope="observe-only-test",
    )

    update = guard.before_model({"messages": messages}, runtime=None)

    assert update is not None
    assert update["guard_observed_patterns"] == ("repeated_tool_call",)
    assert "guard_phase" not in update
    assert "guard_forcing_completion" not in update


def test_loop_guard_enforces_repeated_suppressed_failure_circuit() -> None:
    args = {"pattern": 401, "path": "/workspace"}
    messages: list[BaseMessage] = [
        AIMessage(
            content="",
            tool_calls=[{"name": "grep", "args": args, "id": "grep-physical"}],
        ),
        ToolMessage(
            content="Input should be a valid string",
            tool_call_id="grep-physical",
            name="grep",
            status="error",
            additional_kwargs={
                "beliefkv_error_class": "validation_error",
                "beliefkv_physical_execution": True,
                "beliefkv_suppressed_repeat_intent": False,
                "beliefkv_failure_episode_id": "failure-1",
            },
        ),
    ]
    for index in range(3):
        call_id = f"grep-suppressed-{index}"
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "grep", "args": args, "id": call_id}],
                ),
                ToolMessage(
                    content="duplicate_suppressed",
                    tool_call_id=call_id,
                    name="grep",
                    status="error",
                    additional_kwargs={
                        "beliefkv_error_class": "duplicate_suppressed",
                        "beliefkv_physical_execution": False,
                        "beliefkv_suppressed_repeat_intent": True,
                        "beliefkv_failure_episode_id": "failure-1",
                    },
                ),
            ]
        )
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(),
        completion_schema=ChildCompletion,
        completion_instruction="Return ChildCompletion.",
        audit=None,
        scope="suppressed-failure-circuit-test",
    )

    suspect = guard.before_model({"messages": messages}, runtime=None)

    assert suspect is not None
    assert suspect["guard_phase"] == "SUSPECT"
    assert suspect["guard_reason"] == "repeated_suppressed_tool_intent"
    assert suspect["guard_forcing_completion"] is False
    recovery = guard.before_model(
        {"messages": messages, **suspect}, runtime=None
    )
    assert recovery is not None
    assert recovery["guard_phase"] == "RECOVERY"
    assert recovery["guard_forcing_completion"] is True


def test_loop_guard_clears_legacy_semantic_finalization_state() -> None:
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(enforce_semantic_guard=False),
        completion_schema=ChildCompletion,
        completion_instruction="Return ChildCompletion.",
        audit=None,
        scope="legacy-state-test",
    )

    update = guard.before_model(
        {
            "messages": [],
            "guard_phase": "FINALIZE",
            "guard_forcing_completion": True,
            "guard_reason": "no_observable_progress",
            "guard_recovery_attempt": 4,
        },
        runtime=None,
    )

    assert update is not None
    assert update["guard_phase"] == "NORMAL"
    assert update["guard_forcing_completion"] is False
    assert update["guard_reason"] == ""


def test_loop_guard_enters_suspect_before_bounded_recovery() -> None:
    messages = []
    for index in range(3):
        messages.extend(_tool_exchange("ls", {"path": "/src"}, str(index), "same"))
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(),
        completion_schema=ChildCompletion,
        completion_instruction="Return ChildCompletion.",
        audit=None,
        scope="test",
    )
    update = guard.before_model({"messages": messages}, runtime=None)
    assert update is not None
    assert update["guard_phase"] == "SUSPECT"
    assert update["guard_forcing_completion"] is False
    assert update["guard_reason"] == "repeated_tool_call"
    assert update["guard_trigger_model_calls"] == 3

    recovery = guard.before_model({**update, "messages": messages}, runtime=None)
    assert recovery is not None
    assert recovery["guard_phase"] == "RECOVERY"
    assert recovery["guard_forcing_completion"] is True
    assert recovery["guard_recovery_attempt"] == 1


def test_loop_guard_returns_to_normal_after_credible_recovery_progress() -> None:
    messages = []
    for index in range(3):
        messages.extend(_tool_exchange("ls", {"path": "/src"}, str(index), "same"))
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(enforce_semantic_guard=True),
        completion_schema=ChildCompletion,
        completion_instruction="Return ChildCompletion.",
        audit=None,
        scope="recoverable-test",
    )
    state: dict[str, Any] = {"messages": messages}
    suspect = guard.before_model(state, runtime=None)
    assert suspect is not None
    state.update(suspect)
    recovery = guard.before_model(state, runtime=None)
    assert recovery is not None
    state.update(recovery)

    messages.extend(
        _tool_exchange(
            "grep",
            {"pattern": "PoolManager", "path": "/workspace/requests"},
            "new-evidence",
            "requests/pools.py:PoolManager",
        )
    )
    state["messages"] = messages
    recovered = guard.before_model(state, runtime=None)

    assert recovered is not None
    assert recovered["guard_phase"] == "NORMAL"
    assert recovered["guard_forcing_completion"] is False
    assert recovered["guard_recovery_attempt"] == 0


def test_loop_guard_extends_soft_graph_budget_only_with_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "beliefkv.experiments.agent_protocol.get_config",
        lambda: {
            "recursion_limit": 512,
            "metadata": {"langgraph_step": 384},
        },
    )
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(enforce_soft_graph_budget=True),
        completion_schema=WorkflowCompletion,
        completion_instruction="Return WorkflowCompletion.",
        audit=None,
        scope="soft-lease-test",
    )
    messages = _tool_exchange(
        "read_file",
        {"path": "/workspace/requests/models.py"},
        "read-1",
        "class Request:",
    )

    extended = guard.before_model(
        {
            "messages": messages,
            "guard_graph_progress_keys": (),
            "guard_graph_lease_until": 384,
        },
        runtime=None,
    )
    assert extended is not None
    assert extended["guard_graph_lease_until"] == 480

    stalled = guard.before_model(
        {
            "messages": [],
            "guard_graph_progress_keys": (),
            "guard_graph_lease_until": 384,
        },
        runtime=None,
    )
    assert stalled is not None
    assert stalled["guard_phase"] == "SUSPECT"
    assert stalled["guard_reason"] == "graph_soft_budget_without_progress"


def test_soft_graph_guard_tracks_progress_from_agent_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_step = [1]
    monkeypatch.setattr(
        "beliefkv.experiments.agent_protocol.get_config",
        lambda: {
            "recursion_limit": 512,
            "metadata": {"langgraph_step": graph_step[0]},
        },
    )
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(),
        completion_schema=WorkflowCompletion,
        completion_instruction="Return WorkflowCompletion.",
        audit=None,
        scope="soft-lease-baseline-test",
    )
    messages = _tool_exchange(
        "read_file",
        {"path": "/workspace/requests/models.py"},
        "read-1",
        "class Request:",
    )

    initial = guard.before_model({"messages": messages}, runtime=None)
    assert initial is not None
    assert initial["guard_graph_lease_until"] == 384
    assert initial["guard_graph_progress_keys"]

    graph_step[0] = 384
    stalled = guard.before_model(
        {"messages": messages, **initial},
        runtime=None,
    )
    assert stalled is not None
    assert stalled["guard_phase"] == "SUSPECT"
    assert stalled["guard_reason"] == "graph_soft_budget_without_progress"


def test_loop_guard_observes_soft_graph_budget_without_intervening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "beliefkv.experiments.agent_protocol.get_config",
        lambda: {
            "recursion_limit": 512,
            "metadata": {"langgraph_step": 384},
        },
    )
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(
            enforce_semantic_guard=False,
            enforce_soft_graph_budget=False,
        ),
        completion_schema=WorkflowCompletion,
        completion_instruction="Return WorkflowCompletion.",
        audit=None,
        scope="soft-observe-only-test",
    )

    update = guard.before_model({"messages": []}, runtime=None)

    assert update is not None
    assert update["guard_soft_budget_observed"] is True
    assert "guard_phase" not in update
    assert "guard_forcing_completion" not in update


def test_loop_guard_hard_graph_limit_reserves_terminal_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "beliefkv.experiments.agent_protocol.get_config",
        lambda: {
            "recursion_limit": 512,
            "metadata": {"langgraph_step": 481},
        },
    )
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(graph_step_reserve=32),
        completion_schema=WorkflowCompletion,
        completion_instruction="Return WorkflowCompletion.",
        audit=None,
        scope="graph-budget-test",
    )

    update = guard.before_model({"messages": []}, runtime=None)

    assert update is not None
    assert update["guard_phase"] == "FINALIZE"
    assert update["guard_forcing_completion"] is True
    assert update["guard_reason"] == "graph_step_hard_limit_low"
    assert update["guard_ever_intervened"] is True


def test_loop_guard_2048_step_fuse_reserves_terminal_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "beliefkv.experiments.agent_protocol.get_config",
        lambda: {
            "recursion_limit": 2048,
            "metadata": {"langgraph_step": 2016},
        },
    )
    policy = LoopGuardPolicy(graph_step_reserve=32)
    assert policy.graph_step_hard_limit == 2048

    guard = AgentLoopGuardMiddleware(
        policy=policy,
        completion_schema=WorkflowCompletion,
        completion_instruction="Return WorkflowCompletion.",
        audit=None,
        scope="graph-budget-2048-test",
    )
    update = guard.before_model({"messages": []}, runtime=None)

    assert update is not None
    assert update["guard_phase"] == "FINALIZE"
    assert update["guard_forcing_completion"] is True
    assert update["guard_reason"] == "graph_step_hard_limit_low"


def test_graph_budget_finalization_retains_only_completion_tool() -> None:
    @tool
    def execute(command: str) -> str:
        """Execute one command."""

        return command

    @tool("WorkflowCompletion")
    def completion(status: str) -> str:
        """Return terminal workflow status."""

        return status

    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(enforce_semantic_guard=True),
        completion_schema=WorkflowCompletion,
        completion_instruction="Return WorkflowCompletion.",
        audit=None,
        scope="graph-budget-tools-test",
    )
    model = FakeMessagesListChatModel(responses=[AIMessage(content="")])
    bounded = guard._guard_recovery_request(
        ModelRequest(
            model=model,
            messages=[],
            system_message=SystemMessage(content="base"),
            tools=[execute, completion],
            state={
                "guard_reason": "graph_step_budget_low",
                "guard_recovery_attempt": 1,
            },
        )
    )

    assert [item.name for item in bounded.tools] == ["WorkflowCompletion"]
    assert "RUNTIME COMPLETION DIRECTIVE" in bounded.system_message.text


def test_loop_guard_tracks_and_enforces_activation_wall_clock() -> None:
    now = [10.0]
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(activation_wall_clock_s=5.0),
        completion_schema=ChildCompletion,
        completion_instruction="Return ChildCompletion.",
        audit=None,
        scope="wall-clock-test",
        clock=lambda: now[0],
    )

    initial = guard.before_model({"messages": []}, runtime=None)
    assert initial is not None
    assert initial["guard_activation_started_monotonic"] == 10.0
    now[0] = 14.9
    assert (
        guard.before_model(
            {
                "messages": [],
                "guard_activation_started_monotonic": 10.0,
            },
            runtime=None,
        )
        is None
    )
    now[0] = 15.0
    exhausted = guard.before_model(
        {
            "messages": [],
            "guard_activation_started_monotonic": 10.0,
        },
        runtime=None,
    )
    assert exhausted is not None
    assert exhausted["guard_phase"] == "FINALIZE"
    assert exhausted["guard_forcing_completion"] is True
    assert exhausted["guard_reason"] == "activation_wall_clock_exhausted"


def test_loop_guard_allows_unbounded_activation_wall_clock() -> None:
    now = [10.0]
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(activation_wall_clock_s=None),
        completion_schema=ChildCompletion,
        completion_instruction="Return ChildCompletion.",
        audit=None,
        scope="unbounded-wall-clock-test",
        clock=lambda: now[0],
    )

    initial = guard.before_model({"messages": []}, runtime=None)
    assert initial is not None
    now[0] = 1_000_000.0

    assert (
        guard.before_model(
            {
                "messages": [],
                "guard_activation_started_monotonic": 10.0,
            },
            runtime=None,
        )
        is None
    )


def test_activation_deadline_caps_late_requests_by_remaining_budget() -> None:
    now = [100.0]
    deadline = ActivationDeadline(clock=lambda: now[0])

    assert deadline.request_timeout_s(900.0) == 900.0
    deadline.start(600.0)
    assert deadline.request_timeout_s(900.0) == 600.0
    now[0] = 275.0
    assert deadline.request_timeout_s(900.0) == 425.0
    assert deadline.request_timeout_s(30.0) == 30.0
    now[0] = 700.0
    with pytest.raises(ActivationDeadlineExceeded):
        deadline.request_timeout_s(900.0)

    deadline.clear()
    assert deadline.request_timeout_s(900.0) == 900.0


def test_loop_guard_uses_parent_activation_deadline_for_child_scope() -> None:
    now = [10.0]
    deadline = ActivationDeadline(clock=lambda: now[0])
    deadline.start(5.0)
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(activation_wall_clock_s=5.0),
        completion_schema=ChildCompletion,
        completion_instruction="Return ChildCompletion.",
        audit=None,
        scope="shared-wall-clock-test",
        activation_deadline=deadline,
    )

    now[0] = 15.0
    exhausted = guard.before_model({"messages": []}, runtime=None)
    assert exhausted is not None
    assert exhausted["guard_phase"] == "FINALIZE"
    assert exhausted["guard_forcing_completion"] is True
    assert exhausted["guard_reason"] == "activation_wall_clock_exhausted"


def test_loop_guard_keeps_alternate_tools_during_bounded_recovery() -> None:
    @tool
    def apply_patch(patch: str) -> str:
        """Apply one synthetic patch."""

        return patch

    @tool
    def read_file(path: str) -> str:
        """Read one synthetic file."""

        return path

    @tool
    def execute(command: str) -> str:
        """Execute one synthetic command."""

        return command

    messages = []
    for index in range(3):
        messages.extend(_tool_exchange("probe", {"path": "/same"}, str(index), "same"))
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(recovery_model_call_limit=3),
        completion_schema=WorkflowCompletion,
        completion_instruction="Return WorkflowCompletion.",
        audit=None,
        scope="recovery-test",
        finalization_tool_names=frozenset({"apply_patch", "execute"}),
    )
    model = FakeMessagesListChatModel(responses=[AIMessage(content="")])

    recovering = guard._guard_recovery_request(
        ModelRequest(
            model=model,
            messages=messages,
            system_message=SystemMessage(content="base"),
            tools=[apply_patch, read_file],
            state={
                "guard_reason": "repeated_tool_call",
                "guard_recovery_attempt": 1,
            },
        )
    )
    assert [item.name for item in recovering.tools] == ["apply_patch", "read_file"]
    assert recovering.tool_choice is None
    assert "RUNTIME RECOVERY DIRECTIVE" in recovering.system_message.text
    assert "verify `pwd` and `git rev-parse --show-toplevel`" in (
        recovering.system_message.text
    )
    assert "mounted `/workspace/src` tree" in recovering.system_message.text

    exhausted = guard._guard_recovery_request(
        ModelRequest(
            model=model,
            messages=messages,
            system_message=SystemMessage(content="base"),
            tools=[apply_patch, read_file, execute],
            state={
                "guard_reason": "repeated_tool_call",
                "guard_recovery_attempt": 4,
            },
        )
    )
    assert exhausted.tools == []
    assert "RUNTIME COMPLETION DIRECTIVE" in exhausted.system_message.text


def test_loop_guard_recovery_attempt_is_monotonic_across_compaction() -> None:
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(
            recovery_model_call_limit=3,
            enforce_semantic_guard=True,
        ),
        completion_schema=ChildCompletion,
        completion_instruction="Return ChildCompletion.",
        audit=None,
        scope="compaction-safe-recovery-test",
    )
    state = {
        "messages": [],
        "guard_phase": "RECOVERY",
        "guard_forcing_completion": True,
        "guard_reason": "consecutive_tool_errors",
        "guard_recovery_attempt": 0,
        "guard_recovery_baseline_keys": (),
    }

    for expected in (1, 2, 3, 4):
        update = guard.before_model(state, runtime=None)
        assert update is not None
        assert update["guard_recovery_attempt"] == expected
        assert update["guard_phase"] == (
            "FINALIZE" if expected == 4 else "RECOVERY"
        )
        state = {**state, **update, "messages": []}

    assert state["guard_recovery_attempt"] == 4


def test_unstructured_guard_output_repairs_format_without_synthesizing_blocked() -> None:
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(),
        completion_schema=ChildCompletion,
        completion_instruction="Return ChildCompletion.",
        audit=None,
        scope="forced-terminal-test",
    )

    first = guard.after_model(
        {
            "messages": [AIMessage(content="still no structured result")],
            "guard_forcing_completion": True,
            "guard_reason": "repeated_tool_call",
            "guard_trigger_model_calls": 3,
        },
        runtime=None,
    )

    assert first is not None
    assert first["jump_to"] == "model"
    assert first["protocol_repair_active"] is True
    assert "structured_response" not in first

    second = guard.after_model(
        {
            "messages": [AIMessage(content="still no structured result")],
            "guard_forcing_completion": True,
            "guard_reason": "repeated_tool_call",
            **first,
        },
        runtime=None,
    )
    assert second is not None
    assert second["jump_to"] == "model"
    assert second["protocol_repair_attempt"] == 2
    assert "structured_response" not in second

    third = guard.after_model(
        {
            "messages": [AIMessage(content="still no structured result")],
            "guard_forcing_completion": True,
            "guard_reason": "repeated_tool_call",
            **first,
            **second,
        },
        runtime=None,
    )
    assert third is not None
    assert third["jump_to"] == "end"
    assert third["protocol_repair_failed"] is True
    assert "structured_response" not in third
    with pytest.raises(TerminalProtocolError):
        require_structured_completion(third, ChildCompletion)


def test_graph_limit_finalization_rejects_regular_tool_calls_and_fails_bounded() -> None:
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(),
        completion_schema=WorkflowCompletion,
        completion_instruction="Return WorkflowCompletion.",
        audit=None,
        scope="graph-terminal-tool-test",
    )
    state = {
        "guard_forcing_completion": True,
        "guard_reason": "graph_step_hard_limit_low",
        "protocol_repair_active": True,
        "protocol_repair_attempt": 1,
    }
    response = ModelResponse(
        result=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "grep",
                        "args": {"pattern": "x"},
                        "id": "regular-call",
                    }
                ],
            )
        ]
    )

    sanitized = guard._reject_terminal_regular_tool_calls(response, state)

    assert isinstance(sanitized.result[0], AIMessage)
    assert sanitized.result[0].tool_calls == []
    failed = guard.after_model(
        {**state, "messages": list(sanitized.result)},
        runtime=None,
    )
    assert failed is not None
    assert failed["jump_to"] == "end"
    assert failed["protocol_repair_failed"] is True


def test_loop_guard_exhaustion_requests_honest_completion_without_tools() -> None:
    @tool
    def execute(command: str) -> str:
        """Execute one synthetic command."""

        return command

    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(recovery_model_call_limit=3),
        completion_schema=WorkflowCompletion,
        completion_instruction="Return WorkflowCompletion.",
        audit=None,
        scope="bounded-test-recovery",
        finalization_tool_names=frozenset({"execute"}),
    )
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "edit_file", "args": {"path": "/a"}, "id": "edit"}
            ],
        ),
        ToolMessage(
            content="updated",
            tool_call_id="edit",
            name="edit_file",
        ),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "execute", "args": {"command": "test"}, "id": "test"}
            ],
        ),
        ToolMessage(
            content="passed",
            tool_call_id="test",
            name="execute",
        ),
    ]
    model = FakeMessagesListChatModel(responses=[AIMessage(content="")])

    request = guard._guard_recovery_request(
        ModelRequest(
            model=model,
            messages=messages,
            system_message=SystemMessage(content="base"),
            tools=[execute],
            state={
                "guard_reason": "repeated_tool_call",
                "guard_recovery_attempt": 4,
            },
        )
    )

    assert request.tools == []
    assert "RUNTIME COMPLETION DIRECTIVE" in request.system_message.text
    assert "Select the terminal status honestly" in request.system_message.text


def test_structured_completion_is_required() -> None:
    completion = WorkflowCompletion(
        status="blocked",
        summary="Insufficient evidence",
        unresolved=["missing reproduction"],
    )
    assert require_structured_completion(
        {"structured_response": completion}, WorkflowCompletion
    ) is completion
    with pytest.raises(RuntimeError, match="WorkflowCompletion"):
        require_structured_completion({"messages": []}, WorkflowCompletion)


def test_loop_guard_observes_real_agent_graph_without_blocking_model_completion() -> None:
    class ToolCallingFakeModel(FakeMessagesListChatModel):
        _bound_tool_names: list[list[str]] = PrivateAttr(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            del kwargs
            self._bound_tool_names.append(
                [
                    str(tool.name if hasattr(tool, "name") else tool.get("name"))
                    for tool in tools
                ]
            )
            return self

    @tool
    def probe(path: str) -> str:
        """Read one synthetic path."""

        return f"content from {path}"

    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "probe", "args": {"path": "/same"}, "id": f"probe-{i}"}
            ],
        )
        for i in range(3)
    ]
    responses.append(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "ChildCompletion",
                    "args": {
                        "status": "blocked",
                        "summary": "Stopped after repeated probes",
                        "evidence": ["/same was probed"],
                        "tests": [],
                        "files_changed": [],
                        "unresolved": ["no additional evidence"],
                        "confidence": "low",
                    },
                    "id": "completion-1",
                }
            ],
        )
    )
    model = ToolCallingFakeModel(responses=responses)
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(),
        completion_schema=ChildCompletion,
        completion_instruction="Return ChildCompletion.",
        audit=None,
        scope="integration-test",
    )
    agent = create_agent(
        model=model,
        tools=[probe],
        middleware=[guard],
        response_format=ToolStrategy(ChildCompletion),
    )

    result = agent.invoke(
        {"messages": [{"role": "user", "content": "Inspect the path."}]},
        config={"recursion_limit": 200},
    )

    completion = require_structured_completion(result, ChildCompletion)
    assert completion.status == "blocked"
    assert model.i == 0
    assert "probe" in model._bound_tool_names[0]
    assert model._bound_tool_names[-1] == ["probe", "ChildCompletion"]


def test_tool_circuit_preserves_successful_repeats_in_real_agent_graph() -> None:
    class ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            del tools, kwargs
            return self

    executions: list[str] = []

    @tool
    def probe(path: str) -> str:
        """Read one synthetic path."""

        executions.append(path)
        return f"content from {path}"

    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "probe", "args": {"path": "/same"}, "id": "probe-1"}
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "probe", "args": {"path": "/same"}, "id": "probe-2"}
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ChildCompletion",
                        "args": {
                            "status": "complete",
                            "summary": "Used the existing observation",
                            "evidence": ["/same"],
                            "tests": [],
                            "files_changed": [],
                            "unresolved": [],
                            "confidence": "high",
                        },
                        "id": "completion",
                    }
                ],
            ),
        ]
    )
    circuit = ToolCircuitBreakerMiddleware(
        state_epoch=lambda: 0,
        audit=None,
        scope="graph-circuit-test",
    )
    agent = create_agent(
        model=model,
        tools=[probe],
        middleware=[circuit, ToolOutcomeStatusMiddleware()],
        response_format=ToolStrategy(ChildCompletion),
    )

    result = agent.invoke(
        {"messages": [{"role": "user", "content": "Inspect the path."}]},
        config={"recursion_limit": 40},
    )

    completion = require_structured_completion(result, ChildCompletion)
    assert completion.status == "complete"
    assert executions == ["/same", "/same"]


def test_semantic_protocol_repairs_unstructured_model_stop() -> None:
    class ToolCallingFakeModel(FakeMessagesListChatModel):
        _bound_tool_names: list[list[str]] = PrivateAttr(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            del kwargs
            self._bound_tool_names.append(
                [
                    str(tool.name if hasattr(tool, "name") else tool.get("name"))
                    for tool in tools
                ]
            )
            return self

    @tool
    def probe(path: str) -> str:
        """Read one synthetic path."""

        return path

    model = ToolCallingFakeModel(
        responses=[
            AIMessage(content="ordinary prose without a completion tool"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ChildCompletion",
                        "args": {
                            "status": "blocked",
                            "summary": "Converted to the required protocol",
                            "evidence": [],
                            "tests": [],
                            "files_changed": [],
                            "unresolved": ["initial response was unstructured"],
                            "confidence": "low",
                        },
                        "id": "completion-2",
                    }
                ],
            ),
        ]
    )
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(enabled=False),
        completion_schema=ChildCompletion,
        completion_instruction="Return ChildCompletion.",
        audit=None,
        scope="semantic-repair-test",
    )
    agent = create_agent(
        model=model,
        tools=[probe],
        middleware=[guard],
        response_format=ToolStrategy(ChildCompletion),
    )

    result = agent.invoke(
        {"messages": [{"role": "user", "content": "Finish semantically."}]},
        config={"recursion_limit": 200},
    )

    completion = require_structured_completion(result, ChildCompletion)
    assert completion.status == "blocked"
    assert len(model._bound_tool_names) == 2
    assert "probe" in model._bound_tool_names[0]
    assert model._bound_tool_names[1] == ["ChildCompletion"]


def test_semantic_protocol_normalizes_valid_json_without_another_model_call() -> None:
    completion = ChildCompletion(
        status="complete",
        summary="Repository evidence collected",
        evidence=["sympy/core/basic.py"],
        tests=[],
        files_changed=[],
        unresolved=[],
        confidence="high",
    )
    guard = AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(enabled=False),
        completion_schema=ChildCompletion,
        completion_instruction="Return ChildCompletion.",
        audit=None,
        scope="json-normalization-test",
    )

    update = guard.after_model(
        {"messages": [AIMessage(content=completion.model_dump_json())]},
        runtime=None,
    )

    assert update is not None
    assert update["jump_to"] == "end"
    assert update["protocol_normalized"] is True
    assert update["structured_response"] == completion


def test_oracle_pressure_prompt_is_deterministic_and_bounded(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    pack = tmp_path / "context.txt.gz"
    with gzip.open(pack, "wt", encoding="utf-8") as stream:
        stream.write("\n--- repository file: module.py ---\nVALUE = 1\n" * 100)
    workload = SweBenchWorkload(
        instance_id="pressure-1",
        repo="fixture/repo",
        base_commit="deadbeef",
        problem_statement="Fix VALUE.",
        difficulty="fixture",
        source_repo=workspace,
        oracle_kv_pressure=OracleKVPressureContext(
            target_parent_prompt_tokens=4096,
            actual_parent_prompt_tokens=4096,
            context_seed=7,
            context_pack_path=pack,
            context_pack_blake2b=_blake2b_file(pack),
            source_file_count=1,
            model_context_tokens=8192,
            output_reserve_tokens=512,
            runtime_overhead_reserve_tokens=1024,
        ),
    )

    first = _task_prompt(workload, workspace=workspace)
    second = _task_prompt(workload, workspace=workspace)

    assert first == second
    assert "SWE-bench instance: pressure-1" in first
    assert "repository file: module.py" in first
    assert _task_prompt(workload, include_pressure=False) != first


def test_oracle_pressure_contract_rejects_context_overflow(tmp_path: Path) -> None:
    pack = tmp_path / "context.txt.gz"
    with gzip.open(pack, "wt", encoding="utf-8") as stream:
        stream.write("context")
    with pytest.raises(ValueError, match="reentry context budget"):
        OracleKVPressureContext(
            target_parent_prompt_tokens=7000,
            actual_parent_prompt_tokens=7000,
            context_seed=1,
            context_pack_path=pack,
            context_pack_blake2b=_blake2b_file(pack),
            source_file_count=1,
            model_context_tokens=8192,
            output_reserve_tokens=512,
            runtime_overhead_reserve_tokens=1024,
        )
