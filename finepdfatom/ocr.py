"""Ядро распознавания: страница → подготовка → Tesseract → результат."""
from __future__ import annotations

import dataclasses
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import numpy as np

from . import preprocess, tess, textlayout

PSM_CHOICES = {
    "auto": (tess.PSM_AUTO, "Авто: колонки, блоки, абзацы"),
    "column": (tess.PSM_SINGLE_COLUMN, "Одна колонка текста"),
    "block": (tess.PSM_SINGLE_BLOCK, "Один блок (таблички, фрагменты)"),
    "line": (tess.PSM_SINGLE_LINE, "Одна строка"),
    "sparse": (tess.PSM_SPARSE_TEXT, "Разрозненный текст (схемы, формы)"),
}

LOW_CONF = 60.0


@dataclass
class Settings:
    langs: str = "rus+eng"
    layout: str = "auto"
    prep: preprocess.Options = field(default_factory=preprocess.Options)
    use_pdf_text: bool = True        # брать готовый текстовый слой PDF, если он есть
    keep_lines: bool = True          # сохранять разрывы строк (False — склеивать строки в абзацы)
    dehyphenate: bool = True         # склеивать переносы
    threads: int = 0                 # 0 — авто

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


class Recognizer:
    """Пул движков Tesseract (по одному на поток) + детектор ориентации."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._local = threading.local()
        self._engines = []
        self._lock = threading.Lock()
        self.cancel_event = threading.Event()
        self._pool = None

    # --- движки -----------------------------------------------------------
    def _engine(self):
        e = getattr(self._local, "engine", None)
        if e is None:
            psm = PSM_CHOICES.get(self.settings.layout, PSM_CHOICES["auto"])[0]
            e = tess.Engine(self.settings.langs, psm=psm)
            self._local.engine = e
            with self._lock:
                self._engines.append(e)
        return e

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
        eng = self._engine()
        src_dpi = dpi or 300
        try:
            data = eng.recognize(prep.ocr, dpi=int(max(70, min(1200, src_dpi * prep.scale))))
        except InterruptedError:
            raise Cancelled()
        # координаты слов → в систему display
        if prep.scale != 1.0:
            k = 1.0 / prep.scale
            for w in data.words:
                x0, y0, x1, y1 = w.box
                w.box = (x0 * k, y0 * k, x1 * k, y1 * k)
        data.width, data.height = prep.display.shape[1], prep.display.shape[0]
        _drop_noise(data)
        res = PageResult(data=data, display=prep.display, dpi=dpi or 0, notes=prep.summary(),
                         seconds=time.time() - t0)
        res.relayout(s.keep_lines, s.dehyphenate)
        return res

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
