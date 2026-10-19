"""Discovery of bundled 2D enemy sprite assets for the training canvas.

The sprites live under ``assets/enemies`` next to this package. The training
canvas loads them as pixmaps and scales them per target; if none are present it
falls back to a procedural vector enemy, so the feature never hard-depends on the
image files. Path discovery is kept here (pure) so it is unit-testable.
"""

from __future__ import annotations

from pathlib import Path

ASSET_DIR = Path(__file__).resolve().parent / "assets" / "enemies"


def sprite_files() -> list[Path]:
    """Return the bundled enemy sprite PNGs (sorted), or [] if none exist."""
    if not ASSET_DIR.is_dir():
        return []
    return sorted(p for p in ASSET_DIR.glob("*.png") if p.is_file())
