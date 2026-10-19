APP_STYLE = r"""
QMainWindow, QWidget#root {
    background: #0a0d12;
    color: #e8edf5;
    font-family: "Segoe UI";
    font-size: 14px;
}
QFrame#sidebar {
    background: #10151d;
    border-right: 1px solid #202a38;
}
QLabel#brand {
    color: #f7fafc;
    font-size: 22px;
    font-weight: 700;
    padding: 8px 4px 18px 4px;
}
QLabel#eyebrow {
    color: #75e6be;
    font-size: 11px;
    font-weight: 700;
}
QLabel#pageTitle {
    color: #f7fafc;
    font-size: 28px;
    font-weight: 700;
}
QLabel#pageSubtitle {
    color: #92a0b3;
    font-size: 14px;
}
QPushButton#navButton {
    background: transparent;
    color: #9eabba;
    border: 0;
    border-radius: 8px;
    text-align: left;
    padding: 11px 13px;
    font-weight: 600;
}
QPushButton#navButton:hover { background: #17202b; color: #ffffff; }
QPushButton#navButton:checked {
    background: #17352f;
    color: #8ff0cf;
}
QFrame#card {
    background: #111720;
    border: 1px solid #222d3b;
    border-radius: 12px;
}
QLabel#cardTitle { color: #f2f5fa; font-size: 16px; font-weight: 700; }
QLabel#muted { color: #8997aa; }
QLabel#metric { color: #8ff0cf; font-size: 24px; font-weight: 700; }
QLabel#statusGood {
    color: #8ff0cf;
    background: #16342e;
    border-radius: 9px;
    padding: 5px 10px;
}
QLabel#statusWarn {
    color: #ffcf70;
    background: #3a2e17;
    border-radius: 9px;
    padding: 5px 10px;
}
QPushButton#primary {
    background: #70e1ba;
    color: #07110e;
    border: 0;
    border-radius: 8px;
    padding: 10px 18px;
    font-weight: 700;
}
QPushButton#primary:hover { background: #8ff0cf; }
QPushButton#primary:disabled { background: #33433f; color: #89948f; }
QPushButton#secondary {
    background: #1a2330;
    color: #dbe3ec;
    border: 1px solid #2b3746;
    border-radius: 8px;
    padding: 9px 16px;
    font-weight: 600;
}
QPushButton#secondary:hover { background: #222e3d; }
QComboBox, QLineEdit, QPlainTextEdit, QTextBrowser, QDoubleSpinBox {
    background: #0d1219;
    color: #e6ebf2;
    border: 1px solid #2b3746;
    border-radius: 7px;
    padding: 8px 10px;
    min-height: 18px;
    selection-background-color: #285447;
}
QPlainTextEdit, QTextBrowser { padding: 10px; }
QComboBox:disabled, QLineEdit:disabled { color: #667385; background: #0b0f15; }
QComboBox QAbstractItemView { background: #111720; color: #e6ebf2; selection-background-color: #25483f; }
QFormLayout QLabel { color: #9aa8ba; }
QProgressBar {
    background: #0d1219;
    border: 1px solid #273240;
    border-radius: 6px;
    text-align: center;
    color: #e8edf5;
}
QProgressBar::chunk { background: #70e1ba; border-radius: 5px; }
QScrollArea { border: 0; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QToolTip { background: #1b2430; color: white; border: 1px solid #344256; padding: 6px; }
"""
