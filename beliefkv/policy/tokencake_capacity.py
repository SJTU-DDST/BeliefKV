# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
"""TokenCake public shared/reserved consumption and generation commitments.

Physical allocation remains the native engine's responsibility. Callers supply
native free capacity and charge only live, uniquely owned physical extents.
"""

from collections import Counter
import math

from beliefkv.policy.tokencake import CapacityPlan


def take_runs(runs, count):
    """Split ordered capacity charges without expanding one entry per token."""
    if type(count) is not int or count < 0:
        raise ValueError("capacity charge split requires a nonnegative token count")
    head, tail = [], []
    remaining = count
    for owner, units in runs:
        if type(units) is not int or units <= 0 or owner is not None and type(owner) is not str:
            raise ValueError("invalid capacity charge run")
        selected = min(remaining, units)
        if selected:
            if head and head[-1][0] == owner:
                head[-1] = owner, head[-1][1] + selected
            else:
                head.append((owner, selected))
        if selected < units:
            if tail and tail[-1][0] == owner:
                tail[-1] = owner, tail[-1][1] + units - selected
            else:
                tail.append((owner, units - selected))
        remaining -= selected
    if remaining:
        raise ValueError("capacity charge split exceeds physical extent")
    return tuple(head), tuple(tail)


class TokenCakeCapacityWindow:
    def __init__(self, total, *, priority_borrow_score_margin=500):
        if (
            type(total) is not int or total <= 0
            or type(priority_borrow_score_margin) is not int or priority_borrow_score_margin < 0
        ):
            raise ValueError("TokenCake requires positive native capacity and a valid score margin")
        self.total = total
        self.priority_borrow_score_margin = priority_borrow_score_margin
        self.plan = CapacityPlan(shared=total)
        self.used = Counter()
        self.growth = {}
        self.waiting_scores = {}
        self.free = total
        self.shared_available = total
        self.reserved_available = {}

    def set_plan(self, plan):
        if (
            type(plan.shared) is not int or plan.shared < 0
            or any(type(value) is not int or value < 0 for value in plan.reserved.values())
            or plan.shared + sum(plan.reserved.values()) != self.total
            or not plan.reserved.keys() <= plan.critical <= plan.scores.keys()
            or any(
                type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                for value in plan.scores.values()
            )
        ):
            raise ValueError("TokenCake partition does not conserve native capacity")
        self.plan = CapacityPlan(
            set(plan.critical), dict(plan.scores), dict(plan.reserved), plan.shared,
        )
        self.refresh(self.free)

    def set_waiting(self, scores):
        if any(type(key) is not str or type(value) is not int for key, value in scores.items()):
            raise ValueError("invalid waiting agent-type scores")
        self.waiting_scores = dict(scores)
        self.refresh(self.free)

    @property
    def uncommitted(self):
        return self.free - sum(self.growth.values())

    def refresh(self, physical_free):
        if type(physical_free) is not int or not 0 <= physical_free <= self.total:
            raise ValueError("invalid native free capacity")
        self.free = physical_free
        self.shared_available = max(0, self.plan.shared - self.used[None])
        self.reserved_available = {
            owner: max(0, units - self.used[owner])
            for owner, units in self.plan.reserved.items()
        }
        excess = max(
            0, self.shared_available + sum(self.reserved_available.values())
            - max(0, self.uncommitted),
        )
        reduction = min(excess, self.shared_available)
        self.shared_available -= reduction
        excess -= reduction
        for owner in sorted(self.reserved_available, key=lambda key: self.plan.scores[key]):
            reduction = min(excess, self.reserved_available[owner])
            self.reserved_available[owner] -= reduction
            excess -= reduction

    def consumption(self, agent_type, demand, *, score, borrow_reserved=False):
        """Exact public shared-first, own-reservation, then bounded borrow order."""
        if type(agent_type) is not str or type(demand) is not int or demand < 0 or type(score) is not int:
            raise ValueError("invalid TokenCake capacity consumption")
        shared = min(demand, self.shared_available)
        usage, remaining = [(None, shared)], demand - shared
        if not remaining:
            return tuple(usage)
        if agent_type in self.plan.critical:
            own = min(remaining, self.reserved_available.get(agent_type, 0))
            usage.append((agent_type, own))
            remaining -= own
            if not remaining:
                return tuple(usage)
        donors = sorted(
            (owner for owner in self.reserved_available if owner != agent_type),
            key=lambda owner: (self.reserved_available[owner], self.plan.scores[owner]),
            reverse=True,
        )
        for owner in donors:
            waiting_score = self.waiting_scores.get(owner)
            if (
                not borrow_reserved and owner in self.plan.critical
                and waiting_score is not None and (
                    not self.priority_borrow_score_margin
                    or score - waiting_score < self.priority_borrow_score_margin
                )
            ):
                continue
            borrowed = min(remaining, self.reserved_available[owner])
            usage.append((owner, borrowed))
            remaining -= borrowed
            if not remaining:
                return tuple(usage)
        return tuple(usage) if not remaining else None

    def can_admit(self, agent_type, demand, *, score, borrow_reserved=False):
        usage = self.consumption(agent_type, demand, score=score, borrow_reserved=borrow_reserved)
        return demand <= self.uncommitted and usage is not None

    def charge(self, agent_type, units, *, score, already_admitted=False, borrow_reserved=False):
        usage = self.consumption(agent_type, units, score=score, borrow_reserved=borrow_reserved)
        if usage is None:
            if not already_admitted:
                raise ValueError("native allocation exceeds TokenCake's admitted capacity")
            # The public controller charges accepted growth beyond a revised
            # partition to shared debt; later admissions wait for its release.
            usage = ((None, units),)
        usage = tuple((owner, count) for owner, count in usage if count)
        for owner, count in usage:
            self.used[owner] += count
            if owner is None:
                self.shared_available = max(0, self.shared_available - count)
            else:
                self.reserved_available[owner] -= count
        return usage

    def release(self, runs):
        released = Counter()
        for owner, count in runs:
            if type(count) is not int or count <= 0:
                raise ValueError("invalid released capacity charge")
            released[owner] += count
        if any(self.used[owner] < count for owner, count in released.items()):
            raise ValueError("TokenCake released more physical capacity than it owns")
        self.used.subtract(released)
        self.used += Counter()

    def set_growth(self, request_id, remaining):
        if type(request_id) is not str or not request_id or type(remaining) is not int or remaining < 0:
            raise ValueError("invalid observed generation commitment")
        self.growth[request_id] = remaining

    def forget_growth(self, request_id):
        self.growth.pop(request_id, None)
