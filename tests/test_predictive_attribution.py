import pytest

from beliefkv.policy.predictive_attribution import PredictiveActionAttributionLedger


def register(ledger, intent_id, action="prepare_host", context_epoch=2, now_ms=1):
    ledger.register(
        intent_id=intent_id,
        action=action,
        context_id="ctx",
        context_epoch=context_epoch,
        command_id=f"command-{intent_id}",
        now_ms=now_ms,
    )


def test_prepare_requires_proven_commit_reuse() -> None:
    events = []
    ledger = PredictiveActionAttributionLedger(
        lambda event, _now, outcome: events.append((event, outcome.intent_id))
    )
    register(ledger, "i1")
    ledger.transfer_terminal("i1", completed=True, actual_bytes=10, now_ms=2)
    assert ledger.consume_context(
        "ctx",
        context_epoch=2,
        now_ms=3,
        reason="observed_pressure_commit",
        consumed_intent_id="i1",
    )[0].state == "useful"
    assert events == [("registered", "i1"), ("prepared", "i1"), ("useful", "i1")]


@pytest.mark.parametrize("reason", ["prefetch_first_gpu_service", "wait_reentry"])
def test_prepare_is_not_consumed_by_service_or_wait_reentry(reason) -> None:
    ledger = PredictiveActionAttributionLedger()
    register(ledger, "prepare")
    ledger.transfer_terminal("prepare", completed=True, actual_bytes=10, now_ms=2)
    assert ledger.consume_context(
        "ctx", context_epoch=2, now_ms=3, reason=reason
    ) == ()
    assert ledger.get("prepare").state == "prepared"
    assert ledger.terminal_context(
        "ctx", context_epoch=2, now_ms=4, reason="context_terminal"
    )[0].state == "wasted"


def test_context_only_commit_censors_unproven_prepares() -> None:
    ledger = PredictiveActionAttributionLedger()
    register(ledger, "ready")
    register(ledger, "pending")
    register(ledger, "other-epoch", context_epoch=3)
    ledger.transfer_terminal("ready", completed=True, actual_bytes=10, now_ms=2)
    consumed = ledger.consume_context(
        "ctx", context_epoch=2, now_ms=3, reason="observed_pressure_commit"
    )
    assert [(item.intent_id, item.state) for item in consumed] == [
        ("ready", "censored"),
        ("pending", "censored"),
    ]
    assert ledger.get("other-epoch").state == "pending_transfer"
    ledger.transfer_terminal("pending", completed=True, actual_bytes=10, now_ms=4)
    assert ledger.get("pending").state == "censored"


def test_proven_commit_only_credits_the_identified_prepare() -> None:
    ledger = PredictiveActionAttributionLedger()
    register(ledger, "used")
    register(ledger, "unproven")
    for intent_id in ("used", "unproven"):
        ledger.transfer_terminal(intent_id, completed=True, actual_bytes=10, now_ms=2)
    consumed = ledger.consume_context(
        "ctx",
        context_epoch=2,
        now_ms=3,
        reason="observed_pressure_commit",
        consumed_intent_id="used",
    )
    assert [item.state for item in consumed] == ["useful", "censored"]


@pytest.mark.parametrize(
    ("completed_ts", "actual_bytes"),
    [(4, 10), (2, 0)],
)
def test_commit_cannot_credit_late_or_empty_prepare(completed_ts, actual_bytes) -> None:
    ledger = PredictiveActionAttributionLedger()
    register(ledger, "prepare")
    ledger.transfer_terminal(
        "prepare", completed=True, actual_bytes=actual_bytes, now_ms=completed_ts
    )
    assert ledger.consume_context(
        "ctx",
        context_epoch=2,
        now_ms=3,
        reason="observed_pressure_commit",
        consumed_intent_id="prepare",
    )[0].state == "censored"


@pytest.mark.parametrize(
    "action",
    ["prefetch_gpu", "partial_prefetch_gpu", "reclaim_and_prefetch"],
)
def test_prefetch_is_useful_on_first_gpu_service(action) -> None:
    ledger = PredictiveActionAttributionLedger()
    register(ledger, "prefetch", action=action)
    ledger.transfer_terminal("prefetch", completed=True, actual_bytes=20, now_ms=2)
    assert ledger.consume_context(
        "ctx", context_epoch=2, now_ms=3, reason="prefetch_first_gpu_service"
    )[0].state == "useful"
    assert ledger.consume_context(
        "ctx", context_epoch=2, now_ms=4, reason="prefetch_first_gpu_service"
    ) == ()


def test_prefetch_service_does_not_credit_ambiguous_or_pending_candidates() -> None:
    ledger = PredictiveActionAttributionLedger()
    for intent_id in ("first", "second"):
        register(ledger, intent_id, action="prefetch_gpu")
        ledger.transfer_terminal(intent_id, completed=True, actual_bytes=10, now_ms=2)
    register(ledger, "pending", action="prefetch_gpu")
    consumed = ledger.consume_context(
        "ctx", context_epoch=2, now_ms=3, reason="prefetch_first_gpu_service"
    )
    assert [item.state for item in consumed] == ["censored", "censored"]
    assert ledger.get("pending").state == "pending_transfer"


def test_one_ready_prefetch_with_another_pending_is_not_proven_used() -> None:
    ledger = PredictiveActionAttributionLedger()
    register(ledger, "old", action="prefetch_gpu")
    ledger.transfer_terminal("old", completed=True, actual_bytes=10, now_ms=2)
    register(ledger, "new", action="prefetch_gpu", now_ms=3)
    assert ledger.consume_context(
        "ctx", context_epoch=2, now_ms=4, reason="prefetch_first_gpu_service"
    ) == ()
    assert ledger.get("old").state == "prepared"
    assert ledger.get("new").state == "pending_transfer"


def test_explicit_prefetch_identity_selects_one_candidate() -> None:
    ledger = PredictiveActionAttributionLedger()
    for intent_id in ("first", "second"):
        register(ledger, intent_id, action="prefetch_gpu")
        ledger.transfer_terminal(intent_id, completed=True, actual_bytes=10, now_ms=2)
    assert ledger.consume_context(
        "ctx",
        context_epoch=2,
        now_ms=3,
        reason="prefetch_first_gpu_service",
        consumed_intent_id="second",
    )[0].intent_id == "second"
    assert ledger.get("first").state == "prepared"


def test_commit_and_service_do_not_credit_the_other_action() -> None:
    ledger = PredictiveActionAttributionLedger()
    register(ledger, "prepare")
    register(ledger, "prefetch", action="prefetch_gpu")
    for intent_id in ("prepare", "prefetch"):
        ledger.transfer_terminal(intent_id, completed=True, actual_bytes=10, now_ms=2)
    assert ledger.consume_context(
        "ctx", context_epoch=2, now_ms=3, reason="prefetch_first_gpu_service"
    )[0].intent_id == "prefetch"
    assert ledger.get("prepare").state == "prepared"
    assert ledger.consume_context(
        "ctx", context_epoch=2, now_ms=4, reason="observed_pressure_commit"
    )[0].state == "censored"
    assert ledger.get("prefetch").state == "useful"


@pytest.mark.parametrize(
    ("completed_ts", "actual_bytes"),
    [(4, 10), (2, 0)],
)
def test_prefetch_service_does_not_credit_late_or_empty_transfer(
    completed_ts, actual_bytes
) -> None:
    ledger = PredictiveActionAttributionLedger()
    register(ledger, "prefetch", action="prefetch_gpu")
    ledger.transfer_terminal(
        "prefetch", completed=True, actual_bytes=actual_bytes, now_ms=completed_ts
    )
    assert ledger.consume_context(
        "ctx", context_epoch=2, now_ms=3, reason="prefetch_first_gpu_service"
    ) == ()
    assert ledger.get("prefetch").state == "prepared"


def test_consumption_without_epoch_cannot_credit_an_intent() -> None:
    ledger = PredictiveActionAttributionLedger()
    register(ledger, "prepare")
    ledger.transfer_terminal("prepare", completed=True, actual_bytes=10, now_ms=2)
    assert ledger.consume_context(
        "ctx",
        context_epoch=None,
        now_ms=3,
        reason="observed_pressure_commit",
        consumed_intent_id="prepare",
    ) == ()
    assert ledger.get("prepare").state == "prepared"


def test_failed_transfer_and_censor_are_not_later_credited() -> None:
    ledger = PredictiveActionAttributionLedger()
    register(ledger, "failed")
    ledger.transfer_terminal("failed", completed=False, actual_bytes=10, now_ms=2)
    register(ledger, "shutdown", action="prefetch_gpu")
    ledger.censor_all(now_ms=3, reason="shutdown")
    assert ledger.consume_context(
        "ctx",
        context_epoch=2,
        now_ms=4,
        reason="observed_pressure_commit",
        consumed_intent_id="failed",
    ) == ()
    assert ledger.get("failed").state == "failed"
    assert ledger.get("shutdown").state == "censored"


def test_duplicate_transfer_terminal_does_not_rewrite_prepared_evidence() -> None:
    ledger = PredictiveActionAttributionLedger()
    register(ledger, "prepare")
    ledger.transfer_terminal("prepare", completed=True, actual_bytes=10, now_ms=2)
    ledger.transfer_terminal("prepare", completed=True, actual_bytes=0, now_ms=4)
    assert ledger.get("prepare").actual_bytes == 10
    assert ledger.get("prepare").transfer_completed_ts_ms == 2
