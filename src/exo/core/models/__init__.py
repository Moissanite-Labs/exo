"""Model transports and routing (wave 1, owner W1-A).

- ``AnthropicProvider``: Anthropic Messages API (streaming, tools, prompt caching).
- ``OpenAIChatProvider``: OpenAI-compatible chat (OpenRouter, OpenAI, llama.cpp, Ollama, LM Studio, vLLM).
- ``FakeProvider``: scripted, for tests.
- ``Router``: role to route, fallback, cooldown, lone-credential reprobe; ``aux()`` for side calls.
"""

from exo.core.models.anthropic import AnthropicProvider
from exo.core.models.fake import FakeProvider
from exo.core.models.openai_chat import OpenAIChatProvider
from exo.core.models.router import ModelError, RoleProvider, Route, Router, aux, load_catalog

__all__ = [
    "AnthropicProvider",
    "FakeProvider",
    "ModelError",
    "OpenAIChatProvider",
    "RoleProvider",
    "Route",
    "Router",
    "aux",
    "load_catalog",
]
