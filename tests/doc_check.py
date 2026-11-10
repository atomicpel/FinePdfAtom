"""Проверка анализа документа целиком на tests/make_doc_sample.py."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finepdfatom import docanalysis, docmodel, ocr, sources  # noqa: E402
from finepdfatom.docanalysis import PAGE  # noqa: E402


def show_runs(p):
    out = []
    for r in p.runs:
        t = r.text
        if r.note is not None:
            t = "[СНОСКА %s: %s]" % (r.text, " ".join(q.text for q in r.note))
        elif r.href:
            t = "[ССЫЛКА %s]" % r.href
        elif r.bold:
            t = "*%s*" % t
        out.append(t)
    return "".join(out)


def main(path, langs="auto"):
    rec = ocr.Recognizer(ocr.Settings(langs=langs, use_pdf_text=False))
    refs = sources.page_refs(path)
    res = rec.map(refs)
    for r in res:
        if isinstance(r, Exception):
            raise r
    print("языки:", [getattr(r, "langs", "?") for r in res])
    doc = docanalysis.analyze([docmodel.build(r) for r in res])
    print("НАЙДЕНО:", doc.summary())
    hf = lambda lines: [("".join("{N}" if x is PAGE else x for x in l.parts), l.align) for l in lines]  # noqa: E731
    print("верхний колонтитул:", hf(doc.header), "| нижний:", hf(doc.footer),
          "| первая страница №", doc.page_start, "| основной кегль", doc.body_pt)
    for pg in doc.pages:
        print("---- страница %d%s" % (pg.index + 1, " (продолжение абзаца)" if pg.soft_start else ""))
        for it in pg.items:
            if isinstance(it, docmodel.Para):
                print("  %s[%s %s] %s" % ("H%d " % it.heading if it.heading else "", it.align, it.pt, show_runs(it)[:150]))
            elif isinstance(it, docmodel.TableItem):
                print("  ТАБЛИЦА %d×%d%s" % (it.rows, it.cols, "" if getattr(it, "ruled", True) else " без линий"))
                for r in range(it.rows):
                    print("    | " + " | ".join(" ".join(p.text for p in c.paras) for c in it.cells if c.row == r))
            else:
                print("  <%s>" % type(it).__name__)
    rec.close()
    return doc


if __name__ == "__main__":
    main(sys.argv[1], *(sys.argv[2:3]))
