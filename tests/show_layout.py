"""Наглядная проверка анализа макета: рисует найденные блоки поверх страницы."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from finepdfatom import layout, preprocess, tess  # noqa: E402

COL = {"text": (255, 0, 0), "table": (0, 160, 0), "image": (0, 0, 255)}


def main(src, dst):
    rgb = np.array(Image.open(src).convert("RGB"))
    prep = preprocess.prepare(rgb, osd=tess.OsdEngine())
    g = prep.ocr
    t = time.time()
    seeds = [b for k, b in tess.Engine("rus+eng").analyse_layout(g) if k in tess.IMAGE_TYPES]
    lay = layout.analyze(g, image_seeds=seeds)
    print("время %.2f с, высота символа %.0f px" % (time.time() - t, lay.ch))
    vis = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
    for i, r in enumerate(lay.regions):
        print(i, r.kind, tuple(int(v) for v in r.box), "полоса", r.band, "колонка %d/%d" % (r.column + 1, r.columns))
        cv2.rectangle(vis, tuple(map(int, r.box[:2])), tuple(map(int, r.box[2:])), COL[r.kind], 4)
        cv2.putText(vis, str(i), (int(r.box[0]) - 40, int(r.box[1]) + 30), 0, 1.2, COL[r.kind], 3)
        if r.table:
            print("   таблица %d×%d:" % (r.table.rows, r.table.cols),
                  [(c.row, c.col, c.rowspan, c.colspan) for c in r.table.cells if c.rowspan > 1 or c.colspan > 1])
            for c in r.table.cells:
                cv2.rectangle(vis, tuple(map(int, c.box[:2])), tuple(map(int, c.box[2:])), (0, 200, 200), 2)
    f = min(1.0, 1400 / max(vis.shape[:2]))
    cv2.imwrite(dst, cv2.resize(vis, None, fx=f, fy=f, interpolation=cv2.INTER_AREA))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
