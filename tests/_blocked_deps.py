"""Import blocker for the optional-dependency tests.

Imported by a subprocess (see ``test_optional_deps.py``) before any ``fps_coach``
module, so it can make ``numpy`` / ``mss`` / ``cv2`` unimportable and prove that
the app still starts. The list comes from ``FPSCoach_BLOCK_MODULES``
(comma-separated); nothing is blocked when the variable is empty.
"""

from __future__ import annotations

import os
import sys

BLOCKED = frozenset(
    name.strip()
    for name in os.environ.get("FPSCoach_BLOCK_MODULES", "").split(",")
    if name.strip()
)


class _Blocker:
    """Make the named top-level modules fail to import, as if not installed."""

    def find_spec(self, name, path=None, target=None):  # noqa: ARG002 - import hook API
        if name.split(".")[0] in BLOCKED:
            raise ModuleNotFoundError(f"No module named {name!r} (blocked for test)")
        return None


if BLOCKED:
    # Drop anything already imported and refuse future imports.
    for module_name in list(sys.modules):
        if module_name.split(".")[0] in BLOCKED:
            del sys.modules[module_name]
    sys.meta_path.insert(0, _Blocker())
