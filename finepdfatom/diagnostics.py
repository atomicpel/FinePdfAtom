"""finepdfatom --check — проверка установки и окружения Astra Linux."""
from __future__ import annotations

import glob
import os
import platform
import shutil
import subprocess

from . import __version__, paths


def _ok(flag, text, hint=""):
    mark = "  OK " if flag else "  !! "
    print(mark + text + (("\n       → " + hint) if (hint and not flag) else ""))
    return flag


def _astra_version():
    for f in ("/etc/astra_version", "/etc/astra/build_version"):
        try:
            return open(f).read().strip()
        except OSError:
            pass
    return ""


def missing_qt_libs():
    """Системные библиотеки, которых не хватает Qt-плагину xcb."""
    import PyQt5
    base = os.path.join(os.path.dirname(PyQt5.__file__), "Qt5")
    targets = glob.glob(os.path.join(base, "plugins", "platforms", "libqxcb.so")) + \
        glob.glob(os.path.join(base, "lib", "libQt5XcbQpa.so.5")) + \
        glob.glob(os.path.join(base, "lib", "libQt5Gui.so.5"))
    missing = set()
    env = dict(os.environ, LD_LIBRARY_PATH=os.path.join(base, "lib"))
    for t in targets:
        try:
            out = subprocess.run(["ldd", t], capture_output=True, text=True, env=env).stdout
        except OSError:
            return []
        for line in out.splitlines():
            if "not found" in line:
                missing.add(line.split()[0])
    return sorted(missing)


def main() -> int:
    print("FinePdfAtom %s — проверка установки" % __version__)
    print("Система: %s; Astra: %s; glibc %s" % (platform.platform(), _astra_version() or "?",
                                               platform.libc_ver()[1]))
    good = True
    try:
        from . import tess
        good &= _ok(True, "Tesseract %s" % tess.version())
        langs = tess.available_languages()
        good &= _ok(bool(langs), "Языки: %s" % ", ".join(langs), "нет моделей в %s" % paths.bundled_tessdata())
        good &= _ok(os.path.exists(os.path.join(paths.bundled_tessdata(), "osd.traineddata")),
                    "Модель ориентации страниц (osd)")
        e = tess.Engine("+".join(l for l in ("rus", "eng") if l in langs) or langs[0])
        import numpy as np
        img = np.full((60, 200), 255, np.uint8)
        e.recognize(img)
        e.close()
        _ok(True, "Пробное распознавание")
    except Exception as ex:  # noqa: BLE001
        good &= _ok(False, "Движок распознавания: %s" % ex)
    try:
        import cv2
        import fitz
        _ok(True, "OpenCV %s, PyMuPDF %s" % (cv2.__version__, fitz.VersionBind))
    except Exception as ex:  # noqa: BLE001
        good &= _ok(False, "Библиотеки обработки: %s" % ex)
    try:
        from PyQt5.QtCore import QT_VERSION_STR
        _ok(True, "Qt %s" % QT_VERSION_STR)
        miss = missing_qt_libs()
        good &= _ok(not miss, "Системные библиотеки для окна программы",
                    "не хватает: %s. Установите с диска Astra: libxcb-icccm4 libxcb-image0 "
                    "libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-xinerama0 libxcb-xkb1 "
                    "libxkbcommon-x11-0" % ", ".join(miss))
    except Exception as ex:  # noqa: BLE001
        good &= _ok(False, "Qt: %s" % ex)
    _ok(bool(shutil.which("scanimage")), "Сканер: утилита scanimage (SANE)",
        "необязательно; для сканирования установите пакет sane-utils")
    zps = shutil.which("astra-digsig-control")
    if zps:
        try:
            st = subprocess.run([zps, "is-enabled"], capture_output=True, text=True).stdout.strip()
            _ok(st != "ENABLED", "ЗПС (замкнутая программная среда): %s" % (st or "?"),
                "при включённой ЗПС неподписанные файлы не запустятся")
        except OSError:
            pass
    print("\nИтог: %s" % ("всё в порядке" if good else "есть проблемы, см. «!!» выше"))
    return 0 if good else 1
