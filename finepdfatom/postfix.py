"""Исправления после распознавания: смешение кириллицы и латиницы.

Tesseract с моделями rus+eng иногда выдаёт латинские буквы-двойники внутри русских
слов («иноормациu», «B» вместо «в»). Слово приводится к алфавиту, который в нём
преобладает; одиночные латинские буквы среди русского текста заменяются кириллицей.
"""
from __future__ import annotations

LAT2CYR = {"A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М", "O": "О", "P": "Р",
           "T": "Т", "X": "Х", "Y": "У", "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х",
           "y": "у", "k": "к", "m": "т", "u": "и", "n": "п", "r": "г"}
CYR2LAT = {"А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K", "М": "M", "О": "O", "Р": "P",
           "Т": "T", "Х": "X", "а": "a", "с": "c", "е": "e", "о": "o", "р": "p", "х": "x", "у": "y"}
# одиночное слово-буква: как читается в русском тексте
SINGLE = {"B": "в", "b": "в", "c": "с", "C": "С", "y": "у", "Y": "У", "o": "о", "O": "О", "a": "а",
          "A": "А", "K": "К", "k": "к", "u": "и", "U": "И", "N": "И", "M": "М", "H": "Н"}


def _is_cyr(ch):
    return "Ѐ" <= ch <= "ӿ"


def _is_lat(ch):
    return ("a" <= ch <= "z") or ("A" <= ch <= "Z")


def fix_word(text: str, ctx_cyr: bool) -> str:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return text
    ncyr = sum(_is_cyr(c) for c in letters)
    nlat = sum(_is_lat(c) for c in letters)
    core = "".join(c for c in text if c.isalpha())
    if ctx_cyr and nlat and not ncyr and len(core) == 1 and core in SINGLE:
        return text.replace(core, SINGLE[core])
    if ncyr and nlat:
        if ncyr >= nlat:
            return "".join(LAT2CYR.get(c, c) for c in text)
        if all(c in CYR2LAT or _is_lat(c) or not c.isalpha() for c in text):
            return "".join(CYR2LAT.get(c, c) for c in text)
    return text


def fix_scripts(data, langs: str):
    if "rus" not in langs and "ukr" not in langs and "bel" not in langs:
        return
    words = data.words
    for i, w in enumerate(words):
        # контекст: соседи в той же строке/абзаце
        nb = [words[j] for j in (i - 2, i - 1, i + 1, i + 2) if 0 <= j < len(words) and words[j].para == w.para]
        cyr = sum(sum(_is_cyr(c) for c in x.text) for x in nb)
        lat = sum(sum(_is_lat(c) for c in x.text) for x in nb)
        new = fix_word(w.text, cyr > lat * 2 and cyr > 0)
        if new != w.text:
            w.text = new


BULLET_MISREADS = {"®", "¢", "©", "*", "°", "«", "•", "●", "e", "o", "+"}


def fix_bullets_and_noise(data):
    """Маркеры списков, прочитанные как «®», «¢»; одиночные «|» по краям рисунков и рамок."""
    words = data.words
    first_in_line = set()
    seen = set()
    for i, w in enumerate(words):
        if w.line not in seen:
            seen.add(w.line)
            first_in_line.add(i)
    keep = []
    for i, w in enumerate(words):
        t = w.text
        if t in ("|", "¦", "||", "!") and w.conf < 85 and w.cell is None:
            continue
        if i in first_in_line and t in BULLET_MISREADS and i + 1 < len(words) and words[i + 1].line == w.line:
            x0, y0, x1, y1 = w.box
            nx0, ny0, nx1, ny1 = words[i + 1].box
            lh = max(1.0, ny1 - ny0)
            # маркер: небольшой, почти квадратный знак с заметным отступом от текста
            if (x1 - x0) < lh * 0.9 and (y1 - y0) < lh * 0.95 and (nx0 - x1) > (x1 - x0) * 0.4:
                w.text = "•"
        keep.append(w)
    data.words = keep
