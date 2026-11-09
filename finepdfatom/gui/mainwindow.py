"""Главное окно FinePdfAtom."""
from __future__ import annotations

import os
import time
from collections import OrderedDict

import numpy as np
from PyQt5.QtCore import QRectF, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QImage, QKeySequence, QPainter, QPixmap
from PyQt5.QtWidgets import (QAction, QActionGroup, QApplication, QButtonGroup, QComboBox, QFileDialog,
                             QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                             QMessageBox, QProgressBar, QPushButton, QShortcut, QSizePolicy, QSplitter,
                             QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from .. import __version__, export, ocr, preprocess, sources, tess
from . import icons, theme
from .config import Config
from .dialogs import AboutDialog, BatchDialog, ScannerDialog, SettingsDialog, lang_title
from .imageview import ImageView, to_qimage
from .screencap import ScreenSelector, qimage_to_rgb
from .textpanel import TextPanel
from .workers import ExportWorker, LoadWorker, OcrWorker

ST_NEW, ST_QUEUED, ST_DONE, ST_ERROR = range(4)
THUMB = 112


class Page:
    def __init__(self, ref):
        self.ref = ref
        self.result = None
        self.text = None          # отредактированный пользователем текст
        self.region = None        # (x, y, w, h) в координатах исходника
        self.state = ST_NEW
        self.error = ""
        self.thumb = None

    def final_text(self):
        if self.text is not None:
            return self.text
        return self.result.text if self.result else ""


class ThumbWorker(QThread):
    ready = pyqtSignal(object, QImage)

    def __init__(self, pages, parent=None):
        super().__init__(parent)
        self.pages = list(pages)

    def run(self):
        for p in self.pages:
            if self.isInterruptionRequested():
                return
            try:
                img = _thumb(p.ref)
                self.ready.emit(p, img)
            except Exception:  # noqa: BLE001
                pass


def _thumb(ref) -> QImage:
    if ref.kind == "pdf":
        import fitz
        with fitz.open(ref.path) as d:
            pg = d[ref.index]
            z = THUMB * 2 / max(pg.rect.width, pg.rect.height)
            pix = pg.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
            arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
    elif ref.kind == "memory":
        arr = ref._image
    else:
        from PIL import Image, ImageSequence
        with Image.open(ref.path) as im:
            if ref.index:
                for i, fr in enumerate(ImageSequence.Iterator(im)):
                    if i == ref.index:
                        im = fr.copy()
                        break
            im.draft("RGB", (THUMB * 3, THUMB * 3))
            im = im.convert("RGB")
            im.thumbnail((THUMB * 2, THUMB * 2))
            arr = np.array(im)
    q = to_qimage(arr)
    return q.scaled(THUMB * 2, THUMB * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)


class MainWindow(QMainWindow):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.colors = theme.colors(config.theme)
        self.pages = []
        self.current = None
        self.recognizer = None
        self.worker = None
        self.jobs_pages = []
        self._orig_cache = OrderedDict()
        self._thumbw = []
        self._loaders = []
        self._export = None
        self.setWindowTitle("FinePdfAtom — распознавание текста")
        self.setWindowIcon(icons.app_icon())
        g = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1440, int(g.width() * 0.92)), min(900, int(g.height() * 0.9)))
        self.setAcceptDrops(True)
        self._build()
        if g.width() < 1500:
            # ноутбуки 1366×768: подписи остаются только у главных кнопок
            for b in self._compact:
                b.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._update_actions()
        self._update_lang_button()

    # ================================================================== UI
    def ic(self, name):
        return icons.icon(name, self.colors["icon"])

    def _tool(self, text, icon, slot=None, tip="", shortcut=None, checkable=False):
        b = QToolButton()
        b.setText(text)
        b.setIcon(self.ic(icon))
        b.setIconSize(QSize(20, 20))
        b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if text else Qt.ToolButtonIconOnly)
        b.setCheckable(checkable)
        b.setCursor(Qt.PointingHandCursor)
        if tip or shortcut:
            b.setToolTip(tip + (("  (%s)" % shortcut) if shortcut else ""))
        if slot:
            (b.toggled if checkable else b.clicked).connect(slot)
        if shortcut:
            QShortcut(QKeySequence(shortcut), self, activated=b.click)
        return b

    def _sep(self):
        f = QFrame()
        f.setObjectName("Sep")
        return f

    def _small(self, icon, tip, slot, checkable=False):
        b = QToolButton()
        b.setObjectName("SmallTool")
        b.setIcon(self.ic(icon))
        b.setIconSize(QSize(18, 18))
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setCursor(Qt.PointingHandCursor)
        (b.toggled if checkable else b.clicked).connect(slot)
        return b

    def _build(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- верхняя панель
        top = QWidget()
        top.setObjectName("TopBar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(12, 8, 12, 8)
        tl.setSpacing(4)
        logo = QLabel()
        logo.setPixmap(icons.app_icon().pixmap(30, 30))
        tl.addWidget(logo)
        brand = QVBoxLayout()
        brand.setSpacing(0)
        bl = QLabel("FinePdfAtom")
        bl.setObjectName("Brand")
        bs = QLabel("  распознавание текста · офлайн")
        bs.setObjectName("BrandSub")
        brand.addWidget(bl)
        brand.addWidget(bs)
        tl.addLayout(brand)
        tl.addWidget(self._sep())
        self._compact = []
        for args in (("Открыть", "open", self.open_files, "Изображения, TIFF, PDF", "Ctrl+O"),
                     ("Сканер", "scan", self.open_scanner, "Сканировать через SANE", "Ctrl+Shift+S"),
                     ("Экран", "screen", self.capture_screen, "Распознать область экрана", "Ctrl+Shift+E"),
                     ("Буфер", "clipboard", self.paste_clipboard, "Вставить картинку из буфера", "Ctrl+Shift+V")):
            b = self._tool(*args)
            self._compact.append(b)
            tl.addWidget(b)
        tl.addWidget(self._sep())

        self.btn_rec = self._tool("Распознать", "recognize", self.recognize_all,
                                  "Распознать все нераспознанные страницы", "Ctrl+R")
        self.btn_rec.setObjectName("Primary")
        self.btn_rec.setIcon(icons.icon("recognize", self.colors["accent_text"]))
        m = QMenu(self)
        m.addAction(self.ic("recognize"), "Все страницы заново", lambda: self.recognize_all(force=True))
        m.addAction(self.ic("text"), "Только текущую страницу", self.recognize_current)
        m.addAction(self.ic("area"), "Выделенную область", self.recognize_region)
        self.btn_rec.setMenu(m)
        self.btn_rec.setPopupMode(QToolButton.MenuButtonPopup)
        tl.addWidget(self.btn_rec)
        self.btn_area = self._tool("Область", "area", self.toggle_region_mode,
                                   "Выделить мышью область для распознавания", checkable=True)
        tl.addWidget(self.btn_area)
        self._compact.append(self.btn_area)
        tl.addWidget(self._sep())

        self.btn_save = self._tool("Сохранить", "save", None, "Сохранить документ", None)
        sm = QMenu(self)
        for fmt, title in export.FORMATS.items():
            sm.addAction(title.split(" (")[0], lambda f=fmt: self.save_as(f))
        sm.addSeparator()
        self.act_keep_format = sm.addAction("Сохранять оформление (колонки, шрифты, таблицы, рисунки)")
        self.act_keep_format.setCheckable(True)
        self.act_keep_format.setChecked(self.config.get("export/mode", "formatted") == "formatted")
        self.act_keep_format.toggled.connect(
            lambda on: self.config.set("export/mode", "formatted" if on else "plain"))
        self.btn_save.setMenu(sm)
        self.btn_save.setPopupMode(QToolButton.InstantPopup)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=lambda: self.save_as("pdf"))
        tl.addWidget(self.btn_save)
        self.btn_copy = self._tool("Копировать", "copy", self.copy_all, "Скопировать текст всех страниц",
                                   "Ctrl+Shift+C")
        tl.addWidget(self.btn_copy)
        self._compact += [self.btn_save, self.btn_copy]
        tl.addStretch(1)

        self.btn_lang = self._tool("", "lang", None, "Языки распознавания")
        self.btn_lang.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.lang_menu = QMenu(self)
        self.lang_menu.aboutToShow.connect(self._fill_lang_menu)
        self.btn_lang.setMenu(self.lang_menu)
        self.btn_lang.setPopupMode(QToolButton.InstantPopup)
        tl.addWidget(self.btn_lang)
        self.mode_cb = QComboBox()
        self.mode_cb.setToolTip("Тип изображений")
        for k, t in preprocess.MODES.items():
            self.mode_cb.addItem(t, k)
        self.mode_cb.setCurrentIndex(max(0, self.mode_cb.findData(self.config.get("prep/mode", "auto"))))
        self.mode_cb.currentIndexChanged.connect(self._mode_changed)
        tl.addWidget(self.mode_cb)
        tl.addWidget(self._tool("", "batch", self.open_batch, "Пакетная обработка папки"))
        tl.addWidget(self._tool("", "settings", self.open_settings, "Настройки", "Ctrl+,"))
        more = self._tool("", "info", self.about, "О программе")
        tl.addWidget(more)
        root.addWidget(top)

        # --- тело
        body = QWidget()
        bl2 = QHBoxLayout(body)
        bl2.setContentsMargins(12, 12, 12, 12)
        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(12)
        split.setChildrenCollapsible(False)
        bl2.addWidget(split)

        # Страницы
        left = QFrame()
        left.setObjectName("Card")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(10, 10, 10, 8)
        hdr = QHBoxLayout()
        t = QLabel("Страницы")
        t.setObjectName("CardTitle")
        hdr.addWidget(t)
        self.pages_count = QLabel("")
        self.pages_count.setObjectName("Muted")
        hdr.addWidget(self.pages_count)
        hdr.addStretch(1)
        ll.addLayout(hdr)
        self.list = QListWidget()
        self.list.setObjectName("Pages")
        self.list.setIconSize(QSize(64, 84))
        self.list.setViewMode(QListWidget.ListMode)
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.list.setWordWrap(True)
        self.list.setSpacing(1)
        self.list.setTextElideMode(Qt.ElideMiddle)
        self.list.currentRowChanged.connect(self._select_row)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        ll.addWidget(self.list, 1)
        tools = QHBoxLayout()
        tools.setSpacing(2)
        tools.addWidget(self._small("up", "Выше", lambda: self.move_page(-1)))
        tools.addWidget(self._small("down", "Ниже", lambda: self.move_page(1)))
        tools.addWidget(self._small("rotate_left", "Повернуть влево", lambda: self.rotate_pages(1)))
        tools.addWidget(self._small("rotate_right", "Повернуть вправо", lambda: self.rotate_pages(3)))
        tools.addStretch(1)
        tools.addWidget(self._small("delete", "Удалить страницы (Del)", self.delete_pages))
        QShortcut(QKeySequence.Delete, self.list, activated=self.delete_pages)
        ll.addLayout(tools)
        left.setMinimumWidth(220)
        split.addWidget(left)

        # Изображение
        mid = QFrame()
        mid.setObjectName("Card")
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(10, 10, 10, 10)
        vh = QHBoxLayout()
        tog = QWidget()
        tog.setObjectName("ViewToggle")
        tg = QHBoxLayout(tog)
        tg.setContentsMargins(0, 0, 0, 0)
        tg.setSpacing(0)
        self.view_orig = QToolButton()
        self.view_orig.setText("Исходник")
        self.view_proc = QToolButton()
        self.view_proc.setText("Результат")
        self.view_group = QButtonGroup(self)
        for i, b in enumerate((self.view_orig, self.view_proc)):
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self.view_group.addButton(b, i)
            tg.addWidget(b)
        self.view_orig.setChecked(True)
        self.view_group.buttonClicked.connect(lambda _b: self._show_image())
        vh.addWidget(tog)
        self.btn_boxes = self._small("boxes", "Показывать рамки слов", lambda on: None, checkable=True)
        self.btn_boxes.setChecked(True)
        self.btn_boxes.toggled.connect(self._toggle_boxes)
        vh.addWidget(self.btn_boxes)
        self.btn_zones = self._small("area", "Показывать зоны: текст, таблицы, рисунки и порядок чтения",
                                     lambda on: None, checkable=True)
        self.btn_zones.setChecked(self.config._b("ui/show_zones", True))
        self.btn_zones.toggled.connect(self._toggle_zones)
        vh.addWidget(self.btn_zones)
        self.info_lbl = QLabel("")
        self.info_lbl.setObjectName("Muted")
        vh.addWidget(self.info_lbl, 1)
        vh.addWidget(self._small("zoom_out", "Уменьшить (Ctrl+−)", lambda: self.view.zoom(1 / 1.25)))
        self.zoom_lbl = QLabel("—")
        self.zoom_lbl.setObjectName("Muted")
        self.zoom_lbl.setMinimumWidth(44)
        self.zoom_lbl.setAlignment(Qt.AlignCenter)
        vh.addWidget(self.zoom_lbl)
        vh.addWidget(self._small("zoom_in", "Увеличить (Ctrl++)", lambda: self.view.zoom(1.25)))
        vh.addWidget(self._small("fit", "По размеру окна (Ctrl+0)", lambda: self.view.fit()))
        ml.addLayout(vh)
        self.stack = QStackedWidget()
        self.view = ImageView(self.colors)
        self.view.zoomChanged.connect(lambda z: self.zoom_lbl.setText("%d%%" % round(z * 100)))
        self.view.wordClicked.connect(self._word_clicked)
        self.view.regionSelected.connect(self._region_selected)
        self.view.filesDropped.connect(self.add_paths)
        self.stack.addWidget(self._empty_state())
        self.stack.addWidget(self.view)
        ml.addWidget(self.stack, 1)
        QShortcut(QKeySequence("Ctrl+="), self, activated=lambda: self.view.zoom(1.25))
        QShortcut(QKeySequence("Ctrl++"), self, activated=lambda: self.view.zoom(1.25))
        QShortcut(QKeySequence("Ctrl+-"), self, activated=lambda: self.view.zoom(1 / 1.25))
        QShortcut(QKeySequence("Ctrl+0"), self, activated=lambda: self.view.fit())
        split.addWidget(mid)

        # Текст
        right = QFrame()
        right.setObjectName("Card")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(10, 10, 10, 10)
        th = QHBoxLayout()
        t = QLabel("Текст")
        t.setObjectName("CardTitle")
        th.addWidget(t)
        self.chip_conf = QLabel("")
        self.chip_time = QLabel("")
        for c in (self.chip_conf, self.chip_time):
            c.hide()
            c.setContentsMargins(9, 2, 9, 2)
            c.setAlignment(Qt.AlignCenter)
            th.addWidget(c)
        th.addStretch(1)
        self.btn_low = QPushButton("")
        self.btn_low.setCursor(Qt.PointingHandCursor)
        self.btn_low.setToolTip("Перейти к следующему сомнительному слову (F3)")
        self.btn_low.clicked.connect(lambda: self.text.next_low_conf())
        self.btn_low.hide()
        QShortcut(QKeySequence("F3"), self, activated=lambda: self.text.next_low_conf())
        th.addWidget(self.btn_low)
        th.addWidget(self._small("copy", "Скопировать текст страницы", self.copy_page))
        rl.addLayout(th)
        self.notes_lbl = QLabel("")
        self.notes_lbl.setObjectName("Muted")
        self.notes_lbl.setWordWrap(True)
        rl.addWidget(self.notes_lbl)
        self.text = TextPanel(self.colors)
        self.text.wordActivated.connect(self._text_word)
        self.text.edited.connect(self._text_edited)
        rl.addWidget(self.text, 1)
        split.addWidget(right)
        split.setSizes([240, 700, 480])
        split.setStretchFactor(1, 3)
        split.setStretchFactor(2, 2)
        root.addWidget(body, 1)
        self.setCentralWidget(central)

        # --- строка состояния
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.status_lbl = QLabel("Готово к работе · Tesseract %s" % self._tess_version())
        self.status_lbl.setContentsMargins(10, 0, 0, 0)
        sb.addWidget(self.status_lbl, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(220)
        self.progress.hide()
        sb.addPermanentWidget(self.progress)
        self.btn_stop = QPushButton("Остановить")
        self.btn_stop.setIcon(self.ic("stop"))
        self.btn_stop.clicked.connect(self.stop)
        self.btn_stop.hide()
        sb.addPermanentWidget(self.btn_stop)

        QShortcut(QKeySequence.Paste, self, activated=self.paste_clipboard_if_not_editing)

    def _empty_state(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addStretch(2)
        ic = QLabel()
        ic.setPixmap(icons.app_icon().pixmap(96, 96))
        ic.setAlignment(Qt.AlignCenter)
        lay.addWidget(ic)
        t = QLabel("Перетащите сюда сканы, фото или PDF")
        t.setObjectName("EmptyTitle")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        s = QLabel("Русский и английский текст распознаются полностью без интернета.\n"
                   "Страницы сами повернутся и выровняются, тени исчезнут.")
        s.setObjectName("Empty")
        s.setAlignment(Qt.AlignCenter)
        lay.addWidget(s)
        row = QHBoxLayout()
        row.addStretch(1)
        for text, icon, slot in (("Открыть файлы", "open", self.open_files), ("Сканировать", "scan", self.open_scanner),
                                 ("Область экрана", "screen", self.capture_screen),
                                 ("Из буфера", "clipboard", self.paste_clipboard)):
            b = QPushButton(self.ic(icon), "  " + text)
            b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(36)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addSpacing(12)
        lay.addLayout(row)
        lay.addStretch(3)
        w.setAcceptDrops(False)
        return w

    def _tess_version(self):
        try:
            return tess.version()
        except Exception:  # noqa: BLE001
            return "не найден"

    # ============================================================ языки/режим
    def _fill_lang_menu(self):
        self.lang_menu.clear()
        chosen = set(self.config.get("ocr/langs", "rus+eng").split("+"))
        for code in tess.available_languages():
            a = self.lang_menu.addAction(lang_title(code))
            a.setCheckable(True)
            a.setChecked(code in chosen)
            a.toggled.connect(lambda on, c=code: self._toggle_lang(c, on))
        self.lang_menu.addSeparator()
        self.lang_menu.addAction("Добавить язык…", self.open_settings)

    def _toggle_lang(self, code, on):
        langs = [l for l in self.config.get("ocr/langs", "rus+eng").split("+") if l]
        if on and code not in langs:
            langs.append(code)
        if not on and code in langs and len(langs) > 1:
            langs.remove(code)
        self.config.set("ocr/langs", "+".join(langs))
        self._settings_changed()

    def _update_lang_button(self):
        langs = self.config.get("ocr/langs", "rus+eng").split("+")
        self.btn_lang.setText(" + ".join(l.upper()[:3] for l in langs))

    def _mode_changed(self):
        self.config.set("prep/mode", self.mode_cb.currentData())
        self._settings_changed()

    def _settings_changed(self):
        self._update_lang_button()
        if self.recognizer is not None and self.worker is None:
            self.recognizer.close()
            self.recognizer = None

    def _get_recognizer(self):
        if self.recognizer is None:
            self.recognizer = ocr.Recognizer(self.config.ocr_settings())
        return self.recognizer

    # ============================================================== страницы
    def add_paths(self, paths):
        paths = [p for p in paths if p]
        if not paths:
            return
        self._status("Открываю файлы…")
        w = LoadWorker(paths, self)
        w.loaded.connect(self._loaded)
        w.finished.connect(lambda: self._loaders.remove(w) if w in self._loaders else None)
        self._loaders.append(w)
        w.start()

    def _loaded(self, refs, errors):
        if errors:
            QMessageBox.warning(self, "Открытие файлов", "Не удалось открыть:\n" + "\n".join(errors[:10]))
        if not refs:
            self._status("Нет поддерживаемых файлов")
            return
        self.add_refs(refs)
        self._status("Добавлено страниц: %d" % len(refs))

    def add_refs(self, refs, select=True, auto_recognize=None):
        new = []
        for r in refs:
            p = Page(r)
            self.pages.append(p)
            it = QListWidgetItem(r.label)
            it.setData(Qt.UserRole, id(p))
            ph = QPixmap(THUMB, int(THUMB * 1.3))
            ph.fill(QColor(self.colors["panel2"]))
            it.setIcon(QIcon(ph))
            self.list.addItem(it)
            new.append(p)
        self._refresh_items(new)
        tw = ThumbWorker(new, self)
        tw.ready.connect(self._thumb_ready)
        tw.finished.connect(lambda: self._thumbw.remove(tw) if tw in self._thumbw else None)
        self._thumbw.append(tw)
        tw.start()
        self.stack.setCurrentIndex(1)
        if select:
            self.list.setCurrentRow(len(self.pages) - len(new))
        self._update_actions()
        if auto_recognize is None:
            auto_recognize = self.config._b("ui/auto_recognize", True)
        if auto_recognize:
            self._recognize(new)

    def _thumb_ready(self, page, img):
        if page not in self.pages:
            return
        page.thumb = QPixmap.fromImage(img)
        self._refresh_items([page])

    def _item(self, page):
        try:
            return self.list.item(self.pages.index(page))
        except ValueError:
            return None

    def _refresh_items(self, pages):
        for p in pages:
            it = self._item(p)
            if it is None:
                continue
            n = self.pages.index(p) + 1
            if p.state == ST_DONE:
                badge = "✔ %d%%" % round(p.result.mean_conf) if not p.result.from_text_layer else "✔ текст"
            elif p.state == ST_QUEUED:
                badge = "⏳ в работе"
            elif p.state == ST_ERROR:
                badge = "✘ ошибка"
            else:
                badge = ""
            label = p.ref.label.replace(" — ", "\n")
            first, _, rest = label.partition("\n")
            if len(first) > 22:
                first = first[:10] + "…" + first[-10:]
            label = first + ("\n" + rest if rest else "")
            it.setText("%d. %s%s" % (n, label, ("\n" + badge) if badge else ""))
            it.setToolTip(p.ref.label + ("\n" + p.error if p.error else ""))
            if p.thumb is not None:
                it.setIcon(QIcon(self._decorate(p.thumb, p.state)))
        self.pages_count.setText(str(len(self.pages)) if self.pages else "")

    def _decorate(self, pm, state):
        out = QPixmap(pm)
        if state in (ST_DONE, ST_ERROR, ST_QUEUED):
            p = QPainter(out)
            p.setRenderHint(QPainter.Antialiasing)
            col = {ST_DONE: self.colors["ok"], ST_ERROR: "#e5484d", ST_QUEUED: self.colors["accent"]}[state]
            r = max(6, out.width() // 22)
            p.setBrush(QColor(col))
            p.setPen(Qt.NoPen)
            p.drawEllipse(out.width() - 2 * r - 4, 4, 2 * r, 2 * r)
            p.end()
        return out

    def selected_pages(self):
        rows = sorted({i.row() for i in self.list.selectedIndexes()})
        return [self.pages[r] for r in rows] or ([self.current] if self.current else [])

    def _select_row(self, row):
        if 0 <= row < len(self.pages):
            self.current = self.pages[row]
        else:
            self.current = None
        self.view.set_region_mode(False)
        self.btn_area.setChecked(False)
        if self.current and self.current.state == ST_DONE:
            self.view_proc.setChecked(True)
        else:
            self.view_orig.setChecked(True)
        self._show_image()
        self._show_text()
        self._update_actions()

    def _original(self, page):
        key = id(page)
        if key in self._orig_cache:
            self._orig_cache.move_to_end(key)
            return self._orig_cache[key]
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            img, _dpi = page.ref.load()
        finally:
            QApplication.restoreOverrideCursor()
        self._orig_cache[key] = img
        while len(self._orig_cache) > 6:
            self._orig_cache.popitem(last=False)
        return img

    def _show_image(self):
        p = self.current
        if p is None:
            self.view.clear_all()
            self.info_lbl.setText("")
            if not self.pages:
                self.stack.setCurrentIndex(0)
            return
        self.stack.setCurrentIndex(1)
        proc = self.view_proc.isChecked() and p.result is not None
        try:
            if proc:
                self.view.set_image(p.result.display, p.result.data.words)
                self.view.set_zones(p.result.data, self.btn_zones.isChecked())
                h, w = p.result.display.shape[:2]
            else:
                img = self._original(p)
                self.view.set_image(img)
                h, w = img.shape[:2]
                if p.region:
                    x, y, rw, rh = p.region
                    self.view.set_region(QRectF(x, y, rw, rh))
        except Exception as e:  # noqa: BLE001
            self.view.clear_all()
            self.info_lbl.setText("Не удалось открыть: %s" % e)
            return
        self.view.set_boxes_visible(self.btn_boxes.isChecked())
        self.info_lbl.setText("  %d × %d px" % (w, h))

    def _show_text(self):
        p = self.current
        for c in (self.chip_conf, self.chip_time):
            c.hide()
        self.btn_low.hide()
        if p is None or p.result is None:
            self.text.load("")
            self.notes_lbl.setText(p.error if p and p.state == ST_ERROR else
                                   ("Страница ещё не распознана" if p else ""))
            return
        r = p.result
        self.text.set_langs(self.config.get("ocr/langs", "rus+eng"))
        if p.text is not None:
            self.text.load(p.text)
        else:
            self.text.load(r.text, r.spans, r.data.words)
        if r.from_text_layer:
            self.chip_conf.setText("готовый текст PDF")
            self.chip_conf.setObjectName("ChipOk")
        else:
            self.chip_conf.setText("уверенность %d%%" % round(r.mean_conf))
            self.chip_conf.setObjectName("ChipOk" if r.mean_conf >= 80 else "ChipWarn")
            self.chip_time.setText("%.1f с" % r.seconds)
            self.chip_time.setObjectName("Chip")
            self.chip_time.show()
        for c in (self.chip_conf, self.chip_time):
            c.style().unpolish(c)
            c.style().polish(c)
        self.chip_conf.show()
        n = self.text.low_conf_count()
        if n:
            self.btn_low.setText("Проверить слов: %d  ›" % n)
            self.btn_low.show()
        notes = "Обработка: " + r.notes if r.notes else ""
        if p.text is not None:
            notes += ("  ·  " if notes else "") + "текст изменён вручную"
        self.notes_lbl.setText(notes)

    def _toggle_zones(self, on):
        self.config.set("ui/show_zones", on)
        self.view.set_zones_visible(on)

    def _toggle_boxes(self, on):
        self.view.set_boxes_visible(on)

    def _word_clicked(self, idx):
        self.text.select_word(idx)

    def _text_word(self, idx):
        if self.view_proc.isChecked():
            self.view.highlight_word(idx)

    def _text_edited(self):
        if self.current is not None and self.current.result is not None:
            self.current.text = self.text.toPlainText()

    def move_page(self, d):
        row = self.list.currentRow()
        new = row + d
        if not (0 <= row < len(self.pages) and 0 <= new < len(self.pages)) or self.worker:
            return
        self.pages[row], self.pages[new] = self.pages[new], self.pages[row]
        it = self.list.takeItem(row)
        self.list.insertItem(new, it)
        self.list.setCurrentRow(new)
        self._refresh_items(self.pages)

    def rotate_pages(self, k):
        """k — число поворотов на 90° против часовой."""
        if self.worker:
            return
        for p in self.selected_pages():
            img, dpi = p.ref.load()
            img = np.ascontiguousarray(np.rot90(img, k))
            p.ref = sources.memory_ref(img, p.ref.label, int(dpi or 300), p.ref.hint_mode)
            p.result, p.text, p.region, p.state = None, None, None, ST_NEW
            self._orig_cache.pop(id(p), None)
            p.thumb = QPixmap.fromImage(_thumb(p.ref))
        self._refresh_items(self.pages)
        self._show_image()
        self._show_text()

    def delete_pages(self):
        if self.worker:
            return
        sel = self.selected_pages()
        if not sel:
            return
        for p in sel:
            i = self.pages.index(p)
            self.pages.pop(i)
            self.list.takeItem(i)
            self._orig_cache.pop(id(p), None)
        self._refresh_items(self.pages)
        if not self.pages:
            self.current = None
            self._show_image()
            self._show_text()
        self._update_actions()

    def _list_menu(self, pos):
        m = QMenu(self)
        m.addAction(self.ic("recognize"), "Распознать выбранные", lambda: self._recognize(self.selected_pages(), True))
        m.addAction(self.ic("rotate_left"), "Повернуть влево", lambda: self.rotate_pages(1))
        m.addAction(self.ic("rotate_right"), "Повернуть вправо", lambda: self.rotate_pages(3))
        m.addSeparator()
        m.addAction(self.ic("delete"), "Удалить", self.delete_pages)
        m.exec_(self.list.mapToGlobal(pos))

    # =============================================================== источники
    def open_files(self):
        last = self.config.get("ui/last_dir", os.path.expanduser("~"))
        files, _ = QFileDialog.getOpenFileNames(
            self, "Открыть изображения или PDF", last,
            "Изображения и PDF (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp *.pdf *.pnm *.jp2);;"
            "Все файлы (*)")
        if files:
            self.config.set("ui/last_dir", os.path.dirname(files[0]))
            self.add_paths(files)

    def open_scanner(self):
        d = ScannerDialog(self.config, self)
        d.pageScanned.connect(lambda path: self.add_refs(
            [sources.PageRef(path, 0, "image", "Скан %s" % time.strftime("%H:%M:%S"))]))
        d.exec_()

    def capture_screen(self):
        self._was_visible = self.isVisible() and not self.isMinimized()
        self.hide()
        QTimer.singleShot(350, self._start_selector)

    def _start_selector(self):
        self._sel = ScreenSelector(self.colors["accent"])
        self._sel.selected.connect(self._screen_selected)
        self._sel.cancelled.connect(self._restore)
        self._sel.start()

    def _restore(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _screen_selected(self, img: QImage):
        self._restore()
        arr = qimage_to_rgb(img)
        ref = sources.memory_ref(arr, "Снимок экрана %s" % time.strftime("%H:%M:%S"), 96, "screen")
        self._copy_after = True
        self.add_refs([ref], auto_recognize=True)

    def paste_clipboard_if_not_editing(self):
        if self.text.hasFocus():
            self.text.paste()
        else:
            self.paste_clipboard()

    def paste_clipboard(self):
        cb = QApplication.clipboard()
        md = cb.mimeData()
        if md is not None and md.hasUrls():
            paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            if paths:
                self.add_paths(paths)
                return
        img = cb.image()
        if img.isNull():
            self._status("В буфере обмена нет изображения")
            return
        arr = qimage_to_rgb(img)
        self.add_refs([sources.memory_ref(arr, "Из буфера %s" % time.strftime("%H:%M:%S"), 96)])

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    # ============================================================ распознавание
    def toggle_region_mode(self, on):
        if on:
            self.view_orig.setChecked(True)
            self._show_image()
            self._status("Выделите мышью область на изображении")
        self.view.set_region_mode(on)

    def _region_selected(self, rect):
        if self.current is None:
            return
        self.current.region = (rect.x(), rect.y(), rect.width(), rect.height())
        self.btn_area.setChecked(False)
        self.recognize_region()

    def recognize_all(self, force=False):
        pages = [p for p in self.pages if force or p.state in (ST_NEW, ST_ERROR)]
        if not pages and self.pages and not force:
            self._status("Все страницы уже распознаны. Для повтора — меню кнопки «Распознать».")
            return
        self._recognize(pages, True)

    def recognize_current(self):
        if self.current:
            self.current.region = None
            self._recognize([self.current], True)

    def recognize_region(self):
        if self.current is None:
            return
        if not self.current.region:
            self.btn_area.setChecked(True)
            return
        self._recognize([self.current], True, use_region=True)

    def _recognize(self, pages, force=False, use_region=False):
        if not pages:
            return
        if self.worker is not None:
            self._status("Распознавание уже идёт…")
            return
        langs = self.config.get("ocr/langs", "rus+eng").split("+")
        missing = [l for l in langs if l not in tess.available_languages()]
        if missing:
            QMessageBox.warning(self, "Языки", "Нет моделей: %s" % ", ".join(missing))
            return
        self.jobs_pages = list(pages)
        regions = [p.region if use_region else None for p in pages]
        for p in pages:
            p.state = ST_QUEUED
            p.error = ""
        self._refresh_items(pages)
        self._t0 = time.time()
        self.worker = OcrWorker(self._get_recognizer(), [p.ref for p in pages], regions, self)
        self.worker.pageDone.connect(self._page_done)
        self.worker.progress.connect(self._progress)
        self.worker.failed.connect(lambda tb: QMessageBox.critical(self, "Ошибка", tb))
        self.worker.finished.connect(self._ocr_finished)
        self.progress.setRange(0, len(pages))
        self.progress.setValue(0)
        self.progress.show()
        self.btn_stop.show()
        self._status("Распознавание: 0 из %d…" % len(pages))
        self._update_actions()
        self.worker.start()

    def _page_done(self, i, res):
        p = self.jobs_pages[i]
        if isinstance(res, ocr.Cancelled):
            p.state = ST_NEW if p.result is None else ST_DONE
        elif isinstance(res, Exception):
            p.state = ST_ERROR
            p.error = str(res)
        else:
            p.result = res
            p.text = None
            p.state = ST_DONE
        self._refresh_items([p])
        if p is self.current:
            if p.state == ST_DONE:
                self.view_proc.setChecked(True)
            self._show_image()
            self._show_text()

    def _progress(self, done, total):
        self.progress.setValue(done)
        self._status("Распознавание: %d из %d…" % (done, total))

    def _ocr_finished(self):
        n = len(self.jobs_pages)
        ok = sum(1 for p in self.jobs_pages if p.state == ST_DONE)
        self.worker = None
        self.progress.hide()
        self.btn_stop.hide()
        self._update_actions()
        dt = time.time() - self._t0
        self._status("Распознано %d из %d стр. за %.1f с" % (ok, n, dt))
        if getattr(self, "_copy_after", False):
            self._copy_after = False
            if self.current and self.current.result:
                QApplication.clipboard().setText(self.current.final_text())
                self._status("Текст с экрана распознан и скопирован в буфер обмена")
        if self.recognizer is not None and self._settings_dirty():
            self.recognizer.close()
            self.recognizer = None

    def _settings_dirty(self):
        cur = self.config.ocr_settings()
        return self.recognizer is not None and cur != self.recognizer.settings

    def stop(self):
        if self.worker is not None:
            self.worker.cancel()
            self._status("Останавливаю…")

    # ================================================================== вывод
    def _done_pages(self):
        return [p for p in self.pages if p.result is not None]

    def copy_all(self):
        pages = self._done_pages()
        if not pages:
            self._status("Нет распознанного текста")
            return
        QApplication.clipboard().setText("\n\n".join(p.final_text() for p in pages))
        self._status("Текст %d стр. скопирован в буфер обмена" % len(pages))

    def copy_page(self):
        if self.current and self.current.result:
            QApplication.clipboard().setText(self.current.final_text())
            self._status("Текст страницы скопирован")

    def save_as(self, fmt):
        pages = self._done_pages()
        if not pages:
            QMessageBox.information(self, "Сохранение", "Сначала распознайте страницы.")
            return
        skipped = len(self.pages) - len(pages)
        if skipped:
            r = QMessageBox.question(self, "Сохранение",
                                     "Не распознано страниц: %d. Сохранить только распознанные?" % skipped)
            if r != QMessageBox.Yes:
                return
        base = self.pages[0].ref.path or os.path.join(os.path.expanduser("~"), "Документ")
        default = os.path.join(self.config.get("ui/save_dir", os.path.dirname(base)),
                               os.path.splitext(os.path.basename(base))[0] + ("_ocr" if fmt == "pdf" else "") + "." + fmt)
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить как", default, export.FORMATS[fmt])
        if not path:
            return
        if not path.lower().endswith("." + fmt):
            path += "." + fmt
        self.config.set("ui/save_dir", os.path.dirname(path))
        items = [export.ExportPage(p.result, p.final_text(), p.ref if p.ref.kind == "pdf" and not p.region else None)
                 for p in pages]
        self._export = ExportWorker(items, path, fmt, self.config.pdf_quality, self,
                                    mode=self.config.get("export/mode", "formatted"))
        self._export.progress.connect(lambda a, b: (self.progress.setRange(0, b), self.progress.setValue(a)))
        self._export.finishedOk.connect(self._saved)
        self._export.failed.connect(lambda e: (self.progress.hide(),
                                               QMessageBox.critical(self, "Сохранение", "Ошибка: %s" % e)))
        self.progress.show()
        self._status("Сохраняю…")
        self._export.start()

    def _saved(self, path):
        self.progress.hide()
        size = os.path.getsize(path)
        self._status("Сохранено: %s (%s)" % (path, _human(size)))

    # =============================================================== прочее
    def open_settings(self):
        d = SettingsDialog(self.config, self)
        old_theme = self.config.theme
        if d.exec_():
            self.mode_cb.blockSignals(True)
            self.mode_cb.setCurrentIndex(max(0, self.mode_cb.findData(self.config.get("prep/mode", "auto"))))
            self.mode_cb.blockSignals(False)
            self._settings_changed()
            if self.config.theme != old_theme:
                QMessageBox.information(self, "Тема", "Новая тема применится после перезапуска программы.")
            # пересобрать текст по новым правилам строк/переносов
            st = self.config.ocr_settings()
            for p in self.pages:
                if p.result is not None and p.text is None and not p.result.from_text_layer:
                    p.result.relayout(st.keep_lines, st.dehyphenate)
            self._show_text()

    def open_batch(self):
        BatchDialog(self.config, self).exec_()

    def about(self):
        AboutDialog(self).exec_()

    def _status(self, text):
        self.status_lbl.setText(text)

    def _update_actions(self):
        has = bool(self.pages)
        busy = self.worker is not None
        self.btn_rec.setEnabled(has and not busy)
        self.btn_area.setEnabled(has and not busy)
        done = any(p.result for p in self.pages)
        self.btn_save.setEnabled(done)
        self.btn_copy.setEnabled(done)

    def closeEvent(self, e):
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait(5000)
        for w in self._thumbw:
            w.requestInterruption()
            w.wait(2000)
        if self.recognizer:
            self.recognizer.close()
        super().closeEvent(e)


def _human(n):
    for u in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024:
            return "%.0f %s" % (n, u)
        n /= 1024.0
    return "%.1f ТБ" % n
