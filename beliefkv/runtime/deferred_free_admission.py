"""Coalesce free-budget notifications at the next native scheduling boundary."""


class DeferredFreeAdmission:
    def __init__(self, admission):
        self.admission = admission
        self.dirty = False
        self.next_dispatch = 0.

    def defer(self, preferred):
        if preferred is not None:
            self.admission._remember_dispatch(preferred)
        self.dirty = True
        return False

    def dispatch(self, now):
        if not self.dirty and now < self.next_dispatch:
            return
        self.dirty = False
        self.next_dispatch = now + self.admission.dispatch_interval_ms / 1000.
        self.admission._dispatch_free_capacity()

    def siblings_pending(self, join_id):
        a = self.admission
        join = a._siblings.join(join_id) if a._siblings is not None else None
        if join is None:
            return
        for identity in join.member_invocation_ids - join.completed_member_ids:
            if a._ready_sibling(identity, join) is not None:
                a._remember_dispatch(a.latest[identity])
        self.dirty = True
