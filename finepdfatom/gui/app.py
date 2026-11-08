"""Запуск графического интерфейса и быстрых режимов (--screen, --clipboard)."""
from __future__ import annotations

import os
import sys
import time

from PyQt5.QtCore import QPoint, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QCursor, QFont, QGuiApplication
from PyQt5.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                             QPushButton, QStyleFactory, QVBoxLayout)

from .. import ocr, sources
from . import icons, theme
from .config import Config


def _clean_qt_env():
    """Системные плагины Qt (тема Fly и т.п.) собраны под другую версию Qt — не подгружаем их."""
    for k in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORMTHEME", "QT_STYLE_OVERRIDE", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        os.environ.pop(k, None)
    if not os.environ.get("QT_QPA_PLATFORM") and os.environ.get("DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"


def make_app(argv):
    _clean_qt_env()
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication([sys.argv[0]] + list(argv))
    app.setApplicationName("FinePdfAtom")
    app.setOrganizationName("FinePdfAtom")
    app.setDesktopFileName("finepdfatom")
    app.setWindowIcon(icons.app_icon())
    app.setStyle(QStyleFactory.create("Fusion"))
    # русские стандартные диалоги Qt (выбор файла, кнопки «Да/Нет»)
    from PyQt5.QtCore import QLibraryInfo, QLocale, QTranslator
    tr = QTranslator(app)
    if tr.load(QLocale("ru_RU"), "qtbase", "_", QLibraryInfo.location(QLibraryInfo.TranslationsPath)):
        app.installTranslator(tr)
        app._qt_tr = tr
    cfg = Config()
    app.setPalette(theme.palette(cfg.theme))
    app.setStyleSheet(theme.stylesheet(cfg.theme))
    f = app.font()
    if f.pointSizeF() < 10:
        f.setPointSizeF(10)
        app.setFont(f)
    return app, cfg


class _OneShot(QThread):
    done = pyqtSignal(object)

    def __init__(self, settings, image, mode):
        super().__init__()
        self.settings, self.image, self.mode = settings, image, mode

    def run(self):
        rec = ocr.Recognizer(self.settings)
        try:
            self.done.emit(rec.recognize_image(self.image, 96, mode=self.mode))
        except Exception as e:  # noqa: BLE001
            self.done.emit(e)
        finally:
            rec.close()


class QuickResult(QDialog):
    """Окошко с результатом быстрого распознавания (снимок экрана / буфер)."""

    def __init__(self, colors, image):
        super().__init__(None, Qt.Window | Qt.WindowStaysOnTopHint)
        self.image = image
        self.setWindowTitle("FinePdfAtom — текст с экрана")
        self.setWindowIcon(icons.app_icon())
        self.resize(660, 340)
        lay = QVBoxLayout(self)
        self.head = QLabel("Распознаю…")
        self.head.setObjectName("CardTitle")
        lay.addWidget(self.head)
        self.edit = QPlainTextEdit()
        self.edit.setFont(QFont(self.font().family(), 11))
        lay.addWidget(self.edit, 1)
        row = QHBoxLayout()
        self.hint = QLabel("")
        self.hint.setObjectName("Muted")
        row.addWidget(self.hint, 1)
        b_open = QPushButton(icons.icon("open", colors["icon"]), " Открыть в программе")
        b_open.clicked.connect(self.open_full)
        b_copy = QPushButton(icons.icon("copy", colors["accent_text"]), " Копировать")
        b_copy.setObjectName("Primary")
        b_copy.clicked.connect(self.copy)
        b_close = QPushButton("Закрыть")
        b_close.clicked.connect(self.close)
        for b in (b_open, b_copy, b_close):
            b.setAutoDefault(False)
            row.addWidget(b)
        lay.addLayout(row)
        pos = QCursor.pos() + QPoint(20, 20)
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        g = scr.availableGeometry()
        pos.setX(min(pos.x(), g.right() - self.width() - 20))
        pos.setY(min(pos.y(), g.bottom() - self.height() - 40))
        self.move(pos)

    def set_result(self, res):
        if isinstance(res, Exception):
            self.head.setText("Ошибка распознавания")
            self.edit.setPlainText(str(res))
            return
        text = res.text.strip()
        self.edit.setPlainText(text)
        if text:
            self.copy()
            self.head.setText("Текст скопирован в буфер обмена")
            self.hint.setText("уверенность %d%% · %.1f с" % (round(res.mean_conf), res.seconds))
        else:
            self.head.setText("Текст не найден")

    def copy(self):
        QApplication.clipboard().setText(self.edit.toPlainText())
        self.hint.setText("Скопировано · " + time.strftime("%H:%M:%S"))

    def open_full(self):
        from .mainwindow import MainWindow
        cfg = Config()
        w = MainWindow(cfg)
        w.show()
        ref = sources.memory_ref(self.image, "Снимок экрана %s" % time.strftime("%H:%M:%S"), 96, "screen")
        w.add_refs([ref], auto_recognize=True)
        QApplication.instance()._main = w
        self.hide()

    def closeEvent(self, e):
        # содержимое буфера обмена в X11 живёт, пока жив владелец: отдаём его менеджеру буфера
        QApplication.clipboard().setText(self.edit.toPlainText())
        super().closeEvent(e)
        main = getattr(QApplication.instance(), "_main", None)
        if main is None or not main.isVisible():
            QTimer.singleShot(150, QApplication.instance().quit)


def run_quick(app, cfg, image=None):
    """Быстрый режим: выделить область экрана (или взять буфер) → распознать → скопировать."""
    from .screencap import ScreenSelector, qimage_to_rgb
    colors = theme.colors(cfg.theme)
    state = {}

    def go(arr, mode):
        dlg = QuickResult(colors, arr)
        state["dlg"] = dlg
        dlg.show()
        th = _OneShot(cfg.ocr_settings(), arr, mode)
        state["th"] = th
        th.done.connect(dlg.set_result)
        th.start()

    if image is not None:
        go(image, "")
    else:
        sel = ScreenSelector(colors["accent"])
        state["sel"] = sel
        sel.selected.connect(lambda img: go(qimage_to_rgb(img), "screen"))
        sel.cancelled.connect(app.quit)
        QTimer.singleShot(0, sel.start)
    app.setQuitOnLastWindowClosed(False)
    return app.exec_()


def run(argv) -> int:
    quick_screen = "--screen" in argv
    quick_clip = "--clipboard" in argv
    files = [a for a in argv if not a.startswith("--")]
    app, cfg = make_app([a for a in argv if a.startswith("-") and a not in ("--screen", "--clipboard")])
    if quick_screen:
        return run_quick(app, cfg)
    if quick_clip:
        from .screencap import qimage_to_rgb
        img = app.clipboard().image()
        if img.isNull():
            QMessageBox.information(None, "FinePdfAtom", "В буфере обмена нет изображения.")
            return 1
        return run_quick(app, cfg, qimage_to_rgb(img))
    from .mainwindow import MainWindow
    w = MainWindow(cfg)
    w.show()
    if files:
        w.add_paths([os.path.abspath(f) for f in files])
    return app.exec_()
