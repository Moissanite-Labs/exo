"""exo-memory package. See provider.py."""
from .provider import ExoMemoryProvider, PROVIDER_NAME

__all__ = ["ExoMemoryProvider", "PROVIDER_NAME", "register"]


def register(ctx) -> None:
    """Plugin entry point: register the Exo memory provider."""
    from .episodic.compact import router_summarizer
    from .config import DEFAULTS
    overrides = {key: value for key in DEFAULTS
                 if (value := ctx.get_explicit_config(key)) is not None}
    ctx.register_memory_provider(ExoMemoryProvider(overrides, summarizer=router_summarizer(lambda: ctx.router)))
    if hasattr(ctx, 'register_command'):
        from exo.dispatcher import register_root
        register_root(ctx)  # idempotent per plugin manager: one shared /exo for all Exo plugins
