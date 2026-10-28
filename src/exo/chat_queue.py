"""Bounded, volatile preloaded prompt FIFO for the line client (#233 slice)."""
from __future__ import annotations

from collections import deque

MAX_PROMPTS = 32
MAX_PROMPT_CHARS = 50_000


class PromptQueue:
    def __init__(self) -> None:
        self._items: deque[str] = deque()

    def __len__(self) -> int:
        return len(self._items)

    def add(self, text: str) -> None:
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_PROMPT_CHARS:
            raise ValueError(f"queue prompt must contain 1-{MAX_PROMPT_CHARS} characters")
        if text.lstrip().startswith("/"):
            raise ValueError("queue takes prompt text; run slash commands directly")
        if len(self) >= MAX_PROMPTS:
            raise ValueError(f"queue holds at most {MAX_PROMPTS} prompts")
        self._items.append(text.strip())

    def first(self) -> str:
        return self._items[0]

    def accepted(self) -> None:
        self._items.popleft()

    def clear(self) -> None:
        self._items.clear()

    def render(self) -> str:
        return "\n".join(f"{i}. {text}" for i, text in enumerate(self._items, 1)) if self else "queue is empty"
