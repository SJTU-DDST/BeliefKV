"""CPU-only checks of native merged ACK transaction credit."""

from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.sglang_v0520_physical import (
    PhysicalActionExpectation,
    PhysicalChildExpectation,
    PhysicalReceiptError,
    PhysicalTransactionLedger,
)


def child(anchor, published, kv, mamba, total):
    return PhysicalChildExpectation(
        anchor, published, (("kv", kv), ("mamba", mamba)), total
    )


def expected(command="cmd", *, action="PREPARE_HOST", children=None, epoch=3):
    return PhysicalActionExpectation(
        command_id=command,
        action=action,
        context_id="ctx",
        context_epoch=epoch,
        children=children or (child(11, (11, 12), 20, 5, 32),),
        pool_bytes_per_token=(("kv", 10), ("mamba", 5)),
    )


def receipt(command="cmd", *, anchor=11, published=(11, 12), kv=2, mamba=1, total=32):
    return NS(
        command_id=command,
        anchor_node_id=anchor,
        published_node_ids=published,
        num_tokens_by_pool=(("kv", kv), ("mamba", mamba)),
        num_bytes=total,
    )


def ack(*receipts, direction="d2h", nodes=(11, 12), kv=2, mamba=1, status="completed"):
    return NS(
        direction=direction, status=status, node_ids=nodes,
        num_tokens_by_pool=(("kv", kv), ("mamba", mamba)),
        child_commits=receipts,
    )


def test_merged_native_and_tagged_ack_credits_only_matched_child():
    ledger = PhysicalTransactionLedger()
    ledger.register(expected())
    event = ack(receipt(), nodes=(11, 12, 99), kv=5, mamba=1)
    completed, = ledger.observe(event, live_context_epochs={"ctx": 3})
    assert completed.command_id == "cmd"
    assert completed.node_ids == (11, 12)
    assert completed.pool_bytes == (("kv", 20), ("mamba", 5))
    assert completed.num_bytes == 32
    assert ledger.pending_count == 0
    with pytest.raises(PhysicalReceiptError, match="unknown"):
        ledger.observe(event, live_context_epochs={"ctx": 3})


def test_two_tagged_children_in_same_merged_ack():
    ledger = PhysicalTransactionLedger()
    ledger.register(expected("first"))
    ledger.register(expected(
        "second", action="PREPARE_HOST",
        children=(child(13, (13,), 10, 0, 10),),
    ))
    event = ack(
        receipt(command="first"),
        receipt(command="second", anchor=13, published=(13,), kv=1, mamba=0, total=10),
        nodes=(11, 12, 13), kv=3, mamba=1,
    )
    assert {item.command_id for item in ledger.observe(
        event, live_context_epochs={"ctx": 3},
    )} == {"first", "second"}
    assert ledger.pending_count == 0


def test_invalid_child_of_merged_ack_cannot_credit_other_command():
    ledger = PhysicalTransactionLedger()
    ledger.register(expected("first"))
    ledger.register(expected(
        "second", children=(child(13, (13,), 10, 0, 10),),
    ))
    event = ack(
        receipt(command="first"),
        receipt(command="second", anchor=13, published=(13,), kv=2, mamba=0, total=10),
        nodes=(11, 12, 13), kv=4, mamba=1,
    )
    with pytest.raises(PhysicalReceiptError, match="mismatch"):
        ledger.observe(event, live_context_epochs={"ctx": 3})
    assert ledger.pending_count == 0


def test_merged_ack_missing_one_tagged_child_rejects_all_credit():
    ledger = PhysicalTransactionLedger()
    ledger.register(expected("first"))
    ledger.register(expected(
        "second", children=(child(13, (13,), 10, 0, 10),),
    ))
    with pytest.raises(PhysicalReceiptError, match="omitted"):
        ledger.observe(
            ack(receipt(command="first"), nodes=(11, 12, 13), kv=3, mamba=1),
            live_context_epochs={"ctx": 3},
        )
    assert ledger.pending_count == 0


def test_overlapping_pending_node_or_unbounded_publication_rejected():
    ledger = PhysicalTransactionLedger(max_nodes=2)
    ledger.register(expected())
    with pytest.raises(PhysicalReceiptError, match="already owned"):
        ledger.register(expected("other"))
    with pytest.raises(PhysicalReceiptError, match="node bound"):
        ledger.register(expected(
            "oversized", children=(child(21, (21, 22, 23), 20, 5, 32),),
        ))


def test_two_child_command_waits_for_full_reconciliation_across_acks():
    ledger = PhysicalTransactionLedger()
    ledger.register(expected(children=(
        child(11, (11, 12), 20, 5, 32),
        child(13, (13,), 10, 0, 10),
    )))
    assert ledger.observe(ack(receipt()), live_context_epochs={"ctx": 3}) == ()
    second = ack(
        receipt(anchor=13, published=(13,), kv=1, mamba=0, total=10),
        nodes=(13,), kv=1, mamba=0,
    )
    completed, = ledger.observe(second, live_context_epochs={"ctx": 3})
    assert completed.node_ids == (11, 12, 13)
    assert completed.num_bytes == 42


def test_partial_ack_with_no_child_credit_fails_closed():
    ledger = PhysicalTransactionLedger()
    ledger.register(expected())
    with pytest.raises(PhysicalReceiptError, match="missing child credit"):
        ledger.observe(ack(nodes=(11, 12)), live_context_epochs={"ctx": 3})
    assert ledger.pending_count == 0


@pytest.mark.parametrize("event", [
    ack(receipt(), receipt(), nodes=(11, 12), kv=4, mamba=2),
    ack(receipt(command="unknown")),
    ack(receipt(kv=1)),
    ack(receipt(total=31)),
    ack(receipt(published=(11,))),
    ack(receipt(), status="pending"),
    ack(receipt(), direction="h2d"),
    ack(receipt(), kv=1),
    ack(
        NS(
            command_id="cmd", anchor_node_id=11, published_node_ids=(11, 12),
            num_tokens_by_pool=(("kv", "bad"),), num_bytes=32,
        )
    ),
])
def test_malformed_duplicate_unknown_and_mismatch_poison_credit(event):
    ledger = PhysicalTransactionLedger()
    ledger.register(expected())
    with pytest.raises(PhysicalReceiptError):
        ledger.observe(event, live_context_epochs={"ctx": 3})
    assert ledger.pending_count == 0


def test_stale_context_epoch_and_replay_do_not_credit():
    ledger = PhysicalTransactionLedger()
    ledger.register(expected())
    with pytest.raises(PhysicalReceiptError, match="epoch"):
        ledger.observe(ack(receipt()), live_context_epochs={"ctx": 4})
    with pytest.raises(PhysicalReceiptError):
        ledger.register(expected())
    ledger.register(expected("next"))
    with pytest.raises(PhysicalReceiptError, match="epoch"):
        ledger.observe(
            ack(receipt(command="next")), live_context_epochs={"ctx": True},
        )


def test_session_generation_must_still_match_at_ack():
    from dataclasses import replace

    ledger = PhysicalTransactionLedger()
    ledger.register(replace(
        expected(), session_id="session", session_generation=4
    ))
    with pytest.raises(PhysicalReceiptError, match="session"):
        ledger.observe(
            ack(receipt()),
            live_context_epochs={"ctx": 3},
            live_context_sessions={"ctx": ("session", 5)},
        )
    assert ledger.pending_count == 0
    ledger.register(replace(
        expected("current"), session_id="session", session_generation=5
    ))
    (completed,) = ledger.observe(
        ack(receipt("current")),
        live_context_epochs={"ctx": 3},
        live_context_sessions={"ctx": ("session", 5)},
    )
    assert completed.command_id == "current"


def test_h2d_child_and_mamba_only_pool_counts():
    ledger = PhysicalTransactionLedger()
    ledger.register(expected(
        action="PREFETCH_GPU",
        children=(child(11, (11,), 0, 10, 10),),
    ))
    completed, = ledger.observe(
        ack(
            receipt(published=(11,), kv=0, mamba=2, total=10),
            direction="h2d", nodes=(11,), kv=0, mamba=2,
        ),
        live_context_epochs={"ctx": 3},
    )
    assert completed.action == "PREFETCH_GPU"
    assert completed.pool_bytes == (("kv", 0), ("mamba", 10))


def test_bounds_expiry_and_late_receipt_never_gain_credit(monkeypatch):
    import beliefkv.runtime.sglang_v0520_physical as physical

    clock = [0.0]
    monkeypatch.setattr(physical, "monotonic", lambda: clock[0])
    ledger = PhysicalTransactionLedger(max_pending=1, max_age_s=1)
    ledger.register(expected())
    with pytest.raises(PhysicalReceiptError):
        ledger.register(expected("other"))
    clock[0] = 2.0
    assert ledger.expire() == ("cmd",)
    assert ledger.pending_count == 0
    with pytest.raises(PhysicalReceiptError):
        ledger.observe(ack(receipt()), live_context_epochs={"ctx": 3})


def test_missing_child_credit_with_unknown_nodes_remains_pending_then_expires(monkeypatch):
    import beliefkv.runtime.sglang_v0520_physical as physical

    clock = [0.0]
    monkeypatch.setattr(physical, "monotonic", lambda: clock[0])
    ledger = PhysicalTransactionLedger(max_age_s=1)
    ledger.register(expected())
    assert ledger.observe(
        ack(nodes=(99,), kv=1, mamba=0), live_context_epochs={"ctx": 3},
    ) == ()
    assert ledger.pending_count == 1
    clock[0] = 1.0
    assert ledger.expire() == ("cmd",)
