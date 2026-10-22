from collections import defaultdict
from copy import deepcopy
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.experiments import causal_replay_native as bridge
from beliefkv.experiments.causal_replay_sampling import PARAMETER_KEY
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from tests.test_causal_replay_backend import native_request
from tests.test_causal_replay_tokens import source, write_source


class FakeBase:
    def build_additional_customized_info(self, reqs):
        return {"previous": [[] for _ in reqs]}


class FakeScheduler:
    def get_output_streamer_class(self):
        return FakeBase


@pytest.fixture
def req(tmp_path):
    records, expected, _ = source(count=1, width=100_000)
    path = tmp_path / "tokens.jsonl"
    write_source(path, records)
    with CapturedTokenCorpus(path, expected) as corpus:
        yield native_request(corpus.get("r0"))


def test_receipt_copies_actual_terminal_inputs_outputs_and_stopping_without_future_values(req):
    req.sampling_params.custom_params[PARAMETER_KEY]["output_token_ids"] = [9999]
    result = bridge.terminal_receipt(req)
    assert result["output_token_ids"] == [7, 9]
    assert result["prompt_tokens"] == 100001
    assert result["sampling_parameters"]["max_new_tokens"] == 32
    assert result["native_finish_reason"] == {"type": "stop", "matched": 9}
    assert result["completion_boundary"] == "native_terminal_output_emit"
    req.output_ids[0] = 999
    req.sampling_params.stop_strs.append("later")
    assert result["output_token_ids"] == [7, 9]
    assert result["sampling_parameters"]["stop"] == []


@pytest.mark.parametrize("change", ("unfinished", "foreign_request", "missing_clock"))
def test_missing_native_terminal_or_clock_cannot_produce_a_receipt(req, monkeypatch, change):
    if change == "unfinished":
        req.finished = lambda: False
    elif change == "foreign_request":
        req.sampling_params.custom_params[PARAMETER_KEY]["request_id"] = "other"
    else:
        monkeypatch.setattr(bridge, "local_monotonic_clock_domain", lambda: None)
    with pytest.raises(ValueError):
        bridge.terminal_receipt(req)


def test_mixed_output_keeps_existing_hook_and_only_receipts_emitted_terminal_replay_requests(req):
    streamer = bridge.replay_streamer_class(FakeBase)()
    ordinary = NS(finished=lambda: True, sampling_params=NS(custom_params=None))
    pending = deepcopy(req)
    pending.finished = lambda: False
    result = streamer.build_additional_customized_info([ordinary, pending, req])
    assert result["previous"] == [[], [], []]
    receipts = result[bridge.RECEIPT_KEY]
    assert receipts[:2] == [[], []] and len(receipts[2]) == 1
    second = streamer.build_additional_customized_info([req])
    assert second[bridge.RECEIPT_KEY][0][0] is receipts[2][0]


def test_plugin_is_disabled_without_the_explicit_replay_flag(monkeypatch):
    monkeypatch.delenv(bridge.BRIDGE_ENV, raising=False)
    bridge.register()


@pytest.mark.parametrize("value", ("yes", "2", ""))
def test_invalid_plugin_flag_is_explicitly_rejected(monkeypatch, value):
    monkeypatch.setenv(bridge.BRIDGE_ENV, value)
    with pytest.raises(ValueError):
        bridge.register()


def native_root(monkeypatch):
    checkout = os.environ.get("SGLANG_SOURCE_CHECKOUT")
    if not checkout:
        pytest.skip("set SGLANG_SOURCE_CHECKOUT to qualify the native output API")
    root = Path(checkout)
    monkeypatch.syspath_prepend(str(root / "python"))
    return root


def test_actual_native_hook_registry_keeps_the_original_scheduler_factory_contract(monkeypatch):
    native_root(monkeypatch)
    from sglang.srt.plugins.hook_registry import HookRegistry, HookType

    monkeypatch.setattr(HookRegistry, "_hooks", defaultdict(list))
    monkeypatch.setattr(HookRegistry, "_patched", set())
    monkeypatch.setattr(FakeScheduler, "get_output_streamer_class", FakeScheduler.get_output_streamer_class)
    HookRegistry.register(
        f"{__name__}.FakeScheduler.get_output_streamer_class",
        bridge._streamer_hook, HookType.AFTER,
    )
    HookRegistry.apply_hooks()
    selected = FakeScheduler().get_output_streamer_class()
    assert issubclass(selected, FakeBase) and selected.has_additional_customized_info


@pytest.mark.parametrize("delayed", (False, True))
def test_enabled_plugin_requires_native_delayed_sampling_to_avoid_stale_req_progress(
    monkeypatch, delayed,
):
    native_root(monkeypatch)
    from sglang.srt.plugins.hook_registry import HookRegistry

    monkeypatch.setattr(HookRegistry, "_hooks", defaultdict(list))
    monkeypatch.setenv(bridge.BRIDGE_ENV, "1")
    monkeypatch.setenv("SGLANG_ENABLE_DELAY_SAMPLE", "1" if delayed else "0")
    if delayed:
        bridge.register()
        target = "sglang.srt.managers.scheduler.Scheduler.get_output_streamer_class"
        assert len(HookRegistry._hooks[target]) == 1
    else:
        with pytest.raises(ValueError, match="SGLANG_ENABLE_DELAY_SAMPLE"):
            bridge.register()
        assert not HookRegistry._hooks


def test_actual_native_output_payload_carries_receipts_once_without_a_log_flush(req, monkeypatch):
    root = native_root(monkeypatch)
    path = root / "test/registered/unit/managers/test_output_streamer_customized_info.py"
    spec = importlib.util.spec_from_file_location("_native_replay_output_fixture", path)
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    from sglang.srt.managers.io_struct import unwrap_from_pickle

    native_req = fixtures._FakeReq("r0", [7, 9], finished=True)
    native_req.origin_input_ids = req.origin_input_ids
    native_req.beliefkv_metadata = req.beliefkv_metadata
    for name in (
        "max_new_tokens", "min_new_tokens", "ignore_eos", "stop_strs",
        "stop_regex_strs", "stop_token_ids", "custom_params",
    ):
        setattr(native_req.sampling_params, name, getattr(req.sampling_params, name))
    native_req.finished_reason = req.finished_reason
    native_req.session_id, native_req.session_generation = "s", 1
    native_req.extra_key, native_req.cache_salt = "ns", None
    ordinary = fixtures._FakeReq("ordinary", [10], finished=True)
    pending = fixtures._FakeReq("pending", [11], finished=False)
    for current in (native_req, ordinary, pending):
        current.cached_tokens_device = 0
        current.cached_tokens_host = 0
        current.cached_tokens_storage = 0
    outputs = []
    streamer = bridge.replay_streamer_class(fixtures.SchedulerOutputStreamer)(
        send_to_detokenizer=NS(send_output=outputs.append), tree_cache=Mock(),
        ps=NS(attn_tp_rank=0, dp_rank=0), server_args=Mock(), is_generation=True,
        spec_algorithm=fixtures.SpeculativeAlgorithm.NONE,
        disaggregation_mode=fixtures.DisaggregationMode.NULL,
        enable_hicache_storage=lambda: False,
    )
    with (
        patch("sglang.srt.managers.scheduler_components.output_streamer.get_serving",
              return_value=NS(stream_interval=1, weight_version="default")),
        patch("sglang.srt.managers.scheduler_components.output_streamer.get_observability",
              return_value=NS(enable_request_time_stats_logging=False)),
    ):
        streamer.stream_output([pending, native_req, ordinary], False)
        streamer.stream_output([native_req, ordinary], False)
    assert len(outputs) == 1
    assert outputs[0].rids == ["r0", "ordinary"]
    info = unwrap_from_pickle(outputs[0].customized_info)[bridge.RECEIPT_KEY]
    assert info[1] == [] and len(info[0]) == 1
    assert info[0][0]["output_token_ids"] == [7, 9]
    assert info[0][0]["request_id"] == "r0"
