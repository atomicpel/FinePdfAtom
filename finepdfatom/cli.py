"""Командная строка: finepdfatom ocr <файлы/папки> -o результат."""
from __future__ import annotations

import argparse
import os
import sys
import time

from . import __version__, export, ocr, preprocess, sources, tess


def build_parser():
    p = argparse.ArgumentParser(
        prog="finepdfatom ocr",
        description="Распознавание текста без интернета (Tesseract %s)." % "5",
        epilog="Примеры:\n"
               "  finepdfatom ocr скан.pdf -o скан_с_текстом.pdf\n"
               "  finepdfatom ocr ~/Сканы --outdir ~/Готово --format pdf,docx\n"
               "  finepdfatom ocr фото.jpg -o текст.txt -l rus --mode photo",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("inputs", nargs="+", help="файлы изображений/PDF или папки")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("-o", "--output", help="один итоговый файл (все страницы подряд); формат по расширению")
    g.add_argument("--outdir", help="папка: отдельный результат для каждого входного файла")
    p.add_argument("--format", default="pdf", help="форматы для --outdir через запятую: pdf,txt,docx,odt")
    p.add_argument("-l", "--lang", default="rus+eng", help="языки, например rus+eng (по умолчанию)")
    p.add_argument("--mode", default="auto", choices=list(preprocess.MODES), help="тип изображений")
    p.add_argument("--layout", default="auto", choices=list(ocr.PSM_CHOICES), help="разметка страницы")
    p.add_argument("--no-rotate", action="store_true", help="не поворачивать страницы автоматически")
    p.add_argument("--no-deskew", action="store_true", help="не исправлять перекос")
    p.add_argument("--join-lines", action="store_true", help="склеивать строки в абзацы (для DOCX/ODT)")
    p.add_argument("--force-ocr", action="store_true", help="распознавать даже PDF с текстовым слоем")
    p.add_argument("--pdf-quality", default="color", choices=list(export.PDF_QUALITY))
    p.add_argument("-j", "--threads", type=int, default=0, help="число потоков (0 — авто)")
    p.add_argument("-q", "--quiet", action="store_true")
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    say = (lambda *x: None) if a.quiet else (lambda *x: print(*x, file=sys.stderr, flush=True))
    files = sources.expand_paths(a.inputs)
    if not files:
        say("Нет подходящих файлов.")
        return 2
    missing = [l for l in a.lang.split("+") if l not in tess.available_languages()]
    if missing:
        say("Нет моделей языков: %s. Доступны: %s" % (", ".join(missing), ", ".join(tess.available_languages())))
        return 2
    s = ocr.Settings(langs=a.lang, layout=a.layout, use_pdf_text=not a.force_ocr,
                     keep_lines=not a.join_lines, threads=a.threads,
                     prep=preprocess.Options(mode=a.mode, auto_rotate=not a.no_rotate, deskew=not a.no_deskew))
    rec = ocr.Recognizer(s)
    t0 = time.time()
    say("FinePdfAtom %s · Tesseract %s · языки %s · потоков %d" % (__version__, tess.version(), a.lang,
                                                                     s.worker_count()))

    def run(refs, label):
        def done(i, r):
            if isinstance(r, Exception):
                say("  [%d/%d] %s — ошибка: %s" % (i + 1, len(refs), refs[i].label, r))
            else:
                say("  [%d/%d] %s — %.0f%% уверенности, %.1f с (%s)" % (
                    i + 1, len(refs), refs[i].label, r.mean_conf, r.seconds, r.notes))
        say("%s: страниц %d" % (label, len(refs)))
        res = rec.map(refs, done)
        return [export.ExportPage(r, r.text, ref) for r, ref in zip(res, refs) if not isinstance(r, Exception)]

    rc = 0
    try:
        if a.output:
            refs = [r for f in files for r in sources.page_refs(f)]
            pages = run(refs, "Документ")
            export.save(pages, a.output, pdf_quality=a.pdf_quality)
            say("Сохранено: %s" % a.output)
        else:
            os.makedirs(a.outdir, exist_ok=True)
            fmts = [x.strip().lower() for x in a.format.split(",") if x.strip()]
            for f in files:
                pages = run(sources.page_refs(f), os.path.basename(f))
                base = os.path.join(a.outdir, os.path.splitext(os.path.basename(f))[0])
                for fm in fmts:
                    out = base + "." + fm
                    if os.path.abspath(out) == os.path.abspath(f):
                        out = base + ".ocr." + fm
                    export.save(pages, out, fm, pdf_quality=a.pdf_quality)
                    say("  → %s" % out)
    except KeyboardInterrupt:
        rec.cancel()
        say("Прервано.")
        rc = 130
    finally:
        rec.close()
    say("Готово за %.1f с" % (time.time() - t0))
    return rc
