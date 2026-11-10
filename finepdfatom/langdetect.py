"""Автоматическое определение языков документа.

Образец страницы распознаётся сразу всеми установленными языками, затем слова
сверяются со словарями (русский, украинский, английский) и с характерными буквами.
Работает для кириллических и латинских языков; для прочих (китайский, арабский и т. п.)
язык выбирается вручную.
"""
from __future__ import annotations

import numpy as np

from . import spell, tess

AUTO = "auto"
CYR_LANGS = ("rus", "ukr", "bel", "bul", "srp", "mkd", "kaz", "kir", "tat", "bak", "chv", "uzb_cyrl", "tgk", "mon")
LAT_LANGS = ("eng", "deu", "fra", "spa", "ita", "por", "pol", "ces", "tur", "lat", "nld", "swe", "fin", "aze",
             "uzb", "ron", "hun", "lit", "lav", "est", "slk", "slv", "hrv")
DEFAULT = "rus+eng"
_UKR_CH = set("іїєґ")
_RUS_CH = set("ыэъё")


def is_auto(langs: str) -> bool:
    return not langs or langs == AUTO


def candidates(available=None) -> list:
    av = available if available is not None else tess.available_languages()
    cyr = [l for l in CYR_LANGS if l in av]
    lat = [l for l in LAT_LANGS if l in av]
    return (cyr + lat)[:8]


def fallback(available=None) -> str:
    av = available if available is not None else tess.available_languages()
    pick = [l for l in DEFAULT.split("+") if l in av]
    return "+".join(pick or av[:2]) or DEFAULT


def sample(gray: np.ndarray, max_h: int = 650) -> np.ndarray:
    """Полоса страницы с наибольшим количеством текста."""
    h = gray.shape[0]
    if h <= max_h:
        return gray
    rows = (gray[:, ::4] < 128).sum(axis=1).astype(np.int64)
    cs = np.concatenate([[0], np.cumsum(rows)])
    win = cs[max_h:] - cs[:-max_h]
    y = int(np.argmax(win))
    return np.ascontiguousarray(gray[y:y + max_h])


def _script(word: str):
    cyr = sum("Ѐ" <= c <= "ӿ" for c in word)
    lat = sum(("a" <= c <= "z") or ("A" <= c <= "Z") for c in word)
    if cyr and cyr >= lat:
        return "cyr"
    if lat:
        return "lat"
    return ""


def choose(words, available=None) -> str:
    """words — распознанные слова (строки). Возвращает строку языков вида «rus+eng»."""
    av = available if available is not None else tess.available_languages()
    ws = [w.strip(".,;:!?()«»\"'—–-") for w in words]
    ws = [w for w in ws if sum(c.isalpha() for c in w) >= 3 and not any(c.isdigit() for c in w)]
    if len(ws) < 4:
        return fallback(av)
    cyr = [w.lower() for w in ws if _script(w) == "cyr"]
    lat = [w.lower() for w in ws if _script(w) == "lat"]
    total = len(cyr) + len(lat)
    out = []
    cyr_part, lat_part = [], []
    if len(cyr) >= 3 and len(cyr) >= 0.04 * total:
        d_ru = spell._load("rus") if "rus" in av else None
        d_uk = spell._load("ukr") if "ukr" in av else None
        ru = uk = both = 0
        for w in cyr[:300]:
            a = bool(d_ru and d_ru.lookup(w))
            b = bool(d_uk and d_uk.lookup(w))
            ru += a and not b
            uk += b and not a
            both += a and b
            ru += 2 * any(c in _RUS_CH for c in w)
            uk += 2 * any(c in _UKR_CH for c in w)
        known = ru + uk + both
        if "ukr" in av and uk >= 3 and uk > ru * 1.5:
            cyr_part = ["ukr"] + (["rus"] if "rus" in av and ru >= 0.3 * uk else [])
        elif "rus" in av:
            cyr_part = ["rus"] + (["ukr"] if "ukr" in av and uk >= 3 and uk >= 0.4 * ru else [])
        elif "ukr" in av:
            cyr_part = ["ukr"]
        # слова не из известных словарей — возможно, другой кириллический язык
        others = [l for l in CYR_LANGS if l in av and l not in ("rus", "ukr")]
        if others and known < 0.4 * min(len(cyr), 300):
            cyr_part += others
    if len(lat) >= 3 and len(lat) >= 0.04 * total:
        d_en = spell._load("eng") if "eng" in av else None
        hits = sum(1 for w in lat[:300] if d_en and d_en.lookup(w))
        if "eng" in av:
            lat_part = ["eng"]
        others = [l for l in LAT_LANGS if l in av and l != "eng"]
        if others and hits < 0.5 * min(len(lat), 300):
            lat_part += others
    out = cyr_part + lat_part if len(cyr) >= len(lat) else lat_part + cyr_part
    return "+".join(out[:5]) if out else fallback(av)


def detect(engine_all, gray: np.ndarray, dpi: int = 300, available=None) -> str:
    data = engine_all.recognize(sample(gray), dpi=dpi)
    return choose([w.text for w in data.words if w.conf >= 55], available)
