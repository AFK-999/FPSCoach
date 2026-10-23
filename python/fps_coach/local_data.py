"""Clearing the calibration and recommendation history stored on this machine.

The app no longer records calibration results (the history feature was reverted in rev15).
A file left by rev14 can still hold them: <project folder>/.fpscoach_calibration_history.json.
Clearing writes an empty list to that file. The file is kept, so the change can be reverted
with git if it is tracked. Pure standard library, so it can be tested without Qt.
"""

from __future__ import annotations

import json
from pathlib import Path

CALIBRATION_HISTORY_NAME = ".fpscoach_calibration_history.json"


def count_calibration_records(path: str | Path | None) -> int:
    """Number of saved calibration records. A missing or unreadable file counts as zero."""
    if path is None:
        return 0
    path = Path(path)
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return len(data) if isinstance(data, list) else 0


def clear_calibration_history(path: str | Path | None) -> int:
    """Empty the saved calibration history. Returns how many records were removed.

    A missing file is left missing (nothing is created). An unreadable file is reset to an
    empty list, since the user asked for the history to be cleared.
    """
    if path is None:
        return 0
    path = Path(path)
    removed = count_calibration_records(path)
    if path.exists():
        path.write_text("[]\n", encoding="utf-8")
    return removed
