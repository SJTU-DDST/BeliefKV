"""Cached workflow work at reference ThunderAgent recovery boundaries."""

from __future__ import annotations

from collections import defaultdict
import time

from beliefkv.policy.paper_baselines import ThunderActingDecayWorkingSet


def resume_category(item):
    return 1 if item.steps == 1 else 0 if not item.acting else 2


class WorkflowSizeWorkingSet(ThunderActingDecayWorkingSet):
    """Change recovery ordering; inherit decay, pressure, and native authority."""

    def __init__(
        self, capacity_tokens, *, rank, sibling_group, observe_ready=None,
        select_set=None, defer_first=False, rank_before_category=False, **kwargs,
    ):
        super().__init__(capacity_tokens, **kwargs)
        self.rank = rank
        self.sibling_group = sibling_group
        self.observe_ready = observe_ready
        self.select_set = select_set
        self.defer_first = defer_first
        self.rank_before_category = rank_before_category
        self.last_selection = {}

    def arrive(self, identity, tokens, *, now):
        super().arrive(identity, tokens, now=now)
        item = self.programs[identity]
        if self.defer_first and item.steps == 1 and item.active:
            self._pause(item, now)

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
            and (
                not self.rank_before_category or self.select_set is not None
                or self.observe_ready is not None and self.observe_ready(item)
            )
        ]
        readiness = (
            {item.identity: bool(self.observe_ready(item)) for item in paused}
            if self.observe_ready is not None else None
        )
        ranks = {item.identity: self.rank(item.identity, self._resume_now) for item in paused}
        paused.sort(key=lambda item: (
            (resume_category(item), item.tokens, item.queue_sequence)
            if self.select_set is not None else
            (ranks[item.identity], resume_category(item), item.tokens, item.queue_sequence)
            if self.rank_before_category else
            (resume_category(item), ranks[item.identity], item.tokens, item.queue_sequence)
        ))
        # Only ready members of an observed blocking JOIN may become adjacent.
        groups, memberships = defaultdict(list), {}
        for item in paused:
            group = self.sibling_group(item.identity) if self.select_set is None else None
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
        if self.select_set is not None:
            chosen = self.select_set(ordered, selected, capacity)
            if (
                len({item.identity for item in chosen}) != len(chosen)
                or any(item not in ordered for item in chosen)
                or sum(item.tokens + self.buffer_tokens for item in chosen) > capacity
            ):
                raise ValueError("combination selector violated the reference logical budget")
            selected = list(chosen)
            used = sum(item.tokens + self.buffer_tokens for item in selected)
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
            "rank_before_category": self.rank_before_category and self.select_set is None,
            "thread_cpu_us": (time.thread_time() - cpu) * 1e6,
            "wall_us": (time.perf_counter() - started) * 1e6,
            **({"logical_ready_paused_before_resume": readiness} if readiness is not None else {}),
        }
        return tuple(item.identity for item in selected)
