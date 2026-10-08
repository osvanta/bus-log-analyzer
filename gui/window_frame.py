# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The main window's top row, in place of its title bar.

The row holds the window's icon, the toolbar and, at its right end, the
minimize, maximize and close buttons. Its empty parts and the icon move the
window as the title bar did: a drag moves it, a double-click maximizes it, and
a right-click opens the window menu.

On Windows the window keeps its native frame and loses only the caption, so
all the frame does stays: the shadow, the resize edges, snapping to the edges
of the screen, and on Windows 11 the snap layouts that hovering over the
maximize button offers. Windows asks the window what lies under the mouse
(WM_NCHITTEST), and the row answers with what the title bar had there.

A maximized window leaves a sliver of the screen's edge to a taskbar that hides
itself: a window covering the whole screen counts as full-screen, and the
taskbar would no longer come up when the mouse reaches it.

Elsewhere, and under the offscreen platform the tests use, the window keeps its
title bar and the row shows no window buttons.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPainter, QPalette
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QSizePolicy, QToolBar, QWidget

# What lies under the mouse, as Windows names it.
HTCLIENT = 1
HTCAPTION = 2
HTMAXBUTTON = 9
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14

_WM_NCCALCSIZE = 0x0083
_WM_NCHITTEST = 0x0084
_WM_NCMOUSEMOVE = 0x00A0
_WM_NCLBUTTONDOWN = 0x00A1
_WM_NCLBUTTONUP = 0x00A2
_WM_NCLBUTTONDBLCLK = 0x00A3
_WM_NCMOUSELEAVE = 0x02A2

_SM_CYSIZEFRAME = 33
_SWP_FRAME_ONLY = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020 | 0x0200
_TME_LEAVE_NONCLIENT = 0x0002 | 0x0010
_MONITOR_DEFAULTTONEAREST = 2

# The screen edges a taskbar can sit on, and what the shell is asked about it.
ABE_LEFT, ABE_TOP, ABE_RIGHT, ABE_BOTTOM = 0, 1, 2, 3
_ABM_GETSTATE = 0x0004
_ABM_GETAUTOHIDEBAREX = 0x000B
_ABS_AUTOHIDE = 0x0001
# Pixels of the screen a maximized window leaves to a taskbar that hides itself.
AUTOHIDE_GAP = 2

# The window buttons' size, and the glyphs Windows 11 draws on them.
BUTTON_SIZE = QSize(46, 32)
_MINIMIZE, _MAXIMIZE, _RESTORE, _CLOSE = '', '', '', ''
_CLOSE_HOVER = QColor(196, 43, 28)
_CLOSE_PRESSED = QColor(196, 43, 28, 230)
# Room that stays free to move the window by, however narrow it is.
_MIN_DRAG_WIDTH = 48


class _TrackMouseEvent(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
                ('hwndTrack', wintypes.HWND), ('dwHoverTime', wintypes.DWORD)]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]


class _AppBarData(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('hWnd', wintypes.HWND),
                ('uCallbackMessage', wintypes.UINT), ('uEdge', wintypes.UINT),
                ('rc', wintypes.RECT), ('lParam', wintypes.LPARAM)]


if sys.platform == 'win32':
    # A library object of our own, so no other module's argument types apply.
    _user32 = ctypes.WinDLL('user32')
    _user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM]
    _user32.DefWindowProcW.restype = wintypes.LPARAM
    _user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    _user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.IsZoomed.argtypes = [wintypes.HWND]
    _user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    _user32.GetDpiForWindow.restype = wintypes.UINT
    _user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wintypes.UINT]
    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wintypes.UINT]
    _user32.TrackMouseEvent.argtypes = [ctypes.POINTER(_TrackMouseEvent)]
    _user32.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
    _user32.MonitorFromRect.restype = wintypes.HMONITOR
    _user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MonitorInfo)]
    _shell32 = ctypes.WinDLL('shell32')
    _shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(_AppBarData)]
    _shell32.SHAppBarMessage.restype = ctypes.c_size_t


def captionless_frame_supported() -> bool:
    """Whether the window can lose its caption: only on Windows' own platform."""
    return sys.platform == 'win32' and QGuiApplication.platformName() == 'windows'


def autohide_taskbar_edges(window: wintypes.RECT) -> list[int]:
    """The edges of the screen nearest the window rect that hold a taskbar
    which hides itself; the window may be on its way to that screen."""
    query = _AppBarData(ctypes.sizeof(_AppBarData))
    if not _shell32.SHAppBarMessage(_ABM_GETSTATE, ctypes.byref(query)) & _ABS_AUTOHIDE:
        return []
    monitor = _MonitorInfo(ctypes.sizeof(_MonitorInfo))
    screen = _user32.MonitorFromRect(ctypes.byref(window), _MONITOR_DEFAULTTONEAREST)
    if not _user32.GetMonitorInfoW(screen, ctypes.byref(monitor)):
        return []
    edges = []
    for edge in (ABE_LEFT, ABE_TOP, ABE_RIGHT, ABE_BOTTOM):
        query = _AppBarData(ctypes.sizeof(_AppBarData), uEdge=edge, rc=monitor.rcMonitor)
        if _shell32.SHAppBarMessage(_ABM_GETAUTOHIDEBAREX, ctypes.byref(query)):
            edges.append(edge)
    return edges


class TitleRow(QWidget):
    """The window's icon, the toolbar and the window buttons, in one row."""

    def __init__(self, toolbar: QToolBar, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.toolbar = toolbar
        self.icon = _WindowIcon()
        self.minimize_button = _WindowButton(_MINIMIZE, 'Minimize')
        self.maximize_button = _WindowButton(_MAXIMIZE, 'Maximize')
        self.close_button = _WindowButton(_CLOSE, 'Close', closes=True)
        self.setMinimumHeight(BUTTON_SIZE.height())

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.icon)
        layout.addWidget(toolbar)
        layout.addStretch(1)
        layout.addSpacing(_MIN_DRAG_WIDTH)
        for button in self.window_buttons():
            layout.addWidget(button)
        self.show_window_buttons(False)

    def window_buttons(self) -> tuple[QAbstractButton, ...]:
        return (self.minimize_button, self.maximize_button, self.close_button)

    def show_window_buttons(self, shown: bool) -> None:
        self.icon.setVisible(shown)
        for button in self.window_buttons():
            button.setVisible(shown)

    def show_maximized_state(self, maximized: bool) -> None:
        self.maximize_button.set_glyph(_RESTORE if maximized else _MAXIMIZE,
                                       'Restore' if maximized else 'Maximize')

    def hit_test(self, pos: QPoint) -> int:
        """What the title bar had at pos, in this row's coordinates."""
        child = self.childAt(pos)
        if child is None or child is self.icon:
            return HTCAPTION                # the icon, the free room and the gaps around the toolbar
        if child is self.maximize_button:
            return HTMAXBUTTON              # so Windows 11 offers its snap layouts
        if isinstance(child, QAbstractButton):
            return HTCLIENT
        if child is self.toolbar or child.parentWidget() is self.toolbar:
            return HTCAPTION                # between the toolbar's buttons, and its separators
        return HTCLIENT


class CaptionlessWindow(QObject):
    """Takes the caption off a window's native frame and lets its TitleRow do
    the caption's work. The window's nativeEvent passes each message to
    native_event first.
    """

    def __init__(self, window: QWidget, row: TitleRow) -> None:
        super().__init__(window)
        self._window = window
        self._row = row
        self._hwnd: int | None = None       # the native window that has lost its caption
        self._maximize_pressed = False
        self.active = captionless_frame_supported()
        row.show_window_buttons(self.active)
        if self.active:
            row.minimize_button.clicked.connect(window.showMinimized)
            row.maximize_button.clicked.connect(self._toggle_maximized)
            row.close_button.clicked.connect(window.close)
            window.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind == QEvent.Type.Show:
            # The native window exists by now, and is not on screen yet.
            self._remove_caption()
        elif kind == QEvent.Type.WindowStateChange:
            self._row.show_maximized_state(self._window.isMaximized())
        elif kind == QEvent.Type.ActivationChange:
            for button in self._row.window_buttons():
                button.update()
        return False

    def _remove_caption(self) -> None:
        hwnd = int(self._window.winId())
        if hwnd == self._hwnd:
            return
        self._hwnd = hwnd
        # Windows recalculates the frame, asking WM_NCCALCSIZE again.
        _user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, _SWP_FRAME_ONLY)

    def _toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def native_event(self, message) -> tuple[bool, int] | None:
        """The answer to a Windows message, or None to leave it to Qt."""
        if self._hwnd is None:
            return None
        msg = wintypes.MSG.from_address(int(message))
        kind = msg.message
        if kind == _WM_NCCALCSIZE:
            return True, self._client_area(msg)
        if kind == _WM_NCHITTEST:
            return True, self._hit_test(msg)
        # Over the maximize button the mouse is outside the client area, as
        # far as Windows knows, so the button hears of it here.
        over_maximize = msg.wParam == HTMAXBUTTON
        if kind == _WM_NCMOUSEMOVE:
            self._row.maximize_button.set_hovered_outside(over_maximize)
            if over_maximize:
                track = _TrackMouseEvent(ctypes.sizeof(_TrackMouseEvent),
                                         _TME_LEAVE_NONCLIENT, msg.hWnd, 0)
                _user32.TrackMouseEvent(ctypes.byref(track))
                return True, 0
        elif kind == _WM_NCMOUSELEAVE:
            self._maximize_pressed = False
            self._row.maximize_button.set_hovered_outside(False)
            self._row.maximize_button.set_pressed_outside(False)
        elif kind in (_WM_NCLBUTTONDOWN, _WM_NCLBUTTONDBLCLK) and over_maximize:
            self._maximize_pressed = True
            self._row.maximize_button.set_pressed_outside(True)
            return True, 0
        elif kind == _WM_NCLBUTTONUP:
            clicked = over_maximize and self._maximize_pressed
            self._maximize_pressed = False
            self._row.maximize_button.set_pressed_outside(False)
            if clicked:
                # After this message: maximizing sends messages of its own.
                QTimer.singleShot(0, self._toggle_maximized)
                return True, 0
        return None

    def _client_area(self, msg: wintypes.MSG) -> int:
        # The window's frame, as Windows would have it, except at the top: the
        # client area starts where the window does. A maximized window reaches
        # past its screen by its frame on every side, so its client area keeps
        # off the top by as much as Windows keeps it off the bottom, and off
        # the edge of a taskbar that hides itself by AUTOHIDE_GAP.
        rect = wintypes.RECT.from_address(msg.lParam)   # the first of NCCALCSIZE_PARAMS's
        window = wintypes.RECT(rect.left, rect.top, rect.right, rect.bottom)
        _user32.DefWindowProcW(msg.hWnd, msg.message, msg.wParam, msg.lParam)
        rect.top = window.top
        if _user32.IsZoomed(msg.hWnd):
            rect.top += window.bottom - rect.bottom
            for edge in autohide_taskbar_edges(window):
                if edge == ABE_LEFT:
                    rect.left += AUTOHIDE_GAP
                elif edge == ABE_TOP:
                    rect.top += AUTOHIDE_GAP
                elif edge == ABE_RIGHT:
                    rect.right -= AUTOHIDE_GAP
                else:
                    rect.bottom -= AUTOHIDE_GAP
        return 0

    def _hit_test(self, msg: wintypes.MSG) -> int:
        hwnd = msg.hWnd
        hit = _user32.DefWindowProcW(hwnd, msg.message, msg.wParam, msg.lParam)
        if hit != HTCLIENT:
            return hit                      # the frame's own edges, left, right and bottom
        point = wintypes.POINT(ctypes.c_short(msg.lParam & 0xFFFF).value,
                               ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value)
        _user32.ScreenToClient(hwnd, ctypes.byref(point))
        if not _user32.IsZoomed(hwnd):
            # The top edge resizes, as it did above the caption.
            edge = _user32.GetSystemMetricsForDpi(_SM_CYSIZEFRAME, _user32.GetDpiForWindow(hwnd))
            if point.y < edge:
                client = wintypes.RECT()
                _user32.GetClientRect(hwnd, ctypes.byref(client))
                if point.x < edge:
                    return HTTOPLEFT
                if point.x >= client.right - edge:
                    return HTTOPRIGHT
                return HTTOP
        scale = self._window.devicePixelRatioF()
        pos = self._row.mapFrom(self._window, QPoint(int(point.x / scale), int(point.y / scale)))
        if not self._row.rect().contains(pos):
            return HTCLIENT
        return self._row.hit_test(pos)


class _WindowIcon(QWidget):
    """The application's icon at the start of the row."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedWidth(40)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def paintEvent(self, event) -> None:
        side = 16
        rect = self.rect()
        target = rect.adjusted((rect.width() - side) // 2, (rect.height() - side) // 2, 0, 0)
        target.setSize(QSize(side, side))
        painter = QPainter(self)
        self.window().windowIcon().paint(painter, target)


class _WindowButton(QAbstractButton):
    """Minimize, maximize or close, drawn as Windows 11 draws them."""

    _glyph_font: QFont | None = None

    def __init__(self, glyph: str, name: str, closes: bool = False) -> None:
        super().__init__()
        self._closes = closes
        self._hovered_outside = False
        self._pressed_outside = False
        self.set_glyph(glyph, name)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedWidth(BUTTON_SIZE.width())
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:
        return BUTTON_SIZE

    def set_glyph(self, glyph: str, name: str) -> None:
        self._glyph = glyph
        self.setAccessibleName(name)
        self.setToolTip(name)
        self.update()

    def set_hovered_outside(self, hovered: bool) -> None:
        if hovered != self._hovered_outside:
            self._hovered_outside = hovered
            self.update()

    def set_pressed_outside(self, pressed: bool) -> None:
        if pressed != self._pressed_outside:
            self._pressed_outside = pressed
            self.update()

    def paintEvent(self, event) -> None:
        hovered = self.underMouse() or self._hovered_outside
        pressed = self.isDown() or self._pressed_outside
        text = self.palette().color(QPalette.ColorRole.WindowText)
        painter = QPainter(self)
        if self._closes and (hovered or pressed):
            painter.fillRect(self.rect(), _CLOSE_PRESSED if pressed else _CLOSE_HOVER)
            text = QColor(Qt.GlobalColor.white)
        elif hovered or pressed:
            fill = QColor(text)
            fill.setAlphaF(0.06 if pressed else 0.1)
            painter.fillRect(self.rect(), fill)
        elif not self.isActiveWindow():
            text.setAlphaF(0.45)
        painter.setPen(text)
        painter.setFont(self._font())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._glyph)

    @classmethod
    def _font(cls) -> QFont:
        if cls._glyph_font is None:
            families = QFontDatabase.families()
            family = next((f for f in ('Segoe Fluent Icons', 'Segoe MDL2 Assets')
                           if f in families), '')
            cls._glyph_font = QFont(family)
            cls._glyph_font.setPixelSize(10)
        return cls._glyph_font
