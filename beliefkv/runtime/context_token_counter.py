"""Cached request token counts using the serving model's local chat template."""

from __future__ import annotations

from collections import OrderedDict
from functools import lru_cache, partial
import json
from threading import RLock
from typing import Any

from langchain_core.messages import BaseMessage, convert_to_openai_messages
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.utils.function_calling import convert_to_openai_tool


class ChatTemplateTokenCounter:
    def __init__(self, model_path: str, *, cache_chars: int = 16_000_000) -> None:
        from transformers import AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(
            model_path, local_files_only=True,
        )
        self.separator = "<|im_start|>"
        if len(self.tokenizer.encode(self.separator, add_special_tokens=False)) != 1:
            raise ValueError("cached context counting requires the Qwen chat boundary")
        self.cache_chars = cache_chars
        self._chars = 0
        self._cache: OrderedDict[str, int] = OrderedDict()
        self._lock = RLock()

    def rendered_count(self, rendered: str) -> int:
        # An indivisible added token separates messages. BPE cannot merge
        # across it, so unchanged source and history blocks need no retokenizing.
        blocks = rendered.split(self.separator)
        total = len(blocks) - 1
        with self._lock:
            for block in blocks:
                if not block:
                    continue
                cached = self._cache.get(block)
                if cached is None:
                    cached = len(self.tokenizer.encode(
                        block, add_special_tokens=False,
                    ))
                    if len(block) <= self.cache_chars:
                        while self._cache and self._chars + len(block) > self.cache_chars:
                            evicted, _ = self._cache.popitem(last=False)
                            self._chars -= len(evicted)
                        self._cache[block] = cached
                        self._chars += len(block)
                else:
                    self._cache.move_to_end(block)
                total += cached
        return total

    def __call__(
        self, messages: list[BaseMessage], *, tools: list[Any] | None = None,
    ) -> int:
        if not messages:
            if not tools:
                return 0
            wire = [{"role": "system", "content": ""}]
        else:
            wire = convert_to_openai_messages(messages)
            for message in wire:
                if message.get("tool_calls"):
                    message["tool_calls"] = [
                        {
                            **call, "function": {
                                **call["function"],
                                "arguments": (
                                    json.loads(call["function"]["arguments"])
                                    if isinstance(call["function"].get("arguments"), str)
                                    else call["function"].get("arguments", {})
                                ),
                            },
                        }
                        for call in message["tool_calls"]
                    ]
        rendered = self.tokenizer.apply_chat_template(
            wire,
            tools=[convert_to_openai_tool(tool) for tool in tools] if tools else None,
            tokenize=False, add_generation_prompt=True, enable_thinking=True,
        )
        return self.rendered_count(rendered)


@lru_cache(maxsize=2)
def context_token_counter(model_path: str | None = None):
    if model_path:
        return ChatTemplateTokenCounter(model_path)
    return partial(count_tokens_approximately, chars_per_token=3.0)
