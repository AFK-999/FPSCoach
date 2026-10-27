"""Vantage visual system: palettes, density, and the application stylesheet.

Design direction
  * Graphite surfaces with one accent (chartreuse) used sparingly: active tab,
    primary action, the curve line, and live feedback.
  * Hairline borders and 10px radii instead of shadows or gradients.
  * Numbers use a monospaced face so values line up and read like instruments.
  * "Stage" surfaces (training range, calibration field, crosshair preview,
    capture preview) stay dark in both themes, so figures and crosshairs keep
    their contrast when the chrome is light.

Public API (kept stable for the app and tests):
  PALETTES, build_style(mode, theme), APP_STYLE
  plus set_active_theme()/active_palette() so painted widgets can follow the theme.
"""
from __future__ import annotations

from string import Template

PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#0E0F12",
        "surface": "#15171B",
        "surface2": "#1B1E23",
        "raised": "#21252B",
        "input": "#101216",
        "line": "#262A31",
        "line2": "#353B44",
        "text": "#EDEEF0",
        "text2": "#A3A9B3",
        "text3": "#6E7480",
        "accent": "#D7F249",
        "accent_hover": "#E3F876",
        "accent_ink": "#10130A",
        "accent_soft": "rgba(215, 242, 73, 0.14)",
        "amber": "#F0A94E",
        "good": "#8FDB9B",
        "warn": "#F0B155",
        "bad": "#F07A6B",
    },
    "light": {
        # Soft light gray, as requested for the light theme.
        "bg": "#E4E6E9",
        "surface": "#EFF1F3",
        "surface2": "#E7E9EC",
        "raised": "#F8F9FA",
        "input": "#FBFBFC",
        "line": "#D0D4DA",
        "line2": "#BAC0C8",
        "text": "#16181C",
        "text2": "#4D5560",
        "text3": "#848C97",
        "accent": "#5B7C08",
        "accent_hover": "#4B6905",
        "accent_ink": "#FFFFFF",
        "accent_soft": "rgba(91, 124, 8, 0.14)",
        "amber": "#B06A16",
        "good": "#2A7A46",
        "warn": "#9A5F00",
        "bad": "#B2392A",
    },
}

# Always-dark surfaces that display figures, crosshairs, and captured frames.
STAGE: dict[str, str] = {
    "stage_bg": "#0A0B0D",
    "stage_line": "#1D2127",
    "stage_grid": "#15181C",
    "stage_text": "#8C939E",
    "stage_accent": "#D7F249",
    "stage_hit": "#F4FFC2",
}

_active_theme = "dark"


def set_active_theme(theme: str) -> None:
    """Record which palette painted widgets should use. Call alongside setStyleSheet."""
    global _active_theme
    _active_theme = theme if theme in PALETTES else "dark"


def active_palette() -> dict[str, str]:
    return PALETTES[_active_theme]


def fx_enabled(mode: str) -> bool:
    """Kill effects and motion are off in performance mode."""
    return mode != "performance"


_BASE = Template("""
QWidget {
    background: transparent;
    color: $text;
    font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
    font-size: 13px;
    selection-background-color: $accent;
    selection-color: $accent_ink;
}
QMainWindow, QWidget#shell { background: $bg; }
QWidget#pageContent { background: transparent; }
QToolTip {
    background: $surface2; color: $text; border: 1px solid $line2;
    padding: 5px 7px; border-radius: 5px;
}

/* ---- top navigation ---------------------------------------------------- */
QFrame#topbar { background: $surface; border-bottom: 1px solid $line; }
QLabel#brand { font-size: 12px; font-weight: 700; letter-spacing: 2px; color: $text; }
QScrollArea#navScroll { background: transparent; border: none; }
QScrollArea#navScroll QScrollBar:horizontal { background: transparent; height: 3px; margin: 0; }
QScrollArea#navScroll QScrollBar::handle:horizontal { background: $line2; border-radius: 1px; min-width: 40px; }
QPushButton#navTab {
    background: transparent; color: $text2; border: none;
    border-bottom: 2px solid transparent; border-radius: 0;
    padding: 0 12px; font-size: 13px; font-weight: 500;
}
QPushButton#navTab:hover { color: $text; }
QPushButton#navTab:checked { color: $text; border-bottom: 2px solid $accent; font-weight: 600; }
QToolButton#utility {
    background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 6px;
}
QToolButton#utility:hover { background: $raised; border-color: $line; }
QToolButton#utility:checked { background: $accent_soft; border-color: $accent_soft; }
QFrame#coachStrip { background: $surface2; border-bottom: 1px solid $line; }
QLabel#coachText { color: $text2; font-size: 12px; }

/* ---- surfaces ---------------------------------------------------------- */
QFrame#card {
    background: $surface; border: 1px solid $line; border-radius: 10px;
}
QFrame#tile {
    background: $surface; border: 1px solid $line; border-radius: 10px;
}
QFrame#tile:hover { border-color: $line2; }
QFrame#advancedSection {
    background: transparent; border: none; border-top: 1px solid $line; border-radius: 0;
}
QFrame#stage {
    background: $stage_bg; border: 1px solid $line; border-radius: 10px;
}
QFrame#divider { background: $line; border: none; min-height: 1px; max-height: 1px; }

/* ---- type -------------------------------------------------------------- */
QLabel#eyebrow { color: $accent; font-size: 11px; font-weight: 600; letter-spacing: 1.6px; }
QLabel#pageTitle { font-size: 26px; font-weight: 600; color: $text; }
QLabel#pageSubtitle { color: $text2; font-size: 13px; }
QLabel#cardTitle { font-size: 14px; font-weight: 600; color: $text; }
QLabel#cardSubtitle { color: $text2; font-size: 12px; }
QLabel#formLabel { color: $text2; font-size: 12px; }
QLabel#metric {
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 26px; font-weight: 600; color: $text;
}
QLabel#metricSmall {
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 15px; font-weight: 600; color: $text;
}
QLabel#muted { color: $text3; }
QLabel#statusLabel { color: $text2; font-size: 12px; }
QLabel#statusLabel[state="good"] { color: $good; }
QLabel#statusLabel[state="warn"] { color: $warn; }
QLabel#statusLabel[state="bad"] { color: $bad; }
QLabel#pill {
    background: $surface2; border: 1px solid $line; border-radius: 11px;
    padding: 3px 10px; color: $text2; font-size: 12px;
}
QLabel#coreDot { border-radius: 5px; background: $warn; }
QLabel#coreDot[state="good"] { background: $good; }
QLabel#coreDot[state="warn"] { background: $warn; }
QLabel#sliderValue {
    color: $text; font-size: 12px; font-family: "Cascadia Mono", "Consolas", monospace;
}
QLabel#pill[state="good"] { color: $good; border-color: $line2; }
QLabel#pill[state="warn"] { color: $warn; border-color: $line2; }
QLabel#stepBadge {
    background: $accent_soft; color: $accent; border-radius: 13px;
    font-family: "Cascadia Mono", "Consolas", monospace; font-weight: 600;
}
QLabel#stageText { color: $stage_text; font-size: 13px; }
QLabel#tileValue {
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 20px; font-weight: 600; color: $text;
}

/* ---- buttons ----------------------------------------------------------- */
QPushButton {
    background: $raised; color: $text; border: 1px solid $line; border-radius: 6px;
    padding: 7px 14px; font-weight: 500; min-height: 18px;
}
QPushButton:hover { border-color: $text3; }
QPushButton:pressed { background: $surface2; }
QPushButton:disabled { color: $text3; background: $surface2; border-color: $line; }
QPushButton#primary {
    background: $accent; color: $accent_ink; border: 1px solid $accent; font-weight: 600;
}
QPushButton#primary:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton#primary:disabled { background: $surface2; color: $text3; border-color: $line; }
QPushButton#activation {
    background: $surface2; color: $text2; border: 1px solid $line; border-radius: 8px;
    padding: 10px 14px; font-size: 13px; font-weight: 600; text-align: left;
}
QPushButton#activation:hover { border-color: $line2; color: $text; }
QPushButton#activation:checked { background: $accent_soft; color: $accent; border-color: $accent; }
QPushButton#secondary { background: $raised; }
QPushButton#chip {
    background: transparent; color: $text2; border: 1px solid $line;
    border-radius: 13px; padding: 4px 11px; font-size: 12px;
}
QPushButton#chip:hover { color: $text; border-color: $text3; }
QPushButton#segment {
    background: transparent; color: $text2; border: 1px solid transparent;
    border-radius: 6px; padding: 6px 14px; font-weight: 500;
}
QPushButton#segment:hover { color: $text; }
QPushButton#segment:checked { background: $raised; color: $text; border-color: $line2; }
QToolButton#iconButton {
    background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 5px;
}
QToolButton#iconButton:hover { background: $raised; border-color: $line; }
QToolButton#iconButton:checked { background: $accent_soft; border-color: $accent_soft; }
QToolButton#iconButton:disabled { background: transparent; }

/* ---- inputs ------------------------------------------------------------ */
QLineEdit, QComboBox {
    background: $input; border: 1px solid $line; border-radius: 6px;
    padding: 6px 9px; min-height: 18px; color: $text;
}
QLineEdit:focus, QComboBox:focus { border-color: $accent; }
QLineEdit:disabled, QComboBox:disabled { color: $text3; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: $surface2; border: 1px solid $line2; padding: 4px; outline: 0;
    selection-background-color: $accent_soft; selection-color: $text;
}
QTextEdit#transcript {
    background: $input; border: 1px solid $line; border-radius: 8px; padding: 12px; color: $text;
}
QListWidget#sessionList {
    background: $input; border: 1px solid $line; border-radius: 8px; padding: 4px; outline: 0;
}
QListWidget#sessionList::item { padding: 9px 8px; border-bottom: 1px solid $line; }
QListWidget#sessionList::item:selected { background: transparent; color: $text; }

QCheckBox { spacing: 9px; color: $text; }
QCheckBox::indicator {
    width: 16px; height: 16px; border: 1px solid $line2; border-radius: 4px; background: $input;
}
QCheckBox::indicator:hover { border-color: $text3; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }

QSlider::groove:horizontal { height: 4px; background: $line2; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 2px; }
QSlider::handle:horizontal {
    background: $text; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}

QProgressBar {
    background: $surface2; border: 1px solid $line; border-radius: 9px;
    min-height: 16px; max-height: 16px; color: $text2; font-size: 11px;
}
QProgressBar::chunk { background: $accent; border-radius: 8px; }

/* ---- scrolling --------------------------------------------------------- */
QScrollArea#pageScroll { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $line2; border-radius: 4px; min-height: 36px; }
QScrollBar::handle:vertical:hover { background: $text3; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }
QScrollBar::handle:horizontal { background: $line2; border-radius: 3px; min-width: 36px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
""")


def build_style(mode: str, theme: str) -> str:
    """Render the stylesheet for a palette name.

    ``mode`` is accepted so callers keep one signature. Visual mode changes
    motion, not colors, so it is applied through ``fx_enabled``. Unknown themes
    fall back to dark.
    """
    name = theme if theme in PALETTES else "dark"
    return _BASE.substitute({**PALETTES[name], **STAGE})


APP_STYLE = build_style("luxurious", "dark")
