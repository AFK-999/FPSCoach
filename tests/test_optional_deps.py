"""The app must start and fail gracefully when optional packages are missing.

Regression guard for rev76: ``screen_recorder`` evaluated ``np.ndarray`` in a
module-level type alias, so a machine without numpy crashed at import time with
``AttributeError: 'NoneType' object has no attribute 'ndarray'`` — before the
Capture page could disable its Recording card. Every test here runs in a
subprocess with a meta-path blocker, because the parent process already has the
packages imported.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "python"
TESTS_DIR = ROOT / "tests"


def run_blocked(script: str, blocked: str, timeout: float = 180.0) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["FPSCoach_BLOCK_MODULES"] = blocked
    env["PYTHONPATH"] = os.pathsep.join([str(PYTHON_DIR), str(TESTS_DIR)])
    env["QT_QPA_PLATFORM"] = "offscreen"
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        cwd=str(ROOT),
    )


ENGINE_SCRIPT = """
import json
import _blocked_deps  # installs the import blocker first
from fps_coach import screen_recorder

result = {
    "missing": screen_recorder.missing_dependencies(),
    "available": screen_recorder.dependencies_available(),
}
try:
    screen_recorder.ScreenRecorder(fps=5, analysis_interval=1.0).start()
except RuntimeError as exc:
    result["error"] = str(exc)
print(json.dumps(result))
"""


@pytest.mark.parametrize("blocked", ["numpy", "mss", "cv2", "numpy,mss,cv2"])
def test_engine_imports_without_optional_packages(blocked):
    proc = run_blocked(ENGINE_SCRIPT, blocked)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["available"] is False
    # missing_dependencies() names the package to install ("opencv-python-headless"
    # for cv2), so map the blocked import names onto those labels.
    labels = {"numpy": "numpy", "mss": "mss", "cv2": "opencv-python-headless"}
    for name in blocked.split(","):
        assert labels[name] in payload["missing"], payload
    # start() explains what to install instead of raising AttributeError.
    assert "screen recording" in payload["error"]
    assert 'pip install -e ".[recorder]"' in payload["error"]


UI_SCRIPT = """
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import _blocked_deps  # installs the import blocker first

from PySide6.QtWidgets import QApplication

app = QApplication([])

from fps_coach.ui import pages_coach, pages_setup


class _Backend:
    native_available = True

    def outputs(self):
        return [{"output_name": "Display 1", "adapter_name": "GPU", "width": 1920,
                 "height": 1080, "adapter_index": 0, "output_index": 0}]

    def capture_stats(self):
        return {}

    def latest_frame(self):
        return None

    def start_capture(self, *args):
        pass

    def stop_capture(self):
        pass

    def close(self):
        pass


page = pages_setup.CapturePage(_Backend())
settings = pages_coach.SettingsPage()
result = {
    "recording_status": page.record_status.text(),
    "record_enabled": page.record_start_btn.isEnabled(),
    "detail": page.record_detail.text(),
    "ingame_off": settings.ingame_enabled.isChecked() is False,
}
page.shutdown()
page.deleteLater()
settings.deleteLater()
app.processEvents()
print(json.dumps(result))
"""


def test_capture_page_degrades_without_recorder_packages():
    pytest.importorskip("PySide6")
    proc = run_blocked(UI_SCRIPT, "numpy,mss,cv2")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["recording_status"] == "Unavailable"
    assert payload["record_enabled"] is False
    assert "pip install -e" in payload["detail"]
    assert payload["ingame_off"] is True
