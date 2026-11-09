"""Привязка к libtesseract (C API) через ctypes.

Работает с собственной сборкой Tesseract 5 из пакета (glibc >= 2.17), поэтому
не зависит от версии tesseract в репозиториях Astra Linux.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import threading
from dataclasses import dataclass, field

from . import paths

# Уровни итератора
RIL_BLOCK, RIL_PARA, RIL_TEXTLINE, RIL_WORD, RIL_SYMBOL = range(5)

# Режимы движка
OEM_LSTM_ONLY = 1

# Режимы сегментации страницы
PSM_OSD_ONLY = 0
PSM_AUTO = 3
PSM_SINGLE_COLUMN = 4
PSM_SINGLE_BLOCK = 6
PSM_SINGLE_LINE = 7
PSM_SPARSE_TEXT = 11

_CANCEL_FUNC = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_int)

_lib = None
_lib_lock = threading.Lock()


class TesseractError(RuntimeError):
    pass


def _load():
    global _lib
    with _lib_lock:
        if _lib is not None:
            return _lib
        import glob
        # Глушим служебные сообщения Leptonica (L_SEVERITY_NONE)
        os.environ.setdefault("LEPT_MSG_SEVERITY", "6")
        # Только встроенная сборка: системный tesseract на Astra 1.6 — версия 3.x/4.x
        candidates = sorted(glob.glob(os.path.join(paths.lib_dir(), "libtesseract.so.5*")), key=len)
        if os.environ.get("FINEPDFATOM_SYSTEM_TESSERACT"):
            sysname = ctypes.util.find_library("tesseract")
            if sysname:
                candidates.append(sysname)
        if not candidates:
            raise TesseractError("libtesseract не найдена в %s" % paths.lib_dir())
        last = None
        for c in candidates:
            try:
                lib = ctypes.CDLL(c)
                break
            except OSError as e:  # pragma: no cover
                last = e
        else:
            raise TesseractError("Не удалось загрузить libtesseract: %s" % last)

        P = ctypes.c_void_p
        I = ctypes.c_int
        S = ctypes.c_char_p

        def sig(name, res, *args):
            f = getattr(lib, name)
            f.restype = res
            f.argtypes = list(args)

        sig("TessVersion", S)
        sig("TessBaseAPICreate", P)
        sig("TessBaseAPIDelete", None, P)
        sig("TessBaseAPIEnd", None, P)
        sig("TessBaseAPIInit2", I, P, S, S, I)
        sig("TessBaseAPISetVariable", ctypes.c_int, P, S, S)
        sig("TessBaseAPISetPageSegMode", None, P, I)
        sig("TessBaseAPISetImage", None, P, P, I, I, I, I)
        sig("TessBaseAPISetSourceResolution", None, P, I)
        sig("TessBaseAPISetRectangle", None, P, I, I, I, I)
        sig("TessBaseAPIRecognize", I, P, P)
        sig("TessBaseAPIGetUTF8Text", P, P)
        sig("TessBaseAPIMeanTextConf", I, P)
        sig("TessBaseAPIClear", None, P)
        sig("TessBaseAPIGetIterator", P, P)
        sig("TessBaseAPIDetectOrientationScript", ctypes.c_int, P,
            ctypes.POINTER(I), ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_float))
        sig("TessDeleteText", None, P)
        sig("TessResultIteratorDelete", None, P)
        sig("TessResultIteratorNext", ctypes.c_int, P, I)
        sig("TessResultIteratorGetUTF8Text", P, P, I)
        sig("TessResultIteratorConfidence", ctypes.c_float, P, I)
        sig("TessResultIteratorGetPageIterator", P, P)
        sig("TessPageIteratorBoundingBox", ctypes.c_int, P, I,
            ctypes.POINTER(I), ctypes.POINTER(I), ctypes.POINTER(I), ctypes.POINTER(I))
        sig("TessPageIteratorIsAtBeginningOf", ctypes.c_int, P, I)
        sig("TessPageIteratorIsAtFinalElement", ctypes.c_int, P, I, I)
        sig("TessPageIteratorBlockType", I, P)
        sig("TessBaseAPIAnalyseLayout", P, P)
        sig("TessPageIteratorNext", I, P, I)
        sig("TessPageIteratorDelete", None, P)
        sig("TessPageIteratorParagraphInfo", None, P, ctypes.POINTER(I), ctypes.POINTER(I),
            ctypes.POINTER(I), ctypes.POINTER(I))
        sig("TessResultIteratorWordFontAttributes", ctypes.c_char_p, P, *([ctypes.POINTER(I)] * 6),
            ctypes.POINTER(I), ctypes.POINTER(I))
        sig("TessResultIteratorWordIsFromDictionary", I, P)
        sig("TessResultIteratorWordIsNumeric", I, P)
        sig("TessMonitorCreate", P)
        sig("TessMonitorDelete", None, P)
        sig("TessMonitorSetCancelFunc", None, P, _CANCEL_FUNC)
        sig("TessMonitorGetProgress", I, P)
        _lib = lib
        return lib


_stderr_lock = threading.Lock()


class _quiet_stderr:
    """Временно направляет файловый дескриптор 2 в /dev/null."""

    def __enter__(self):
        _stderr_lock.acquire()
        try:
            self.saved = os.dup(2)
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, 2)
            os.close(devnull)
        except OSError:
            self.saved = None
        return self

    def __exit__(self, *exc):
        try:
            if self.saved is not None:
                os.dup2(self.saved, 2)
                os.close(self.saved)
        finally:
            _stderr_lock.release()
        return False


def version() -> str:
    return _load().TessVersion().decode()


def _take_text(lib, ptr) -> str:
    if not ptr:
        return ""
    try:
        return ctypes.string_at(ptr).decode("utf-8", "replace")
    finally:
        lib.TessDeleteText(ptr)


# Типы блоков Tesseract (PolyBlockType)
PT_FLOWING_TEXT, PT_HEADING_TEXT, PT_PULLOUT_TEXT = 1, 2, 3
PT_TABLE, PT_VERTICAL_TEXT, PT_CAPTION_TEXT = 6, 7, 8
PT_FLOWING_IMAGE, PT_HEADING_IMAGE, PT_PULLOUT_IMAGE = 9, 10, 11
IMAGE_TYPES = (PT_FLOWING_IMAGE, PT_HEADING_IMAGE, PT_PULLOUT_IMAGE)

# Выравнивание абзаца (TessParagraphJustification)
JUST_NAMES = {0: "", 1: "left", 2: "center", 3: "right"}


@dataclass
class Word:
    text: str
    conf: float
    box: tuple  # (x0, y0, x1, y1) в пикселях изображения
    line: int
    para: int
    block: int
    pt: float = 0.0           # кегль по высоте строки (пункты, при известном dpi)
    bold: bool = False
    italic: bool = False
    in_dict: bool = True
    cell: tuple | None = None  # (row, col) для слов в ячейке таблицы


@dataclass
class Block:
    """Блок страницы: text | heading | caption | image | table."""
    kind: str
    box: tuple
    ptype: int = 0
    table: object = None       # layout.Table для kind == "table"
    container: tuple = ()      # границы колонки/страницы
    columns: int = 1           # колонок в полосе
    column: int = 0
    band: int = 0


@dataclass
class ParaInfo:
    box: tuple = (0, 0, 0, 0)
    justify: str = ""          # оценка Tesseract: left/center/right
    indent: int = 0
    list_item: bool = False
    align: str = ""            # итоговое выравнивание: left/center/right/justify
    pt: float = 0.0
    bold: bool = False
    italic: bool = False
    space_before: float = 0.0  # пункты
    heading: bool = False
    cell: bool = False
    nlines: int = 1
    nchars: int = 0
    text_end_dot: bool = False
    left_margin: float = 0.0


@dataclass
class OcrData:
    words: list = field(default_factory=list)
    width: int = 0
    height: int = 0
    mean_conf: float = 0.0
    blocks: dict = field(default_factory=dict)    # id → Block
    order: list = field(default_factory=list)     # порядок чтения блоков (id)
    paras: dict = field(default_factory=dict)     # id → ParaInfo
    line_boxes: dict = field(default_factory=dict)  # id → (x0, y0, x1, y1)
    dpi: float = 0.0           # фактическое разрешение координат (для кегля)
    dpi_known: bool = False
    body_pt: float = 11.0

    def lines(self):
        """Слова, сгруппированные по строкам: [(block, para, line, [Word])]."""
        out = []
        for w in self.words:
            key = (w.block, w.para, w.line)
            if out and out[-1][0] == key:
                out[-1][1].append(w)
            else:
                out.append((key, [w]))
        return [(k[0], k[1], k[2], ws) for k, ws in out]


class Engine:
    """Один экземпляр Tesseract. Не потокобезопасен — по одному на поток."""

    def __init__(self, langs: str, datapath: str | None = None, psm: int = PSM_AUTO,
                 variables: dict | None = None):
        self.lib = _load()
        self.datapath = datapath or paths.tessdata_dir()
        self.langs = langs
        self.h = self.lib.TessBaseAPICreate()
        with _quiet_stderr():
            rc = self.lib.TessBaseAPIInit2(self.h, self.datapath.encode(), langs.encode(), OEM_LSTM_ONLY)
        if rc != 0:
            self.lib.TessBaseAPIDelete(self.h)
            self.h = None
            raise TesseractError("Не удалось загрузить языки «%s» из %s" % (langs, self.datapath))
        self.psm = psm
        self.lib.TessBaseAPISetPageSegMode(self.h, psm)
        base_vars = {"debug_file": "/dev/null"}
        base_vars.update(variables or {})
        for k, v in base_vars.items():
            self.lib.TessBaseAPISetVariable(self.h, k.encode(), str(v).encode())
        self._monitor = None
        self._cancel_cb = None
        self.cancel_flag = threading.Event()

    def set_psm(self, psm: int):
        self.psm = psm
        self.lib.TessBaseAPISetPageSegMode(self.h, psm)

    def set_variable(self, name: str, value):
        self.lib.TessBaseAPISetVariable(self.h, name.encode(), str(value).encode())

    def close(self):
        if self.h:
            self.lib.TessBaseAPIEnd(self.h)
            self.lib.TessBaseAPIDelete(self.h)
            self.h = None
        if self._monitor:
            self.lib.TessMonitorDelete(self._monitor)
            self._monitor = None

    def __del__(self):  # pragma: no cover
        try:
            self.close()
        except Exception:
            pass

    def _set_image(self, gray, dpi: int):
        import numpy as np
        a = np.ascontiguousarray(gray, dtype=np.uint8)
        if a.ndim != 2:
            raise ValueError("ожидается полутоновое изображение")
        h, w = a.shape
        self._buf = a  # держим ссылку, пока Tesseract работает с данными
        self.lib.TessBaseAPISetImage(self.h, a.ctypes.data, w, h, 1, w)
        self.lib.TessBaseAPISetSourceResolution(self.h, max(70, int(dpi)))
        return w, h

    def recognize(self, gray, dpi: int = 300, rect=None) -> OcrData:
        """Распознаёт изображение (numpy uint8, 2D). rect=(x, y, w, h) — область."""
        lib = self.lib
        w, h = self._set_image(gray, dpi)
        if rect:
            x, y, rw, rh = [int(v) for v in rect]
            lib.TessBaseAPISetRectangle(self.h, x, y, rw, rh)
        self._ensure_monitor()
        rc = lib.TessBaseAPIRecognize(self.h, self._monitor)
        if self.cancel_flag.is_set():
            lib.TessBaseAPIClear(self.h)
            raise InterruptedError("отменено")
        if rc != 0:
            lib.TessBaseAPIClear(self.h)
            raise TesseractError("Ошибка распознавания (код %d)" % rc)
        data = OcrData(width=w, height=h, mean_conf=float(lib.TessBaseAPIMeanTextConf(self.h)))
        self._collect(data, image_blocks=rect is None)
        lib.TessBaseAPIClear(self.h)
        return data

    def _ensure_monitor(self):
        if self._monitor is None:
            lib = self.lib
            self._monitor = lib.TessMonitorCreate()
            flag = self.cancel_flag
            self._cancel_cb = _CANCEL_FUNC(lambda _p, _w: flag.is_set())
            lib.TessMonitorSetCancelFunc(self._monitor, self._cancel_cb)

    def _box(self, pi, level):
        x0, y0, x1, y1 = (ctypes.c_int() for _ in range(4))
        ok = self.lib.TessPageIteratorBoundingBox(pi, level, ctypes.byref(x0), ctypes.byref(y0),
                                                  ctypes.byref(x1), ctypes.byref(y1))
        return (x0.value, y0.value, x1.value, y1.value) if ok else None

    def _collect(self, data, image_blocks=True, base=(0, 0, 0)):
        """Разбор результата. base — смещения счётчиков (block, para, line) при добавлении."""
        lib = self.lib
        I = ctypes.c_int
        if image_blocks:
            it = lib.TessBaseAPIGetIterator(self.h)
            if it:
                pi = lib.TessResultIteratorGetPageIterator(it)
                n = 0
                while True:
                    bt = lib.TessPageIteratorBlockType(pi)
                    if bt in IMAGE_TYPES:
                        box = self._box(pi, RIL_BLOCK)
                        if box:
                            bid = 100000 + n
                            data.blocks[bid] = Block("image", box, bt)
                            data.order.append(bid)
                            n += 1
                    if not lib.TessResultIteratorNext(it, RIL_BLOCK):
                        break
                lib.TessResultIteratorDelete(it)
        it = lib.TessBaseAPIGetIterator(self.h)
        if not it:
            return
        pi = lib.TessResultIteratorGetPageIterator(it)
        block, para, line = base[0] - 1, base[1] - 1, base[2] - 1
        flags = [I() for _ in range(6)]
        psize, fid = I(), I()
        line_pt = 0.0
        while True:
            if lib.TessPageIteratorIsAtBeginningOf(pi, RIL_BLOCK):
                block += 1
                bt = lib.TessPageIteratorBlockType(pi)
                kind = {PT_HEADING_TEXT: "heading", PT_CAPTION_TEXT: "caption"}.get(bt, "text")
                data.blocks[block] = Block(kind, self._box(pi, RIL_BLOCK) or (0, 0, 0, 0), bt)
                data.order.append(block)
            if lib.TessPageIteratorIsAtBeginningOf(pi, RIL_PARA):
                para += 1
                j, lst, crown, ind = I(), I(), I(), I()
                lib.TessPageIteratorParagraphInfo(pi, ctypes.byref(j), ctypes.byref(lst), ctypes.byref(crown),
                                                  ctypes.byref(ind))
                data.paras[para] = ParaInfo(self._box(pi, RIL_PARA) or (0, 0, 0, 0),
                                            JUST_NAMES.get(j.value, ""), ind.value, bool(lst.value))
            if lib.TessPageIteratorIsAtBeginningOf(pi, RIL_TEXTLINE):
                line += 1
                data.line_boxes[line] = self._box(pi, RIL_TEXTLINE) or (0, 0, 0, 0)
                lib.TessResultIteratorWordFontAttributes(it, *[ctypes.byref(f) for f in flags],
                                                         ctypes.byref(psize), ctypes.byref(fid))
                line_pt = float(psize.value)
            txt = _take_text(lib, lib.TessResultIteratorGetUTF8Text(it, RIL_WORD))
            if txt.strip():
                box = self._box(pi, RIL_WORD)
                conf = float(lib.TessResultIteratorConfidence(it, RIL_WORD))
                in_dict = bool(lib.TessResultIteratorWordIsFromDictionary(it) or
                               lib.TessResultIteratorWordIsNumeric(it))
                data.words.append(Word(txt.strip(), conf, box, line, para, block, pt=line_pt, in_dict=in_dict))
            if not lib.TessResultIteratorNext(it, RIL_WORD):
                break
        lib.TessResultIteratorDelete(it)

    def analyse_layout(self, gray, dpi: int = 300):
        """Быстрый анализ разметки без распознавания: [(тип блока, box)]."""
        lib = self.lib
        self._set_image(gray, dpi)
        out = []
        pi = lib.TessBaseAPIAnalyseLayout(self.h)
        if pi:
            while True:
                box = self._box(pi, RIL_BLOCK)
                if box:
                    out.append((lib.TessPageIteratorBlockType(pi), box))
                if not lib.TessPageIteratorNext(pi, RIL_BLOCK):
                    break
            lib.TessPageIteratorDelete(pi)
        lib.TessBaseAPIClear(self.h)
        return out

    def recognize_rects(self, gray, rects, dpi: int = 300, psm: int = PSM_SINGLE_BLOCK):
        """Распознаёт набор прямоугольников одного изображения (ячейки таблиц).

        Возвращает список OcrData (координаты — в системе всего изображения)."""
        lib = self.lib
        self._set_image(gray, dpi)
        self._ensure_monitor()
        out = []
        try:
            self.lib.TessBaseAPISetPageSegMode(self.h, psm)
            for (x, y, rw, rh) in rects:
                if self.cancel_flag.is_set():
                    raise InterruptedError("отменено")
                lib.TessBaseAPISetRectangle(self.h, int(x), int(y), int(rw), int(rh))
                rc = lib.TessBaseAPIRecognize(self.h, self._monitor)
                d = OcrData(width=rw, height=rh)
                if rc == 0:
                    self._collect(d, image_blocks=False)
                out.append(d)
        finally:
            self.lib.TessBaseAPISetPageSegMode(self.h, self.psm)
            lib.TessBaseAPIClear(self.h)
        return out

    def cancel(self):
        self.cancel_flag.set()


class OsdEngine:
    """Определение ориентации страницы (0/90/180/270) по модели osd."""

    def __init__(self, datapath: str | None = None):
        self.lib = _load()
        self.h = self.lib.TessBaseAPICreate()
        dp = (datapath or paths.tessdata_dir()).encode()
        # osd — модель «старого» движка, OEM 0; при загрузке Leptonica шумит в stderr
        with _quiet_stderr():
            rc = self.lib.TessBaseAPIInit2(self.h, dp, b"osd", 0)
        if rc != 0:
            self.lib.TessBaseAPIDelete(self.h)
            self.h = None
            raise TesseractError("Модель osd.traineddata не найдена")
        self.lib.TessBaseAPISetPageSegMode(self.h, PSM_OSD_ONLY)
        self.lib.TessBaseAPISetVariable(self.h, b"debug_file", b"/dev/null")

    def detect(self, gray, dpi=300):
        """Возвращает (угол поворота по часовой стрелке для исправления, уверенность)."""
        import numpy as np
        a = np.ascontiguousarray(gray, dtype=np.uint8)
        h, w = a.shape
        self.lib.TessBaseAPISetImage(self.h, a.ctypes.data, w, h, 1, w)
        self.lib.TessBaseAPISetSourceResolution(self.h, int(dpi))
        deg = ctypes.c_int()
        oconf = ctypes.c_float()
        script = ctypes.c_char_p()
        sconf = ctypes.c_float()
        ok = self.lib.TessBaseAPIDetectOrientationScript(self.h, ctypes.byref(deg), ctypes.byref(oconf),
                                                         ctypes.byref(script), ctypes.byref(sconf))
        self.lib.TessBaseAPIClear(self.h)
        if not ok:
            return 0, 0.0
        return deg.value % 360, float(oconf.value)

    def close(self):
        if self.h:
            self.lib.TessBaseAPIEnd(self.h)
            self.lib.TessBaseAPIDelete(self.h)
            self.h = None


def available_languages(datapath: str | None = None) -> list:
    dirs = [datapath] if datapath else paths.tessdata_dirs()
    langs = set()
    for d in dirs:
        try:
            for f in os.listdir(d):
                if f.endswith(".traineddata") and f != "osd.traineddata":
                    langs.add(f[:-len(".traineddata")])
        except OSError:
            pass
    return sorted(langs)
