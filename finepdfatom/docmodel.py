"""Логическая модель документа для экспорта с оформлением.

Страница → последовательность элементов в порядке чтения:
  Para       — абзац/заголовок с фрагментами (жирный/курсив), кегль, выравнивание, отступы;
  TableItem  — таблица: сетка, объединения, текст ячеек, ширины столбцов;
  ImageItem  — рисунок (PNG/JPEG) с размером в пунктах;
  Columns    — начало многоколоночного участка (n колонок), ColumnBreak, ColumnsEnd.
"""
from __future__ import annotations

import copy
import io
import re
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from . import textlayout

A4 = (595.3, 841.9)


@dataclass
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    href: str = ""                # гиперссылка
    sup: bool = False             # надстрочный знак
    note: object = None           # сноска: список Para (текст сноски), знак сноски ставится на месте фрагмента


@dataclass
class Para:
    runs: list
    pt: float = 11.0
    align: str = "left"           # left | center | right | justify
    heading: int = 0              # 0 — обычный абзац, 1..3 — уровень заголовка
    indent: float = 0.0           # красная строка, пт
    left: float = 0.0             # отступ слева, пт
    space_before: float = 0.0
    list_item: bool = False
    fbox: tuple = ()              # положение на странице в долях (x0, y0, x1, y1)
    nlines: int = 1
    role: str = ""                # header | footer | pagenum | footnote

    @property
    def text(self):
        return "".join(r.text for r in self.runs)


@dataclass
class CellItem:
    row: int
    col: int
    rowspan: int
    colspan: int
    paras: list                   # [Para]
    align: str = "left"


@dataclass
class TableItem:
    rows: int
    cols: int
    col_widths: list              # пункты
    row_heights: list             # пункты
    cells: list                   # [CellItem]
    fbox: tuple = ()
    ruled: bool = True            # False — таблица без линий


@dataclass
class ImageItem:
    data: bytes
    fmt: str                      # png | jpeg
    width: float                  # пункты
    height: float
    align: str = "center"
    fbox: tuple = ()


@dataclass
class Columns:
    n: int
    gap: float = 18.0


class ColumnBreak:
    pass


class ColumnsEnd:
    pass


@dataclass
class PageModel:
    items: list
    width: float = A4[0]          # пункты
    height: float = A4[1]
    margins: tuple = (56.7, 56.7, 56.7, 56.7)   # слева, сверху, справа, снизу
    body_pt: float = 11.0
    tables: list = field(default_factory=list)
    soft_start: bool = False      # страница продолжает абзац предыдущей — без разрыва страницы
    index: int = 0


_NUM_RE = re.compile(r"^[-+−]?\d{1,3}(?:[  ]\d{3})*(?:[.,]\d+)?%?$|^[-+−]?\d+(?:[.,]\d+)?%?$")


def is_number(s: str) -> bool:
    return bool(_NUM_RE.match(s.strip()))


def to_number(s: str):
    t = s.strip().replace(" ", "").replace(" ", "").replace("−", "-")
    pct = t.endswith("%")
    if pct:
        t = t[:-1]
    t = t.replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return None
    if pct:
        v /= 100.0
    elif v.is_integer() and "." not in t and len(t.lstrip("-+")) < 15:
        v = int(v)
    return v


def page_data(result, edited_text=None):
    """Данные страницы с учётом ручных правок текста (копия, исходник не меняется)."""
    if edited_text is None or edited_text == result.text:
        return result.data
    d = copy.deepcopy(result.data)
    textlayout.apply_edits(d, result.text, result.spans, edited_text)
    return d


def _runs(words, dehyphenate=True):
    """Слова абзаца → фрагменты (склейка строк и переносов)."""
    runs = []
    prev = None
    for w in words:
        t = w.text
        if prev is not None:
            if prev.line != w.line and dehyphenate and textlayout._is_hyphen_break(prev.text, t):
                # убираем дефис переноса у последнего фрагмента
                last = runs[-1]
                if last.text.endswith(("-", "‐", "‑", "¬")):
                    last.text = last.text[:-1]
                sep = ""
            else:
                sep = " "
        else:
            sep = ""
        if runs and runs[-1].bold == w.bold and runs[-1].italic == w.italic:
            runs[-1].text += sep + t
        else:
            if sep and runs:
                runs[-1].text += sep
            runs.append(Run(t, w.bold, w.italic))
        prev = w
    return runs


def _heading_level(p, body_pt):
    if not p.heading:
        return 0
    if p.pt >= body_pt * 1.6:
        return 1
    if p.pt >= body_pt * 1.2:
        return 2
    return 3


def build(result, edited_text=None, plain=False, dehyphenate=True) -> PageModel:
    data = page_data(result, edited_text)
    dpi = data.dpi or result.dpi or 300
    known = getattr(data, "dpi_known", False)
    H, W = result.display.shape[:2]
    if known:
        pw, ph = W / dpi * 72.0, H / dpi * 72.0
        if not (300 < pw < 1300 and 400 < ph < 1800):
            pw, ph = A4
            known = False
    else:
        pw, ph = A4
    # масштаб «пиксели → пункты»: по dpi или так, чтобы текст страницы лёг в поля A4
    words = data.words
    if words:
        tx0 = min(w.box[0] for w in words)
        tx1 = max(w.box[2] for w in words)
        ty0 = min(w.box[1] for w in words)
    else:
        tx0, tx1, ty0 = 0, W, 0
    for b in data.blocks.values():
        if b.kind in ("table", "image"):
            tx0, tx1 = min(tx0, b.box[0]), max(tx1, b.box[2])
    if known:
        k = 72.0 / dpi
        ml = max(28.0, min(90.0, tx0 * k))
        mr = max(28.0, min(90.0, (W - tx1) * k))
        mt = max(28.0, min(72.0, ty0 * k))
    else:
        ml = mr = mt = 56.7
        k = (pw - ml - mr) / max(1.0, tx1 - tx0)
    model = PageModel([], pw, ph, (ml, mt, mr, 40.0), getattr(data, "body_pt", 11.0) or 11.0)
    usable = pw - ml - mr

    by_block = {}
    for w in words:
        by_block.setdefault(w.block, []).append(w)
    order = [b for b in data.order if b in data.blocks]
    for b in by_block:
        if b not in order:
            order.append(b)

    if plain:
        for bid in order:
            blk = data.blocks.get(bid)
            if blk is not None and blk.kind == "image":
                continue
            ws = by_block.get(bid, [])
            if blk is not None and blk.kind == "table" and blk.table is not None:
                model.items.append(_table_item(blk, ws, data, k, plain=True))
                continue
            for pid, pws in _group(ws, lambda w: w.para):
                model.items.append(Para(_runs(pws, dehyphenate) if not plain else
                                        [Run(" ".join(x.text for x in pws))], model.body_pt))
        return model

    cur_band = None
    cur_cols = 1
    for bid in order:
        blk = data.blocks.get(bid)
        if blk is None:
            ws = by_block.get(bid, [])
            for pid, pws in _group(ws, lambda w: w.para):
                model.items.append(_para(data, pid, pws, k, model.body_pt, dehyphenate))
            continue
        # колонки
        if blk.columns > 1 and blk.kind == "text":
            if cur_band != blk.band:
                if cur_cols > 1:
                    model.items.append(ColumnsEnd())
                model.items.append(Columns(blk.columns, _gutter(data, blk.band, k)))
                cur_band, cur_cols, cur_col = blk.band, blk.columns, blk.column
            elif blk.column != cur_col:
                model.items.append(ColumnBreak())
                cur_col = blk.column
        elif cur_cols > 1:
            model.items.append(ColumnsEnd())
            cur_band, cur_cols = None, 1
        if blk.kind == "image":
            img = _image_item(result.display, blk.box, k, usable)
            if img:
                img.fbox = _fb(blk.box, W, H)
                model.items.append(img)
            continue
        ws = by_block.get(bid, [])
        if blk.kind == "table" and blk.table is not None:
            t = _table_item(blk, ws, data, k)
            t.fbox = _fb(blk.box, W, H)
            model.items.append(t)
            model.tables.append(t)
            continue
        for pid, pws in _group(ws, lambda w: w.para):
            model.items.append(_para(data, pid, pws, k, model.body_pt, dehyphenate, blk.container or None))
    if cur_cols > 1:
        model.items.append(ColumnsEnd())
    return model


def _fb(box, W, H):
    return (box[0] / float(W), box[1] / float(H), box[2] / float(W), box[3] / float(H))


def _group(ws, key):
    out = []
    for w in ws:
        kv = key(w)
        if out and out[-1][0] == kv:
            out[-1][1].append(w)
        else:
            out.append((kv, [w]))
    return out


def _para(data, pid, ws, k, body_pt, dehyphenate, container=None):
    info = data.paras.get(pid)
    p = Para(_runs(ws, dehyphenate), body_pt)
    if ws and data.width and data.height:
        W, H = float(data.width), float(data.height)
        p.fbox = (min(w.box[0] for w in ws) / W, min(w.box[1] for w in ws) / H,
                  max(w.box[2] for w in ws) / W, max(w.box[3] for w in ws) / H)
        p.nlines = len({w.line for w in ws})
    if info is not None:
        p.pt = info.pt or body_pt
        p.align = info.align or "left"
        p.heading = _heading_level(info, body_pt)
        p.indent = max(0.0, min(72.0, info.indent * k))
        p.left = max(0.0, min(200.0, info.left_margin * k))
        p.space_before = max(0.0, min(36.0, info.space_before))
        p.list_item = info.list_item
        if info.bold:
            for r in p.runs:
                r.bold = True
    return p


def _gutter(data, band, k):
    bs = sorted((b for b in data.blocks.values() if b.band == band and b.columns > 1), key=lambda b: b.box[0])
    gaps = []
    for a, b in zip(bs, bs[1:]):
        if b.column != a.column:
            gaps.append(b.box[0] - a.box[2])
    g = (sum(gaps) / len(gaps) * k) if gaps else 18.0
    return max(9.0, min(48.0, g))


def _image_item(display, box, k, usable):
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    H, W = display.shape[:2]
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    crop = np.ascontiguousarray(display[y0:y1, x0:x1])
    buf = io.BytesIO()
    im = Image.fromarray(crop)
    # фото → JPEG, штриховые рисунки → PNG
    g = crop.mean(axis=2) if crop.ndim == 3 else crop
    mid = float(np.mean((g > 60) & (g < 200)))
    if mid > 0.2:
        im.save(buf, "JPEG", quality=85, optimize=True)
        fmt = "jpeg"
    else:
        im.save(buf, "PNG", optimize=True)
        fmt = "png"
    wpt, hpt = (x1 - x0) * k, (y1 - y0) * k
    if wpt > usable:
        hpt *= usable / wpt
        wpt = usable
    return ImageItem(buf.getvalue(), fmt, wpt, hpt)


def _table_item(blk, ws, data, k, plain=False):
    t = blk.table
    cw = [max(12.0, (t.col_edges[i + 1] - t.col_edges[i]) * k) for i in range(t.cols)] \
        if len(t.col_edges) == t.cols + 1 else [72.0] * t.cols
    rh = [max(10.0, (t.row_edges[i + 1] - t.row_edges[i]) * k) for i in range(t.rows)] \
        if len(t.row_edges) == t.rows + 1 else [14.0] * t.rows
    by_cell = {}
    for w in ws:
        by_cell.setdefault(w.cell, []).append(w)
    cells = []
    for c in t.cells:
        cws = by_cell.get((c.row, c.col), [])
        paras = []
        align = "left"
        if cws:
            for pid, pws in _group(cws, lambda w: w.para):
                info = data.paras.get(pid)
                runs = [Run(" ".join(w.text for w in pws), all(w.bold for w in pws))] if plain else _runs(pws)
                paras.append(Para(runs, (info.pt if info and info.pt else data.body_pt)))
            # выравнивание текста в ячейке — по положению слов
            x0 = min(w.box[0] for w in cws)
            x1 = max(w.box[2] for w in cws)
            lg, rg = x0 - c.box[0], c.box[2] - x1
            width = max(1.0, c.box[2] - c.box[0])
            if abs(lg - rg) < width * 0.12 and lg > width * 0.08:
                align = "center"
            elif rg < lg * 0.5 and lg > width * 0.25:
                align = "right"
        cells.append(CellItem(c.row, c.col, c.rowspan, c.colspan, paras, align))
    return TableItem(t.rows, t.cols, cw, rh, cells, ruled=getattr(t, "ruled", True))
