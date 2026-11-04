"""Read-only, explicitly self-reported setup review; no hardware or game detection."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPointF, QRect, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from fps_coach.aiming_motion import BY_KEY as MOTIONS
from fps_coach.calibration_wizard import GRIPS, PADS, PROBLEMS, clone_answers

from .styles import active_palette
from .widgets import _label


def _selected(mapping, key: str) -> str:
    if not key:
        return "Not provided"
    if key == "unsure":
        return "Not sure"
    return mapping[key][0] if key in mapping else "Not provided"


def _motion(key: str) -> str:
    if key == "unsure":
        return "Not sure"
    return MOTIONS[key].label if key in MOTIONS else "Not provided"


class SetupScene(QWidget):
    """Illustrative hand highlights reflect self-reports, not tracked anatomy."""

    # Full hand plus a substantial part of the forearm, framed top-down.
    ART_SOURCE = QRectF(55, 90, 265, 598)
    ART_DEST = QRectF(142, 94, 136, 307)
    SCENE_HEIGHT = 430.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.grip = self.motion = ""
        asset = Path(__file__).resolve().parents[1] / "assets" / "calibration_guide" / "review_hand.png"
        self.hand_art = QPixmap(str(asset))
        self.setMinimumSize(260, 310)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Illustrative top-down desk, mouse, articulated five-finger hand, wrist and forearm with selected area highlights")
        self.setToolTip("Illustrative regions, not tracked anatomy. The same hand image is used for every grip; shoulder and elbow are not shown.")
        self._glow_level = 0.65
        self._animation_enabled = False
        self._glow_animation = QVariantAnimation(self)
        self._glow_animation.setDuration(1800)
        self._glow_animation.setStartValue(0.35)
        self._glow_animation.setKeyValueAt(0.5, 1.0)
        self._glow_animation.setEndValue(0.35)
        self._glow_animation.setLoopCount(-1)
        self._glow_animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._glow_animation.valueChanged.connect(self._set_glow_level)
        self._update_accessibility()

    # The source is one generic top-down image, not distinct grip poses. These
    # masks are deliberately schematic and limited to visible hand/forearm areas.
    GRIP_REGIONS = {
        "palm": ("thumb", "index", "middle", "ring", "little", "palm"),
        "claw": ("thumb_tip", "index_upper", "middle_upper", "ring_upper", "little_upper", "palm_heel"),
        "fingertip": ("thumb_tip", "index_tip", "middle_tip", "ring_tip", "little_tip"),
    }
    MOTION_REGIONS = {
        "fingers": ("thumb", "index", "middle", "ring", "little"),
        "wrist": ("wrist",),
        "arm": ("forearm",),
        "shoulder_hybrid": ("forearm",),  # shoulder/elbow outside illustration
    }

    def highlighted_regions(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return self.GRIP_REGIONS.get(self.grip, ()), self.MOTION_REGIONS.get(self.motion, ())

    def _update_accessibility(self) -> None:
        grip = _selected(GRIPS, self.grip)
        motion = _motion(self.motion)
        note = " Shoulder and elbow are not shown; forearm is a schematic proxy." if self.motion == "shoulder_hybrid" else ""
        self.setAccessibleDescription(f"Reported grip: {grip}. Reported flick motion: {motion}. Gold highlights grip areas; teal highlights motion areas. Illustrative, not tracked.{note}")

    def set_answers(self, answers: dict) -> None:
        self.grip = answers["grip"]
        self.motion = answers["motion"]
        self._update_accessibility()
        self._sync_glow_animation()
        self.update()

    def _set_glow_level(self, value) -> None:
        self._glow_level = float(value)
        self.update()

    def set_animation_enabled(self, enabled: bool) -> None:
        self._animation_enabled = bool(enabled)
        self._sync_glow_animation()

    def _sync_glow_animation(self) -> None:
        groups = self.highlighted_regions()
        should_run = self._animation_enabled and self.isVisible() and any(groups)
        if should_run:
            if self._glow_animation.state() != self._glow_animation.State.Running:
                self._glow_animation.start()
        else:
            self._glow_animation.stop()
            self._glow_level = 0.65
            self.update()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._sync_glow_animation()

    def hideEvent(self, event):  # noqa: N802
        self._glow_animation.stop()
        super().hideEvent(event)

    @staticmethod
    def _part(path: QPainterPath, fraction: float) -> QPainterPath:
        """Clip a digit silhouette at a joint, instead of placing a circular marker."""
        bounds = path.boundingRect()
        clip = QPainterPath()
        clip.addRect(QRectF(bounds.left() - 1, bounds.top() - 1, bounds.width() + 2,
                            bounds.height() * fraction + 1))
        return path.intersected(clip)

    @staticmethod
    def _contour(start: tuple[float, float], curves: tuple[tuple[float, ...], ...]) -> QPainterPath:
        path = QPainterPath(QPointF(*start))
        for curve in curves:
            path.cubicTo(*curve)
        path.closeSubpath()
        return path

    @classmethod
    @lru_cache(maxsize=2)
    def _region_paths(cls, fallback: bool) -> dict[str, QPainterPath]:
        """Hand-fitted silhouettes in image or vector-fallback coordinates.

        These are illustrative masks on one fixed hand, not tracked anatomy or
        actual contact. Distinct digit paths follow the visible finger contours;
        the wrist and arm masks follow their tapered silhouettes.
        """
        if fallback:
            fingers = {}
            # The same curved outlines used to draw the fallback fingers.
            for name, x, tip, width, base, bend in (
                ("index", 187, 138, 14, 190, -1),
                ("middle", 203, 131, 15, 187, 0),
                ("ring", 220, 140, 14, 190, 1),
                ("little", 235, 154, 12, 197, 2),
            ):
                fingers[name] = cls._contour((x, base), (
                    (x - 1 + bend, base - 13, x - 1 + bend, tip + 13, x + 1 + bend, tip + 6),
                    (x + 2 + bend, tip - 2, x + width - 2 + bend, tip - 2, x + width + bend, tip + 6),
                    (x + width + 1 + bend, tip + 16, x + width, base - 12, x + width, base),
                    (x + width - 2, base + 6, x + 2, base + 6, x, base),
                ))
            thumb = cls._contour((188, 202), (
                (180, 200, 168, 197, 164, 189), (161, 183, 161, 174, 165, 171),
                (168, 168, 173, 171, 175, 176), (178, 182, 184, 184, 191, 184),
                (191, 189, 190, 196, 188, 202),
            ))
            palm = cls._contour((181, 183), (
                (183, 173, 196, 168, 211, 169), (231, 168, 244, 174, 248, 187),
                (251, 202, 245, 216, 239, 228), (230, 237, 202, 239, 190, 228),
                (183, 215, 178, 196, 181, 183),
            ))
            heel = cls._contour((190, 209), (
                (203, 212, 228, 211, 242, 207), (240, 217, 237, 226, 234, 230),
                (224, 235, 202, 235, 193, 228), (190, 223, 190, 215, 190, 209),
            ))
            wrist = cls._contour((191, 229), (
                (201, 234, 230, 234, 240, 229), (243, 237, 245, 244, 244, 251),
                (230, 255, 204, 254, 189, 251), (190, 244, 191, 236, 191, 229),
            ))
            forearm = cls._contour((190, 252), (
                (205, 255, 229, 257, 242, 252), (239, 274, 230, 293, 220, 310),
                (213, 323, 205, 337, 199, 345), (181, 345, 161, 345, 148, 345),
                (152, 328, 164, 313, 171, 301), (184, 280, 187, 264, 190, 252),
            ))
        else:
            fingers = {
                "index": cls._contour((134, 176), (
                    (134, 161, 140, 154, 149, 155), (159, 155, 165, 164, 166, 176),
                    (166, 197, 166, 218, 169, 237), (170, 255, 172, 279, 173, 297),
                    (175, 311, 172, 326, 164, 333), (157, 337, 144, 335, 137, 328),
                    (134, 311, 137, 292, 136, 277), (132, 258, 131, 241, 132, 220),
                    (133, 202, 134, 187, 134, 176),
                )),
                "middle": cls._contour((198, 159), (
                    (198, 143, 204, 137, 212, 139), (222, 139, 228, 145, 228, 158),
                    (228, 179, 227, 200, 229, 220), (230, 241, 227, 260, 226, 280),
                    (226, 302, 229, 318, 226, 331), (221, 339, 207, 340, 198, 332),
                    (192, 319, 195, 302, 195, 283), (194, 263, 197, 243, 195, 222),
                    (196, 202, 196, 181, 198, 159),
                )),
                "ring": cls._contour((244, 189), (
                    (244, 176, 249, 169, 256, 169), (266, 168, 272, 177, 273, 190),
                    (274, 210, 273, 231, 274, 249), (274, 270, 271, 289, 270, 308),
                    (270, 321, 266, 332, 258, 337), (250, 340, 239, 335, 237, 327),
                    (237, 307, 241, 291, 241, 273), (241, 255, 244, 238, 244, 219),
                    (243, 207, 244, 197, 244, 189),
                )),
                "little": cls._contour((277, 237), (
                    (276, 225, 279, 219, 284, 220), (289, 219, 293, 226, 295, 237),
                    (300, 249, 301, 265, 301, 280), (301, 295, 296, 308, 294, 320),
                    (292, 330, 286, 339, 279, 341), (271, 340, 268, 332, 270, 322),
                    (272, 310, 273, 295, 274, 280), (276, 265, 277, 249, 277, 237),
                )),
            }
            thumb = cls._contour((98, 253), (
                (105, 252, 111, 260, 113, 270), (116, 282, 114, 292, 115, 303),
                (116, 322, 121, 342, 124, 359), (126, 368, 120, 376, 111, 378),
                (101, 375, 94, 366, 91, 355), (88, 342, 88, 325, 83, 313),
                (80, 304, 82, 293, 87, 284), (92, 274, 93, 258, 98, 253),
            ))
            palm = cls._contour((132, 318), (
                (146, 322, 163, 319, 175, 323), (188, 327, 201, 320, 214, 322),
                (227, 321, 243, 321, 254, 324), (267, 329, 270, 341, 267, 357),
                (263, 381, 260, 414, 249, 440), (243, 451, 227, 458, 211, 459),
                (191, 462, 167, 455, 149, 446), (133, 430, 118, 400, 111, 375),
                (108, 356, 117, 332, 132, 318),
            ))
            heel = cls._contour((125, 384), (
                (151, 391, 177, 391, 199, 390), (220, 390, 242, 387, 258, 381),
                (256, 405, 251, 425, 245, 442), (239, 452, 224, 457, 208, 458),
                (189, 460, 166, 453, 150, 445), (138, 429, 130, 406, 125, 384),
            ))
            wrist = cls._contour((147, 448), (
                (162, 455, 185, 461, 205, 459), (222, 459, 238, 455, 248, 446),
                (247, 462, 252, 478, 254, 496), (255, 508, 255, 517, 254, 526),
                (231, 529, 173, 527, 151, 521), (150, 501, 150, 481, 147, 465),
                (146, 459, 146, 454, 147, 448),
            ))
            forearm = cls._contour((151, 524), (
                (177, 528, 226, 530, 254, 524), (254, 547, 256, 570, 260, 594),
                (264, 625, 271, 654, 274, 681), (239, 684, 183, 684, 151, 681),
                (152, 653, 153, 626, 152, 596), (152, 568, 151, 544, 151, 524),
            ))
        regions = {"thumb": thumb, "thumb_tip": cls._part(thumb, 0.42),
                   "palm": palm, "palm_heel": heel, "wrist": wrist, "forearm": forearm}
        for name, path in fingers.items():
            regions[name] = path
            regions[name + "_upper"] = cls._part(path, 0.66)
            regions[name + "_tip"] = cls._part(path, 0.35)
        return regions

    @staticmethod
    def _narrow(path: QPainterPath) -> QPainterPath:
        # When the finger motion overlaps a grip, keep gold visible around teal.
        center = path.boundingRect().center()
        transform = QTransform()
        transform.translate(center.x(), center.y())
        transform.scale(0.55, 0.94)
        transform.translate(-center.x(), -center.y())
        return transform.map(path)

    @classmethod
    @lru_cache(maxsize=64)
    def _combined_region_path(cls, fallback: bool, names: tuple[str, ...], narrow: bool) -> QPainterPath:
        """Merge adjoining masks so touching fingers/palm do not create glowing blobs at joins."""
        paths = cls._region_paths(fallback)
        combined = QPainterPath()
        for name in names:
            path = cls._narrow(paths[name]) if narrow else paths[name]
            combined = combined.united(path)
        return combined

    def _paint_regions(self, painter: QPainter, path: QPainterPath,
                       color: QColor, *, width_scale: float = 1.0) -> None:
        if path.isEmpty():
            return
        intensity = self._glow_level
        for width, alpha in ((16, 18), (9, 34), (4, 70)):
            glow = QColor(color)
            glow.setAlpha(round(alpha * (0.55 + intensity * 0.45)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(glow, width * width_scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(path)
        fill = QColor(color)
        fill.setAlpha(round(76 + intensity * 48))
        edge = QColor(color)
        edge.setAlpha(round(155 + intensity * 90))
        painter.setBrush(fill)
        painter.setPen(QPen(edge, 2.1 * width_scale))
        painter.drawPath(path)

    def _paint_highlights(self, painter: QPainter) -> None:
        grip, motion = self.highlighted_regions()
        if not grip and not motion:
            return
        fallback = self.hand_art.isNull()
        grip_path = self._combined_region_path(fallback, grip, False)
        motion_path = self._combined_region_path(fallback, motion, self.motion == "fingers")
        painter.save()
        if fallback:
            painter.translate(210, 194)
            painter.scale(1.3, 1.3)
            painter.translate(-210, -194)
        else:
            painter.translate(self.ART_DEST.left(), self.ART_DEST.top())
            painter.scale(self.ART_DEST.width() / self.ART_SOURCE.width(),
                          self.ART_DEST.height() / self.ART_SOURCE.height())
            painter.translate(-self.ART_SOURCE.left(), -self.ART_SOURCE.top())
        width_scale = 0.4 if fallback else 1.0
        self._paint_regions(painter, grip_path, QColor("#ffbe22"), width_scale=width_scale)
        self._paint_regions(painter, motion_path, QColor("#00e9e5"), width_scale=width_scale)
        painter.restore()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = min(self.width() / 420.0, self.height() / self.SCENE_HEIGHT)
        painter.translate((self.width() - 420 * scale) / 2, (self.height() - self.SCENE_HEIGHT * scale) / 2)
        painter.scale(scale, scale)
        dark = QColor(active_palette()["input"]).lightness() < 128
        border = QColor("#4b5960" if dark else "#778990")
        line = QColor("#82959a" if dark else "#5f7377")
        fill = QColor("#1c262b" if dark else "#d2dce0")
        painter.setPen(QPen(border, 1.5))
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(115, 6, 190, 76), 6, 6)
        painter.drawLine(QPointF(198, 82), QPointF(194, 94))
        painter.drawLine(QPointF(224, 82), QPointF(228, 94))
        painter.drawLine(QPointF(177, 95), QPointF(244, 95))
        painter.setPen(QPen(border, 1.2))
        painter.setBrush(QColor("#151d21" if dark else "#e6ecec"))
        pad = QPainterPath(QPointF(90, 113))
        pad.lineTo(333, 113)
        pad.lineTo(363, 256)
        pad.lineTo(57, 256)
        pad.closeSubpath()
        painter.drawPath(pad)
        if not self.hand_art.isNull():
            # A wider top-down framing retains the full available forearm rather
            # than clipping just below the wrist. It is not the user's anatomy.
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(self.ART_DEST, self.hand_art, self.ART_SOURCE)
        else:
            painter.save()
            painter.translate(210, 194)
            painter.scale(1.3, 1.3)
            painter.translate(-210, -194)
            # An illustrative top-down hand, with separate fingers, nails and joint
            # creases. It is not fitted to, or measured from, the user's body.
            arm = QPainterPath(QPointF(191, 218))
            arm.cubicTo(182, 245, 183, 278, 164, 310)
            arm.cubicTo(157, 321, 147, 333, 144, 349)
            arm.lineTo(207, 349)
            arm.cubicTo(224, 322, 240, 292, 247, 262)
            arm.cubicTo(250, 247, 244, 229, 239, 218)
            arm.closeSubpath()
            skin = QColor("#75868d" if dark else "#9daeb0")
            skin_light = QColor("#a5b4b7" if dark else "#c4d1d0")
            skin_shadow = QColor("#566970" if dark else "#7f9699")
            gradient = QLinearGradient(170, 0, 250, 0)
            gradient.setColorAt(0, skin_shadow)
            gradient.setColorAt(0.5, skin)
            gradient.setColorAt(1, skin_light)
            painter.setPen(QPen(border, 1.1))
            painter.setBrush(gradient)
            painter.drawPath(arm)
            # Mouse is visible beneath the separately articulated digits.
            painter.setBrush(QColor("#34444b" if dark else "#83989e"))
            painter.drawRoundedRect(QRectF(175, 130, 83, 116), 36, 36)
            painter.setPen(QPen(line, 0.8))
            painter.drawLine(QPointF(216, 133), QPointF(216, 168))
            painter.drawRoundedRect(QRectF(212, 143, 7, 14), 3, 3)
            # Thumb wraps the left edge; the palm is distinct from the wrist.
            painter.setPen(QPen(border, 1.1))
            painter.setBrush(gradient)
            thumb = QPainterPath(QPointF(188, 202))
            thumb.cubicTo(180, 200, 168, 197, 164, 189)
            thumb.cubicTo(161, 183, 161, 174, 165, 171)
            thumb.cubicTo(168, 168, 173, 171, 175, 176)
            thumb.cubicTo(178, 182, 184, 184, 191, 184)
            thumb.closeSubpath()
            painter.drawPath(thumb)
            palm = QPainterPath(QPointF(181, 183))
            palm.cubicTo(183, 173, 196, 168, 211, 169)
            palm.cubicTo(231, 168, 244, 174, 248, 187)
            palm.cubicTo(251, 202, 245, 216, 239, 228)
            palm.cubicTo(230, 237, 202, 239, 190, 228)
            palm.cubicTo(183, 215, 178, 196, 181, 183)
            palm.closeSubpath()
            painter.drawPath(palm)
            # Four separate fingers. Rounded tips, bends, joint creases and nails
            # make the segments distinguishable even in the small review diagram.
            for x, tip, width, base, bend in (
                (187, 138, 14, 190, -1),  # index
                (203, 131, 15, 187, 0),   # middle
                (220, 140, 14, 190, 1),   # ring
                (235, 154, 12, 197, 2),   # little
            ):
                finger = QPainterPath(QPointF(x, base))
                finger.cubicTo(x - 1 + bend, base - 13, x - 1 + bend, tip + 13, x + 1 + bend, tip + 6)
                finger.cubicTo(x + 2 + bend, tip - 2, x + width - 2 + bend, tip - 2, x + width + bend, tip + 6)
                finger.cubicTo(x + width + 1 + bend, tip + 16, x + width, base - 12, x + width, base)
                finger.cubicTo(x + width - 2, base + 6, x + 2, base + 6, x, base)
                painter.setBrush(gradient)
                painter.setPen(QPen(border, 0.9))
                painter.drawPath(finger)
                painter.setPen(QPen(skin_shadow, 0.8))
                for joint in (tip + 20, tip + 30):
                    painter.drawArc(QRectF(x + 2 + bend, joint, width - 4, 3.5), 10 * 16, 160 * 16)
                painter.setPen(QPen(skin_light, 0.8))
                painter.setBrush(QColor("#aab7b8" if dark else "#d0d9d6"))
                painter.drawRoundedRect(QRectF(x + 3 + bend, tip + 3, width - 6, 9), 3, 3)
            painter.setPen(QPen(skin_shadow, 0.8))
            painter.drawArc(QRectF(165, 178, 9, 5), 0, 180 * 16)
            painter.drawArc(QRectF(167, 182, 8, 5), 0, 180 * 16)
            painter.drawArc(QRectF(192, 207, 42, 12), 195 * 16, 150 * 16)
            painter.drawArc(QRectF(198, 214, 33, 8), 195 * 16, 150 * 16)
            painter.drawArc(QRectF(195, 225, 35, 7), 195 * 16, 150 * 16)
            painter.setPen(QPen(skin_light, 0.8))
            painter.drawLine(QPointF(192, 196), QPointF(195, 207))
            painter.drawLine(QPointF(207, 195), QPointF(209, 206))
            painter.drawLine(QPointF(225, 195), QPointF(224, 206))
            painter.setBrush(QColor("#aab7b8" if dark else "#d0d9d6"))
            painter.drawRoundedRect(QRectF(163, 172, 6, 9), 3, 3)
            painter.restore()
        # Color is applied directly to the illustrated hand/forearm. No leader
        # lines or point markers imply tracking or precise anatomical contact.
        self._paint_highlights(painter)
        painter.setPen(line)
        font = painter.font()
        font.setPixelSize(14)
        painter.setFont(font)
        painter.drawText(QRectF(45, 407, 330, 19), Qt.AlignmentFlag.AlignCenter, "Illustrative · not tracked")
        painter.end()


class SetupPreview(QFrame):
    """A complete review of entered answers with prominent grip and motion callouts."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("wizardSetup")
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(8)
        self.metrics = {}
        stats = QGridLayout()
        for index, label in enumerate(("Game", "Sensitivity", "DPI", "Usable mouse travel")):
            box = QVBoxLayout()
            box.setSpacing(3)
            box.addWidget(_label(label.upper(), "wizardOptionCaption"))
            value = _label("Not provided", "wizardOptionTitle", wrap=True)
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            box.addWidget(value)
            stats.addLayout(box, 0, index)
            self.metrics[label] = value
        root.addLayout(stats)
        row = QHBoxLayout()
        row.setSpacing(6)
        left = QVBoxLayout()
        right = QVBoxLayout()
        self.mouse_value = self._callout(left, "YOUR MOUSE", "Not provided")
        left.addStretch(1)
        self.pad_value = self._callout(left, "YOUR MOUSEPAD", "Not provided")
        left.addStretch(1)
        self.grip_value = self._callout(right, "YOUR GRIP", "Not provided", "#e4a52f")
        right.addStretch(1)
        self.motion_value = self._callout(right, "WHAT MOVES WHEN YOU FLICK?", "Not provided", "#2da7a2")
        self.shoulder_note = _label("Shoulder not shown; forearm highlight is a schematic proxy.", "cardSubtitle", wrap=True)
        self.motion_value.parentWidget().layout().addWidget(self.shoulder_note)
        self.shoulder_note.hide()
        right.addStretch(1)
        row.addLayout(left, 2)
        self.scene = SetupScene()
        row.addWidget(self.scene, 5)
        row.addLayout(right, 2)
        root.addLayout(row, 1)
        root.addWidget(_label("Gold: reported grip areas  |  Teal: reported flick-motion area", "cardSubtitle", wrap=True))
        root.addWidget(_label("HOW A CURVE CHANGE WORKS", "wizardOptionCaption"))
        self.flow_steps = _label(
            "Your answers  →  Optional local AI preview  →  Confirm in-app curve", "cardSubtitle", wrap=True
        )
        root.addWidget(self.flow_steps)
        self.flow_bar = QProgressBar()
        self.flow_bar.setRange(0, 100)
        self.flow_bar.setTextVisible(False)
        self.flow_bar.setFixedHeight(5)
        self.flow_bar.setStyleSheet(
            "QProgressBar { min-height: 5px; max-height: 5px; border-radius: 2px; } "
            "QProgressBar::chunk { border-radius: 2px; }"
        )
        self.flow_bar.setAccessibleName("Illustration of the optional preview and confirmation sequence")
        root.addWidget(self.flow_bar)
        self.flow_note = _label(
            "Illustrative, not tracked. An AI suggestion changes the in-app curve only if confirmed; game and device settings are not changed.",
            "cardSubtitle", wrap=True,
        )
        root.addWidget(self.flow_note)
        self._flow_animation = QVariantAnimation(self)
        self._flow_animation.setDuration(1500)
        self._flow_animation.setStartValue(0)
        self._flow_animation.setEndValue(100)
        self._flow_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._flow_animation.valueChanged.connect(self.flow_bar.setValue)
        self.problems = _label("", "cardSubtitle", wrap=True)
        self.problems.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.problems)
        root.addWidget(_label("Self-reported setup · same illustrative hand for every grip, not tracked or measured.", "cardSubtitle", wrap=True))

    def start_flow(self, animations_enabled: bool) -> None:
        self._flow_animation.stop()
        self.flow_bar.setValue(0 if animations_enabled else 100)
        if animations_enabled:
            self._flow_animation.start()
        self.scene.set_animation_enabled(animations_enabled)

    def hideEvent(self, event):  # noqa: N802
        self._flow_animation.stop()
        super().hideEvent(event)

    @staticmethod
    def _callout(layout: QVBoxLayout, title: str, value: str, accent: str = "") -> QLabel:
        frame = QFrame()
        frame.setObjectName("wizardSetup")
        frame.setProperty("previewCallout", True)
        if accent:
            frame.setStyleSheet(f"QFrame#wizardSetup {{ border-left: 3px solid {accent}; }}")
        box = QVBoxLayout(frame)
        box.setContentsMargins(10, 8, 10, 8)
        box.setSpacing(4)
        heading = _label(title, "wizardOptionCaption", wrap=True)
        heading.setTextFormat(Qt.TextFormat.PlainText)
        text = _label(value, "wizardOptionTitle", wrap=True)
        text.setTextFormat(Qt.TextFormat.PlainText)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(heading)
        box.addWidget(text)
        layout.addWidget(frame)
        return text

    @staticmethod
    def _set_value(label: QLabel, value: str) -> None:
        label.setText(value)
        label.setToolTip(value)
        # Reserve enough wrapped height even before the surrounding layouts know
        # their final widths. This avoids silently clipping long user-entered names.
        bounds = label.fontMetrics().boundingRect(
            QRect(0, 0, 105, 3000), int(Qt.TextFlag.TextWordWrap), value
        )
        label.setMinimumHeight(max(label.fontMetrics().height(), bounds.height()))
        frame = label.parentWidget()
        if frame is not None and frame.property("previewCallout"):
            heading = frame.layout().itemAt(0).widget()
            head_bounds = heading.fontMetrics().boundingRect(
                QRect(0, 0, 105, 1000), int(Qt.TextFlag.TextWordWrap), heading.text()
            )
            heading.setMinimumHeight(max(heading.fontMetrics().height(), head_bounds.height()))
            frame.setMinimumHeight(label.minimumHeight() + heading.minimumHeight() + 24)

    def set_answers(self, data: dict) -> None:
        a = clone_answers(data)
        self._set_value(self.metrics["Game"], a["game"] or "Not provided")
        self._set_value(self.metrics["Sensitivity"], str(a["sensitivity"]) if a["sensitivity"] else "Not provided")
        self._set_value(self.metrics["DPI"], str(a["dpi"]) if a["dpi"] else "Not provided")
        self._set_value(self.metrics["Usable mouse travel"], f"{a['pad_width_mm']} mm" if a["pad_width_mm"] else "Not provided")
        self._set_value(self.mouse_value, a["mouse_model"] or "Not provided")
        self._set_value(self.pad_value, _selected(PADS, a["pad"]))
        self._set_value(self.grip_value, _selected(GRIPS, a["grip"]))
        self._set_value(self.motion_value, _motion(a["motion"]))
        self.shoulder_note.setVisible(a["motion"] == "shoulder_hybrid")
        reports = ", ".join(PROBLEMS[key][0] for key in a["problems"]) or "Not provided"
        self.problems.setText("Reported issues: " + reports)
        self.scene.set_answers(a)
