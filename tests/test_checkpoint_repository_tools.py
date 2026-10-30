import asyncio

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
import pytest

from beliefkv.experiments import deepagents_swebench as harness
from beliefkv.runtime.checkpoint_repository_tools import FOCUSED_TOOL_PROFILE
from tests.test_child_work_budget import ToolModel


@pytest.mark.parametrize("path", (None, "/workspace", "/workspace/pkg"))
@pytest.mark.parametrize("asynchronous", (False, True))
def test_shell_absolute_glob_matches_the_existing_mounted_file(tmp_path, path, asynchronous):
    workspace = tmp_path / "repo"
    source = workspace / "pkg/src/adapters/social_sharing.py"
    source.parent.mkdir(parents=True)
    source.write_text("class Adapter:\n    pass\n")
    audit = harness.JsonlAudit(tmp_path / "audit.jsonl")
    backend = harness.DockerWorkspaceBackend(
        workspace, image="fixture:unused", audit=audit, support_dir=None,
    )
    try:
        pattern = "/workspace/pkg/src/**/*social*.py"
        result = (
            asyncio.run(backend.aglob(pattern, path)) if asynchronous
            else backend.glob(pattern, path)
        )
        assert result.error is None
        assert [item["path"] for item in result.matches] == [
            "/workspace/pkg/src/adapters/social_sharing.py",
        ]
    finally:
        audit.close()


@pytest.mark.parametrize("profile,default", (("native", 100), (FOCUSED_TOOL_PROFILE, 400)))
@pytest.mark.parametrize("explicit_limit", (None, 17))
@pytest.mark.parametrize("asynchronous", (False, True))
def test_real_read_tool_schema_and_execution_agree_on_pagination(
    tmp_path, profile, default, explicit_limit, asynchronous,
):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "source.py").write_text("".join(f"VALUE_{i} = {i}\n" for i in range(450)))
    audit = harness.JsonlAudit(tmp_path / "audit.jsonl")
    backend = harness.DockerWorkspaceBackend(
        workspace, image="fixture:unused", audit=audit, support_dir=None,
    )
    try:
        middleware = harness._filesystem_middleware(
            backend, allow_direct_edits=False, profile=profile,
        )
        read = next(tool for tool in middleware.tools if tool.name == "read_file")
        schema = convert_to_openai_tool(read)["function"]["parameters"]
        assert schema["properties"]["limit"]["default"] == default
        args = {"file_path": "/workspace/source.py"}
        if explicit_limit is not None:
            args["limit"] = explicit_limit
        agent = create_agent(
            model=ToolModel(responses=[
                AIMessage(content="", tool_calls=[{"name": "read_file", "id": "read", "args": args}]),
                AIMessage(content="Read the requested source window."),
            ]),
            middleware=[middleware],
        )
        inputs = {"messages": [HumanMessage(content="Inspect the specified source file.")]}
        result = asyncio.run(agent.ainvoke(inputs)) if asynchronous else agent.invoke(inputs)
        message = next(
            item for item in result["messages"] if getattr(item, "tool_call_id", None) == "read"
        )
        count = default if explicit_limit is None else explicit_limit
        assert f"VALUE_{count - 1} =" in message.content
        assert f"VALUE_{count} =" not in message.content
        assert "VALUE_0 =" in message.content
        assert message.status == "success"
        assert not {"edit_file", "write_file"} & {tool.name for tool in middleware.tools}
    finally:
        audit.close()


def test_relative_glob_stays_scoped_and_absolute_glob_cannot_escape_workspace(tmp_path):
    workspace = tmp_path / "repo"
    first, second = workspace / "a/one.py", workspace / "b/two.py"
    for path in (first, second):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("VALUE = 1\n")
    foreign = tmp_path / "foreign.py"
    foreign.write_text("PRIVATE = 1\n")
    (workspace / "a/leak.py").symlink_to(foreign)
    audit = harness.JsonlAudit(tmp_path / "audit.jsonl")
    backend = harness.DockerWorkspaceBackend(
        workspace, image="fixture:unused", audit=audit, support_dir=None,
    )
    try:
        assert [item["path"] for item in backend.glob("**/*.py", "/workspace/a").matches] == [
            "/workspace/a/one.py",
        ]
        assert [item["path"] for item in backend.glob("/workspace/**/*.py").matches] == [
            "/workspace/a/one.py", "/workspace/b/two.py",
        ]
        with pytest.raises(ValueError, match="traversal"):
            backend.glob("/workspace/../*.py")
    finally:
        audit.close()
