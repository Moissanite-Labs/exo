"""Decision-model transport (TypeSafe Jev, and Laya's compatible local server).

Decision models return probabilities, not text (research/DECISION-MODELS.md). Verified from
https://docs.typesafe.ai/api.md: ``POST https://api.typesafe.ai/v1/systemone`` with
``Authorization: Bearer <key>`` and a body of ``state``, ``model`` and ``questions``; the reply
has ``answers`` (per question id), ``model`` and ``usage``. The per-question request fields
(choice, score, noul) are not in the pages checked, so ``decide`` is a stub that raises
NotImplementedError until the question builder lands. As a chat provider this transport
reports a clear, non-fatal error so the router moves on to the next route.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from exo.core.models.base import error_event
from exo.core.types import Message, ProviderEvent, ToolSpec

NOT_IMPLEMENTED = ("decision provider {name!r} is configured but its adapter is not implemented yet "
                   "(POST /systemone question shapes are unverified); falling back")


class DecisionProvider:
    """Stub adapter for /systemone-style decision APIs."""

    path = "/systemone"

    def __init__(self, base_url: str, api_key: str | None = None, *, name: str = "decision",
                 client: Any = None, headers: dict[str, str] | None = None, **_: Any) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.name = name
        self.extra_headers = dict(headers or {})
        self._client = client

    def headers(self) -> dict[str, str]:
        h = {"content-type": "application/json", **self.extra_headers}
        if self.api_key:
            h["authorization"] = f"Bearer {self.api_key}"
        return h

    async def decide(self, state: Any, questions: dict[str, dict], *, model: str) -> dict[str, Any]:
        raise NotImplementedError(NOT_IMPLEMENTED.format(name=self.name))

    async def stream(self, messages: list[Message], tools: list[ToolSpec],
                     **opts: Any) -> AsyncIterator[ProviderEvent]:
        yield error_event(NOT_IMPLEMENTED.format(name=self.name), None, retryable=True)
