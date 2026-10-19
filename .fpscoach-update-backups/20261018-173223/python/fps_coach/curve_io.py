"""Import/export adapters for Custom Curve 3 (.ccurve) and 4 (.cc4).

The application keeps a small neutral representation and never writes mouse or
game settings directly. Users explicitly import/export a profile file.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import uuid
from typing import Any, Iterable


class CurveFormatError(ValueError):
    """Raised when a curve file is malformed or incompatible."""


@dataclass(slots=True)
class CurvePoint:
    speed: float
    multiplier: float
    smoothness: float = 0.5
    corner: bool = False


@dataclass(slots=True)
class CurveProfile:
    name: str
    horizontal: list[CurvePoint]
    vertical: list[CurvePoint]
    description: str = ""
    source_format: str = "generated"
    use_mouse_dpi: bool = False
    mouse_dpi: int = 800
    # Unknown CC4 fields are retained when a CC4 profile is loaded and saved.
    cc4_document: dict[str, Any] = field(default_factory=dict, repr=False)


_FORMATS = {"ccurve", "cc4"}


def _finite(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise CurveFormatError(f"{field_name} must be numeric.") from error
    if not math.isfinite(number):
        raise CurveFormatError(f"{field_name} must be finite.")
    return number


def _validate_points(points: list[CurvePoint], axis: str) -> list[CurvePoint]:
    if not 2 <= len(points) <= 256:
        raise CurveFormatError(f"{axis} curve must contain between 2 and 256 points.")
    previous = -math.inf
    for point in points:
        if point.speed < 0 or point.multiplier < 0:
            raise CurveFormatError(f"{axis} curve values cannot be negative.")
        if point.speed < previous:
            raise CurveFormatError(f"{axis} curve speeds must be sorted.")
        point.smoothness = max(0.0, min(1.0, point.smoothness))
        previous = point.speed
    return points


def _format_number(value: float) -> str:
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text if text and text != "-0" else "0"


def _selected_format(path: str | Path, compatibility: str) -> str:
    requested = compatibility.lower().lstrip(".")
    if requested == "auto":
        requested = Path(path).suffix.lower().lstrip(".")
    if requested not in _FORMATS:
        raise CurveFormatError("Choose Custom Curve 3 (.ccurve) or Custom Curve 4 (.cc4).")
    return requested


def _legacy_points(x_line: str, y_line: str, corner_line: str, axis: str) -> list[CurvePoint]:
    xs = [_finite(value, f"{axis} speed") for value in x_line.split()]
    ys = [_finite(value, f"{axis} multiplier") for value in y_line.split()]
    corners = corner_line.split()
    if len(xs) != len(ys) or len(xs) != len(corners):
        raise CurveFormatError(f"{axis} .ccurve rows contain different point counts.")
    points = [
        CurvePoint(x, y, 0.5, marker.lower() not in {"0", "false", "no"})
        for x, y, marker in zip(xs, ys, corners)
    ]
    return _validate_points(points, axis)


def _load_ccurve(path: Path) -> CurveProfile:
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    except UnicodeError as error:
        raise CurveFormatError("The .ccurve file is not valid UTF-8 text.") from error
    if len(lines) < 3:
        raise CurveFormatError("A .ccurve file needs at least three rows.")
    horizontal = _legacy_points(lines[0], lines[1], lines[2], "Horizontal")
    vertical = (
        _legacy_points(lines[3], lines[4], lines[5], "Vertical")
        if len(lines) >= 6
        else deepcopy(horizontal)
    )
    return CurveProfile(
        name=path.stem,
        description="Imported legacy Custom Curve profile",
        horizontal=horizontal,
        vertical=vertical,
        source_format="ccurve",
    )


def _cc4_points(values: Any, axis: str) -> list[CurvePoint]:
    if not isinstance(values, list):
        raise CurveFormatError(f"{axis} CC4 Points must be a list.")
    points: list[CurvePoint] = []
    for index, encoded in enumerate(values):
        if not isinstance(encoded, str):
            raise CurveFormatError(f"{axis} point {index + 1} must be a pipe-delimited string.")
        parts = [part.strip() for part in encoded.split("|")]
        if len(parts) < 2:
            raise CurveFormatError(f"{axis} point {index + 1} is incomplete.")
        smoothness = _finite(parts[2], f"{axis} smoothness") if len(parts) >= 3 else 0.5
        corner = any(part.lower() == "e" for part in parts[3:])
        points.append(
            CurvePoint(
                _finite(parts[0], f"{axis} speed"),
                _finite(parts[1], f"{axis} multiplier"),
                smoothness,
                corner,
            )
        )
    return _validate_points(points, axis)


def _load_cc4(path: Path) -> CurveProfile:
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise CurveFormatError("The .cc4 file is not valid JSON.") from error
    if not isinstance(document, dict):
        raise CurveFormatError("The .cc4 root must be a JSON object.")
    x_curve = document.get("XCurve")
    if not isinstance(x_curve, dict):
        raise CurveFormatError("The .cc4 file does not contain XCurve settings.")
    horizontal = _cc4_points(x_curve.get("Points"), "Horizontal")
    y_curve = document.get("YCurve")
    vertical = (
        _cc4_points(y_curve.get("Points"), "Vertical")
        if isinstance(y_curve, dict) and "Points" in y_curve
        else deepcopy(horizontal)
    )
    return CurveProfile(
        name=str(document.get("Name") or path.stem),
        description=str(document.get("Description") or ""),
        horizontal=horizontal,
        vertical=vertical,
        source_format="cc4",
        use_mouse_dpi=bool(document.get("UseMouseDpi", False)),
        mouse_dpi=max(1, int(document.get("MouseDpi", 800))),
        cc4_document=document,
    )


def load_curve(path: str | Path, compatibility: str = "auto") -> CurveProfile:
    """Load a curve, using the extension unless compatibility is explicit."""
    source = Path(path)
    selected = _selected_format(source, compatibility)
    if not source.is_file():
        raise CurveFormatError(f"Curve file not found: {source}")
    return _load_cc4(source) if selected == "cc4" else _load_ccurve(source)


def _legacy_rows(points: Iterable[CurvePoint]) -> list[str]:
    values = list(points)
    return [
        " ".join(_format_number(point.speed) for point in values) + " ",
        " ".join(_format_number(point.multiplier) for point in values) + " ",
        " ".join("1" if point.corner else "0" for point in values) + " ",
    ]


def _save_ccurve(profile: CurveProfile, path: Path) -> None:
    horizontal = _validate_points(deepcopy(profile.horizontal), "Horizontal")
    vertical = _validate_points(deepcopy(profile.vertical), "Vertical")
    rows = _legacy_rows(horizontal) + _legacy_rows(vertical)
    path.write_text("\r\n".join(rows) + "\r\n", encoding="utf-8", newline="")


def _encoded_points(points: list[CurvePoint]) -> list[str]:
    return [
        "|".join(
            [
                _format_number(point.speed),
                _format_number(point.multiplier),
                _format_number(point.smoothness),
                *(["e"] if point.corner else []),
            ]
        )
        for point in points
    ]


def _default_cc4_document(profile: CurveProfile) -> dict[str, Any]:
    return {
        "Id": str(uuid.uuid4()),
        "Name": profile.name or "FPS Coach Curve",
        "Description": profile.description or "Generated by FPS Coach",
        "EditingVerticalCurve": False,
        "XCurve": {
            "AllowNonZeroLeftPoint": False,
            "LeftSlopeMode": "Flat",
            "CustomLeftSlope": 0,
            "RightSlopeMode": "Flat",
            "CustomRightSlope": 0,
            "Points": [],
        },
        "RotationMode": "Both",
        "RotationHorizontal": 0,
        "RotationVertical": 0,
        "AngleSnappingMode": "Legacy",
        "AngleSnappingHorizontal": 0,
        "AngleSnappingVertical": 0,
        "InputSpeedMetric": "Euclidean",
        "InputSmoothingTimeMs": 0,
        "HandAccelScaleOn": "Acceleration",
        "HandAccelDetectionSmoothing": "Low",
        "HandAccelScale": 0,
        "HandAccelLimit": 2,
        "BiasMode": 100,
        "BiasCurve": {"Points": ["0|0|0.5", "1|0|0.5"]},
        "OutputScalingDimension": 0,
        "OutputScalingDimensionShape": 0,
        "OutputSmoothingTimeMs": 0,
        "InvertVertical": False,
        "MousePollingRate": "Auto",
        "UseMouseDpi": profile.use_mouse_dpi,
        "MouseDpi": profile.mouse_dpi,
        "NormalizeOutput": False,
        "IsNew": False,
    }


def _save_cc4(profile: CurveProfile, path: Path) -> None:
    horizontal = _validate_points(deepcopy(profile.horizontal), "Horizontal")
    vertical = _validate_points(deepcopy(profile.vertical), "Vertical")
    document = deepcopy(profile.cc4_document) if profile.cc4_document else _default_cc4_document(profile)
    document["Id"] = str(document.get("Id") or uuid.uuid4())
    document["Name"] = profile.name or "FPS Coach Curve"
    document["Description"] = profile.description or "Generated by FPS Coach"
    document["UseMouseDpi"] = profile.use_mouse_dpi
    document["MouseDpi"] = profile.mouse_dpi
    x_curve = document.setdefault("XCurve", {})
    x_curve.setdefault("AllowNonZeroLeftPoint", False)
    x_curve.setdefault("LeftSlopeMode", "Flat")
    x_curve.setdefault("CustomLeftSlope", 0)
    x_curve.setdefault("RightSlopeMode", "Flat")
    x_curve.setdefault("CustomRightSlope", 0)
    x_curve["Points"] = _encoded_points(horizontal)
    if vertical != horizontal:
        y_curve = document.setdefault("YCurve", deepcopy(x_curve))
        y_curve["Points"] = _encoded_points(vertical)
    elif "YCurve" in document:
        document["YCurve"]["Points"] = _encoded_points(vertical)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def save_curve(profile: CurveProfile, path: str | Path, compatibility: str = "auto") -> Path:
    """Save a profile in the explicitly selected or extension-derived format."""
    destination = Path(path)
    selected = _selected_format(destination, compatibility)
    expected_suffix = f".{selected}"
    if destination.suffix.lower() != expected_suffix:
        destination = destination.with_suffix(expected_suffix)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if selected == "cc4":
        _save_cc4(profile, destination)
    else:
        _save_ccurve(profile, destination)
    return destination


def recommendation_profile(
    recommendation: dict[str, Any], name: str = "FPS Coach Recommendation", max_speed: float = 100.0
) -> CurveProfile:
    """Convert normalized output-speed points to Custom Curve gain points."""
    raw_points = list(recommendation.get("points") or [])
    if len(raw_points) < 2:
        raise CurveFormatError("Complete calibration before exporting a recommendation.")
    sensitivity = _finite(recommendation.get("sensitivity_multiplier", 1.0), "Sensitivity")
    positive_gains = [
        sensitivity * _finite(y, "Output speed") / _finite(x, "Input speed")
        for x, y in raw_points
        if float(x) > 1e-9
    ]
    first_gain = positive_gains[0] if positive_gains else sensitivity
    points: list[CurvePoint] = []
    for x, y in raw_points:
        normalized_x = _finite(x, "Input speed")
        normalized_y = _finite(y, "Output speed")
        gain = first_gain if normalized_x <= 1e-9 else sensitivity * normalized_y / normalized_x
        points.append(
            CurvePoint(
                speed=max(0.0, normalized_x * max_speed),
                multiplier=max(0.05, min(20.0, gain)),
            )
        )
    points = _validate_points(points, "Recommended")
    return CurveProfile(
        name=name,
        description="Explainable curve recommendation generated locally by FPS Coach",
        horizontal=points,
        vertical=deepcopy(points),
    )


def chart_points(profile: CurveProfile) -> list[tuple[float, float]]:
    """Return profile geometry normalized to the existing 0..1 chart."""
    max_speed = max((point.speed for point in profile.horizontal), default=1.0) or 1.0
    max_gain = max((point.multiplier for point in profile.horizontal), default=1.0) or 1.0
    return [
        (point.speed / max_speed, point.multiplier / max_gain)
        for point in profile.horizontal
    ]
