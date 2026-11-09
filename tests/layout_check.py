"""Проверка распознавания с анализом макета на тестовой странице (tests/make_layout_sample.py)."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finepdfatom import ocr, sources  # noqa: E402


def main(folder):
    truth = json.load(open(os.path.join(folder, "layout.json")))
    rec = ocr.Recognizer(ocr.Settings())
    ref = sources.page_refs(os.path.join(folder, "layout.png"))[0]
    t = time.time()
    r = rec.recognize_ref(ref)
    print("время %.1f с, уверенность %.0f%%, основной кегль %s" % (time.time() - t, r.mean_conf, r.data.body_pt))
    d = r.data
    for bid in d.order:
        b = d.blocks[bid]
        print("— блок %d: %s, колонка %d/%d, полоса %d" % (bid, b.kind, b.column + 1, b.columns, b.band))
        if b.kind == "table":
            t = b.table
            grid = [["" for _ in range(t.cols)] for _ in range(t.rows)]
            for w in d.words:
                if w.block == bid:
                    rr, cc = w.cell
                    grid[rr][cc] = (grid[rr][cc] + " " + w.text).strip() + ("*" if w.bold else "")
            for row in grid:
                print("   | " + " | ".join(row))
            continue
        for pid in sorted({w.para for w in d.words if w.block == bid}):
            p = d.paras[pid]
            txt = " ".join(w.text for w in d.words if w.para == pid)
            print("   [%s %s%s%s%s] %s" % (p.align, p.pt, " Ж" if p.bold else "", " К" if p.italic else "",
                                        " ЗАГОЛОВОК" if p.heading else "", txt[:90]))
    print()
    print("Эталон:")
    for p in truth["paras"]:
        print("   [%s %s%s] %s" % (p["align"], p["pt"], " Ж" if p["bold"] else "", p["text"][:60]))
    rec.close()


if __name__ == "__main__":
    main(sys.argv[1])
