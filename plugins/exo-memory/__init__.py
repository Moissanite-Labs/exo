"""exo-memory: Hermes memory-provider plugin for Exo.

Drop this directory into ``$HERMES_HOME/plugins/exo-memory/`` (the Exo installer does it),
install the ``exo`` package into the Hermes environment, and set ``memory.provider: exo-memory``.
Uses the MemoryProvider contract only (agent/memory_provider.py); no Hermes core edits.
"""
if __package__:
    from .exo_memory import ExoMemoryProvider, PROVIDER_NAME, register
else:  # imported as a plain module (pytest collects this directory as a package)
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from exo_memory import ExoMemoryProvider, PROVIDER_NAME, register

__all__ = ["ExoMemoryProvider", "PROVIDER_NAME", "register"]
__version__ = "0.1.0"
