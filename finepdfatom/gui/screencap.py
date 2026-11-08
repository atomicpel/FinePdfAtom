"""Выделение области экрана мышью (как в NormCap), результат — QImage."""
from __future__ import annotations

from PyQt5.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QFont, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QWidget


def grab_desktop():
    """Снимок всех мониторов: (QPixmap в физических пикселях, виртуальная геометрия, масштаб)."""
    screens = QGuiApplication.screens()
    geo = QRect()
    for s in screens:
        geo = geo.united(s.geometry())
    ratio = max(s.devicePixelRatio() for s in screens) if screens else 1.0
    canvas = QPixmap(int(geo.width() * ratio), int(geo.height() * ratio))
    canvas.fill(Qt.black)
    p = QPainter(canvas)
    for s in screens:
        pm = s.grabWindow(0)
        g = s.geometry()
        target = QRect(int((g.x() - geo.x()) * ratio), int((g.y() - geo.y()) * ratio),
                       int(g.width() * ratio), int(g.height() * ratio))
        p.drawPixmap(target, pm)
    p.end()
    return canvas, geo, ratio


class ScreenSelector(QWidget):
    selected = pyqtSignal(QImage)
    cancelled = pyqtSignal()

    def __init__(self, accent="#5b8cff"):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.X11BypassWindowManagerHint)
        self.accent = QColor(accent)
        self.canvas, self.geo, self.ratio = grab_desktop()
        self.setGeometry(self.geo)
        self.setCursor(QCursor(Qt.CrossCursor))
        self.setMouseTracking(True)
        self.origin = None
        self.current = None
        self.mouse = QPoint()

    def start(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.grabKeyboard()

    def _sel(self):
        if self.origin is None or self.current is None:
            return QRect()
        return QRect(self.origin, self.current).normalized()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.canvas)
        p.fillRect(self.rect(), QColor(8, 12, 24, 120))
        r = self._sel()
        if not r.isNull() and r.width() > 1:
            src = QRect(int(r.x() * self.ratio), int(r.y() * self.ratio),
                        int(r.width() * self.ratio), int(r.height() * self.ratio))
            p.drawPixmap(r, self.canvas, src)
            pen = QPen(self.accent, 2)
            p.setPen(pen)
            p.drawRect(r.adjusted(0, 0, -1, -1))
            label = "%d × %d" % (src.width(), src.height())
            f = QFont()
            f.setPointSize(9)
            f.setBold(True)
            p.setFont(f)
            tw = p.fontMetrics().horizontalAdvance(label) + 14
            box = QRect(r.x(), max(0, r.y() - 24), tw, 20)
            p.fillRect(box, self.accent)
            p.setPen(Qt.white)
            p.drawText(box, Qt.AlignCenter, label)
        else:
            f = QFont()
            f.setPointSize(13)
            f.setBold(True)
            p.setFont(f)
            text = "Выделите область с текстом  ·  Esc — отмена"
            tw = p.fontMetrics().horizontalAdvance(text) + 40
            scr = QGuiApplication.screenAt(self.mapToGlobal(self.mouse)) or QGuiApplication.primaryScreen()
            g = scr.geometry().translated(-self.geo.topLeft())
            box = QRect(g.center().x() - tw // 2, g.y() + 40, tw, 44)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(20, 24, 34, 220))
            p.drawRoundedRect(box, 12, 12)
            p.setPen(Qt.white)
            p.drawText(box, Qt.AlignCenter, text)
            # перекрестие
            pen = QPen(QColor(255, 255, 255, 120), 1, Qt.DashLine)
            p.setPen(pen)
            p.drawLine(0, self.mouse.y(), self.width(), self.mouse.y())
            p.drawLine(self.mouse.x(), 0, self.mouse.x(), self.height())
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.origin = e.pos()
            self.current = e.pos()
            self.update()
        elif e.button() == Qt.RightButton:
            self._cancel()

    def mouseMoveEvent(self, e):
        self.mouse = e.pos()
        if self.origin is not None:
            self.current = e.pos()
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self.origin is None:
            return
        r = self._sel()
        if r.width() < 6 or r.height() < 6:
            self.origin = self.current = None
            self.update()
            return
        src = QRect(int(r.x() * self.ratio), int(r.y() * self.ratio),
                    int(r.width() * self.ratio), int(r.height() * self.ratio))
        img = self.canvas.copy(src).toImage()
        self.releaseKeyboard()
        self.hide()
        self.selected.emit(img)
        self.close()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._cancel()

    def _cancel(self):
        self.releaseKeyboard()
        self.hide()
        self.cancelled.emit()
        self.close()


def qimage_to_rgb(img: QImage):
    import numpy as np
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    ptr.setsize(img.bytesPerLine() * h)
    arr = np.frombuffer(ptr, np.uint8).reshape(h, img.bytesPerLine())[:, :w * 3].reshape(h, w, 3)
    return np.ascontiguousarray(arr.copy())
