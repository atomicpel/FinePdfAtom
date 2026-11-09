"""Анализ макета страницы (до распознавания).

Находит:
  * таблицы с линовкой — сетку, ячейки и объединения;
  * рисунки (фото, схемы);
  * текстовые блоки и колонки — рекурсивным разрезанием по пустым полосам (XY-cut),
    что сразу даёт порядок чтения: сверху вниз, колонки слева направо.

Все координаты — в пикселях подготовленного изображения (preprocess.Prepared.ocr).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class Cell:
    row: int
    col: int
    rowspan: int
    colspan: int
    box: tuple            # (x0, y0, x1, y1) внутренняя область ячейки


@dataclass
class Table:
    box: tuple
    rows: int
    cols: int
    cells: list = field(default_factory=list)
    col_edges: list = field(default_factory=list)   # x-границы столбцов (len = cols + 1)
    row_edges: list = field(default_factory=list)   # y-границы строк (len = rows + 1)


@dataclass
class Region:
    kind: str             # text | table | image
    box: tuple            # (x0, y0, x1, y1)
    container: tuple      # границы колонки/страницы, в которой лежит блок (для выравнивания)
    table: Table | None = None
    columns: int = 1      # число колонок в полосе, к которой относится блок
    column: int = 0       # номер колонки в полосе
    band: int = 0         # номер полосы (группа блоков-колонок одной высоты)


@dataclass
class PageLayout:
    regions: list
    ink: np.ndarray            # бинарная маска чернил (255 — чернила)
    ch: float                  # медианная высота символа, px
    text_image: np.ndarray     # изображение для распознавания текста (таблицы и рисунки забелены)
    cell_image: np.ndarray     # изображение для ячеек (линии таблиц удалены)
    text_box: tuple            # общая область текста на странице


# ----------------------------------------------------------------------------- базовое

def binarize(gray: np.ndarray) -> np.ndarray:
    """Чернила = 255. Комбинация Оцу и адаптивного порога (тонкие линии, неравномерный фон)."""
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    block = max(15, (min(gray.shape) // 60) | 1)
    adapt = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, block, 15)
    return cv2.bitwise_and(otsu, cv2.bitwise_or(adapt, (gray < 90).astype(np.uint8) * 255))


def char_height(ink: np.ndarray) -> float:
    n, _, st, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    if n < 10:
        return 20.0
    h, w, a = st[1:, 3], st[1:, 2], st[1:, 4]
    H, W = ink.shape
    ok = (h >= 5) & (h < H / 10) & (w < W / 6) & (a >= 8) & (w <= h * 3)
    if ok.sum() < 10:
        return 20.0
    return float(np.median(h[ok]))


def _runs(mask1d: np.ndarray):
    """Отрезки подряд идущих True: [(start, end)), end не включается."""
    m = np.concatenate([[False], mask1d.astype(bool), [False]])
    d = np.diff(m.astype(np.int8))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    return list(zip(starts, ends))


def _cluster(vals, tol):
    """Группировка близких координат → средние значения."""
    vals = sorted(vals)
    groups = []
    for v in vals:
        if groups and v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [int(round(sum(g) / len(g))) for g in groups]


# ----------------------------------------------------------------------------- таблицы

def find_lines(ink: np.ndarray, ch: float):
    """Маски горизонтальных и вертикальных линий."""
    H, W = ink.shape
    hl = int(max(ch * 3.0, W / 45))
    vl = int(max(ch * 1.6, H / 90))
    horiz = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (hl, 1)))
    vert = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, vl)))
    # заделываем разрывы в линиях скана
    g = max(3, int(ch * 0.4))
    horiz = cv2.morphologyEx(horiz, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (g * 3, 1)))
    vert = cv2.morphologyEx(vert, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, g * 3)))
    return horiz, vert


def detect_tables(ink: np.ndarray, ch: float):
    """Таблицы с линовкой. Возвращает (список Table, маска линий)."""
    horiz, vert = find_lines(ink, ch)
    lines = cv2.bitwise_or(horiz, vert)
    k = max(3, int(ch * 0.25)) | 1
    grid = cv2.dilate(lines, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(grid, connectivity=8)
    tables = []
    ink_wo_grid = cv2.bitwise_and(ink, cv2.bitwise_not(grid))
    for i in range(1, n):
        x, y, w, h, _a = st[i]
        if w < ch * 4 or h < ch * 1.5:
            continue
        sub_h = horiz[y:y + h, x:x + w]
        sub_v = vert[y:y + h, x:x + w]
        # должны быть и горизонтали, и вертикали
        hr = _runs(sub_h.max(axis=1) > 0)
        vr = _runs(sub_v.max(axis=0) > 0)
        if len(hr) < 2 or len(vr) < 2:
            continue
        t = _table_from_grid(grid[y:y + h, x:x + w] * (lab[y:y + h, x:x + w] == i), x, y, ch,
                             ink_wo_grid)
        if t is not None:
            tables.append(t)
    return tables, lines


def _table_from_grid(g: np.ndarray, ox: int, oy: int, ch: float, g_ink=None):
    h, w = g.shape
    closed = g.copy()
    cv2.rectangle(closed, (0, 0), (w - 1, h - 1), 255, max(2, int(ch * 0.2)))
    free = cv2.bitwise_not(closed)
    n, _lab, st, _ = cv2.connectedComponentsWithStats(free, connectivity=4)
    boxes = []
    for i in range(1, n):
        x, y, cw, chh, a = st[i]
        if cw < ch * 1.2 or chh < ch * 0.9:
            continue
        if a < 0.5 * cw * chh:
            continue
        boxes.append((x, y, x + cw, y + chh))
    if len(boxes) < 2 or w < ch * 6:
        return None
    covered = sum((b[2] - b[0]) * (b[3] - b[1]) for b in boxes)
    if covered < 0.45 * w * h:
        return None
    tol = max(4, int(ch * 0.6))
    xs = _cluster([b[0] for b in boxes] + [b[2] for b in boxes], tol)
    ys = _cluster([b[1] for b in boxes] + [b[3] for b in boxes], tol)
    # границы — середины промежутков между соседними краями (на линии)
    def edges(vals):
        out = [vals[0]]
        i = 1
        while i < len(vals):
            if i + 1 < len(vals) and vals[i + 1] - vals[i] <= tol * 4:
                out.append((vals[i] + vals[i + 1]) // 2)
                i += 2
            else:
                out.append(vals[i])
                i += 1
        return out
    xe, ye = edges(xs), edges(ys)
    if len(xe) < 2 or len(ye) < 2:
        return None

    def idx(edges_, v):
        return int(np.argmin([abs(e - v) for e in edges_]))

    cells = []
    for b in boxes:
        c0, c1 = idx(xe, b[0]), idx(xe, b[2])
        r0, r1 = idx(ye, b[1]), idx(ye, b[3])
        if c1 <= c0 or r1 <= r0:
            continue
        cells.append(Cell(r0, c0, r1 - r0, c1 - c0, (b[0] + ox, b[1] + oy, b[2] + ox, b[3] + oy)))
    rows, cols = len(ye) - 1, len(xe) - 1
    if rows * cols < 2 or len(cells) < 2:
        return None
    # в ячейках должен быть текст (а не пустота внутри крупных букв/рамок)
    filled = 0
    for c in cells:
        x0, y0, x1, y1 = c.box
        sub = g_ink[y0 + 2:y1 - 2, x0 + 2:x1 - 2] if g_ink is not None else None
        if sub is not None and sub.size and (sub > 0).sum() > ch * ch * 0.2:
            filled += 1
    if g_ink is not None and filled < max(2, len(cells) * 0.3):
        return None
    # одиночная рамка вокруг текста — не таблица
    if len(cells) == 1:
        return None
    cells.sort(key=lambda c: (c.row, c.col))
    return Table((ox, oy, ox + w, oy + h), rows, cols, cells,
                 [e + ox for e in xe], [e + oy for e in ye])


# ----------------------------------------------------------------------------- рисунки

def grow_seeds(ink: np.ndarray, ch: float, seeds, exclude) -> list:
    """Расширяет рисунки, найденные Tesseract, до полного размера (штрихи, выходящие за рамку)."""
    if not seeds:
        return []
    k = max(1, int(ch * 0.25))
    m = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    n, _lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    out = []
    for sx0, sy0, sx1, sy1 in seeds:
        if (sx1 - sx0) < ch * 2 or (sy1 - sy0) < ch * 2:
            continue
        if (sx1 - sx0) * (sy1 - sy0) > 0.5 * ink.shape[0] * ink.shape[1]:
            continue    # Tesseract иногда считает рисунком весь лист
        if any(_inter((sx0, sy0, sx1, sy1), e) > 0.5 * _area((sx0, sy0, sx1, sy1)) for e in exclude):
            continue
        x0, y0, x1, y1 = sx0, sy0, sx1, sy1
        for _it in range(4):
            grown = False
            for i in range(1, n):
                x, y, w, h, _a = st[i]
                b = (x, y, x + w, y + h)
                if _inter(b, (x0, y0, x1, y1)) <= 0:
                    continue
                if b[0] >= x0 and b[1] >= y0 and b[2] <= x1 and b[3] <= y1:
                    continue
                if h < ch * 1.7 and w < ch * 4:      # буква/слово — не трогаем
                    continue
                if w > ink.shape[1] * 0.9 and h > ink.shape[0] * 0.9:
                    continue
                x0, y0, x1, y1 = min(x0, b[0]), min(y0, b[1]), max(x1, b[2]), max(y1, b[3])
                grown = True
            if not grown:
                break
        out.append((int(x0), int(y0), int(x1), int(y1)))
    return out


def detect_images(gray: np.ndarray, ink: np.ndarray, ch: float, exclude) -> list:
    H, W = ink.shape
    k = max(3, int(ch * 0.5))
    nonwhite = ((gray < 215).astype(np.uint8) * 255) | ink
    m = cv2.dilate(nonwhite, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    for (x0, y0, x1, y1) in exclude:
        e = k + 3
        m[max(0, y0 - e):y1 + e, max(0, x0 - e):x1 + e] = 0
    n, _lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if w < ch * 3.5 or h < ch * 3.5:
            continue
        if w > W * 0.97 and h > H * 0.97:
            continue
        roi = gray[y:y + h, x:x + w]
        mid = float(np.mean((roi > 70) & (roi < 200)))
        dens = float(np.mean(ink[y:y + h, x:x + w] > 0))
        big = w > ch * 8 and h > ch * 8
        if mid > 0.18 or (big and dens > 0.25) or (big and _few_text_like(ink[y:y + h, x:x + w], ch)):
            out.append((x, y, x + w, y + h))
    return out


def _few_text_like(sub, ch):
    """Схема/рисунок: в области мало компонент размером с букву."""
    n, _l, st, _ = cv2.connectedComponentsWithStats(sub, connectivity=8)
    if n <= 1:
        return False
    hs = st[1:, 3]
    letters = np.sum((hs > ch * 0.4) & (hs < ch * 2.0))
    area = sub.shape[0] * sub.shape[1]
    # у текста ~1 буква на каждые ch×ch*0.8 пикселей строки
    return letters < area / (ch * ch * 6.0)


# ----------------------------------------------------------------------------- XY-cut

def _gaps(profile: np.ndarray, min_gap: float):
    """Пустые промежутки профиля длиной ≥ min_gap (внутренние, не у краёв)."""
    runs = _runs(profile == 0)
    n = len(profile)
    return [(s, e) for s, e in runs if s > 0 and e < n and (e - s) >= min_gap]


def xy_cut(mask: np.ndarray, ch: float, box=None, container=None, depth=0):
    """Рекурсивное разрезание. Возвращает листья [(box, container, band_info)]."""
    H, W = mask.shape
    if box is None:
        ys, xs = np.nonzero(mask)
        if xs.size == 0:
            return []
        box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    x0, y0, x1, y1 = box
    sub = mask[y0:y1, x0:x1]
    # подрезаем до чернил
    rows = sub.max(axis=1) > 0
    cols = sub.max(axis=0) > 0
    if not rows.any():
        return []
    ry = np.flatnonzero(rows)
    rx = np.flatnonzero(cols)
    x0, x1 = x0 + int(rx[0]), x0 + int(rx[-1]) + 1
    y0, y1 = y0 + int(ry[0]), y0 + int(ry[-1]) + 1
    box = (x0, y0, x1, y1)
    if container is None:
        container = box
    sub = mask[y0:y1, x0:x1]
    if depth > 40 or (y1 - y0) < ch * 0.8:
        return [(box, container, None)]

    hprof = sub.max(axis=1)
    vprof = sub.max(axis=0)

    # 1. колонки: вертикальный просвет на всю высоту (минимум 2 строки высотой)
    wg = _word_gap(sub, ch)
    base_gut = max(ch * 1.4, (x1 - x0) * 0.02)
    nl = len([1 for a, b in _runs(hprof > 0) if b - a >= ch * 0.5])
    # просвет через 4+ строки случайным быть не может; для коротких — строже
    min_gut = base_gut if nl >= 4 else max(base_gut, wg * 1.5)
    if (y1 - y0) > ch * 4.0:
        vg = _good_columns(vprof, _gaps(vprof, min_gut), ch)
        if vg:
            cuts = [0] + [((s + e) // 2) for s, e in vg] + [x1 - x0]
            leaves = []
            ncol = len(cuts) - 1
            for i in range(ncol):
                cb = (x0 + cuts[i], y0, x0 + cuts[i + 1], y1)
                # граница колонки — середины просветов
                for leaf in xy_cut(mask, ch, cb, cb, depth + 1):
                    b, c, info = leaf
                    leaves.append((b, c, info or (ncol, i, (x0, y0, x1, y1))))
            return leaves

    # 2. крупный горизонтальный просвет (между абзацами/разделами)
    hg = _gaps(hprof, ch * 1.6)
    if hg:
        return _split_h(mask, ch, box, container, hg, depth)

    # 3. мелкие просветы (между строками): ищем смену «колоночной структуры»
    lg = _gaps(hprof, max(2, ch * 0.2))
    if lg:
        strips = []
        prev = 0
        for s, e in lg + [(y1 - y0, y1 - y0)]:
            strips.append((prev, s))
            prev = e
        masks = [sub[s:e].max(axis=0) > 0 for s, e in strips]
        # жадно собираем группы строк с общим вертикальным просветом (колонки)
        merged = []
        i = 0
        while i < len(strips):
            j = i
            acc = masks[i].copy()
            best = i
            while j + 1 < len(strips):
                acc2 = acc | masks[j + 1]
                if not _good_columns(acc2, _gaps((acc2).astype(np.uint8), base_gut), ch):
                    break
                acc = acc2
                j += 1
                best = j
            nlines = best - i + 1
            col = nlines >= 4 and bool(_good_columns(acc, _gaps(acc.astype(np.uint8), base_gut), ch))
            if col:
                merged.append([strips[i][0], strips[best][1], True, None])
                i = best + 1
            else:
                if merged and not merged[-1][2]:
                    merged[-1][1] = strips[i][1]
                else:
                    merged.append([strips[i][0], strips[i][1], False, None])
                i += 1
        if len(merged) > 1:
            cuts = []
            for a, b in zip(merged, merged[1:]):
                # просвет между a и b
                for s, e in lg:
                    if a[1] <= s and e <= b[0] + 1:
                        cuts.append((s, e))
                        break
            if cuts:
                return _split_h(mask, ch, box, container, cuts, depth)
    return [(box, container, None)]


def _word_gap(sub: np.ndarray, ch: float) -> float:
    """Медианный межсловный пробел в области (px)."""
    hprof = sub.max(axis=1)
    gaps = []
    for s, e in _runs(hprof > 0):
        if e - s < ch * 0.5:
            continue
        vp = sub[s:e].max(axis=0)
        for a, b in _runs(vp == 0):
            if a > 0 and b < len(vp) and (b - a) >= ch * 0.2:
                gaps.append(b - a)
    return float(np.median(gaps)) if gaps else ch * 0.4


def _good_columns(prof_mask: np.ndarray, gaps, ch: float):
    """Отбрасывает просветы, после которых получаются слишком узкие «колонки»."""
    n = len(prof_mask)
    min_w = max(ch * 5, n * 0.10)
    out = []
    prev = 0
    for s, e in gaps:
        if s - prev >= min_w:
            out.append((s, e))
            prev = e
    # последняя колонка тоже должна быть широкой
    while out and n - out[-1][1] < min_w:
        out.pop()
    return out


def _overlap_gaps(a, b):
    for s1, e1 in a:
        for s2, e2 in b:
            if min(e1, e2) - max(s1, s2) > 0:
                return True
    return False


def _split_h(mask, ch, box, container, gaps, depth):
    x0, y0, x1, y1 = box
    cuts = [0] + [((s + e) // 2) for s, e in gaps] + [y1 - y0]
    leaves = []
    for i in range(len(cuts) - 1):
        leaves += xy_cut(mask, ch, (x0, y0 + cuts[i], x1, y0 + cuts[i + 1]), container, depth + 1)
    return leaves


# ----------------------------------------------------------------------------- главное

def _long_lines(horiz, vert, ch, W, H):
    """Отдельные длинные линии вне таблиц: разделители, подчёркивания, рамки."""
    out = np.zeros_like(horiz)
    for m, horizontal in ((horiz, True), (vert, False)):
        n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        for i in range(1, n):
            x, y, w, h, _a = st[i]
            if horizontal and w >= max(ch * 7, W / 8) and h <= ch * 0.5:
                out[lab == i] = 255
            elif not horizontal and h >= max(ch * 6, H / 12) and w <= ch * 0.5:
                out[lab == i] = 255
    return out


def _merge_boxes(boxes):
    boxes = [tuple(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        out = []
        while boxes:
            b = boxes.pop()
            for i, o in enumerate(out):
                if _inter(b, o) > 0:
                    out[i] = (min(b[0], o[0]), min(b[1], o[1]), max(b[2], o[2]), max(b[3], o[3]))
                    changed = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes


def remove_border_noise(ink: np.ndarray) -> np.ndarray:
    """Тени и края листа у границ кадра (сканы книг, фото) — не содержимое страницы."""
    H, W = ink.shape
    n, lab, st, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    bad = np.zeros(n, bool)
    for i in range(1, n):
        x, y, w, h, a = st[i]
        touches = x <= 2 or y <= 2 or x + w >= W - 2 or y + h >= H - 2
        if touches and (w > W * 0.25 or h > H * 0.25 or a > ink.size * 0.002):
            bad[i] = True
    if bad.any():
        ink = ink.copy()
        ink[bad[lab]] = 0
    return ink


def analyze(gray: np.ndarray, ink: np.ndarray | None = None, image_seeds=()) -> PageLayout:
    ink = binarize(gray) if ink is None else ink
    ink = remove_border_noise(ink)
    ch = char_height(ink)
    H, W = ink.shape
    tables, _all_lines = detect_tables(ink, ch)
    tboxes = [t.box for t in tables]
    horiz, vert = find_lines(ink, ch)
    # линии убираем только внутри таблиц и если они длинные (иначе пострадают жирные буквы)
    lines = _long_lines(horiz, vert, ch, W, H)
    for (x0, y0, x1, y1) in tboxes:
        lines[y0:y1, x0:x1] |= (horiz[y0:y1, x0:x1] | vert[y0:y1, x0:x1])
    images = _merge_boxes(detect_images(gray, ink, ch, tboxes) + grow_seeds(ink, ch, image_seeds, tboxes))
    # «рисунок» на пол-страницы, внутри которого обычный текст, — ошибка (рамка, тень по краю листа)
    images = [b for b in images if _area(b) < 0.4 * H * W or
              (_area(b) < 0.75 * H * W and _few_text_like(ink[b[1]:b[3], b[0]:b[2]], ch))]
    text_mask = cv2.bitwise_and(ink, cv2.bitwise_not(lines))
    n, lab, st, _ = cv2.connectedComponentsWithStats(text_mask, connectivity=8)
    small = np.zeros(n, bool)
    small[1:] = (st[1:, 4] < max(4, ch * ch * 0.01))
    text_mask[small[lab]] = 0
    objects = [("table", t.box, t) for t in tables] + [("image", im, None) for im in images]
    pure_text = text_mask.copy()
    for _k, (x0, y0, x1, y1), _t in objects:
        pure_text[y0:y1, x0:x1] = 0

    cut_mask = pure_text.copy()
    for _k, (x0, y0, x1, y1), _t in objects:
        cut_mask[y0:y1, x0:x1] = 255  # таблицы и рисунки участвуют в разрезании как сплошные блоки
    # склеиваем буквы в слова, чтобы межбуквенные просветы не рвали блоки
    k = max(1, int(ch * 0.25))
    cut_mask = cv2.dilate(cut_mask, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1)))

    text_cut = cv2.dilate(pure_text, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1)))
    leaves = xy_cut(cut_mask, ch)
    regions = []
    band = 0
    last_band_key = None
    used = set()
    for box, container, info in leaves:
        ncol, col, key = (info if info else (1, 0, None))
        if key != last_band_key or key is None:
            band += 1
            last_band_key = key
        inside = [o for o in objects if _inter(box, o[1]) > 0.5 * _area(o[1])]
        if not inside:
            regions.append(Region("text", box, container, None, ncol, col, band))
            continue
        # лист содержит таблицы/рисунки: разрезаем его текст отдельно (текст сбоку, сверху, снизу)
        sub_leaves = xy_cut(text_cut, ch, box, container)
        items = [("text", b, None, c) for b, c, _i in sub_leaves]
        for kind, ob, tab in sorted(inside, key=lambda o: o[1][1]):
            if id(ob) in used:
                continue
            used.add(id(ob))
            pos = len(items)
            for j, it in enumerate(items):
                if it[0] == "text" and it[1][1] >= ob[1] - ch:
                    pos = j
                    break
            items.insert(pos, (kind, tuple(int(v) for v in ob), tab, container))
        for kind, b, tab, c in items:
            regions.append(Region(kind, b, c, tab, ncol, col, band))
    for kind, ob, tab in objects:
        if id(ob) not in used:
            regions.append(Region(kind, tuple(int(v) for v in ob), ob, tab))
    _fix_containers(regions)

    text_image = gray.copy()
    text_image[lines > 0] = 255
    for _k, (x0, y0, x1, y1), _t in objects:
        text_image[y0:y1, x0:x1] = 255
    cell_image = gray.copy()
    cell_image[cv2.dilate(lines, np.ones((3, 3), np.uint8)) > 0] = 255
    ys, xs = np.nonzero(pure_text)
    tb = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if xs.size else (0, 0, W, H)
    lay = PageLayout(regions, ink, ch, text_image, cell_image, tb)
    lay.lines = lines
    lay.text_mask = text_mask
    return lay


def _ink_box(mask, box):
    x0, y0, x1, y1 = [int(v) for v in box]
    if y1 <= y0 or x1 <= x0:
        return None
    sub = mask[y0:y1, x0:x1]
    ys, xs = np.nonzero(sub)
    if xs.size < 10:
        return None
    return (x0 + int(xs.min()), y0 + int(ys.min()), x0 + int(xs.max()) + 1, y0 + int(ys.max()) + 1)


def _fix_containers(regions):
    """Границы для выравнивания: у колонки — крайние края текста этой колонки,
    у текста во всю ширину — крайние края всех таких блоков страницы."""
    groups = {}
    for r in regions:
        if r.kind != "text":
            continue
        key = ("col", r.band, r.column) if r.columns > 1 else ("page",)
        groups.setdefault(key, []).append(r)
    for key, rs in groups.items():
        x0 = min(r.box[0] for r in rs)
        x1 = max(r.box[2] for r in rs)
        if key == ("page",) and len(rs) >= 2:
            # одиночные короткие строки (заголовки по центру) не должны сужать страницу
            widths = sorted(r.box[2] - r.box[0] for r in rs)
            if widths[-1] > 0:
                wide = [r for r in rs if (r.box[2] - r.box[0]) > widths[-1] * 0.6]
                x0 = min(r.box[0] for r in wide)
                x1 = max(r.box[2] for r in wide)
        for r in rs:
            r.container = (x0, r.box[1], x1, r.box[3])


def _area(b):
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def _inter(a, b):
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def _dedup(regions):
    out = []
    seen = set()
    for r in regions:
        if r.kind in ("table", "image"):
            key = (r.kind, r.box)
            if key in seen:
                continue
            seen.add(key)
        out.append(r)
    return out
