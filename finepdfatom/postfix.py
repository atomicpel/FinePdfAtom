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


# частые короткие русские слова, которые целиком читаются латиницей («Ha», «He», «TO», «o6»)
_SHORT_RU = {"на", "не", "но", "он", "от", "то", "та", "те", "ко", "ее", "её", "мы", "вы", "ты", "нас",
             "вас", "нет", "кто", "там", "тем", "тот", "око", "раз", "пар", "сто", "ум", "уж", "же",
             "см", "ст", "тот", "как", "так", "ток", "тут", "тема", "сам", "нам", "вам", "ему", "мне",
             "хор", "рот", "кот", "сор", "вес", "все", "всё", "час", "мера", "мост", "место", "текст"}


def _fix_short(text: str, cyr: int, lat: int) -> str:
    core = "".join(c for c in text if c.isalpha())
    if not core:
        return text
    # латинское слово из букв-двойников среди русского текста
    if cyr > lat * 2 and cyr > 0 and len(core) <= 5 and all(_is_lat(c) and c in LAT2CYR for c in core):
        conv = "".join(LAT2CYR[c] for c in core)
        if conv.lower() in _SHORT_RU:
            if core[0].isupper() and len(core) > 1 and core[1:].islower() or core.islower():
                conv = conv[0] + conv[1:].lower()
            elif core.isupper() and len(core) <= 2:
                conv = conv[0] + conv[1:].lower()
            return text.replace(core, conv)
    # одиночная кириллическая буква среди английского текста («to а grounded»)
    if lat > cyr * 2 and lat > 0 and len(core) == 1 and _is_cyr(core) and core in CYR2LAT:
        return text.replace(core, CYR2LAT[core])
    return text


def fix_scripts(data, langs: str):
    if "rus" not in langs and "ukr" not in langs and "bel" not in langs:
        return
    words = data.words
    para_cyr, para_lat = {}, {}
    for w in words:
        para_cyr[w.para] = para_cyr.get(w.para, 0) + sum(_is_cyr(c) for c in w.text)
        if "@" not in w.text and "/" not in w.text and "." not in w.text[:-1]:
            para_lat[w.para] = para_lat.get(w.para, 0) + sum(_is_lat(c) for c in w.text)
    for i, w in enumerate(words):
        # контекст: соседи в той же строке/абзаце
        nb = [words[j] for j in (i - 2, i - 1, i + 1, i + 2) if 0 <= j < len(words) and words[j].para == w.para]
        cyr = sum(sum(_is_cyr(c) for c in x.text) for x in nb)
        lat = sum(sum(_is_lat(c) for c in x.text) for x in nb)
        new = fix_word(w.text, cyr > lat * 2 and cyr > 0)
        if new == w.text:
            new = _fix_short(w.text, cyr, lat)
        if new == w.text and para_cyr.get(w.para, 0) > 3 * para_lat.get(w.para, 0):
            new = _fix_short(w.text, 3, 1)
        if new != w.text:
            # «направляйте Ha адрес» → «на»: заглавная посреди предложения — ошибка распознавания
            if len(new) <= 3 and new[:1].isupper() and i > 0 and words[i - 1].para == w.para and \
                    words[i - 1].text[-1:] not in ".!?:" and words[i - 1].text[-1:].isalpha():
                new = new[0].lower() + new[1:]
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


_ONE_LETTER = set("ВСКУОИАЯ")


def fix_single_letters(data):
    """«информации В автоматизированных» → «в»; одинокая «З» в ячейке таблицы → «3»."""
    words = data.words
    cell_count = {}
    for w in words:
        if w.cell is not None:
            k = (w.block, w.cell)
            cell_count[k] = cell_count.get(k, 0) + 1
    for i, w in enumerate(words):
        t = w.text
        if w.cell is not None and cell_count.get((w.block, w.cell)) == 1 and t in ("З", "з", "З.", "З)"):
            w.text = t.replace("З", "3").replace("з", "3")
            continue
        if len(t) == 1 and t in _ONE_LETTER and 0 < i < len(words) - 1:
            a, b = words[i - 1], words[i + 1]
            if a.para == w.para == b.para and a.text[-1:].islower() and b.text[:1].islower():
                w.text = t.lower()
