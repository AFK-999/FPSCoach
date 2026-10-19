"""Fluid layout helpers that keep columns from cramping or overlapping on resize.

* :class:`ResponsiveCards` - lays a set of equal cards into a grid whose column
  count adapts to the available width (down to a single stacked column), so the
  cards reflow instead of being squeezed.
* :class:`ResponsiveRow` - two (or more) widgets side by side that automatically
  stack vertically below a width breakpoint, using ``QBoxLayout.setDirection``.

Spacing defaults track the Vantage design system's base unit (16px) so grids
built here sit flush with the rest of the app without per-call tuning.
"""

from __future__ import annotations

from PySide6.QtWidgets import QBoxLayout, QGridLayout, QSizePolicy, QWidget

_MAX_TRACKED_COLUMNS = 8


class ResponsiveCards(QWidget):
    """Grid of cards whose column count follows the widget width."""

    def __init__(
        self,
        cards: list[QWidget],
        min_col_width: int = 320,
        spacing: int = 16,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cards = cards
        self._min_col_width = max(160, min_col_width)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(spacing)
        self._columns = 0
        for card in self._cards:
            card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._relayout(1)

    def _columns_for_width(self, width: int) -> int:
        if not self._cards:
            return 1
        usable = max(1, width + self._grid.spacing())
        fit = usable // (self._min_col_width + self._grid.spacing())
        return max(1, min(len(self._cards), int(fit)))

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        columns = self._columns_for_width(self.width())
        if columns != self._columns:
            self._relayout(columns)
        super().resizeEvent(event)

    def _relayout(self, columns: int) -> None:
        self._columns = columns
        for card in self._cards:
            self._grid.removeWidget(card)
        for index, card in enumerate(self._cards):
            row, col = divmod(index, columns)
            self._grid.addWidget(card, row, col)
        for col in range(_MAX_TRACKED_COLUMNS):
            self._grid.setColumnStretch(col, 1 if col < columns else 0)


class ResponsiveRow(QWidget):
    """A row of widgets that stacks vertically when narrower than ``breakpoint``."""

    def __init__(self, breakpoint: int = 760, spacing: int = 16, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(spacing)
        self._breakpoint = breakpoint
        self._horizontal = True
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def add_widget(self, widget: QWidget, stretch: int = 0) -> None:
        self._box.addWidget(widget, stretch)

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        want_horizontal = self.width() >= self._breakpoint
        if want_horizontal != self._horizontal:
            self._horizontal = want_horizontal
            self._box.setDirection(
                QBoxLayout.Direction.LeftToRight
                if want_horizontal
                else QBoxLayout.Direction.TopToBottom
            )
        super().resizeEvent(event)