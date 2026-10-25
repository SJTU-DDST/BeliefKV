"""CONCUR agent permits, with a separately declared observed-JOIN extension."""

from dataclasses import dataclass

from beliefkv.policy.paper_baselines import ConcurWindow


@dataclass
class ConcurAgent:
    identity: str
    order: int
    active: bool = False
    pending: bool = False
    in_flight: bool = False
    blocked: bool = False
    pause_at_boundary: bool = False
    queue_order: int = 0


class ConcurAdmission:
    def __init__(self, window: ConcurWindow):
        self.window = window
        self.agents: dict[str, ConcurAgent] = {}
        self._sequence = 0

    def _pause(self, item: ConcurAgent) -> None:
        self._sequence += 1
        item.active, item.pause_at_boundary = False, False
        item.queue_order = self._sequence

    def arrive(self, identity: str) -> None:
        if type(identity) is not str or not identity:
            raise ValueError("observed agent identity is required")
        item = self.agents.get(identity)
        if item is None:
            self._sequence += 1
            item = self.agents[identity] = ConcurAgent(
                identity, self._sequence, queue_order=self._sequence,
            )
        if item.pending:
            raise ValueError("a sequential CONCUR agent already has a pending call")
        item.pending, item.blocked = True, False
        self.rebalance()

    def begin(self, identity: str) -> None:
        item = self.agents[identity]
        if not item.active or not item.pending or item.blocked:
            raise ValueError("CONCUR cannot launch an agent without a live permit")
        item.in_flight = True

    def completed(self, identity: str) -> None:
        item = self.agents[identity]
        if not item.pending:
            raise ValueError("CONCUR agent completed without its pending call")
        item.pending, item.in_flight = False, False
        if item.pause_at_boundary or item.blocked:
            self._pause(item)
        self.rebalance()

    def set_blocked(self, identity: str, blocked: bool) -> bool:
        """Yield only logical capacity; native KV state and priority are untouched."""
        item = self.agents.get(identity)
        if item is None:
            return False
        yielded = blocked and item.active and not item.in_flight
        item.blocked = blocked
        if yielded:
            self._pause(item)
        self.rebalance()
        return yielded

    def rebalance(self) -> None:
        active = [item for item in self.agents.values() if item.active]
        for item in active:
            item.pause_at_boundary = False
        excess = max(0, len(active) - self.window.window)
        # The paper leaves victim ties unspecified. Preserve earlier admissions;
        # pause idle later arrivals first, then mark running calls for their boundary.
        victims = sorted(active, key=lambda item: (item.in_flight, -item.order))
        for item in victims[:excess]:
            if item.in_flight:
                item.pause_at_boundary = True
            else:
                self._pause(item)
        slots = max(0, self.window.window - sum(item.active for item in self.agents.values()))
        waiting = sorted(
            (item for item in self.agents.values()
             if not item.active and item.pending and not item.blocked),
            key=lambda item: item.queue_order,
        )
        for item in waiting[:slots]:
            item.active = True

    def forget(self, identity: str) -> None:
        self.agents.pop(identity, None)
        self.rebalance()
