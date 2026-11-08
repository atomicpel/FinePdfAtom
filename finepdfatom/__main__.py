"""Точка входа.

  finepdfatom                  — графический интерфейс
  finepdfatom файл1 файл2      — открыть файлы в интерфейсе
  finepdfatom --screen         — распознать область экрана (для горячей клавиши)
  finepdfatom --clipboard      — распознать картинку из буфера обмена
  finepdfatom ocr ...          — командная строка (см. finepdfatom ocr --help)
  finepdfatom --check          — проверка установки
"""
import sys


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "ocr":
        from . import cli
        return cli.main(argv[1:])
    if argv and argv[0] in ("--check", "check"):
        from . import diagnostics
        return diagnostics.main()
    if argv and argv[0] in ("--version", "-V"):
        from . import __version__, tess
        print("FinePdfAtom %s, Tesseract %s" % (__version__, tess.version()))
        return 0
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    from .gui.app import run
    return run(argv)


if __name__ == "__main__":
    sys.exit(main() or 0)
