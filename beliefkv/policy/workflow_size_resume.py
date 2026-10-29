"""Workflow work classes within the reference ThunderAgent resume categories."""

from __future__ import annotations

from collections import defaultdict
import time

from beliefkv.policy.paper_baselines import ThunderActingDecayWorkingSet


def resume_category(item):
    return 1 if item.steps == 1 else 0 if not item.acting else 2


class WorkflowSizeWorkingSet(ThunderActingDecayWorkingSet):
    """Change recovery ordering; inherit decay, pressure, and native authority."""

    def __init__(self, capacity_tokens, *, rank, sibling_group, **kwargs):
        super().__init__(capacity_tokens, **kwargs)
        self.rank = rank
        self.sibling_group = sibling_group
        self.last_selection = {}

    def _resume(self):
        if self._resume_now is None:
            raise ValueError("workflow resume requires the reference control timestamp")
        started, cpu = time.perf_counter(), time.thread_time()
        capacity = self.remaining_capacity_with_decay(now=self._resume_now)
        self.last_resume_capacity_tokens = capacity
        if capacity <= self.buffer_tokens:
            self.last_selection = {
                "capacity_tokens": capacity, "selected_tokens": 0, "ordered": (),
                "selected_in_priority_order": (),
                "thread_cpu_us": (time.thread_time() - cpu) * 1e6,
                "wall_us": (time.perf_counter() - started) * 1e6,
            }
            return ()
        paused = [
            item for item in self.programs.values()
            if not item.active and (self.resume_eligible is None or self.resume_eligible(item))
        ]
        ranks = {item.identity: self.rank(item.identity, self._resume_now) for item in paused}
        paused.sort(key=lambda item: (
            resume_category(item), ranks[item.identity], item.tokens, item.queue_sequence,
        ))
        # Anchor order remains the sorted reference order within each class.
        # Only genuinely ready members of one observed blocking JOIN are moved.
        groups, memberships = defaultdict(list), {}
        for item in paused:
            group = self.sibling_group(item.identity)
            if group is not None:
                key = resume_category(item), ranks[item.identity], group
                memberships[item.identity] = key
                groups[key].append(item)
        ordered, seen = [], set()
        for item in paused:
            if item.identity in seen:
                continue
            peers = groups.get(memberships.get(item.identity), (item,))
            for peer in peers:
                if peer.identity not in seen:
                    seen.add(peer.identity)
                    ordered.append(peer)
        selected, used = [], 0
        for item in ordered:
            required = item.tokens + self.buffer_tokens
            if used + required <= capacity:
                selected.append(item)
                used += required
        selection_order = tuple(item.identity for item in selected)
        # Keep the public one-backend placement convention and post-resume pressure.
        selected.sort(key=lambda item: -item.tokens)
        for item in selected:
            item.active = True
        self.last_selection = {
            "capacity_tokens": capacity, "selected_tokens": used,
            "ordered": tuple(
                (item.identity, resume_category(item), ranks[item.identity],
                 item.tokens, item.queue_sequence, memberships.get(item.identity))
                for item in ordered
            ),
            "selected_in_priority_order": selection_order,
            "thread_cpu_us": (time.thread_time() - cpu) * 1e6,
            "wall_us": (time.perf_counter() - started) * 1e6,
        }
        return tuple(item.identity for item in selected)
