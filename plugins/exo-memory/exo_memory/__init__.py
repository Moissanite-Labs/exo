"""exo-memory package. See provider.py."""
from .provider import ExoMemoryProvider, PROVIDER_NAME

__all__ = ["ExoMemoryProvider", "PROVIDER_NAME", "register"]


def register(ctx) -> None:
    """Hermes plugin entry point: register the Exo memory provider."""
    ctx.register_memory_provider(ExoMemoryProvider())
