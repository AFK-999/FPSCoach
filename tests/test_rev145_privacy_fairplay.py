"""Privacy controls report failure; public disclosures describe real boundaries."""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from fps_coach.analytics import AnalyticsStore, SessionResult
from fps_coach.ui import pages_coach
from PySide6.QtWidgets import QApplication, QMessageBox

_KEEP = []


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_history_clear_returns_failure_when_file_cannot_be_written(tmp_path, monkeypatch):
    path = tmp_path / "training.json"
    result = SessionResult.create("flick", [100.0], 1, 0)
    store = AnalyticsStore(path=path, sessions=[result])
    assert store.save()
    original = path.read_text()
    monkeypatch.setattr(Path, "write_text", lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError()))
    assert store.clear() is False
    assert not store.sessions
    assert path.read_text() == original


def test_privacy_page_reports_training_clear_write_failure(app, tmp_path, monkeypatch):
    path = tmp_path / "training.json"
    path.write_text('[{"old": true}]')
    store = AnalyticsStore(path=path, sessions=[SessionResult.create("flick", [100.0], 1, 0)])
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(Path, "write_text", lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError()))
    page = pages_coach.PrivacyPage(store, None)
    _KEEP.append(page)
    page._clear()
    assert "could not clear the saved training file" in page.status.text()
    assert not store.sessions and json.loads(path.read_text()) == [{"old": True}]
    page.close()


def test_privacy_page_reports_calibration_clear_write_failure(app, tmp_path, monkeypatch):
    path = tmp_path / "calibration.json"
    path.write_text('[{"score": 1}]')
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(Path, "write_text", lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError()))
    page = pages_coach.PrivacyPage(None, None, calibration_path=path)
    _KEEP.append(page)
    cleared = []
    page.calibration_cleared.connect(lambda: cleared.append(True))
    page._clear_calibration()
    assert "could not clear the saved file" in page.calibration_status.text()
    assert json.loads(path.read_text()) == [{"score": 1}] and cleared
    page.close()


def test_privacy_page_explains_real_game_and_network_boundaries():
    text = " ".join(pages_coach.PRIVACY_POINTS)
    for expected in ("Look now", "remote endpoint", "loopback service", "can still forward",
                     "limited process-query", "bg_capture_", "activation is saved", "no anti-cheat"):
        assert expected.lower() in text.lower()
