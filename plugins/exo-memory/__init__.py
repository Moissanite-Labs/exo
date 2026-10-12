"""exo-memory: memory-provider plugin for the Exo core.

The folder holds ``plugin.yaml`` and ``register(ctx)``; the Exo plugin loader imports it and the
provider fills the memory slot (``exo.core.slots.MemoryProvider``). Config: ``plugin_config`` in
``$EXO_HOME/config.json``, ``<exo_home>/exo-memory/config.json`` or ``EXO_PRINCIPAL`` / ``EXO_STORE``.
"""
if __package__:
    from .exo_memory import ExoMemoryProvider, PROVIDER_NAME, register
else:  # imported as a plain module (pytest collects this directory as a package)
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from exo_memory import ExoMemoryProvider, PROVIDER_NAME, register

__all__ = ["ExoMemoryProvider", "PROVIDER_NAME", "register"]
__version__ = "0.2.0"
