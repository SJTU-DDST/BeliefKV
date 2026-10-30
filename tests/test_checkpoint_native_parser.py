"""Exercise the actual serving parser for nested checkpoint tool arguments."""

from collections import defaultdict
import json
import os
from pathlib import Path

from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_core.tools import StructuredTool
import pytest

from beliefkv.runtime.checkpoint_subagents import (
    CheckpointTaskSchema, ReadOnlyCheckpointTaskSchema,
    ParallelCheckpointSchema, ReadOnlyParallelCheckpointSchema,
)
from tests.test_checkpoint_subagents import assignment


@pytest.mark.parametrize("read_only", (False, True))
@pytest.mark.parametrize("parallel", (False, True))
@pytest.mark.parametrize("chunk_size", (None, 1, 7, 64))
def test_actual_qwen_coder_parser_preserves_complete_checkpoint_arguments(
    monkeypatch, read_only, parallel, chunk_size,
):
    checkout = os.environ.get("SGLANG_SOURCE_CHECKOUT")
    if not checkout:
        pytest.skip("set SGLANG_SOURCE_CHECKOUT to the serving engine")
    monkeypatch.syspath_prepend(str(Path(checkout) / "python"))
    from sglang.srt.entrypoints.openai.protocol import Tool
    from sglang.srt.function_call.function_call_parser import FunctionCallParser

    if parallel:
        schema = ReadOnlyParallelCheckpointSchema if read_only else ParallelCheckpointSchema
        name = "parallel_tasks"
    else:
        schema = ReadOnlyCheckpointTaskSchema if read_only else CheckpointTaskSchema
        name = "task"
    tool = StructuredTool.from_function(
        name=name, description="Return bounded checkpoint evidence.",
        func=lambda **kwargs: kwargs, args_schema=schema, infer_schema=False,
    )
    parser = FunctionCallParser(
        tools=[Tool.model_validate(convert_to_openai_tool(tool))],
        tool_call_parser="qwen3_coder",
    )
    expected, texts = [], []
    for index in range(2):
        first = {
            **assignment(), "description": f"Question {index}: verify a mapping.",
            "existing_evidence": 'Observed a value with quotes: "x" and a newline.\n[] {}',
        }
        if not read_only:
            first["write_paths"] = ["src/model.py"]
        second = {
            **assignment(), "description": f"Question {index}: verify a separate path.",
            "known_paths": ["tests/test_model.py"], "write_paths": [],
        }
        payload = {"assignments": [first, second]} if parallel else first
        expected.append(payload)
        parameters = "\n".join(
            f"<parameter={key}>\n"
            + (json.dumps(value) if isinstance(value, list) else value)
            + "\n</parameter>"
            for key, value in payload.items()
        )
        texts.append(f"<tool_call>\n<function={name}>\n{parameters}\n</function>\n</tool_call>")
    text = "\n".join(texts)
    if chunk_size is None:
        _, calls = parser.parse_non_stream(text)
    else:
        calls = []
        for offset in range(0, len(text), chunk_size):
            calls.extend(parser.parse_stream_chunk(text[offset:offset + chunk_size])[1])
        calls.extend(parser.parse_stream_end()[1])
    arguments, names = defaultdict(str), {}
    for call in calls:
        arguments[call.tool_index] += call.parameters
        if call.name:
            names[call.tool_index] = call.name
    assert names == {0: name, 1: name}
    assert [json.loads(arguments[index]) for index in range(2)] == expected
    for payload in expected:
        assert schema.model_validate(payload)
