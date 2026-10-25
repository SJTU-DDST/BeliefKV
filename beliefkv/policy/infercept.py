"""InferCept paper components; native transfer integration remains separate."""

from dataclasses import dataclass
from beliefkv.policy.paper_baselines import _integer, _number


@dataclass(frozen=True)
class IterationBudget:
    query_tokens: int
    swap_in_tokens: int
    swap_out_tokens: int


def iteration_budget(
    *, query_demand: int, swap_in_demand: int, swap_out_demand: int,
    swap_limit: int, free_gpu_tokens: int, free_host_tokens: int,
    page_tokens: int = 1,
) -> IterationBudget:
    """Solve section 4.1's token-capacity constraints in constant time.

    Query demand already excludes running queries and the saturation limit.
    Among solutions maximizing new queries, prefer more FCFS swap-in service,
    then more swap-out. The paper does not specify this tie break.

    Host credit from swap-in assumes move semantics. An adapter retaining Host
    copies must actually release them after ACK before spending this credit.
    This is a plan, not native memory or transfer authorization. The aggregate
    constraints alone do not prove that a chunk can start when both tiers are
    full; the adapter must validate real free space and completed moves.
    """
    query, incoming, outgoing, limit, gpu, host = (
        _integer(value, name) for value, name in (
            (query_demand, "query demand"), (swap_in_demand, "swap-in demand"),
            (swap_out_demand, "swap-out demand"), (swap_limit, "swap limit"),
            (free_gpu_tokens, "free GPU"), (free_host_tokens, "free Host"),
        )
    )
    page = _integer(page_tokens, "transfer page", minimum=1)
    incoming, outgoing, limit, host = (
        value // page for value in (incoming, outgoing, limit, host)
    )
    queries = min(query, gpu + min(outgoing, limit, host) * page)
    # Outgoing pages must cover incoming pages minus the remaining GPU slack.
    slack = (gpu - queries) // page
    swap_in = min(incoming, limit, outgoing + slack, (limit + slack) // 2)
    swap_out = min(outgoing, limit - swap_in, host + swap_in)
    return IterationBudget(queries, swap_in * page, swap_out * page)


def unknown_interception_seconds(*, started: float, now: float) -> float:
    """Section 4.4: estimate an unprofiled interception by observed elapsed time."""
    begin, end = _number(started, "interception start"), _number(now, "current time")
    if end < begin:
        raise ValueError("interception time cannot run backwards")
    return end - begin


@dataclass(frozen=True)
class ResumeChunk:
    identity: str
    tokens: int


@dataclass(frozen=True)
class ResumePlan:
    queries: tuple[ResumeChunk, ...]
    swaps: tuple[ResumeChunk, ...]


@dataclass
class _ResumeRequest:
    query_tokens: int
    swap_tokens: int


class ResumeQueues:
    """Section 4.3's distinct query/swap FCFS queues and original agent age.

    Planning does not consume tokens. The native adapter must acknowledge actual
    query work or completed swap chunks separately. A partly restored request
    remains in the swap queue and retains its original arrival.
    """

    def __init__(self):
        self.arrivals: dict[str, tuple[float, int]] = {}
        self.pending: dict[str, _ResumeRequest] = {}
        self.sequence = 0

    def enqueue(self, identity: str, *, arrived: float, query_tokens: int, swap_tokens: int = 0):
        if type(identity) is not str or not identity:
            raise ValueError("resumed request identity is required")
        arrival = _number(arrived, "request arrival")
        query = _integer(query_tokens, "query tokens", minimum=1)
        swap = _integer(swap_tokens, "swap tokens")
        if identity in self.pending:
            raise ValueError("an agent already has a pending request")
        if identity in self.arrivals and arrival < self.arrivals[identity][0]:
            raise ValueError("reentry precedes the original arrival")
        if identity not in self.arrivals:
            self.sequence += 1
            self.arrivals[identity] = arrival, self.sequence
        self.pending[identity] = _ResumeRequest(query, swap)

    def plan(self, *, query_budget: int, swap_budget: int, page_tokens: int = 1) -> ResumePlan:
        query = _integer(query_budget, "query budget")
        swap = _integer(swap_budget, "swap budget")
        page = _integer(page_tokens, "transfer page", minimum=1)
        queries, swaps = [], []
        for identity in sorted(self.pending, key=self.arrivals.__getitem__):
            request = self.pending[identity]
            if request.swap_tokens:
                if request.swap_tokens % page:
                    raise ValueError("swap demand does not match native page granularity")
                count = min(request.swap_tokens, swap)
                count -= count % page
                if count:
                    swaps.append(ResumeChunk(identity, count))
                    swap -= count
            elif query:
                count = min(request.query_tokens, query)
                queries.append(ResumeChunk(identity, count))
                query -= count
        return ResumePlan(tuple(queries), tuple(swaps))

    def acknowledge(self, identity: str, *, query_tokens: int = 0, swap_tokens: int = 0):
        query = _integer(query_tokens, "completed query tokens")
        swap = _integer(swap_tokens, "completed swap tokens")
        request = self.pending[identity]
        if not query and not swap or query and swap:
            raise ValueError("acknowledge one physical phase at a time")
        if swap:
            if swap > request.swap_tokens:
                raise ValueError("swap ACK exceeds pending restore")
            request.swap_tokens -= swap
        else:
            if request.swap_tokens or query > request.query_tokens:
                raise ValueError("query ACK has an unresolved restore or excess work")
            request.query_tokens -= query
            if not request.query_tokens:
                del self.pending[identity]

    def discard_pending(self, identity: str):
        """Drop one cancelled/retracted attempt without resetting its agent age."""
        self.pending.pop(identity, None)

    def retire(self, identity: str):
        self.discard_pending(identity)
        self.arrivals.pop(identity, None)
