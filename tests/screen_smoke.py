"""Проверка быстрого режима: выделение области экрана → распознавание → буфер обмена."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt5.QtCore import QPoint, Qt, QTimer  # noqa: E402
from PyQt5.QtGui import QPixmap  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QLabel  # noqa: E402

from finepdfatom.gui import app as gapp  # noqa: E402

out = sys.argv[1]
application, cfg = gapp.make_app([])
bg = QLabel()
bg.setPixmap(QPixmap(sys.argv[2]))
bg.setStyleSheet("background: white")
bg.setAlignment(Qt.AlignLeft | Qt.AlignTop)
bg.setGeometry(0, 0, 1200, 400)
bg.setWindowFlags(Qt.FramelessWindowHint)
bg.show()
state = {}


def start():
    from finepdfatom.gui.screencap import ScreenSelector, qimage_to_rgb
    sel = ScreenSelector()
    state["sel"] = sel

    def got(img):
        img.save(os.path.join(out, "screen_crop.png"))
        dlg = gapp.QuickResult(gapp.theme.colors("light"), qimage_to_rgb(img))
        state["dlg"] = dlg
        dlg.show()
        th = gapp._OneShot(cfg.ocr_settings(), qimage_to_rgb(img), "screen")
        state["th"] = th
        th.done.connect(dlg.set_result)
        th.done.connect(lambda r: QTimer.singleShot(300, finish))
        th.start()

    sel.selected.connect(got)
    sel.start()
    QTimer.singleShot(400, lambda: drag(sel))


def drag(sel):
    sel.grab().save(os.path.join(out, "screen_overlay_idle.png"))
    QTest.mousePress(sel, Qt.LeftButton, Qt.NoModifier, QPoint(4, 2))
    QTest.mouseMove(sel, QPoint(500, 90))
    sel.grab().save(os.path.join(out, "screen_overlay_drag.png"))
    QTest.mouseRelease(sel, Qt.LeftButton, Qt.NoModifier, QPoint(700, 170))


def finish():
    dlg = state["dlg"]
    dlg.grab().save(os.path.join(out, "screen_quick_result.png"))
    print("БУФЕР:", repr(QApplication.clipboard().text()[:200]))
    application.quit()


QTimer.singleShot(500, start)
application.exec_()
