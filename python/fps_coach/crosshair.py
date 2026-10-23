"""Customizable crosshair generator.

Holds a validated :class:`CrosshairConfig` (shape, size, thickness, gap, dot,
color, outline, opacity) plus presets and (de)serialization. The UI renders it to
a live preview and can export a PNG; this module is the pure data/validation layer.

The optional on-screen overlay (ui/crosshair_overlay.py) is off by default and draws
the crosshair on the user's primary screen only. FPS Coach does not read, attach to,
or inject into any game process.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

SHAPES = ("cross", "dot", "cross_dot", "circle")


def _clamp(value, lo, hi):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


@dataclass(slots=True)
class CrosshairConfig:
    shape: str = "cross_dot"
    size: int = 12           # arm length (px)
    thickness: int = 2       # line thickness (px)
    gap: int = 4             # center gap (px)
    dot_size: int = 2        # center dot radius (px)
    color: str = "#70e1ba"
    outline: bool = True
    outline_color: str = "#06110d"
    opacity: float = 1.0     # 0..1

    def normalized(self) -> CrosshairConfig:
        return CrosshairConfig(
            shape=self.shape if self.shape in SHAPES else "cross_dot",
            size=int(_clamp(self.size, 0, 64)),
            thickness=int(_clamp(self.thickness, 1, 12)),
            gap=int(_clamp(self.gap, 0, 40)),
            dot_size=int(_clamp(self.dot_size, 0, 12)),
            color=_hex(self.color, "#70e1ba"),
            outline=bool(self.outline),
            outline_color=_hex(self.outline_color, "#06110d"),
            opacity=round(_clamp(self.opacity, 0.1, 1.0), 2),
        )

    def to_dict(self) -> dict:
        return asdict(self.normalized())

    @classmethod
    def from_dict(cls, data: dict) -> CrosshairConfig:
        if not isinstance(data, dict):
            return cls()
        base = cls()
        merged = {**asdict(base), **{k: data[k] for k in asdict(base) if k in data}}
        return cls(**merged).normalized()


def _hex(value, fallback: str) -> str:
    s = str(value or "").strip()
    if not s.startswith("#"):
        return fallback
    body = s[1:]
    if len(body) in (3, 6, 8) and all(c in "0123456789abcdefABCDEF" for c in body):
        return s
    return fallback


PRESETS: dict[str, CrosshairConfig] = {
    "classic_cross": CrosshairConfig(shape="cross", size=12, thickness=2, gap=4, dot_size=0),
    "dot": CrosshairConfig(shape="dot", dot_size=3),
    "cross_dot": CrosshairConfig(shape="cross_dot", size=10, thickness=2, gap=3, dot_size=2),
    "circle": CrosshairConfig(shape="circle", size=14, thickness=2, dot_size=2),
}


def preset_names() -> list[str]:
    return list(PRESETS.keys())
