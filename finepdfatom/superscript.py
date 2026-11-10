"""Знаки сносок: мелкие приподнятые цифры и звёздочки.

Tesseract читает надстрочную «1» как кавычку, апостроф или маркер списка. Здесь такие знаки
находятся по геометрии (маленькая приподнятая фигура в конце слова или в начале строки),
распознаются отдельно в увеличенном виде и записываются в текст надстрочными цифрами (¹ ² ³).

Чтобы не принять за сноску обычную кавычку, знак в тексте принимается только тогда,
когда внизу страницы есть строка, начинающаяся с такого же знака (текст сноски).
"""
from __future__ import annotations

import re

import cv2
import numpy as np

SUP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
UNSUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_JUNK = "\"'*°^`’”‘“•®¢©"
_TAIL_RE = re.compile(r"^(.*?[^\W\d_][»)]?)([%s]+|\d{1,2})?([.,;:!?]*)$" % re.escape(_JUNK), re.S)
_HEAD_RE = re.compile(r"^([%s]|\d){1,2}" % re.escape(_JUNK))


def _read_mark(eng, gray, box, dpi):
    x0, y0, x1, y1 = box
    H, W = gray.shape
    pad = 3
    crop = gray[max(0, y0 - pad):min(H, y1 + pad), max(0, x0 - pad):min(W, x1 + pad)]
    if crop.size == 0:
        return ""
    f = 44.0 / max(1, crop.shape[0])
    big = cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    canvas = np.full((big.shape[0] + 40, big.shape[1] + 60), 255, np.uint8)
    canvas[20:20 + big.shape[0], 30:30 + big.shape[1]] = big
    old = eng.psm
    try:
        eng.set_psm(8)          # одно слово
        d = eng.recognize(canvas, dpi=300)
    finally:
        eng.set_psm(old)
    txt = "".join(w.text for w in d.words).strip()
    txt = txt.replace("І", "1").replace("I", "1").replace("l", "1").replace("|", "1")
    if re.fullmatch(r"\d{1,2}", txt):
        return txt
    if txt in ("*", "**", "***"):
        return txt
    return ""


def detect(data, ink: np.ndarray, gray: np.ndarray, eng, dpi: int, ch: float) -> int:
    """Находит знаки сносок и вписывает их в слова. Возвращает число найденных ссылок."""
    words = [w for w in data.words if w.cell is None]
    if len(words) < 5:
        return 0
    n, _lab, st, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    if n < 10:
        return 0
    x, y, w, h = st[1:, 0], st[1:, 1], st[1:, 2], st[1:, 3]
    cx, cy, bottom = x + w / 2.0, y + h / 2.0, y + h
    by_line = {}
    for wd in words:
        by_line.setdefault(wd.line, []).append(wd)
    # знак, который Tesseract счёл отдельной строчкой над строкой текста, возвращаем в строку
    lboxes = {}
    for lid, ws in by_line.items():
        lboxes[lid] = (min(q.box[0] for q in ws), min(q.box[1] for q in ws),
                       max(q.box[2] for q in ws), max(q.box[3] for q in ws))
    for lid, ws in list(by_line.items()):
        if len(ws) != 1 or len(ws[0].text) > 2:
            continue
        o = ws[0]
        oh = o.box[3] - o.box[1]
        for tid, tb in lboxes.items():
            if tid == lid or len(by_line.get(tid, ())) < 2:
                continue
            th = tb[3] - tb[1]
            if oh < th * 0.8 and tb[1] - th * 0.5 <= o.box[1] <= tb[1] + th * 0.3 and \
                    o.box[3] < tb[3] - th * 0.25 and tb[0] - th <= o.box[0] <= tb[2] + th:
                o.line, o.block, o.para = tid, by_line[tid][0].block, by_line[tid][0].para
                by_line[tid].append(o)
                by_line[tid].sort(key=lambda q: q.box[0])
                del by_line[lid]
                break
    page_h = float(ink.shape[0])
    leading, trailing = [], []          # (word, box)
    orphans = {}                        # id(слово-знак) → слово слева
    for lid, ws in by_line.items():
        lx0 = min(q.box[0] for q in ws)
        lx1 = max(q.box[2] for q in ws)
        ly0 = min(q.box[1] for q in ws)
        ly1 = max(q.box[3] for q in ws)
        sel = np.flatnonzero((cx >= lx0 - 3) & (cx <= lx1 + 3) & (cy >= ly0 - ch * 0.4) & (cy <= ly1 + 2) & (h >= 3))
        if sel.size < 4:
            continue
        hl = float(np.percentile(h[sel], 75))
        tall = sel[h[sel] >= 0.6 * hl]
        if tall.size < 3:
            continue
        base = float(np.median(bottom[tall]))
        normal = tall[bottom[tall] > base - 0.3 * hl]      # стоящие на линии строки
        if normal.size < 3:
            continue
        rs = sel[(h[sel] <= 1.05 * hl) & (h[sel] >= 0.3 * hl) & (bottom[sel] <= base - 0.3 * hl) & (w[sel] >= 2)]
        if rs.size == 0:
            continue
        first_normal_x = float(x[normal].min())
        # знак в начале строки (текст сноски)
        lead = rs[(x[rs] + w[rs]) <= first_normal_x + 1]
        if lead.size and lead.size <= 3 and ly0 > page_h * 0.4:
            box = (int(x[lead].min()), int(y[lead].min()), int((x[lead] + w[lead]).max()), int(bottom[lead].max()))
            if first_normal_x - box[2] < hl * 3:
                leading.append((ws[0], box, ws))
        # знак в конце слова (ссылка на сноску)
        for wi, wd in enumerate(ws):
            wx0, wx1 = wd.box[0] - 2, wd.box[2] + 2
            inw = rs[(cx[rs] >= wx0) & (cx[rs] <= wx1)]
            if inw.size == 0 or inw.size > 3:
                continue
            nw = normal[(cx[normal] >= wx0) & (cx[normal] <= wx1)]
            if nw.size == 0:
                # слово целиком из приподнятых знаков — относится к слову слева
                if wi > 0 and len(wd.text) <= 3 and wd.box[0] - ws[wi - 1].box[2] < hl * 1.2:
                    box = (int(x[inw].min()), int(y[inw].min()), int((x[inw] + w[inw]).max()),
                           int(bottom[inw].max()))
                    orphans[id(wd)] = ws[wi - 1]
                    trailing.append((wd, box))
                continue
            last_x = float((x[nw] + w[nw]).max())
            tail = inw[x[inw] >= last_x - 1]
            if tail.size == 0:
                continue
            box = (int(x[tail].min()), int(y[tail].min()), int((x[tail] + w[tail]).max()), int(bottom[tail].max()))
            trailing.append((wd, box))
    if not leading:
        return 0
    marks = set()
    for wd, box, ws in leading[:12]:
        m = _read_mark(eng, gray, box, dpi)
        if not m:
            continue
        marks.add(m)
        shown = m.translate(SUP) if m.isdigit() else m
        # знак мог распознаться отдельным «словом» или прилипнуть к первому слову строки
        if wd.box[2] <= box[2] + 3:
            wd.text = shown
        else:
            wd.text = shown + " " + _HEAD_RE.sub("", wd.text, count=1).lstrip()
        wd.conf = max(wd.conf, 90.0)
        wd.in_dict = True
    if not marks:
        return 0
    found = 0
    # сначала слова, в тексте которых на конце уже стоит подозрительный знак
    def suspicious(t):
        mm = _TAIL_RE.match(t[0].text)
        return 0 if (mm and mm.group(2)) else 1
    trailing.sort(key=suspicious)
    for wd, box in trailing[:30]:
        m = _read_mark(eng, gray, box, dpi)
        if not m or m not in marks:
            continue
        shown = m.translate(SUP) if m.isdigit() else m
        if id(wd) in orphans:
            left = orphans[id(wd)]
            mm = re.match(r"^(.*?)([.,;:!?]*)$", left.text, re.S)
            left.text = mm.group(1) + shown + mm.group(2)
            wd.text = ""                 # слово-знак больше не нужно
            found += 1
            continue
        mm = _TAIL_RE.match(wd.text)
        if mm:
            wd.text = mm.group(1) + shown + mm.group(3)
        else:
            wd.text = wd.text + shown
        wd.conf = max(wd.conf, 85.0)
        found += 1
    data.words = [q for q in data.words if q.text]
    return found
