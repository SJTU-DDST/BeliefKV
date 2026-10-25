from itertools import product
import random

import pytest

from beliefkv.policy.infercept import (
    IterationBudget, ResumeChunk, ResumeQueues, iteration_budget,
    unknown_interception_seconds,
)


@pytest.mark.parametrize("page", (1, 2, 3, 16))
def test_budget_matches_exhaustive_integer_optimization(page):
    for query, incoming, outgoing, limit, gpu, host in product(range(4), repeat=6):
        expected = max(
            (
                (min(query, gpu + out - inc), inc, out)
                for inc in range(0, min(incoming, limit) + 1, page)
                for out in range(0, min(outgoing, limit - inc, host + inc) + 1, page)
                if inc <= out + gpu
            ),
        )
        actual = iteration_budget(
            query_demand=query, swap_in_demand=incoming, swap_out_demand=outgoing,
            swap_limit=limit, free_gpu_tokens=gpu, free_host_tokens=host, page_tokens=page,
        )
        assert (actual.query_tokens, actual.swap_in_tokens, actual.swap_out_tokens) == expected


def test_host_full_can_exchange_pages_without_claiming_net_new_capacity():
    plan = iteration_budget(
        query_demand=100, swap_in_demand=100, swap_out_demand=100,
        swap_limit=80, free_gpu_tokens=20, free_host_tokens=0,
    )
    assert plan == IterationBudget(20, 40, 40)


def test_both_full_is_only_aggregate_feasibility_and_not_permission_to_enqueue():
    result = iteration_budget(
        query_demand=100, swap_in_demand=100, swap_out_demand=100,
        swap_limit=80, free_gpu_tokens=0, free_host_tokens=0,
    )
    assert result == IterationBudget(0, 40, 40)
    # Neither first physical chunk has allocation space. The future adapter
    # must obtain actual capacity before attempting either half of this plan.
    assert result.query_tokens == 0


def test_new_queries_remain_primary_before_the_declared_swap_in_tie_break():
    assert iteration_budget(
        query_demand=80, swap_in_demand=100, swap_out_demand=100,
        swap_limit=100, free_gpu_tokens=0, free_host_tokens=100,
    ) == IterationBudget(80, 10, 90)


@pytest.mark.parametrize("seed", (0, 21, 88))
def test_large_token_budgets_respect_all_physical_constraints_and_page_sizes(seed):
    rng = random.Random(seed)
    for _ in range(1000):
        q, inc, out, limit, gpu, host = [rng.randrange(262145) for _ in range(6)]
        page = rng.choice((1, 16, 64, 256))
        result = iteration_budget(
            query_demand=q, swap_in_demand=inc, swap_out_demand=out,
            swap_limit=limit, free_gpu_tokens=gpu, free_host_tokens=host, page_tokens=page,
        )
        assert 0 <= result.query_tokens <= q
        assert 0 <= result.swap_in_tokens <= inc
        assert 0 <= result.swap_out_tokens <= out
        assert result.swap_in_tokens + result.swap_out_tokens <= limit
        assert result.swap_out_tokens <= host + result.swap_in_tokens
        assert result.swap_in_tokens + result.query_tokens <= gpu + result.swap_out_tokens
        assert result.swap_in_tokens % page == result.swap_out_tokens % page == 0


@pytest.mark.parametrize("bad", (-1, True, .5))
def test_budget_rejects_invalid_native_token_counters(bad):
    with pytest.raises(ValueError):
        iteration_budget(
            query_demand=bad, swap_in_demand=0, swap_out_demand=0,
            swap_limit=0, free_gpu_tokens=0, free_host_tokens=0,
        )


def test_tool_duration_uses_only_elapsed_wait_and_never_a_future_end():
    assert unknown_interception_seconds(started=10., now=10.) == 0.
    assert unknown_interception_seconds(started=10., now=12.5) == 2.5
    with pytest.raises(ValueError):
        unknown_interception_seconds(started=10., now=9.)


def test_tool_reentry_retains_original_fcfs_age():
    queues = ResumeQueues()
    queues.enqueue("old", arrived=0., query_tokens=1)
    queues.acknowledge("old", query_tokens=1)
    queues.enqueue("new", arrived=1., query_tokens=100)
    queues.enqueue("old", arrived=2., query_tokens=100)
    plan = queues.plan(query_budget=120, swap_budget=0)
    assert plan.queries == (ResumeChunk("old", 100), ResumeChunk("new", 20))
    assert queues.arrivals["old"][0] == 0.


def test_query_and_swap_budgets_have_independent_fcfs_queues():
    queues = ResumeQueues()
    queues.enqueue("cold-old", arrived=0., query_tokens=3, swap_tokens=32)
    queues.enqueue("warm-new", arrived=1., query_tokens=10)
    queues.enqueue("cold-new", arrived=2., query_tokens=4, swap_tokens=16)
    plan = queues.plan(query_budget=6, swap_budget=40, page_tokens=16)
    assert plan.queries == (ResumeChunk("warm-new", 6),)
    assert plan.swaps == (ResumeChunk("cold-old", 32),)
    assert queues.pending["cold-old"].swap_tokens == 32
    with pytest.raises(ValueError):
        queues.acknowledge("cold-old", query_tokens=1)
    queues.acknowledge("cold-old", swap_tokens=16)
    assert queues.plan(query_budget=6, swap_budget=32, page_tokens=16).swaps == (
        ResumeChunk("cold-old", 16), ResumeChunk("cold-new", 16),
    )
    queues.acknowledge("cold-old", swap_tokens=16)
    assert queues.plan(query_budget=6, swap_budget=0).queries == (
        ResumeChunk("cold-old", 3), ResumeChunk("warm-new", 3),
    )


def test_partial_recompute_keeps_its_age_until_actual_work_is_acknowledged():
    queues = ResumeQueues()
    queues.enqueue("old", arrived=0., query_tokens=100)
    queues.enqueue("new", arrived=1., query_tokens=100)
    assert queues.plan(query_budget=20, swap_budget=0).queries == (ResumeChunk("old", 20),)
    queues.acknowledge("old", query_tokens=20)
    assert queues.pending["old"].query_tokens == 80
    assert queues.plan(query_budget=100, swap_budget=0).queries == (
        ResumeChunk("old", 80), ResumeChunk("new", 20),
    )


def test_retirement_resets_age_but_a_retracted_attempt_does_not():
    queues = ResumeQueues()
    queues.enqueue("agent", arrived=0., query_tokens=100)
    queues.discard_pending("agent")
    queues.enqueue("agent", arrived=1., query_tokens=100)
    assert queues.arrivals["agent"][0] == 0.
    queues.retire("agent")
    queues.enqueue("agent", arrived=2., query_tokens=100)
    assert queues.arrivals["agent"][0] == 2.


def test_duplicate_calls_and_invalid_acknowledgements_do_not_spend_budgets():
    queues = ResumeQueues()
    queues.enqueue("agent", arrived=0., query_tokens=10, swap_tokens=16)
    with pytest.raises(ValueError):
        queues.enqueue("agent", arrived=1., query_tokens=10)
    for kwargs in (
        {}, {"query_tokens": 1}, {"swap_tokens": 17},
        {"query_tokens": 1, "swap_tokens": 1},
    ):
        with pytest.raises(ValueError):
            queues.acknowledge("agent", **kwargs)
    assert queues.pending["agent"].query_tokens == 10
    assert queues.pending["agent"].swap_tokens == 16


def test_misaligned_swap_demands_are_rejected_before_any_queue_plan_is_returned():
    queues = ResumeQueues()
    queues.enqueue("old", arrived=0., query_tokens=1, swap_tokens=17)
    queues.enqueue("new", arrived=1., query_tokens=1, swap_tokens=16)
    with pytest.raises(ValueError, match="granularity"):
        queues.plan(query_budget=0, swap_budget=16, page_tokens=16)
    assert queues.pending["old"].swap_tokens == 17
