"""Frozen-app entry point used by PyInstaller (see fpscoach.spec).

Kept separate from ``python -m fps_coach`` so the packaged build has a stable
script target. Delegates straight to the normal application entry.
"""

from fps_coach.app import main

if __name__ == "__main__":
    raise SystemExit(main())
