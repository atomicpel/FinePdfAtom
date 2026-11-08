"""Расположение файлов программы и пользовательских данных."""
from __future__ import annotations

import os

_PKG = os.path.dirname(os.path.abspath(__file__))


def app_root() -> str:
    """Корень установки (/opt/finepdfatom) или папка сборки при разработке."""
    env = os.environ.get("FINEPDFATOM_HOME")
    if env:
        return env
    # /opt/finepdfatom/app/finepdfatom -> /opt/finepdfatom
    return os.path.dirname(os.path.dirname(_PKG))


def lib_dir() -> str:
    return os.path.join(app_root(), "lib")


def bundled_tessdata() -> str:
    return os.path.join(app_root(), "tessdata")


def data_home() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "finepdfatom")


def user_tessdata() -> str:
    return os.path.join(data_home(), "tessdata")


def tessdata_dirs() -> list:
    return [user_tessdata(), bundled_tessdata()]


def tessdata_dir() -> str:
    """Единая папка моделей: пользовательская, со ссылками на встроенные модели.

    Tesseract принимает только один путь к моделям, поэтому встроенные модели
    подключаются в пользовательскую папку символическими ссылками, а добавленные
    пользователем языки лежат там же обычными файлами.
    """
    user = user_tessdata()
    bundled = bundled_tessdata()
    try:
        os.makedirs(user, exist_ok=True)
        for f in os.listdir(bundled):
            if not f.endswith(".traineddata"):
                continue
            dst = os.path.join(user, f)
            src = os.path.join(bundled, f)
            if os.path.islink(dst) and not os.path.exists(dst):
                os.unlink(dst)  # битая ссылка после переустановки
            if not os.path.lexists(dst):
                os.symlink(src, dst)
        return user
    except OSError:
        return bundled


def resource(*parts) -> str:
    return os.path.join(_PKG, "resources", *parts)
