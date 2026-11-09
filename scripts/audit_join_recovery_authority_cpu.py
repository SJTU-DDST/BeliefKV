"""Expose current JOIN authority/backoff boundaries using bounded CPU fixtures."""

import argparse
from collections import Counter
from datetime import datetime
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.join_blocked_handoff import JoinBlockedHandoff
from beliefkv.runtime.reference_thunder_admission import ReferenceThunderAdmission
from scripts.train_workflow_work import write
from tests.test_join_confirmed_reentry import client_submit, complete_join, continuing_join_fixture


def probe():
    runtime, previous, _opportunity, cache, _reserved = continuing_join_fixture()
    try:
        complete_join(runtime)
        runtime.on_events((client_submit(),))
        ticket = runtime._join_ticket
        target = ticket.key
        if not runtime._live_join_ticket() or not runtime._pending_confirmed_join_submit(target):
            raise ValueError("the fixture lacks a validated live client-bound JOIN")
        # The real policy entry points are exercised; budget and native streams
        # remain CPU stand-ins, without asserting physical fit or performance.
        admission = object.__new__(ReferenceThunderAdmission)
        admission.runtime = runtime
        owner = NS(
            active=False, pending=False, marked_for_pause=False,
            tokens=16, identity=previous.invocation_id,
        )
        admission.policy = NS(
            programs={previous.invocation_id: owner},
            remaining_capacity=lambda: 10000, buffer_tokens=100,
        )
        admission.latest = {previous.invocation_id: previous}
        admission.join_free_admission = True
        admission.counts = Counter()
        admission.requests = {}
        admission._live_requests = {}
        admission._join_handoff = JoinBlockedHandoff(admission)
        owner_recognized = admission._restore_owner(target, source="join_ticket") is owner
        paused = admission.claim_restore(target, source="join_ticket")
        owner.active = True
        active = admission.claim_restore(target, source="join_ticket")
        recovery = admission._join_handoff.recovery
        recovery.consumer = None, target
        confirmed_consumer = recovery._consumer_live()
        result = {
            "validated_client_bound_ticket": True,
            "same_session_and_generation": (
                previous.session_id, previous.session_generation,
            ) == (target.session_id, target.session_generation),
            "consumer_not_yet_native_visible": target.request_id not in runtime.visible,
            "context_session_detached_during_client_handoff": (
                runtime.context_sessions.get(target.context_id) is None
            ),
            "restore_owner_recognized": owner_recognized,
            "logical_free_tokens_cpu_standin": admission.policy.remaining_capacity(),
            "paused_owner_can_claim_restore": paused,
            "already_active_owner_can_claim_restore": active,
            "confirmed_JOIN_is_satisfied": runtime.graph.joins[ticket.join_id].satisfied,
            "confirmed_ticket_has_provisional_EOS_authority": (
                runtime._join_ticket_has_native_eos(ticket)
            ),
            "confirmed_unsubmitted_consumer_physically_eligible": confirmed_consumer,
        }
        cache.tree_components = (0,)
        runtime.event_server = None
        recovery.consumer = None
        recovery.plans[previous.invocation_id] = (previous, "join", (), 100.2)
        observer = Mock(return_value=NS(observable=True, device_full_free_tokens=10000))
        budget = NS(reserve_full_tokens=0, native_growth_full_tokens=0, native_prefill_full_tokens=0)
        with (
            patch("beliefkv.runtime.join_physical_recovery.observe_static_full_mamba_headroom", observer),
            patch.object(recovery, "_consumer_live", return_value=True),
            patch.object(runtime, "_current_residency_budget", return_value=budget),
            patch.object(runtime, "_read_request_reentry", return_value={"missing_full_tokens": 0}),
            patch.object(runtime, "_request_restore_cost", return_value=(0, 0)),
        ):
            before = runtime.counts["join_physical_plan_attempts"]
            with patch("time.monotonic", return_value=100.05):
                recovery.advance(target, request=NS())
            early = runtime.counts["join_physical_plan_attempts"] - before
            early_reads = observer.call_count
            with patch("time.monotonic", return_value=100.21):
                recovery.advance(target, request=NS())
            later = runtime.counts["join_physical_plan_attempts"] - before
        result["same_session_new_epoch_backoff"] = {
            "attempts_at_50ms": early, "headroom_reads_at_50ms": early_reads,
            "cumulative_attempts_at_210ms": later,
            "later_outcome_no_missing_prefix": runtime.counts[
                "join_physical_plan_outcome_no_missing_prefix"
            ],
        }
        if (
            not owner_recognized or paused or not active or confirmed_consumer
            or early != 0 or early_reads != 0 or later != 1
        ):
            raise ValueError("current boundaries differ; review rather than accepting old diagnoses")
        return result
    finally:
        runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("preserve earlier evidence and hide CUDA")
    files = (
        Path(__file__), ROOT / "beliefkv/runtime/reference_thunder_admission.py",
        ROOT / "beliefkv/runtime/join_blocked_handoff.py",
        ROOT / "beliefkv/runtime/join_physical_recovery.py",
        ROOT / "beliefkv/runtime/sglang_v0520_runtime.py",
        ROOT / "tests/test_join_confirmed_reentry.py",
    )
    result = {
        "observed_at_local": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "observations": probe(),
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in files},
        "scope": (
            "Live confirmed binding and actual policy/recovery entry points with CPU "
            "budget/native-stream stand-ins. Backoff probe holds authority valid to "
            "isolate its timer. No DMA, performance benefit, physical fit, trace "
            "occurrence count or correctness repair is established."
        ),
        "GPU_action": False, "runtime_changed": False,
    }
    write(args.output, result)
    print(result)


if __name__ == "__main__":
    main()
