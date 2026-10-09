from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from openai import AsyncStream, Stream
from openai.resources.chat.completions import AsyncCompletions, Completions


def _uses_decoded_stream(kwargs: dict[str, Any]) -> bool:
    extra_body = kwargs.get("extra_body")
    return (
        kwargs.get("stream") is True
        and "response_format" not in kwargs
        and isinstance(extra_body, Mapping)
        and extra_body.get("beliefkv_metadata") is not None
    )


class DecodedChatCompletions:
    """Keep SDK SSE handling without a typed-object/dict round trip."""

    def __init__(self, resource: Completions) -> None:
        self._resource = resource

    def create(self, **kwargs: Any) -> Any:
        if _uses_decoded_stream(kwargs):
            response = self._resource.with_raw_response.create(**kwargs)
            return response.parse(to=Stream[object])
        return self._resource.create(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resource, name)


class DecodedAsyncChatCompletions:
    """Async counterpart using the same SDK decoder and response lifecycle."""

    def __init__(self, resource: AsyncCompletions) -> None:
        self._resource = resource

    async def create(self, **kwargs: Any) -> Any:
        if _uses_decoded_stream(kwargs):
            response = await self._resource.with_raw_response.create(**kwargs)
            return response.parse(to=AsyncStream[object])
        return await self._resource.create(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resource, name)
