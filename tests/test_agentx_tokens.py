from dataclasses import asdict
import hashlib
import json

import pytest

from beliefkv.experiments.agentx_replay import (
    AgentXAgent, AgentXCall, AgentXWorkflow, build_agentx_plan, load_agentx_preparation,
)
from beliefkv.experiments.agentx_tokens import (
    AgentXTokenCorpus, AgentXTokenSynthesis, prepare_agentx_tokens,
)
from beliefkv.experiments.replay_timing import AGENTX_TIMING


def prepared_source(tmp_path):
    workflows = tuple(
        AgentXWorkflow(wid, (AgentXAgent(
            wid + ":root", tuple(
                AgentXCall(
                    wid + f":r{index}", float(index), 1., 128, 4, "source-model",
                    (1, 2 if not index else 3), (index,),
                ) for index in range(2)
            ),
        ),), (), 64, wid + ":source")
        for wid in ("w", "other")
    )
    plan = build_agentx_plan(workflows)
    directory = tmp_path / "prepared"
    directory.mkdir()
    contents = {
        "workflows.json": {"workflows": [asdict(workflow) for workflow in workflows]},
        "plan.json": {
            "nodes": [{
                **asdict(node), "event": node.event.to_dict() if node.event else None,
            } for node in plan.nodes],
            "request_usage": plan.request_usage,
        },
        "timing.json": {"per_turn_delay_clamping": False, "policy": asdict(AGENTX_TIMING)},
    }
    digests = {}
    for name, value in contents.items():
        data = json.dumps(value).encode()
        (directory / name).write_bytes(data)
        digests[name] = hashlib.sha256(data).hexdigest()
    (directory / "preparation.json").write_text(json.dumps({
        "workload_kind": "agentx_whole_public_trace_structural_adaptation",
        "requests_removed_from_retained_sessions": 0,
        "native_source_capture_required": False, "seed": 21,
        "source_full_sha256": "fixture-only", "roots": 2, "summary": plan.summary(),
        "artifacts": digests,
    }))
    return directory, workflows, plan


def synthesizer():
    return AgentXTokenSynthesis(range(100, 1124), seed=21, eos_token_id=9)


def test_published_trace_tokens_need_no_native_capture_and_preserve_hash_scopes(tmp_path):
    prepared, workflows, plan = prepared_source(tmp_path)
    output = tmp_path / "tokens"
    manifest = prepare_agentx_tokens(
        prepared, output, synthesizer(), corpus_evidence={"scope": "CPU fixture"},
    )
    with AgentXTokenCorpus(output, prepared) as corpus:
        a, b, other = (corpus.get(rid) for rid in ("w:r0", "w:r1", "other:r0"))
        assert a.prompt_tokens[:64] == b.prompt_tokens[:64]
        assert a.prompt_tokens[64:] != b.prompt_tokens[64:]
        assert a.extra_key == b.extra_key and a.extra_key != other.extra_key
        assert a.cache_salt != other.cache_salt
        assert a.output_tokens[-1] == 9
        assert a.sampling_parameters["ignore_eos"]
        assert a.sampling_parameters["max_new_tokens"] == a.sampling_parameters["min_new_tokens"] == 4
        assert a.session_id is None and a.session_generation is None
        assert a.generation_parameters["sampling_seed"] == 21
        assert a.identity == ("w", "w:root", "w:root:context", 0)
        a.prompt_tokens[0] = 123456
        assert corpus.get("w:r0").prompt_tokens[0] != 123456
    assert manifest["requests"] == len(plan.request_usage)
    assert manifest["native_source_capture_required"] is False
    assert not manifest["original_content_or_eos_reconstructed"]
    assert load_agentx_preparation(prepared)[1] == AGENTX_TIMING


def test_synthesis_is_independent_of_request_order_and_scopes_shared_blocks_per_workflow():
    first, second = synthesizer(), synthesizer()
    a = {"request_id": "a", "input_tokens": 150, "output_tokens": 4, "hash_ids": [1, 2]}
    b = {**a, "request_id": "b", "hash_ids": [1, 3]}
    expected_a, expected_b = first.prompt("w", 64, a), first.prompt("w", 64, b)
    assert second.prompt("w", 64, b) == expected_b
    assert second.prompt("w", 64, a) == expected_a
    assert expected_a[:64] == expected_b[:64]
    assert expected_a[128:] != expected_b[128:]
    assert second.prompt("other", 64, a) != expected_a
    assert len(expected_a) == 150


@pytest.mark.parametrize("kind", ("plan", "index", "bytes", "mid_run_bytes", "identity"))
def test_modified_prepared_work_or_tokens_cannot_silently_enter_a_baseline(tmp_path, kind):
    prepared, _, _ = prepared_source(tmp_path)
    output = tmp_path / "tokens"
    prepare_agentx_tokens(prepared, output, synthesizer(), corpus_evidence={})
    if kind == "plan":
        with (prepared / "plan.json").open("ab") as stream:
            stream.write(b" ")
    elif kind == "index":
        with (output / "index.json").open("ab") as stream:
            stream.write(b" ")
    elif kind == "bytes":
        with (output / "tokens.u32").open("ab") as stream:
            stream.write(b"x")
    elif kind == "identity":
        index = json.loads((output / "index.json").read_bytes())
        index["w:r0"]["identity"][1] = "foreign"
        (output / "index.json").write_text(json.dumps(index))
        manifest = json.loads((output / "manifest.json").read_bytes())
        manifest["artifacts"]["index.json"] = hashlib.sha256((output / "index.json").read_bytes()).hexdigest()
        (output / "manifest.json").write_text(json.dumps(manifest))
    else:
        with AgentXTokenCorpus(output, prepared) as corpus:
            with (output / "tokens.u32").open("ab") as stream:
                stream.write(b"x")
            with pytest.raises(ValueError):
                corpus.get("w:r0")
        return
    with pytest.raises(ValueError):
        AgentXTokenCorpus(output, prepared)


def test_synthesis_rejects_a_different_seed_before_writing_the_bundle(tmp_path):
    prepared, _, _ = prepared_source(tmp_path)
    output = tmp_path / "tokens"
    synthesis = AgentXTokenSynthesis(range(512), seed=22, eos_token_id=9)
    with pytest.raises(ValueError, match="random seed"):
        prepare_agentx_tokens(prepared, output, synthesis, corpus_evidence={})
    assert not output.exists()
