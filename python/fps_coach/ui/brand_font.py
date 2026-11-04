"""Load the bundled Inter font for the wordmark, with a safe system fallback."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFontDatabase

_font_loaded = False


def load_brand_font() -> bool:
    """Register Inter once after QApplication exists; missing font is non-fatal."""
    global _font_loaded
    if _font_loaded:
        return True
    path = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "Inter-Variable.ttf"
    if not path.is_file():
        return False
    font_id = QFontDatabase.addApplicationFont(str(path))
    if font_id < 0:
        return False
    _font_loaded = "Inter" in QFontDatabase.applicationFontFamilies(font_id)
    return _font_loaded
