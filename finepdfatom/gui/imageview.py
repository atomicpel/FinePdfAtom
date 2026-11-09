"""Просмотр изображения: масштаб, рамки слов, выделение области распознавания."""
from __future__ import annotations

import numpy as np
from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
                             QGraphicsView)


def to_qimage(arr: np.ndarray) -> QImage:
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    if arr.ndim == 2:
        img = QImage(arr.data, w, h, w, QImage.Format_Grayscale8)
    else:
        img = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
    return img.copy()


class WordBox(QGraphicsRectItem):
    def __init__(self, rect, index, word, colors):
        super().__init__(rect)
        self.index = index
        self.word = word
        self.colors = colors
        self.setAcceptHoverEvents(True)
        self.setToolTip("«%s» — уверенность %.0f%%" % (word.text, word.conf))
        self._apply(False)

    def _apply(self, active):
        low = self.word.conf < 60
        base = QColor(self.colors["warn"] if low else self.colors["box"])
        pen = QPen(base, 0)
        pen.setCosmetic(True)
        pen.setWidthF(2.0 if active else (1.4 if low else 1.0))
        fill = QColor(base)
        fill.setAlpha(80 if active else (45 if low else 18))
        self.setPen(pen)
        self.setBrush(QBrush(fill))
        self.setZValue(3 if active else 2)

    def set_active(self, a):
        self._apply(a)


class ImageView(QGraphicsView):
    wordClicked = pyqtSignal(int)
    regionSelected = pyqtSignal(QRectF)
    filesDropped = pyqtSignal(list)
    zoomChanged = pyqtSignal(float)

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self.colors = colors
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setAcceptDrops(True)
        self.pix = None
        self.boxes = []
        self.region_item = None
        self.region_mode = False
        self._drag_origin = None
        self._fit = True
        self.show_boxes = True

    # --- содержимое -------------------------------------------------------
    def clear_all(self):
        self.scene().clear()
        self.pix = None
        self.zones = []
        self.boxes = []
        self.region_item = None

    def set_image(self, arr: np.ndarray, words=None, keep_view=False):
        tr = self.transform() if keep_view else None
        self.clear_all()
        if arr is None:
            return
        self.pix = QGraphicsPixmapItem(QPixmap.fromImage(to_qimage(arr)))
        self.pix.setTransformationMode(Qt.SmoothTransformation)
        self.scene().addItem(self.pix)
        self.scene().setSceneRect(QRectF(self.pix.pixmap().rect()).adjusted(-40, -40, 40, 40))
        if words:
            for i, w in enumerate(words):
                x0, y0, x1, y1 = w.box
                b = WordBox(QRectF(x0, y0, x1 - x0, y1 - y0), i, w, self.colors)
                b.setVisible(self.show_boxes)
                self.scene().addItem(b)
                self.boxes.append(b)
        if tr is not None and not self._fit:
            self.setTransform(tr)
        else:
            self.fit()

    def set_zones(self, data, visible=True):
        """Зоны страницы: текст — синие, таблицы — зелёные, рисунки — красные; цифра — порядок чтения."""
        from PyQt5.QtGui import QFont
        from PyQt5.QtWidgets import QGraphicsSimpleTextItem
        self.zones = []
        if data is None or not getattr(data, "order", None):
            return
        colors = {"text": QColor("#3a6df0"), "heading": QColor("#3a6df0"), "caption": QColor("#3a6df0"),
                  "table": QColor("#1f9d61"), "image": QColor("#e5484d")}
        n = 0
        for bid in data.order:
            b = data.blocks.get(bid)
            if b is None:
                continue
            if b.kind not in ("table", "image") and not any(w.block == bid for w in data.words):
                continue
            n += 1
            c = colors.get(b.kind, QColor("#3a6df0"))
            x0, y0, x1, y1 = b.box
            pen = QPen(c, 0)
            pen.setCosmetic(True)
            pen.setWidthF(2.0)
            fill = QColor(c)
            fill.setAlpha(14)
            r = self.scene().addRect(QRectF(x0, y0, x1 - x0, y1 - y0), pen, QBrush(fill))
            r.setZValue(1)
            r.setToolTip({"table": "Таблица", "image": "Рисунок"}.get(b.kind, "Текст") + " — зона %d" % n)
            label = QGraphicsSimpleTextItem(str(n))
            f = QFont()
            f.setBold(True)
            f.setPixelSize(max(14, int((y1 - y0) * 0.0 + self.pix.pixmap().height() / 70)) if self.pix else 14)
            label.setFont(f)
            label.setBrush(QBrush(c))
            label.setPos(x0 - label.boundingRect().width() - 4, y0)
            label.setZValue(4)
            self.scene().addItem(label)
            if b.kind == "table" and b.table is not None:
                cpen = QPen(c, 0, Qt.DotLine)
                cpen.setCosmetic(True)
                for cell in b.table.cells:
                    cx0, cy0, cx1, cy1 = cell.box
                    ci = self.scene().addRect(QRectF(cx0, cy0, cx1 - cx0, cy1 - cy0), cpen)
                    ci.setZValue(1)
                    self.zones.append(ci)
            self.zones += [r, label]
        self.set_zones_visible(visible)

    def set_zones_visible(self, v):
        for z in getattr(self, "zones", []):
            z.setVisible(v)

    def set_boxes_visible(self, v):
        self.show_boxes = v
        for b in self.boxes:
            b.setVisible(v)

    def highlight_word(self, index):
        for b in self.boxes:
            b.set_active(b.index == index)
        if 0 <= index < len(self.boxes):
            b = self.boxes[index]
            if not self.show_boxes:
                b.setVisible(True)
            self.ensureVisible(b, 80, 80)

    def set_region(self, rect: QRectF | None):
        if self.region_item is not None:
            self.scene().removeItem(self.region_item)
            self.region_item = None
        if rect is not None and self.pix is not None:
            pen = QPen(QColor(self.colors["accent"]), 0, Qt.DashLine)
            pen.setCosmetic(True)
            pen.setWidthF(2)
            fill = QColor(self.colors["accent"])
            fill.setAlpha(28)
            self.region_item = self.scene().addRect(rect, pen, QBrush(fill))
            self.region_item.setZValue(5)
            self.region_item.setFlag(QGraphicsItem.ItemIsSelectable, False)

    def region(self):
        return self.region_item.rect() if self.region_item is not None else None

    # --- масштаб ----------------------------------------------------------
    def fit(self):
        if self.pix is None:
            return
        self._fit = True
        # в режиме «по размеру» полосы прокрутки не нужны; иначе их появление/исчезновение
        # меняет размер области и вызывает бесконечный цикл подгонки
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.fitInView(self.pix, Qt.KeepAspectRatio)
        self.zoomChanged.emit(self.transform().m11())

    def _free_scroll(self):
        self._fit = False
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    def zoom(self, factor):
        self._free_scroll()
        cur = self.transform().m11()
        factor = max(0.03 / cur, min(16.0 / cur, factor))
        self.scale(factor, factor)
        self.zoomChanged.emit(self.transform().m11())

    def zoom_100(self):
        self._free_scroll()
        self.resetTransform()
        self.zoomChanged.emit(1.0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._fit:
            self.fit()

    def wheelEvent(self, e):
        # Ctrl+колесо — масштаб; без Ctrl — прокрутка (а если прокручивать некуда — масштаб)
        d = e.angleDelta().y()
        if d and (e.modifiers() & Qt.ControlModifier or self._fit):
            self.zoom(1.15 if d > 0 else 1 / 1.15)
            return
        super().wheelEvent(e)

    # --- выделение области ------------------------------------------------
    def set_region_mode(self, on: bool):
        self.region_mode = on
        self.setDragMode(QGraphicsView.NoDrag if on else QGraphicsView.ScrollHandDrag)
        self.viewport().setCursor(Qt.CrossCursor if on else Qt.OpenHandCursor)

    def mousePressEvent(self, e):
        if self.region_mode and e.button() == Qt.LeftButton and self.pix is not None:
            self._drag_origin = self.mapToScene(e.pos())
            self.set_region(QRectF(self._drag_origin, self._drag_origin))
            return
        item = self.itemAt(e.pos())
        if isinstance(item, WordBox) and e.button() == Qt.LeftButton:
            self.wordClicked.emit(item.index)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.region_mode and self._drag_origin is not None:
            p = self.mapToScene(e.pos())
            r = QRectF(self._drag_origin, p).normalized()
            r = r.intersected(self.pix.boundingRect())
            if self.region_item is not None:
                self.region_item.setRect(r)
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.region_mode and self._drag_origin is not None:
            self._drag_origin = None
            r = self.region()
            if r is not None and r.width() > 8 and r.height() > 8:
                self.regionSelected.emit(r)
            else:
                self.set_region(None)
            return
        super().mouseReleaseEvent(e)

    # --- перетаскивание файлов -------------------------------------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasImage():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            e.acceptProposedAction()
