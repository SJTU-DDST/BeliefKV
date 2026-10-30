"""Prefer supported long-tool owners when the existing working set must shrink."""

from beliefkv.policy.paper_baselines import ThunderActingDecayWorkingSet, _number


class PredictedActingPressureWorkingSet(ThunderActingDecayWorkingSet):
    def __init__(self, capacity_tokens, *, acting_preference, **kwargs):
        super().__init__(capacity_tokens, **kwargs)
        self.acting_preference = acting_preference
        self.last_pressure = []

    def tick(self, *, now):
        timestamp = _number(now, "decay control tick")
        self._resume_now = timestamp
        self.last_pressure = []
        preferences = {}
        try:
            resumed = self._resume()
            while self.remaining_capacity() < 0:
                acting = sorted(
                    (item for item in self.programs.values() if item.active and item.acting),
                    key=lambda item: item.tokens,
                )
                if acting:
                    evidence = None
                    selected = acting[0]
                    if len(acting) > 1:
                        for item in acting:
                            if item.identity not in preferences:
                                preferences[item.identity] = self.acting_preference(item, timestamp)
                            evidence = preferences[item.identity]
                            if evidence is not None:
                                selected = item
                                break
                    before = self.remaining_capacity()
                    self._pause(selected, timestamp)
                    self.last_pressure.append({
                        "selected": selected.identity,
                        "reference_selected": acting[0].identity,
                        "context_tokens": selected.tokens,
                        "acting_candidates": len(acting),
                        "remaining_before_tokens": before,
                        "remaining_after_tokens": self.remaining_capacity(),
                        "evidence": evidence,
                    })
                    continue
                reasoning = sorted(
                    (item for item in self.programs.values()
                     if item.active and not item.acting and not item.marked_for_pause),
                    key=lambda item: item.tokens,
                )
                if not reasoning:
                    break
                # As in the reference core, a mark does not release capacity.
                reasoning[0].marked_for_pause = True
            return resumed
        finally:
            self._resume_now = None
