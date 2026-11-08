"""Редактор распознанного текста с подсветкой сомнительных слов."""
from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt5.QtWidgets import QPlainTextEdit, QTextEdit

LOW_CONF = 60.0


class TextPanel(QPlainTextEdit):
    wordActivated = pyqtSignal(int)   # курсор встал на слово с индексом
    edited = pyqtSignal()

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self.colors = colors
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        f = QFont()
        f.setPointSizeF(11.5)
        self.setFont(f)
        self.setPlaceholderText("Здесь появится распознанный текст.\nСомнительные слова подсвечиваются — "
                                "щёлкните по слову, чтобы увидеть его на изображении.")
        self._cursors = []      # [(QTextCursor, word_index, conf)]
        self._loading = False
        self._show_low = True
        self._active = None
        self.cursorPositionChanged.connect(self._on_cursor)
        self.textChanged.connect(self._on_changed)

    def load(self, text: str, spans=None, words=None):
        self._loading = True
        self.setPlainText(text or "")
        self._cursors = []
        if spans and words:
            doc = self.document()
            for s, e, wi in spans:
                c = QTextCursor(doc)
                c.setPosition(s)
                c.setPosition(e, QTextCursor.KeepAnchor)
                self._cursors.append((c, wi, words[wi].conf))
        self._active = None
        self._loading = False
        self._refresh()
        self.moveCursor(QTextCursor.Start)

    def set_show_low_conf(self, v: bool):
        self._show_low = v
        self._refresh()

    def low_conf_count(self) -> int:
        return sum(1 for _c, _w, conf in self._cursors if conf < LOW_CONF)

    def _refresh(self):
        sels = []
        if self._show_low:
            fmt = QTextCharFormat()
            fmt.setBackground(QColor(self.colors["lowconf"]))
            fmt.setForeground(QColor(self.colors["lowconf_text"]))
            for c, _wi, conf in self._cursors:
                if conf < LOW_CONF and c.hasSelection():
                    s = QTextEdit.ExtraSelection()
                    s.cursor = c
                    s.format = fmt
                    sels.append(s)
        if self._active is not None:
            fmt = QTextCharFormat()
            fmt.setBackground(QColor(self.colors["selection"]))
            fmt.setFontUnderline(True)
            fmt.setUnderlineColor(QColor(self.colors["accent"]))
            s = QTextEdit.ExtraSelection()
            s.cursor = self._active
            s.format = fmt
            sels.append(s)
        self.setExtraSelections(sels)

    def word_at(self, pos):
        for c, wi, _conf in self._cursors:
            if c.selectionStart() <= pos <= c.selectionEnd() and c.hasSelection():
                return c, wi
        return None, None

    def _on_cursor(self):
        if self._loading:
            return
        c, wi = self.word_at(self.textCursor().position())
        if wi is not None:
            self._active = c
            self._refresh()
            self.wordActivated.emit(wi)

    def select_word(self, index):
        for c, wi, _conf in self._cursors:
            if wi == index and c.hasSelection():
                self._active = c
                self._refresh()
                tc = QTextCursor(c)
                tc.setPosition(c.selectionStart())
                self.blockSignals(True)
                self.setTextCursor(tc)
                self.blockSignals(False)
                self.ensureCursorVisible()
                return

    def next_low_conf(self):
        """Переход к следующему сомнительному слову."""
        pos = self.textCursor().position()
        low = sorted((c.selectionStart(), c, wi) for c, wi, conf in self._cursors
                     if conf < LOW_CONF and c.hasSelection())
        if not low:
            return False
        for start, c, wi in low:
            if start > pos:
                break
        else:
            start, c, wi = low[0]
        tc = QTextCursor(c)
        self.setTextCursor(tc)
        self.ensureCursorVisible()
        self.setFocus(Qt.OtherFocusReason)
        return True

    def _on_changed(self):
        if not self._loading:
            self.edited.emit()
            self._refresh()
