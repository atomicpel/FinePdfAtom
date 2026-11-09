"""Редактор распознанного текста с подсветкой сомнительных слов."""
from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt5.QtWidgets import QAction, QPlainTextEdit, QTextEdit

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
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance("0") * 14)
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
                self._cursors.append((c, wi, words[wi].conf, getattr(words[wi], 'in_dict', True)))
        self._active = None
        self._loading = False
        self._refresh()
        self.moveCursor(QTextCursor.Start)

    def set_show_low_conf(self, v: bool):
        self._show_low = v
        self._refresh()

    def low_conf_count(self) -> int:
        return sum(1 for _c, _w, conf, ind in self._cursors if conf < LOW_CONF or not ind)

    def _refresh(self):
        sels = []
        if self._show_low:
            fmt = QTextCharFormat()
            fmt.setBackground(QColor(self.colors["lowconf"]))
            fmt.setForeground(QColor(self.colors["lowconf_text"]))
            for c, _wi, conf, *_x in self._cursors:
                if conf < LOW_CONF and c.hasSelection():
                    s = QTextEdit.ExtraSelection()
                    s.cursor = c
                    s.format = fmt
                    sels.append(s)
            sp = QTextCharFormat()
            sp.setUnderlineStyle(QTextCharFormat.SpellCheckUnderline)
            sp.setUnderlineColor(QColor("#e5484d"))
            for c, _wi, conf, ind in self._cursors:
                if not ind and conf >= LOW_CONF and c.hasSelection():
                    s = QTextEdit.ExtraSelection()
                    s.cursor = c
                    s.format = sp
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
        for c, wi, _conf, *_x in self._cursors:
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
        for c, wi, _conf, *_x in self._cursors:
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
        low = sorted(((c.selectionStart(), id(c), c, wi) for c, wi, conf, ind in self._cursors
                      if (conf < LOW_CONF or not ind) and c.hasSelection()), key=lambda t: t[:2])
        low = [(a, c, wi) for a, _i, c, wi in low]
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

    def set_langs(self, langs: str):
        self._langs = langs

    def contextMenuEvent(self, e):
        menu = self.createStandardContextMenu()
        cur = self.cursorForPosition(e.pos())
        c, wi = self.word_at(cur.position())
        if c is not None and getattr(self, "_langs", ""):
            from .. import spell
            word = c.selectedText()
            chk = spell.Checker(self._langs)
            if chk.enabled() and not chk.check(word):
                first = menu.actions()[0] if menu.actions() else None
                sugg = chk.suggest(word)
                acts = []
                for sgg in sugg:
                    a = QAction(sgg, menu)
                    f = a.font()
                    f.setBold(True)
                    a.setFont(f)
                    a.triggered.connect(lambda _=False, cc=c, t=sgg: self._replace(cc, t))
                    acts.append(a)
                if not sugg:
                    a = QAction("(нет вариантов)", menu)
                    a.setEnabled(False)
                    acts.append(a)
                add = QAction("Добавить «%s» в словарь" % spell.clean(word), menu)
                add.triggered.connect(lambda _=False, w=word, cc=c: self._add_word(w, cc))
                acts += [add]
                for a in acts:
                    menu.insertAction(first, a)
                menu.insertSeparator(first)
        menu.exec_(e.globalPos())

    def _replace(self, c, text):
        tc = QTextCursor(c)
        start = tc.selectionStart()
        old = tc.selectedText()
        core = old.strip(".,;:!?»«()\"'")
        tc.insertText(old.replace(core, text) if core else text)
        tc.setPosition(start)
        tc.setPosition(start + len(old.replace(core, text) if core else text), QTextCursor.KeepAnchor)
        for i, (cc, wi, conf, ind) in enumerate(self._cursors):
            if cc is c:
                self._cursors[i] = (tc, wi, 100.0, True)
        self._refresh()

    def _add_word(self, word, c):
        from .. import spell
        spell.add_user_word(word)
        key = spell.clean(word).lower()
        for i, (cc, wi, conf, ind) in enumerate(self._cursors):
            if spell.clean(cc.selectedText()).lower() == key:
                self._cursors[i] = (cc, wi, conf, True)
        self._refresh()

    def _on_changed(self):
        if not self._loading:
            self.edited.emit()
            self._refresh()
