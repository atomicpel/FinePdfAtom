#!/bin/sh
# FinePdfAtom — запуск. Программа самодостаточна: свой Python, Qt и Tesseract в /opt/finepdfatom.
ROOT=/opt/finepdfatom
# Системные переменные Python/Qt не должны влиять на встроенное окружение
unset PYTHONHOME PYTHONPATH QT_PLUGIN_PATH QT_QPA_PLATFORMTHEME QT_STYLE_OVERRIDE
exec "$ROOT/python/bin/python3" -I "$ROOT/app/run.py" "$@"
