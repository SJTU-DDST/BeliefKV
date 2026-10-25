"""Bounded native GPU-only measurements for CONCUR's congestion feedback."""

from collections import Counter
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ConcurSample:
    sample_id: int
    gpu_usage: float
    allocated_gpu_fraction: float
    gpu_hit_rate: float | None
    prompt_tokens: int
    gpu_hit_tokens: int
    observed_requests: int

    def to_dict(self):
        return asdict(self)


class ConcurFeedback:
    def __init__(self, cache):
        capacity = getattr(getattr(cache, "token_to_kv_pool_allocator", None), "size_full", None)
        if (
            getattr(cache, "tree_components", ()) != (0,)
            or type(capacity) is not int or capacity <= 0
        ):
            raise ValueError("CONCUR feedback requires measured FULL-only GPU capacity")
        self.cache, self.capacity = cache, capacity
        self.seen = set()
        self.prompt_tokens = self.gpu_hit_tokens = self.requests = 0
        self.counts = Counter()

    def observe_request(self, req) -> None:
        if req.rid in self.seen:
            return
        prompt = len(getattr(req, "origin_input_ids", ()))
        hits = getattr(req, "cached_tokens_device", None)
        if (
            getattr(req, "_cache_breakdown_computed", False) is not True
            or getattr(req, "retracted_stain", False)
            or type(hits) is not int or not 0 <= hits <= prompt or prompt <= 0
        ):
            self.counts["unavailable_request_prefix"] += 1
            return
        self.seen.add(req.rid)
        self.prompt_tokens += prompt
        self.gpu_hit_tokens += hits
        self.requests += 1
        self.counts["measured_request_prefix"] += 1

    def sample(self, sample_id: int) -> ConcurSample | None:
        if type(sample_id) is not int or sample_id < 0:
            raise ValueError("CONCUR feedback requires a nonnegative sample identity")
        try:
            free = self.cache.token_to_kv_pool_allocator.available_size()
            evictable = self.cache.evictable_size()
            if (
                type(free) is not int or type(evictable) is not int
                or min(free, evictable) < 0 or free + evictable > self.capacity
            ):
                raise ValueError("native GPU availability counters are inconsistent")
        except (AttributeError, TypeError, ValueError, RuntimeError):
            self.counts["unavailable_gpu_usage"] += 1
            return None
        result = ConcurSample(
            sample_id,
            (self.capacity - free - evictable) / self.capacity,
            (self.capacity - free) / self.capacity,
            self.gpu_hit_tokens / self.prompt_tokens if self.prompt_tokens else None,
            self.prompt_tokens, self.gpu_hit_tokens, self.requests,
        )
        self.prompt_tokens = self.gpu_hit_tokens = self.requests = 0
        self.counts["samples"] += 1
        return result

    def forget(self, request: str) -> None:
        self.seen.discard(request)
