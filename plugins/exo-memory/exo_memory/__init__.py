"""exo-memory package. See provider.py."""
from .provider import ExoMemoryProvider, PROVIDER_NAME

__all__ = ["ExoMemoryProvider", "PROVIDER_NAME", "register"]


def register(ctx) -> None:
    """Plugin entry point: register the Exo memory provider."""
    ctx.register_memory_provider(ExoMemoryProvider())
    if hasattr(ctx, 'register_command'):
        from exo.ext.compat import ensure_root_command
        ensure_root_command(ctx)  # one shared /exo for all Exo plugins
