"""Диалоги: настройки, сканер, пакетная обработка, о программе."""
from __future__ import annotations

import os
import shutil

from PyQt5.QtCore import QThread, Qt, pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                             QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                             QSpinBox, QVBoxLayout)

from .. import __version__, export, ocr, paths, preprocess, sources, tess
from .workers import ListScannersWorker, ScanWorker

LANG_NAMES = {
    "rus": "Русский", "eng": "Английский", "ukr": "Украинский", "bel": "Белорусский",
    "deu": "Немецкий", "fra": "Французский", "kaz": "Казахский", "tat": "Татарский",
    "chi_sim": "Китайский (упр.)", "spa": "Испанский", "ita": "Итальянский", "pol": "Польский",
    "bak": "Башкирский", "chv": "Чувашский", "kir": "Киргизский", "uzb": "Узбекский",
    "uzb_cyrl": "Узбекский (кир.)", "aze": "Азербайджанский", "hye": "Армянский", "kat": "Грузинский",
    "lat": "Латынь", "ell": "Греческий", "tur": "Турецкий", "ara": "Арабский", "heb": "Иврит",
    "jpn": "Японский", "kor": "Корейский", "srp": "Сербский", "bul": "Болгарский", "mkd": "Македонский",
}


def lang_title(code):
    return "%s (%s)" % (LANG_NAMES.get(code, code), code)


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Настройки распознавания")
        self.setMinimumWidth(620)
        st = config.ocr_settings()
        root = QVBoxLayout(self)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        root.addLayout(grid)

        # Языки
        g = QGroupBox("Языки документа")
        v = QVBoxLayout(g)
        self.langs = QListWidget()
        self.langs.setMinimumHeight(150)
        chosen = set(st.langs.split("+"))
        for code in tess.available_languages():
            it = QListWidgetItem(lang_title(code))
            it.setData(Qt.UserRole, code)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if code in chosen else Qt.Unchecked)
            self.langs.addItem(it)
        v.addWidget(self.langs)
        add = QPushButton("Добавить язык (.traineddata)…")
        add.clicked.connect(self._add_lang)
        v.addWidget(add)
        hint = QLabel("Модели: github.com/tesseract-ocr/tessdata_best.\nЧем меньше языков — тем быстрее и точнее.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        v.addWidget(hint)
        grid.addWidget(g, 0, 0, 2, 1)

        # Подготовка
        g = QGroupBox("Подготовка изображения")
        f = QFormLayout(g)
        self.mode = QComboBox()
        for k, t in preprocess.MODES.items():
            self.mode.addItem(t, k)
        self.mode.setCurrentIndex(max(0, self.mode.findData(st.prep.mode)))
        f.addRow("Тип изображений:", self.mode)
        self.rotate = QCheckBox("Поворачивать страницы (90°/180°/270°)")
        self.rotate.setChecked(st.prep.auto_rotate)
        self.deskew = QCheckBox("Исправлять перекос")
        self.deskew.setChecked(st.prep.deskew)
        self.rescale = QCheckBox("Подбирать масштаб под размер шрифта")
        self.rescale.setChecked(st.prep.rescale)
        self.clean = QCheckBox("Убирать тени и неровный фон")
        self.clean.setChecked(st.prep.clean_background)
        self.denoise = QCheckBox("Сильное шумоподавление (медленнее)")
        self.denoise.setChecked(st.prep.denoise)
        for w in (self.rotate, self.deskew, self.rescale, self.clean, self.denoise):
            f.addRow(w)
        grid.addWidget(g, 0, 1)

        # Текст
        g = QGroupBox("Текст и документ")
        f = QFormLayout(g)
        self.layout_cb = QComboBox()
        for k, (_psm, t) in ocr.PSM_CHOICES.items():
            self.layout_cb.addItem(t, k)
        self.layout_cb.setCurrentIndex(max(0, self.layout_cb.findData(st.layout)))
        f.addRow("Разметка:", self.layout_cb)
        self.keep_lines = QCheckBox("Сохранять разрывы строк")
        self.keep_lines.setChecked(st.keep_lines)
        self.dehyph = QCheckBox("Склеивать слова с переносом")
        self.dehyph.setChecked(st.dehyphenate)
        self.pdf_text = QCheckBox("Брать готовый текст из PDF, если он есть")
        self.pdf_text.setChecked(st.use_pdf_text)
        self.spell = QCheckBox("Проверять орфографию и исправлять явные ошибки")
        self.spell.setChecked(st.spellcheck)
        for w in (self.keep_lines, self.dehyph, self.pdf_text, self.spell):
            f.addRow(w)
        self.quality = QComboBox()
        for k, t in export.PDF_QUALITY.items():
            self.quality.addItem(t, k)
        self.quality.setCurrentIndex(max(0, self.quality.findData(config.pdf_quality)))
        f.addRow("Картинка в PDF:", self.quality)
        self.threads = QSpinBox()
        self.threads.setRange(0, 32)
        self.threads.setSpecialValueText("авто (%d)" % ocr.Settings().worker_count())
        self.threads.setValue(st.threads)
        f.addRow("Потоков:", self.threads)
        self.theme = QComboBox()
        self.theme.addItem("Светлая", "light")
        self.theme.addItem("Тёмная", "dark")
        self.theme.setCurrentIndex(max(0, self.theme.findData(config.theme)))
        f.addRow("Тема:", self.theme)
        grid.addWidget(g, 1, 1)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("Сохранить")
        bb.button(QDialogButtonBox.Cancel).setText("Отмена")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

    def _add_lang(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Модель языка Tesseract", os.path.expanduser("~"),
                                                "Модели Tesseract (*.traineddata)")
        if not files:
            return
        dst = paths.tessdata_dir()
        for fp in files:
            try:
                shutil.copy2(fp, os.path.join(dst, os.path.basename(fp)))
            except OSError as e:
                QMessageBox.warning(self, "Языки", "Не удалось скопировать %s: %s" % (fp, e))
                continue
            code = os.path.basename(fp)[:-len(".traineddata")]
            if not self.langs.findItems(lang_title(code), Qt.MatchExactly):
                it = QListWidgetItem(lang_title(code))
                it.setData(Qt.UserRole, code)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked)
                self.langs.addItem(it)

    def _save(self):
        langs = [self.langs.item(i).data(Qt.UserRole) for i in range(self.langs.count())
                 if self.langs.item(i).checkState() == Qt.Checked]
        if not langs:
            QMessageBox.warning(self, "Языки", "Отметьте хотя бы один язык.")
            return
        st = ocr.Settings(
            langs="+".join(langs), layout=self.layout_cb.currentData(),
            prep=preprocess.Options(mode=self.mode.currentData(), auto_rotate=self.rotate.isChecked(),
                                    deskew=self.deskew.isChecked(), rescale=self.rescale.isChecked(),
                                    clean_background=self.clean.isChecked(), denoise=self.denoise.isChecked()),
            use_pdf_text=self.pdf_text.isChecked(), keep_lines=self.keep_lines.isChecked(),
            dehyphenate=self.dehyph.isChecked(), threads=self.threads.value(),
            spellcheck=self.spell.isChecked())
        self.config.save_ocr_settings(st)
        self.config.pdf_quality = self.quality.currentData()
        self.config.theme = self.theme.currentData()
        self.accept()


class ScannerDialog(QDialog):
    """Сканирование через SANE. Каждый отсканированный лист сразу добавляется в документ."""
    pageScanned = pyqtSignal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Сканирование")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.device = QComboBox()
        self.device.setMinimumWidth(320)
        row = QHBoxLayout()
        row.addWidget(self.device, 1)
        self.refresh = QPushButton("Найти")
        self.refresh.clicked.connect(self._find)
        row.addWidget(self.refresh)
        f.addRow("Сканер:", row)
        self.dpi = QComboBox()
        for d in (150, 200, 300, 400, 600):
            self.dpi.addItem("%d dpi" % d, d)
        self.dpi.setCurrentIndex(self.dpi.findData(int(config.get("scan/dpi", 300))))
        f.addRow("Разрешение:", self.dpi)
        self.mode = QComboBox()
        self.mode.addItem("Оттенки серого (рекомендуется)", "Gray")
        self.mode.addItem("Цветной", "Color")
        self.mode.addItem("Чёрно-белый", "Lineart")
        self.mode.setCurrentIndex(max(0, self.mode.findData(config.get("scan/mode", "Gray"))))
        f.addRow("Режим:", self.mode)
        lay.addLayout(f)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.hide()
        lay.addWidget(self.bar)
        btns = QHBoxLayout()
        btns.addStretch(1)
        self.scan_btn = QPushButton("Сканировать лист")
        self.scan_btn.setObjectName("Primary")
        self.scan_btn.clicked.connect(self._scan)
        close = QPushButton("Готово")
        close.clicked.connect(self.accept)
        btns.addWidget(self.scan_btn)
        btns.addWidget(close)
        lay.addLayout(btns)
        self.count = 0
        self._w = None
        if not sources.scanimage_path():
            self.status.setText("Утилита scanimage не найдена. Установите пакет sane-utils с диска Astra Linux "
                                "(sudo apt install sane-utils) и драйвер сканера.")
            self.scan_btn.setEnabled(False)
            self.refresh.setEnabled(False)
        else:
            last = config.get("scan/device", "")
            if last:
                self.device.addItem(last, last)
            self._find()

    def _busy(self, on, text=""):
        self.bar.setVisible(on)
        self.scan_btn.setEnabled(not on and self.device.count() > 0)
        self.refresh.setEnabled(not on)
        self.status.setText(text)

    def _find(self):
        self._busy(True, "Поиск сканеров…")
        self._w = ListScannersWorker(self)
        self._w.found.connect(self._found)
        self._w.failed.connect(lambda e: self._busy(False, "Ошибка: " + e))
        self._w.start()

    def _found(self, devs):
        cur = self.device.currentData()
        self.device.clear()
        for dev, desc in devs:
            self.device.addItem(desc or dev, dev)
        if cur:
            i = self.device.findData(cur)
            if i >= 0:
                self.device.setCurrentIndex(i)
        self._busy(False, "Найдено сканеров: %d" % len(devs) if devs else
                   "Сканеры не найдены. Проверьте подключение и драйвер (scanimage -L).")

    def _scan(self):
        dev = self.device.currentData()
        if not dev:
            return
        self.config.set("scan/device", dev)
        self.config.set("scan/dpi", self.dpi.currentData())
        self.config.set("scan/mode", self.mode.currentData())
        self._busy(True, "Сканирование…")
        self._w = ScanWorker(dev, self.dpi.currentData(), self.mode.currentData(), self)
        self._w.scanned.connect(self._scanned)
        self._w.failed.connect(lambda e: self._busy(False, "Ошибка сканирования: " + e))
        self._w.start()

    def _scanned(self, path):
        self.count += 1
        self._busy(False, "Отсканировано листов: %d. Положите следующий лист или нажмите «Готово»." % self.count)
        self.scan_btn.setText("Сканировать следующий")
        self.pageScanned.emit(path)


class BatchWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    done = pyqtSignal()

    def __init__(self, settings, files, outdir, fmts, quality, parent=None):
        super().__init__(parent)
        self.settings, self.files, self.outdir, self.fmts, self.quality = settings, files, outdir, fmts, quality
        self.mode = "formatted"
        self.rec = None
        self._stop = False

    def run(self):
        self.rec = ocr.Recognizer(self.settings)
        try:
            os.makedirs(self.outdir, exist_ok=True)
            for n, f in enumerate(self.files):
                if self._stop:
                    break
                self.progress.emit(n, len(self.files))
                try:
                    refs = sources.page_refs(f)
                    res = self.rec.map(refs)
                    errs = [r for r in res if isinstance(r, Exception)]
                    if self._stop:
                        break
                    pages = [export.ExportPage(r, r.text, ref) for r, ref in zip(res, refs)
                             if not isinstance(r, Exception)]
                    base = os.path.join(self.outdir, os.path.splitext(os.path.basename(f))[0])
                    for fm in self.fmts:
                        out = base + "." + fm
                        if os.path.abspath(out) == os.path.abspath(f):
                            out = base + ".ocr." + fm
                        export.save(pages, out, fm, self.quality, mode=self.mode)
                    conf = sum(p.result.mean_conf for p in pages) / max(1, len(pages))
                    self.log.emit("✔ %s — страниц %d, уверенность %.0f%%%s" % (
                        os.path.basename(f), len(pages), conf, (", ошибок %d" % len(errs)) if errs else ""))
                except Exception as e:  # noqa: BLE001
                    self.log.emit("✘ %s — %s" % (os.path.basename(f), e))
            self.progress.emit(len(self.files), len(self.files))
        finally:
            self.rec.close()
            self.done.emit()

    def stop(self):
        self._stop = True
        if self.rec:
            self.rec.cancel()


class BatchDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Пакетная обработка папки")
        self.setMinimumSize(620, 480)
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.src = QLineEdit(config.get("batch/src", ""))
        self.dst = QLineEdit(config.get("batch/dst", ""))
        for le, title in ((self.src, "Папка с файлами"), (self.dst, "Папка для результатов")):
            row = QHBoxLayout()
            row.addWidget(le, 1)
            b = QPushButton("Обзор…")
            b.clicked.connect(lambda _=False, le=le, t=title: self._pick(le, t))
            row.addWidget(b)
            f.addRow(title + ":", row)
        fm_row = QHBoxLayout()
        self.fmt_boxes = {}
        saved = (config.get("batch/formats", "pdf") or "pdf").split(",")
        for k in ("pdf", "docx", "odt", "xlsx", "html", "txt"):
            cb = QCheckBox(k.upper())
            cb.setChecked(k in saved)
            self.fmt_boxes[k] = cb
            fm_row.addWidget(cb)
        fm_row.addStretch(1)
        f.addRow("Сохранить как:", fm_row)
        lay.addLayout(f)
        hint = QLabel("Каждый файл (изображение, многостраничный TIFF или PDF) превращается в отдельный "
                      "документ. Используются текущие настройки распознавания.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.bar = QProgressBar()
        lay.addWidget(self.bar)
        self.logw = QPlainTextEdit()
        self.logw.setReadOnly(True)
        lay.addWidget(self.logw, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.start_btn = QPushButton("Начать")
        self.start_btn.setObjectName("Primary")
        self.start_btn.clicked.connect(self._start)
        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self._close)
        row.addWidget(self.start_btn)
        row.addWidget(self.close_btn)
        lay.addLayout(row)
        self.w = None

    def _pick(self, le, title):
        d = QFileDialog.getExistingDirectory(self, title, le.text() or os.path.expanduser("~"))
        if d:
            le.setText(d)

    def _start(self):
        if self.w is not None:
            self.w.stop()
            self.start_btn.setEnabled(False)
            return
        src, dst = self.src.text().strip(), self.dst.text().strip()
        fmts = [k for k, cb in self.fmt_boxes.items() if cb.isChecked()]
        if not os.path.isdir(src) or not dst or not fmts:
            QMessageBox.warning(self, "Пакетная обработка", "Укажите папки и хотя бы один формат.")
            return
        files = sources.expand_paths([src])
        if not files:
            QMessageBox.information(self, "Пакетная обработка", "В папке нет изображений или PDF.")
            return
        self.config.set("batch/src", src)
        self.config.set("batch/dst", dst)
        self.config.set("batch/formats", ",".join(fmts))
        self.logw.appendPlainText("Файлов: %d" % len(files))
        self.w = BatchWorker(self.config.ocr_settings(), files, dst, fmts, self.config.pdf_quality, self)
        self.w.mode = self.config.get("export/mode", "formatted")
        self.w.log.connect(self.logw.appendPlainText)
        self.w.progress.connect(lambda a, b: (self.bar.setMaximum(b), self.bar.setValue(a)))
        self.w.done.connect(self._done)
        self.start_btn.setText("Остановить")
        self.w.start()

    def _done(self):
        self.logw.appendPlainText("Готово.")
        self.w = None
        self.start_btn.setText("Начать")
        self.start_btn.setEnabled(True)

    def _close(self):
        if self.w is not None:
            self.w.stop()
            self.w.wait(10000)
        self.accept()

    def closeEvent(self, e):
        if self.w is not None:
            self.w.stop()
            self.w.wait(10000)
        super().closeEvent(e)


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("О программе")
        lay = QVBoxLayout(self)
        try:
            tv = tess.version()
        except Exception:  # noqa: BLE001
            tv = "?"
        from PyQt5.QtCore import QT_VERSION_STR
        t = QLabel(
            "<h2>FinePdfAtom %s</h2>"
            "<p>Распознавание текста без интернета для Astra Linux SE 1.6 и новее.</p>"
            "<p><b>Движок:</b> Tesseract %s (LSTM, модели tessdata_best)<br>"
            "<b>Интерфейс:</b> Qt %s<br><b>Обработка:</b> OpenCV, PyMuPDF</p>"
            "<p>Идеи взяты из открытых проектов: gImageReader (связь текста и изображения, "
            "области распознавания), OCRmyPDF (выравнивание, поворот страниц, PDF с текстовым слоем), "
            "NormCap (распознавание области экрана).</p>"
            "<p style='color:gray'>Автор: Павел Дудин · github.com/atomicpel/FinePdfAtom<br>"
            "Лицензии компонентов — в /opt/finepdfatom/LICENSES.</p>" % (__version__, tv, QT_VERSION_STR))
        t.setWordWrap(True)
        t.setTextFormat(Qt.RichText)
        t.setMinimumWidth(460)
        lay.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)
