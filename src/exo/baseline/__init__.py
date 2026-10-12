"""The house taste baseline (D-044, docs/TASTE-BASELINE.md).

- `pack`: the shipped, read-only, versioned house pack and its release check.
- `layers`: house, user and pins; per-item lookup with a source tag on every item; the prompt block.
- `privacy`: the export's privacy check and the stricter value-domain check.
- `export`: `exo taste export-house`.
- `review`: the principal's per-item tick and the final release.
- `cli`: `exo taste`.
"""
from .layers import precedence, resolve, taste_block, view
from .pack import HouseItem, HousePack, load_pack

__all__ = ["HouseItem", "HousePack", "load_pack", "precedence", "resolve", "taste_block", "view"]
