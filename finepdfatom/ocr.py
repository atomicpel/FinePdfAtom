"""Ядро распознавания: страница → подготовка → Tesseract → результат."""
from __future__ import annotations

import dataclasses
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import formatting, langdetect, layout, postfix, preprocess, spell, superscript, tess, textlayout

PSM_CHOICES = {
    "auto": (tess.PSM_AUTO, "Авто: колонки, таблицы, рисунки, оформление"),
    "tesseract": (tess.PSM_AUTO, "Разметка Tesseract (без анализа таблиц)"),
    "column": (tess.PSM_SINGLE_COLUMN, "Одна колонка текста"),
    "block": (tess.PSM_SINGLE_BLOCK, "Один блок (таблички, фрагменты)"),
    "line": (tess.PSM_SINGLE_LINE, "Одна строка"),
    "sparse": (tess.PSM_SPARSE_TEXT, "Разрозненный текст (схемы, формы)"),
}

LOW_CONF = 60.0


@dataclass
class Settings:
    langs: str = "auto"              # «auto» — определить по документу; иначе «rus+eng»
    layout: str = "auto"
    prep: preprocess.Options = field(default_factory=preprocess.Options)
    use_pdf_text: bool = True        # брать готовый текстовый слой PDF, если он есть
    keep_lines: bool = True          # сохранять разрывы строк (False — склеивать строки в абзацы)
    dehyphenate: bool = True         # склеивать переносы
    threads: int = 0                 # 0 — авто
    spellcheck: bool = True          # проверка орфографии и исправление явных ошибок распознавания

    def worker_count(self) -> int:
        if self.threads > 0:
            return self.threads
        return max(1, min(4, (os.cpu_count() or 2)))


@dataclass
class PageResult:
    data: tess.OcrData                    # слова в координатах display
    display: np.ndarray                   # изображение, на которое ложатся слова
    dpi: int
    notes: str = ""
    from_text_layer: bool = False
    seconds: float = 0.0
    text: str = ""
    spans: list = field(default_factory=list)   # (start, end, word_index) в self.text
    langs: str = ""                             # языки, которыми распознана страница

    def relayout(self, keep_lines: bool, dehyphenate: bool):
        self.text, self.spans = textlayout.layout(self.data, keep_lines, dehyphenate)

    @property
    def mean_conf(self) -> float:
        return self.data.mean_conf

    def low_conf_words(self, threshold=LOW_CONF):
        return [w for w in self.data.words if w.conf < threshold]


class Cancelled(Exception):
    pass


def _drop_noise(data):
    """Убирает «мусорные» строки: обрезанные края, рамки, шум (низкая уверенность и мало букв)."""
    keep = []
    for _b, _p, _l, ws in data.lines():
        txt = "".join(w.text for w in ws)
        if not txt:
            continue
        letters = sum(ch.isalnum() for ch in txt) / len(txt)
        conf = sum(w.conf for w in ws) / len(ws)
        if conf < 25 or (conf < 55 and letters < 0.6) or (conf < 40 and len(txt) <= 3):
            continue
        keep.extend(ws)
    if len(keep) != len(data.words):
        data.words = keep
        if keep:
            data.mean_conf = sum(w.conf for w in keep) / len(keep)


def _dpi_trusted(dpi, shape) -> bool:
    """Можно ли доверять разрешению (для кегля шрифта)."""
    if not dpi or dpi < 100 or dpi > 1200:
        return False
    h, w = shape[:2]
    win, hin = w / float(dpi), h / float(dpi)
    return 2.0 <= win <= 20 and 2.0 <= hin <= 30


def _page_container(data):
    if not data.words:
        return (0, 0, data.width, data.height)
    return (min(w.box[0] for w in data.words), min(w.box[1] for w in data.words),
            max(w.box[2] for w in data.words), max(w.box[3] for w in data.words))


def _sb(b, k):
    return tuple(v * k for v in b) if b else b


def _scale_data(data, k):
    if k == 1.0:
        return
    for w in data.words:
        w.box = _sb(w.box, k)
    for lid in list(data.line_boxes):
        data.line_boxes[lid] = _sb(data.line_boxes[lid], k)
    for p in data.paras.values():
        p.box = _sb(p.box, k)
        p.indent *= k
        p.left_margin *= k
    seen = set()
    for b in data.blocks.values():
        b.box = _sb(b.box, k)
        b.container = _sb(b.container, k)
        t = b.table
        if t is not None and id(t) not in seen:
            seen.add(id(t))
            t.box = _sb(t.box, k)
            t.col_edges = [v * k for v in t.col_edges]
            t.row_edges = [v * k for v in t.row_edges]
            for c in t.cells:
                c.box = _sb(c.box, k)


class Recognizer:
    """Пул движков Tesseract (по одному на поток) + детектор ориентации."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._local = threading.local()
        self._engines = []
        self._lock = threading.Lock()
        self.cancel_event = threading.Event()
        self._pool = None
        self.doc_langs = ""              # языки, определённые по документу (режим «auto»)

    # --- движки -----------------------------------------------------------
    def _engine(self, langs=None):
        langs = langs or self.settings.langs
        cache = getattr(self._local, "engines", None)
        if cache is None:
            cache = self._local.engines = {}
        e = cache.get(langs)
        if e is None:
            psm = PSM_CHOICES.get(self.settings.layout, PSM_CHOICES["auto"])[0]
            e = tess.Engine(langs, psm=psm)
            cache[langs] = e
            with self._lock:
                self._engines.append(e)
        return e

    # --- языки ------------------------------------------------------------
    def _detect_engine(self):
        cands = langdetect.candidates()
        if not cands:
            return None
        key = "detect:" + "+".join(cands)
        cache = getattr(self._local, "engines", None)
        if cache is None:
            cache = self._local.engines = {}
        e = cache.get(key)
        if e is None:
            e = tess.Engine("+".join(cands), psm=tess.PSM_AUTO)
            cache[key] = e
            with self._lock:
                self._engines.append(e)
        return e

    def _page_langs(self, gray, dpi) -> str:
        """Языки для страницы: заданные, определённые по документу или по самой странице."""
        if not langdetect.is_auto(self.settings.langs):
            return self.settings.langs
        if self.doc_langs:
            return self.doc_langs
        eng = self._detect_engine()
        if eng is None:
            return langdetect.fallback()
        try:
            return langdetect.detect(eng, gray, dpi)
        except InterruptedError:
            raise
        except Exception:  # noqa: BLE001
            return langdetect.fallback()

    def detect_document_langs(self, refs) -> str:
        """Определяет языки по нескольким страницам документа (первая, средняя, последняя)."""
        n = len(refs)
        if n == 0:
            return langdetect.fallback()
        words = []
        eng = None
        for i in sorted({0, n // 2, n - 1}):
            try:
                ref = refs[i]
                if hasattr(ref, "load"):
                    image, dpi = ref.load()
                    layer = ref.pdf_words(dpi) if (self.settings.use_pdf_text and ref.kind == "pdf") else None
                    mode = getattr(ref, "hint_mode", "")
                else:
                    (image, dpi), layer, mode = ref, None, ""
                if layer:
                    words += [t[0] for t in layer]
                    continue
                popts = dataclasses.replace(self.settings.prep, mode=mode) if mode else self.settings.prep
                prep = preprocess.prepare(image, popts, self._osd())
                eng = eng or self._detect_engine()
                if eng is None:
                    break
                d = eng.recognize(langdetect.sample(prep.ocr), dpi=int(max(70, min(1200, (dpi or 300) * prep.scale))))
                words += [w.text for w in d.words if w.conf >= 55]
            except InterruptedError:
                raise
            except Exception:  # noqa: BLE001
                continue
        return langdetect.choose(words)

    def _osd(self):
        o = getattr(self._local, "osd", None)
        if o is None:
            try:
                o = tess.OsdEngine()
            except tess.TesseractError:
                o = False
            self._local.osd = o
            if o:
                with self._lock:
                    self._engines.append(o)
        return o or None

    def warmup(self):
        self._engine()

    def cancel(self):
        self.cancel_event.set()
        with self._lock:
            for e in self._engines:
                if isinstance(e, tess.Engine):
                    e.cancel()

    def close(self):
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None
        with self._lock:
            for e in self._engines:
                e.close()
            self._engines.clear()

    # --- распознавание ---------------------------------------------------
    def _check(self):
        if self.cancel_event.is_set():
            raise Cancelled()

    def recognize_image(self, image: np.ndarray, dpi: int = 0, region=None, mode: str = "") -> PageResult:
        """image — RGB numpy; region=(x, y, w, h) в координатах image; mode — тип изображения."""
        t0 = time.time()
        s = self.settings
        self._check()
        if region:
            x, y, w, h = [int(v) for v in region]
            x, y = max(0, x), max(0, y)
            image = np.ascontiguousarray(image[y:y + h, x:x + w])
        popts = dataclasses.replace(s.prep, mode=mode) if mode else s.prep
        prep = preprocess.prepare(image, popts, self._osd())
        self._check()
        src_dpi = dpi or 300
        dpi_eff = int(max(70, min(1200, src_dpi * prep.scale)))
        try:
            langs = self._page_langs(prep.ocr, dpi_eff)
        except InterruptedError:
            raise Cancelled()
        eng = self._engine(langs)
        dpi_known = _dpi_trusted(dpi, image.shape)
        try:
            data = None
            if s.layout == "auto":
                data = self._recognize_layout(eng, prep, dpi_eff, dpi_known, langs)
            if data is None:
                data = eng.recognize(prep.ocr, dpi=dpi_eff)
                _drop_noise(data)
                ink = layout.binarize(prep.ocr)
                meta = {bid: (b.box if b.kind != "image" else b.box) for bid, b in data.blocks.items()}
                cont = _page_container(data)
                postfix.fix_scripts(data, langs)
                postfix.fix_bullets_and_noise(data)
                postfix.fix_single_letters(data)
                if s.spellcheck:
                    spell.check_words(data, langs)
                formatting.analyze(data, ink, {k: cont for k in meta}, dpi_eff, dpi_known,
                                   layout.char_height(ink))
        except InterruptedError:
            raise Cancelled()
        _scale_data(data, 1.0 / prep.scale)
        data.width, data.height = prep.display.shape[1], prep.display.shape[0]
        data.dpi = src_dpi
        data.dpi_known = dpi_known
        res = PageResult(data=data, display=prep.display, dpi=dpi or 0, notes=prep.summary(),
                         seconds=time.time() - t0, langs=langs)
        res.relayout(s.keep_lines, s.dehyphenate)
        return res

    def _recognize_layout(self, eng, prep, dpi_eff, dpi_known, langs):
        """Распознавание по собственному анализу макета: блоки, колонки, таблицы, рисунки."""
        gray = prep.ocr
        seeds = [b for t, b in eng.analyse_layout(gray, dpi_eff) if t in tess.IMAGE_TYPES]
        lay = layout.analyze(gray, image_seeds=seeds)
        self._check()
        if not lay.regions:
            return None
        H, W = gray.shape
        data = tess.OcrData(width=W, height=H)
        pad = max(3, int(lay.ch * 0.6))

        def clip_rect(b, p):
            x0, y0 = max(0, int(b[0]) - p), max(0, int(b[1]) - p)
            x1, y1 = min(W, int(b[2]) + p), min(H, int(b[3]) + p)
            return (x0, y0, x1 - x0, y1 - y0)

        text_ids = [i for i, r in enumerate(lay.regions) if r.kind == "text"]
        res_text = eng.recognize_rects(lay.text_image, [clip_rect(lay.regions[i].box, pad) for i in text_ids],
                                       dpi_eff, psm=tess.PSM_SINGLE_COLUMN) if text_ids else []
        line_base = 0
        for i, r in enumerate(lay.regions):
            data.blocks[i] = tess.Block(r.kind, tuple(r.box), table=r.table, container=tuple(r.container),
                                        columns=r.columns, column=r.column, band=r.band)
            data.order.append(i)
        for i, d in zip(text_ids, res_text):
            _drop_noise(d)
            for lid, box in d.line_boxes.items():
                data.line_boxes[lid + line_base] = box
            for w in d.words:
                w.block = i
                w.line += line_base
                data.words.append(w)
            line_base += (max(d.line_boxes) + 1) if d.line_boxes else 0
        self._check()
        # ячейки таблиц
        ink_nolines = cv2.bitwise_and(lay.ink, cv2.bitwise_not(cv2.dilate(lay.lines, np.ones((3, 3), np.uint8))))
        jobs = {tess.PSM_SINGLE_LINE: [], tess.PSM_SINGLE_BLOCK: []}
        for i, r in enumerate(lay.regions):
            if r.kind != "table":
                continue
            for c in r.table.cells:
                x0, y0, x1, y1 = c.box
                m = 2
                sub = ink_nolines[y0 + m:y1 - m, x0 + m:x1 - m]
                if sub.size == 0 or int((sub > 0).sum()) < lay.ch * lay.ch * 0.15:
                    continue
                rows = np.flatnonzero(sub.max(axis=1) > 0)
                ink_h = rows[-1] - rows[0] + 1 if rows.size else 0
                psm = tess.PSM_SINGLE_LINE if ink_h < lay.ch * 2.2 else tess.PSM_SINGLE_BLOCK
                jobs[psm].append((i, c, (x0 + m, y0 + m, x1 - x0 - 2 * m, y1 - y0 - 2 * m)))
        for psm, items in jobs.items():
            if not items:
                continue
            outs = eng.recognize_rects(lay.cell_image, [it[2] for it in items], dpi_eff, psm=psm)
            for (i, c, _rect), d in zip(items, outs):
                for lid, box in d.line_boxes.items():
                    data.line_boxes[lid + line_base] = box
                for w in d.words:
                    w.block = i
                    w.line += line_base
                    w.cell = (c.row, c.col)
                    data.words.append(w)
                line_base += (max(d.line_boxes) + 1) if d.line_boxes else 0
        # порядок слов: по блокам в порядке чтения, в таблице — по ячейкам
        order_idx = {bid: n for n, bid in enumerate(data.order)}
        data.words.sort(key=lambda w: (order_idx[w.block], w.cell or (0, 0), w.line, w.box[0]))
        if data.words:
            data.mean_conf = sum(w.conf for w in data.words) / len(data.words)
        meta = {i: tuple(r.container) for i, r in enumerate(lay.regions)}
        try:
            superscript.detect(data, ink_nolines, gray, eng, dpi_eff, lay.ch)
        except InterruptedError:
            raise
        except Exception:  # noqa: BLE001 — знаки сносок не должны ронять распознавание страницы
            pass
        data.words.sort(key=lambda w: (order_idx[w.block], w.cell or (0, 0), w.line, w.box[0]))
        postfix.fix_scripts(data, langs)
        postfix.fix_bullets_and_noise(data)
        postfix.fix_single_letters(data)
        if self.settings.spellcheck:
            spell.check_words(data, langs)
        formatting.analyze(data, ink_nolines, meta, dpi_eff, dpi_known, lay.ch)
        return data

    def recognize_ref(self, ref, region=None) -> PageResult:
        """ref — sources.PageRef."""
        self._check()
        image, dpi = ref.load()
        if self.settings.use_pdf_text and region is None and ref.kind == "pdf":
            words = ref.pdf_words(dpi)
            if words:
                return self._from_text_layer(image, dpi, words)
        return self.recognize_image(image, dpi, region, getattr(ref, "hint_mode", ""))

    def _from_text_layer(self, image, dpi, words) -> PageResult:
        data = tess.OcrData(width=image.shape[1], height=image.shape[0], mean_conf=100.0)
        for text, box, block, line, _wn in words:
            data.words.append(tess.Word(text, 100.0, box, line=block * 10000 + line, para=block, block=block))
        res = PageResult(data=data, display=image, dpi=dpi, notes="текстовый слой PDF", from_text_layer=True)
        res.relayout(self.settings.keep_lines, self.settings.dehyphenate)
        return res

    def map(self, refs, on_done=None, region_for=None):
        """Параллельно распознаёт страницы. on_done(index, result|exception)."""
        self.cancel_event.clear()
        for e in list(self._engines):
            if isinstance(e, tess.Engine):
                e.cancel_flag.clear()
        results = [None] * len(refs)
        self.doc_langs = ""
        if langdetect.is_auto(self.settings.langs) and refs:
            try:
                self.doc_langs = self.detect_document_langs(refs)
            except InterruptedError:
                self.doc_langs = langdetect.fallback()
        if self._pool is None:
            # постоянный пул: движки (загруженные модели) живут между запусками
            self._pool = ThreadPoolExecutor(max_workers=self.settings.worker_count(),
                                            thread_name_prefix="ocr")
        futs = {}
        for i, r in enumerate(refs):
            reg = region_for(i) if region_for else None
            futs[self._pool.submit(self._run_one, r, reg)] = i
        for f in as_completed(futs):
            i = futs[f]
            try:
                results[i] = f.result()
            except Exception as e:  # noqa: BLE001
                results[i] = e
            if on_done:
                on_done(i, results[i])
        return results

    def _run_one(self, ref, region):
        if hasattr(ref, "load"):
            return self.recognize_ref(ref, region)
        image, dpi = ref           # (numpy, dpi) — изображение из памяти
        return self.recognize_image(image, dpi, region)
