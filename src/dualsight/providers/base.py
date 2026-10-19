"""Provider protocol shared by all chat/vision adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from ..chat import ChatRequest, ChatResult, StreamChunk


class ChatProvider(Protocol):
    """The minimal surface DualSight needs from an LLM endpoint."""

    name: str
    model: str
    supports_tools: bool

    async def complete(self, request: ChatRequest) -> ChatResult: ...

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]: ...

    async def aclose(self) -> None: ...
