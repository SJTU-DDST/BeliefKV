"""Reuse exact Qwen token blocks separated by an indivisible chat boundary."""

from __future__ import annotations

from array import array
from collections import OrderedDict
import sys
from threading import RLock
from typing import Any


class ChatPromptTokenCache:
    separator = "<|im_start|>"

    def __init__(
        self, tokenizer: Any, *, max_bytes: int = 64 << 20, max_entries: int = 2048,
    ) -> None:
        self.tokenizer = tokenizer
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.cached_bytes = 0
        self.hits = self.misses = self.bypasses = 0
        self._cache: OrderedDict[str, array] = OrderedDict()
        self._lock = RLock()
        self.separator_id = self._safe_separator_id()

    def _safe_separator_id(self) -> int | None:
        try:
            backend = self.tokenizer.backend_tokenizer
            token_id = self.tokenizer.convert_tokens_to_ids(self.separator)
            token = backend.get_added_tokens_decoder()[token_id]
            processor = backend.post_processor
            if processor is not None:
                from tokenizers.processors import ByteLevel

                if not isinstance(processor, ByteLevel):
                    return None
            if (
                token.content != self.separator or not token.special or token.normalized
                or token.single_word or token.lstrip or token.rstrip
                or self.tokenizer.encode(
                    self.separator, add_special_tokens=False,
                ) != [token_id]
            ):
                return None
            return token_id
        except (AttributeError, ImportError, KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _size(text: str, tokens: array) -> int:
        return sys.getsizeof(text) + sys.getsizeof(tokens) + 256

    def encode(self, text: str, **kwargs: Any) -> list[int]:
        if self.separator_id is None or kwargs.keys() - {"add_special_tokens"}:
            self.bypasses += 1
            return self.tokenizer.encode(text, **kwargs)
        result: list[int] = []
        with self._lock:
            for index, block in enumerate(text.split(self.separator)):
                if index:
                    result.append(self.separator_id)
                if not block:
                    continue
                tokens = self._cache.get(block)
                if tokens is None:
                    self.misses += 1
                    # The recognized postprocessor never inserts token IDs.
                    tokens = array("I", self.tokenizer.encode(
                        block, add_special_tokens=False,
                    ))
                    size = self._size(block, tokens)
                    if size <= self.max_bytes and self.max_entries > 0:
                        while self._cache and (
                            self.cached_bytes + size > self.max_bytes
                            or len(self._cache) >= self.max_entries
                        ):
                            old_text, old_tokens = self._cache.popitem(last=False)
                            self.cached_bytes -= self._size(old_text, old_tokens)
                        self._cache[block] = tokens
                        self.cached_bytes += size
                else:
                    self.hits += 1
                    self._cache.move_to_end(block)
                result.extend(tokens)
        return result
