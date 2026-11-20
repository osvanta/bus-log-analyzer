# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Parts of the plot toolbar: a button that opens a menu, the plot layouts by
name, and the rail of pane buttons at the plot's right edge.

The toolbar keeps one kind of control in one place: the layout in one
picker, the view and measuring buttons a click away, the display choices
that are set once in one menu, and the views that open beside the plot
(Trace, Table, XY) in a glass list that the pointer brings out of the
plot's right edge and that folds back when it leaves.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QCursor, QLinearGradient, QPainter, QPainterPath, QPalette, QPen,
)
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QMenu, QPushButton, QVBoxLayout, QWidget,
)

#: Each plot layout as a configuration names it, its name on the picker,
#: and what it is.
LAYOUTS = (
    ('normal', 'Single plot', 'Every signal in one plot, on one axis'),
    ('multi_axis', 'Multi-Axis', 'Every signal in one plot, each on its own axis'),
    ('stacked', 'Stacked', 'A row per signal'),
    ('multistack', 'MultiStack', 'Rows you group: drag signals into a stack to overlay them'),
)
LAYOUT_NAMES = {mode: name for mode, name, _tip in LAYOUTS}
#: After the text of a button that opens a menu.
MENU_MARK = ' ▾'


class MenuButton(QPushButton):
    """A plot button that opens a menu, filled anew at each click so that
    its checks and greyed entries are current."""

    def __init__(self, fill: Callable[[QMenu], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fill = fill
        self.clicked.connect(self.open_menu)

    def menu(self) -> QMenu:
        """The menu as a click would open it."""
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        self._fill(menu)
        return menu

    def open_menu(self) -> None:
        """Under the button, or at the pointer when it is in the "»" menu."""
        at = (self.mapToGlobal(QPoint(0, self.height())) if self.isVisible()
              else QCursor.pos())
        menu = self.menu()
        menu.exec(at)
        menu.deleteLater()


#: On the tab that the pointer brings out, and on the open list's first row.
UNFOLD_MARK = '«'
FOLD_MARK = '»'

#: The rail's three states.
REST, PEEK, OPEN = 'rest', 'peek', 'open'


class PaneRail(QWidget):
    """The views that open beside the plot (Trace, Table, XY), kept off the
    plot until they are wanted.

    It floats on the right edge of what it is attached to (attach()), a
    sibling, and is one of three things:

    - at rest, a short thin mark on the edge;
    - with the pointer near the edge, a small tab drawn like a plot tab
      (outline, rounded corners, a flare into the edge), with « on it;
    - after a click on that tab, a glass list of the views' buttons under »,
      which folds back when the pointer leaves it, or at a click on ».

    The mark, the tab's « and the list's » are at one height. Just after it
    is first shown the tab is out for a moment, so that it is seen.
    """

    stateChanged = Signal(str)

    #: From the top of what it floats on to the middle of mark, « and ».
    HANDLE_CENTRE = 34
    #: The pointer this near the edge brings the tab out...
    NEAR = 24
    #: ...once it has stayed that long (ms); passing by does not.
    PEEK_AFTER_MS = 120
    #: The tab goes back, and the list folds, this long after the pointer left.
    REST_AFTER_MS = 350
    #: How long the tab stays out when the rail is first shown.
    HINT_MS = 2500
    POLL_MS = 60

    MARK_SIZE = (4, 44)
    TAB_WIDTH, TAB_HEIGHT, FLARE, TAB_RADIUS = 20, 44, 7, 9
    RADIUS = 14

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName('paneRail')
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 8, 6, 8)
        self._layout.setSpacing(4)
        self.handle = QPushButton(FOLD_MARK)
        self.handle.setObjectName('paneRailHandle')
        self.handle.setAccessibleName('Fold side buttons')
        self.handle.setToolTip('Fold these buttons away')
        self.handle.clicked.connect(self.peek)
        self._layout.addWidget(self.handle)
        self._buttons: list[QAbstractButton] = []
        self._state = OPEN          # as built; rest() below
        self._over: QWidget | None = None
        self._since: float | None = None    # when the pointer came near, or left
        self._hint_until = 0.0
        self._hinted = False
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._poll)
        self.setToolTip('Trace, Table and XY')
        self._restyle()
        self.rest()

    # ── Its buttons ───────────────────────────────────────────────────────

    def add_button(self, button: QAbstractButton) -> None:
        self._layout.addWidget(button)
        self._buttons.append(button)
        button.setVisible(self._state == OPEN)
        self._fit()

    def buttons(self) -> list[QAbstractButton]:
        return list(self._buttons)

    # ── Its three states ──────────────────────────────────────────────────

    def state(self) -> str:
        return self._state

    def rest(self) -> None:
        self._set_state(REST)

    def peek(self) -> None:
        self._set_state(PEEK)

    def open(self) -> None:
        self._set_state(OPEN)

    def _set_state(self, state: str) -> None:
        self._since = None
        if state == self._state:
            return
        self._state = state
        for widget in (self.handle, *self._buttons):
            widget.setVisible(state == OPEN)
        self._fit()
        self.update()
        self.stateChanged.emit(state)

    def mousePressEvent(self, event) -> None:
        if self._state != OPEN and event.button() == Qt.MouseButton.LeftButton:
            self.open()
            event.accept()
            return
        super().mousePressEvent(event)

    # ── Following the pointer ─────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._timer.start()
        if not self._hinted:
            # Out for a moment at the start, so that the user sees it is there.
            self._hinted = True
            self._hint_until = time.monotonic() + self.HINT_MS / 1000
            if self._state == REST:
                self.peek()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def _poll(self) -> None:
        position = QCursor.pos()
        under = QApplication.widgetAt(position)
        # Not while a menu, a dialog or another window has the pointer.
        ours = under is not None and under.window() is self.window()
        self.follow_pointer(position if ours else None, time.monotonic())

    def follow_pointer(self, position: QPoint | None, now: float) -> None:
        """Move between the states for a pointer at global *position* (None:
        not over this window) at time *now*, in seconds."""
        on_rail = near = False
        if position is not None and self._over is not None and self.parentWidget() is not None:
            local = self.parentWidget().mapFromGlobal(position)
            area = self._over.geometry()
            on_rail = self.geometry().adjusted(-6, -6, 6, 6).contains(local)
            near = area.contains(local) and local.x() >= area.right() - self.NEAR
        if self._state == REST:
            if not (near or on_rail):
                self._since = None
            elif self._since is None:
                self._since = now
            elif (now - self._since) * 1000 >= self.PEEK_AFTER_MS:
                self.peek()
            return
        if now < self._hint_until and self._state == PEEK:
            return
        staying = on_rail or (near and self._state == PEEK)
        if staying:
            self._since = None
        elif self._since is None:
            self._since = now
        elif (now - self._since) * 1000 >= self.REST_AFTER_MS:
            self.rest()

    # ── Where it floats ───────────────────────────────────────────────────

    def attach(self, over: QWidget) -> None:
        """Float on *over*, a sibling: on its right edge, mark and handle a
        fixed way below its top, and stay there as it is resized."""
        self._over = over
        over.installEventFilter(self)
        self._place()

    def eventFilter(self, watched, event) -> bool:
        if watched is self._over and event.type() in (
                QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show):
            self._place()
        return super().eventFilter(watched, event)

    def handle_offset(self) -> int:
        """From the rail's top to the middle of its mark, « or »."""
        if self._state == OPEN:
            self._layout.activate()
            return self.handle.geometry().center().y()
        return self.height() // 2

    def _fit(self) -> None:
        if self._state == REST:
            self.resize(*self.MARK_SIZE)
        elif self._state == PEEK:
            self.resize(self.TAB_WIDTH, self.TAB_HEIGHT + 2 * self.FLARE)
        else:
            self._layout.activate()
            self.resize(self.sizeHint())
        self._place()

    def _place(self) -> None:
        if self._over is None:
            return
        area = self._over.geometry()
        self.move(area.x() + area.width() - self.width(),
                  area.y() + self.HANDLE_CENTRE - self.handle_offset())
        self.raise_()

    # ── Its look ──────────────────────────────────────────────────────────

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.PaletteChange:
            self._restyle()
        super().changeEvent(event)

    def _restyle(self) -> None:
        """Buttons as tabs in the glass, in the window's own text colour: no
        fill until pointed at, an outline for the view that is open."""
        text = self.palette().color(QPalette.ColorRole.WindowText)
        rgb = f'{text.red()}, {text.green()}, {text.blue()}'
        self.setStyleSheet(f"""
            #paneRail QPushButton {{
                background: transparent; color: rgb({rgb});
                border: 1px solid transparent; border-radius: 10px;
                padding: 6px 12px; min-width: 0px;
            }}
            #paneRail QPushButton:hover {{ background: rgba({rgb}, 30); }}
            #paneRail QPushButton:checked {{
                background: rgba({rgb}, 38); border-color: rgba({rgb}, 150);
            }}
            #paneRail QPushButton:disabled {{ color: rgba({rgb}, 105); }}
            #paneRail QPushButton#paneRailHandle {{ padding: 2px 8px; }}
        """)

    def glass_path(self) -> QPainterPath:
        """The open list's outline: rounded where it is free, open along the
        edge it grows out of."""
        body = QRectF(self.rect()).adjusted(0.5, 0.5, 0, -0.5)
        radius = min(self.RADIUS, body.height() / 2)
        path = QPainterPath()
        path.moveTo(body.right(), body.top())
        path.lineTo(body.left() + radius, body.top())
        path.quadTo(body.left(), body.top(), body.left(), body.top() + radius)
        path.lineTo(body.left(), body.bottom() - radius)
        path.quadTo(body.left(), body.bottom(), body.left() + radius, body.bottom())
        path.lineTo(body.right(), body.bottom())
        return path

    def tab_path(self) -> QPainterPath:
        """The tab's outline, a plot tab's stood on the edge: out of the edge
        in a flare, round two corners, and back into the edge in a flare."""
        edge = self.width() - 0.5
        left = 0.5
        top, bottom = self.FLARE + 0.5, self.height() - self.FLARE - 0.5
        flare, radius = self.FLARE, self.TAB_RADIUS
        path = QPainterPath()
        path.moveTo(edge, top - flare)
        path.quadTo(edge, top, edge - flare, top)
        path.lineTo(left + radius, top)
        path.quadTo(left, top, left, top + radius)
        path.lineTo(left, bottom - radius)
        path.quadTo(left, bottom, left + radius, bottom)
        path.lineTo(edge - flare, bottom)
        path.quadTo(edge, bottom, edge, bottom + flare)
        return path

    def paintEvent(self, event) -> None:
        palette = self.palette()
        window = palette.color(QPalette.ColorRole.Window)
        text = palette.color(QPalette.ColorRole.WindowText)
        outline_color = _blend(window, text, 0.5)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._state == REST:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(outline_color)
            painter.drawRoundedRect(QRectF(self.rect()), 2, 2)
        elif self._state == PEEK:
            outline = self.tab_path()
            inside = QPainterPath(outline)
            inside.closeSubpath()
            painter.fillPath(inside, window)
            painter.strokePath(outline, QPen(outline_color, 1))
            painter.setPen(text)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, UNFOLD_MARK)
        else:
            outline = self.glass_path()
            inside = QPainterPath(outline)
            inside.closeSubpath()
            # The window's colour, thinned so the plot shows through...
            painter.fillPath(inside, QColor(window.red(), window.green(), window.blue(), 150))
            # ...under a sheen that is brightest at the top left...
            sheen = QLinearGradient(0, 0, self.width(), self.height())
            sheen.setColorAt(0.0, QColor(text.red(), text.green(), text.blue(), 60))
            sheen.setColorAt(1.0, QColor(text.red(), text.green(), text.blue(), 14))
            painter.fillPath(inside, sheen)
            # ...and a bright rim, as glass has.
            painter.strokePath(outline, QPen(QColor(text.red(), text.green(), text.blue(), 95), 1))
        painter.end()


def _blend(base: QColor, toward: QColor, amount: float) -> QColor:
    """base moved amount (0..1) of the way toward toward."""
    return QColor(*(round(a + (b - a) * amount) for a, b in (
        (base.red(), toward.red()), (base.green(), toward.green()),
        (base.blue(), toward.blue()))))
