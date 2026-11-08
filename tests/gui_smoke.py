"""Дымовой тест интерфейса: открыть образцы, распознать, сделать снимки окна, сохранить во все форматы."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt5.QtCore import QTimer  # noqa: E402

from finepdfatom import export  # noqa: E402
from finepdfatom.gui.app import make_app  # noqa: E402
from finepdfatom.gui.mainwindow import MainWindow  # noqa: E402

out = sys.argv[1]
files = sys.argv[2:]
theme_name = os.environ.get("THEME", "light")
os.makedirs(out, exist_ok=True)
app, cfg = make_app([])
cfg.theme = theme_name
from finepdfatom.gui import theme  # noqa: E402
app.setPalette(theme.palette(theme_name))
app.setStyleSheet(theme.stylesheet(theme_name))
w = MainWindow(cfg)
w.resize(1500, 900)
w.show()
steps = []


def shot(name):
    w.grab().save(os.path.join(out, name))
    print("снимок", name, flush=True)


def wait_done(then, n=0):
    if (w.worker is None and w.pages and all(p.state in (2, 3) for p in w.pages)) or n > 600:
        then()
    else:
        QTimer.singleShot(200, lambda: wait_done(then, n + 1))


def s0():
    shot("%s_0_empty.png" % theme_name)
    w.add_paths(files)
    QTimer.singleShot(500, lambda: wait_done(s1))


def s1():
    w.list.setCurrentRow(1 if len(w.pages) > 1 else 0)
    QTimer.singleShot(400, s2)


def s2():
    w.text.next_low_conf()
    shot("%s_1_result.png" % theme_name)
    pages = [export.ExportPage(p.result, p.final_text(), p.ref if p.ref.kind == "pdf" else None)
             for p in w.pages if p.result]
    for fmt in ("pdf", "docx", "odt", "txt"):
        export.save(pages, os.path.join(out, "gui_export.%s" % fmt), fmt)
    print("страниц:", len(w.pages), "распознано:", len(pages), flush=True)
    for p in w.pages:
        print(" ", p.ref.label, p.state, round(p.result.mean_conf) if p.result else None,
              p.result.notes if p.result else p.error, flush=True)
    w.view_orig.setChecked(True)
    w._show_image()
    w.btn_area.setChecked(True)
    QTimer.singleShot(300, s3)


def s3():
    shot("%s_2_original.png" % theme_name)
    w.close()
    app.quit()


QTimer.singleShot(600, s0)
app.exec_()
