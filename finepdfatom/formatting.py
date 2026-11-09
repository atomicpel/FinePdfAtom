"""Восстановление оформления: абзацы, кегль, жирный, курсив, выравнивание, заголовки.

Работает с результатом распознавания (tess.OcrData) в координатах изображения,
которое видел Tesseract, и маской чернил этого же изображения.
"""
from __future__ import annotations

import statistics

import cv2
import numpy as np

from .tess import ParaInfo

STD_SIZES = [6, 7, 8, 9, 10, 10.5, 11, 12, 13, 14, 16, 18, 20, 22, 24, 26, 28, 32, 36, 40, 48, 54, 60, 72]


def snap_pt(v: float) -> float:
    if v <= 0:
        return 0.0
    return min(STD_SIZES, key=lambda s: abs(s - v))


# ------------------------------------------------------------------ жирность и наклон

def _stroke_ratio(ink: np.ndarray, box, line_h: float) -> float:
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    if x1 - x0 < 3 or y1 - y0 < 3 or line_h <= 0:
        return 0.0
    sub = ink[max(0, y0):y1, max(0, x0):x1] > 0
    n = int(sub.sum())
    if n < 12:
        return 0.0
    er = cv2.erode(sub.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    edge = n - int(er.sum())
    if edge <= 0:
        return 0.0
    # толщина штриха ≈ площадь / (половина периметра); нормируем на высоту строки
    return (2.0 * n / edge) / line_h


def _slant(ink: np.ndarray, box) -> float:
    """Наклон штрихов (курсив): угол сдвига, при котором вертикальный профиль самый «острый»."""
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    sub = (ink[max(0, y0):y1, max(0, x0):x1] > 0).astype(np.uint8)
    h, w = sub.shape
    if h < 8 or w < 8 or sub.sum() < 30:
        return 0.0
    best, best_s = 0.0, -1.0
    ys = np.arange(h, dtype=np.float32) - h / 2.0
    for sh in (-0.05, 0.0, 0.1, 0.18, 0.25, 0.32):
        m = np.float32([[1, sh, -sh * h / 2.0], [0, 1, 0]])
        r = cv2.warpAffine(sub, m, (w, h), flags=cv2.INTER_NEAREST)
        prof = r.sum(axis=0).astype(np.float64)
        s = float((prof ** 2).sum())
        if s > best_s:
            best_s, best = s, sh
    del ys
    return best


# ------------------------------------------------------------------ главное

def analyze(data, ink: np.ndarray, regions_meta: dict, dpi: float, dpi_known: bool, ch: float):
    """Заполняет data.paras (ParaInfo) и поля слов pt/bold/italic, перенумеровывает абзацы.

    regions_meta: block_id → (container_box)"""
    words = data.words
    if not words:
        return
    # высоты строк
    line_h = {}
    for lid, box in data.line_boxes.items():
        line_h[lid] = max(1.0, box[3] - box[1])
    for w in words:
        if w.line not in line_h:
            line_h[w.line] = max(1.0, w.box[3] - w.box[1])

    # --- жирный: толщина штриха относительно типичной
    ratios = []
    for w in words:
        size = w.pt * dpi / 72.0 if (w.pt > 0 and dpi > 0) else line_h[w.line]
        r = _stroke_ratio(ink, w.box, size)
        w._sr = r
        if r > 0 and sum(c.isalpha() for c in w.text) >= 3:
            ratios.append(r)
    if len(ratios) >= 8:
        base = float(np.percentile(ratios, 40))
        for w in words:
            w.bold = w._sr > base * 1.32 and sum(c.isalnum() for c in w.text) >= 1
    # сглаживание по строкам: одиночные «жирные» слова среди обычных — шум
    by_line = {}
    for w in words:
        by_line.setdefault(w.line, []).append(w)
    for lid, ws in by_line.items():
        nb = sum(w.bold for w in ws)
        if len(ws) >= 3 and nb / len(ws) >= 0.7:
            for w in ws:
                if w._sr > 0:
                    w.bold = True
        elif len(ws) >= 3 and nb / len(ws) <= 0.25:
            # оставляем только явно жирные слова
            for w in ws:
                strong = len(ratios) >= 8 and w._sr > base * 1.45
                w.bold = w.bold and strong and sum(c.isalnum() for c in w.text) >= 3

    # --- курсив по строкам (дорого — считаем на строке целиком)
    for lid, ws in by_line.items():
        if len(ws) < 2 or ws[0].cell is not None:
            continue
        x0 = min(w.box[0] for w in ws)
        x1 = max(w.box[2] for w in ws)
        box = data.line_boxes.get(lid) or (x0, min(w.box[1] for w in ws), x1, max(w.box[3] for w in ws))
        if _slant(ink, box) >= 0.18:
            for w in ws:
                w.italic = True

    # --- кегль
    raw = []
    for w in words:
        lh = line_h[w.line]
        if w.pt <= 0 and dpi > 0:
            w.pt = lh / dpi * 72.0 / 1.18
        raw.append((w.pt, len(w.text)))
    body_raw = _weighted_median(raw)
    k = 1.0
    if not dpi_known or not (6.5 <= body_raw <= 20):
        k = 11.0 / body_raw if body_raw > 0 else 1.0
    for w in words:
        w.pt = snap_pt(w.pt * k)
    body_pt = snap_pt(body_raw * k)

    # --- абзацы внутри текстовых блоков
    old_paras = data.paras
    data.paras = {}
    pid = -1
    new_words = []
    for bid in data.order:
        blk = data.blocks.get(bid)
        if blk is None:
            continue
        bws = [w for w in words if w.block == bid]
        if not bws:
            continue
        if blk.kind == "table":
            # абзац на ячейку
            cells = {}
            for w in bws:
                cells.setdefault(w.cell, []).append(w)
            for cell, cws in cells.items():
                pid += 1
                for w in cws:
                    w.para = pid
                data.paras[pid] = _para_info(cws, data, None, body_pt, cell=True)
                new_words += cws
            continue
        container = regions_meta.get(bid, blk.box)
        lines = []
        for w in bws:
            if lines and lines[-1][0] == w.line:
                lines[-1][1].append(w)
            else:
                lines.append((w.line, [w]))
        groups = _split_paragraphs(lines, data, container, ch, old_paras)
        for g in groups:
            pid += 1
            ws = [w for _l, lw in g for w in lw]
            for w in ws:
                w.para = pid
            data.paras[pid] = _para_info(ws, data, container, body_pt, lines=g, ch=ch)
            new_words += ws
    data.words = new_words
    data.body_pt = body_pt
    _smooth_sizes(data, body_pt)
    # заголовки и интервалы
    prev_box = None
    for p in data.paras.values():
        p.heading = (not p.cell) and not p.list_item and (
            (p.pt >= max(body_pt + 2, body_pt * 1.25) and p.nlines <= 3 and p.nchars <= 160) or
            (p.bold and p.nlines <= 2 and p.nchars < 140 and not p.text_end_dot))
        if prev_box is not None and not p.cell:
            gap = p.box[1] - prev_box[3]
            p.space_before = max(0.0, gap / dpi * 72.0 * (k if dpi_known else 1.0)) if dpi > 0 else 0.0
        if not p.cell:
            prev_box = p.box


def _smooth_sizes(data, body_pt):
    """Выравнивает «дрожащий» кегль соседних абзацев одного блока и кегль ячеек таблиц."""
    by_block = {}
    for w in data.words:
        lst = by_block.setdefault(w.block, [])
        if not lst or lst[-1] != w.para:
            lst.append(w.para)
    for bid, pids in by_block.items():
        ps = [data.paras[p] for p in pids if p in data.paras]
        if not ps:
            continue
        if ps[0].cell:
            for p in ps:
                if not (body_pt * 0.75 <= p.pt <= body_pt * 1.4):
                    p.pt = body_pt
            continue
        run = []
        for p in ps + [None]:
            if p is not None and run and abs(p.pt - run[-1].pt) <= 2.5 and not p.heading and not run[-1].heading:
                run.append(p)
                continue
            if len(run) > 1:
                weights = {}
                for q in run:
                    weights[q.pt] = weights.get(q.pt, 0) + q.nchars
                mode = max(weights, key=weights.get)
                for q in run:
                    q.pt = mode
            run = [p] if p is not None else []
    # основной текст и ячейки, близкие к основному кеглю, — ровно основной кегль
    for p in data.paras.values():
        if not p.heading and abs(p.pt - body_pt) <= 2:
            p.pt = body_pt
    # в одной таблице — единый кегль
    tab_pts = {}
    for bid, pids in by_block.items():
        ps = [data.paras[q] for q in pids if q in data.paras and data.paras[q].cell]
        if ps:
            pts = sorted(q.pt for q in ps)
            m = pts[len(pts) // 2]
            for q in ps:
                q.pt = m
    del tab_pts
    for w in data.words:
        p = data.paras.get(w.para)
        if p is not None:
            w.pt = p.pt


def _weighted_median(pairs):
    pairs = [(v, n) for v, n in pairs if v > 0]
    if not pairs:
        return 11.0
    pairs.sort()
    total = sum(n for _v, n in pairs)
    acc = 0
    for v, n in pairs:
        acc += n
        if acc >= total / 2:
            return v
    return pairs[-1][0]


def _line_box(data, lid, lw):
    b = data.line_boxes.get(lid)
    xs0 = min(w.box[0] for w in lw)
    xs1 = max(w.box[2] for w in lw)
    ys0 = min(w.box[1] for w in lw)
    ys1 = max(w.box[3] for w in lw)
    if b:
        return (min(b[0], xs0), min(b[1], ys0), max(b[2], xs1), max(b[3], ys1))
    return (xs0, ys0, xs1, ys1)


def _split_paragraphs(lines, data, container, ch, old_paras):
    """Делит строки блока на абзацы: отступ, интервал, короткая последняя строка, смена кегля/жирности."""
    if not lines:
        return []
    cx0, _cy0, cx1, _cy1 = container
    width = max(1.0, cx1 - cx0)
    boxes = [_line_box(data, lid, lw) for lid, lw in lines]
    pitches = [boxes[i + 1][1] - boxes[i][1] for i in range(len(boxes) - 1)]
    pitch = statistics.median(pitches) if pitches else ch * 2
    lefts = [b[0] for b in boxes]
    left_ref = float(np.percentile(lefts, 25)) if lefts else cx0
    groups = [[lines[0]]]
    for i in range(1, len(lines)):
        pb, b = boxes[i - 1], boxes[i]
        lid, lw = lines[i]
        plw = lines[i - 1][1]
        brk = False
        if b[1] - pb[1] > pitch * 1.45:
            brk = True
        # висячий отступ списка: строка-продолжение начинается под текстом после маркера
        elif _is_list_start(plw) and len(plw) > 1 and abs(b[0] - plw[1].box[0]) < ch * 1.2:
            brk = False
        elif _is_list_start(lw) and len(lw) > 1:
            brk = True
        # красная строка: начало заметно правее обычного левого края
        elif b[0] - left_ref > ch * 1.2 and abs(pb[0] - left_ref) < ch * 0.8:
            brk = True
        # предыдущая строка короткая и закончилась точкой/двоеточием → конец абзаца
        elif (cx1 - pb[2]) > width * 0.18 and plw[-1].text[-1:] in ".:;!?»)" and \
                abs(b[0] - left_ref) < ch * 3:
            brk = True
        else:
            pt_prev = statistics.median([w.pt for w in plw])
            pt_cur = statistics.median([w.pt for w in lw])
            if abs(pt_prev - pt_cur) >= 2.5 and max(pt_prev, pt_cur) / max(1.0, min(pt_prev, pt_cur)) > 1.3:
                brk = True
            elif (sum(w.bold for w in plw) / len(plw) > 0.7) != (sum(w.bold for w in lw) / len(lw) > 0.7):
                brk = True
        if brk:
            groups.append([lines[i]])
        else:
            groups[-1].append(lines[i])
    return groups


_LIST_RE = None


def _is_list_start(lw) -> bool:
    import re
    global _LIST_RE
    if _LIST_RE is None:
        _LIST_RE = re.compile(r"^([•●▪■◦\-–—*]|\d{1,3}[.)]|[а-яa-z][)])$")
    return bool(lw) and bool(_LIST_RE.match(lw[0].text))


def _para_info(ws, data, container, body_pt, lines=None, cell=False, ch=20.0):
    xs0 = min(w.box[0] for w in ws)
    ys0 = min(w.box[1] for w in ws)
    xs1 = max(w.box[2] for w in ws)
    ys1 = max(w.box[3] for w in ws)
    p = ParaInfo((xs0, ys0, xs1, ys1))
    chars = sum(len(w.text) for w in ws)
    p.pt = snap_pt(_weighted_median([(w.pt, len(w.text)) for w in ws])) or body_pt
    p.bold = sum(len(w.text) for w in ws if w.bold) > chars * 0.6
    p.italic = sum(len(w.text) for w in ws if w.italic) > chars * 0.6
    p.nchars = chars
    p.text_end_dot = ws[-1].text[-1:] in ".;,!?" if ws else False
    p.list_item = _is_list_start([ws[0]]) if ws else False
    p.cell = cell
    p.nlines = len(lines) if lines else len({w.line for w in ws})
    if cell or not lines or container is None:
        p.align = "left"
        if cell and container is None:
            p.align = ""
        return p
    cx0, _y0, cx1, _y1 = container
    boxes = [_line_box(data, lid, lw) for lid, lw in lines]
    tol = max(ch * 0.9, (cx1 - cx0) * 0.012)
    lefts = [b[0] for b in boxes]
    rights = [b[2] for b in boxes]
    centers = [(b[0] + b[2]) / 2 for b in boxes]
    mid = (cx0 + cx1) / 2
    left_flush = all(abs(l - cx0) < tol for l in lefts[1:]) if len(lefts) > 1 else abs(lefts[0] - cx0) < tol
    right_flush_body = all(abs(r - cx1) < tol for r in rights[:-1]) if len(rights) > 1 else False
    centered = all(abs(c - mid) < tol * 1.5 for c in centers)
    if len(boxes) == 1:
        lm, rm = lefts[0] - cx0, cx1 - rights[0]
        if centered and lm > tol * 2 and rm > tol * 2:
            p.align = "center"
        elif rm < tol and lm > (cx1 - cx0) * 0.25:
            p.align = "right"
        else:
            p.align = "left"
    elif left_flush and right_flush_body:
        p.align = "justify"
    elif centered and not left_flush:
        p.align = "center"
    elif all(abs(r - cx1) < tol for r in rights) and not left_flush:
        p.align = "right"
    else:
        p.align = "left"
    first_indent = lefts[0] - (statistics.median(lefts[1:]) if len(lefts) > 1 else cx0)
    p.indent = first_indent if p.align in ("left", "justify") and first_indent > ch * 0.8 else 0
    p.left_margin = max(0.0, (statistics.median(lefts[1:]) if len(lefts) > 1 else lefts[0]) - cx0) \
        if p.align in ("left", "justify") else 0.0
    return p
