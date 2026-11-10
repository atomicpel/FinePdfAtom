"""Проверка орфографии по словарям Hunspell (как в LibreOffice) — без интернета.

Словари: /opt/finepdfatom/dict/<код>.aff/.dic; свой словарь пользователя —
~/.local/share/finepdfatom/user_dict.txt (по слову в строке).
"""
from __future__ import annotations

import os
import re
import threading

from . import paths

LANG_DICTS = {"rus": "ru_RU", "eng": "en_US", "ukr": "uk_UA"}
CYR = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяіїєґ"
LAT = "abcdefghijklmnopqrstuvwxyz"

_lock = threading.RLock()
_dicts = {}
_user = None
_TRIM = re.compile(r"^[^\w]+|[^\w]+$", re.UNICODE)


def dict_dir() -> str:
    return os.path.join(paths.app_root(), "dict")


def user_dict_path() -> str:
    return os.path.join(paths.data_home(), "user_dict.txt")


def available(langs: str) -> list:
    out = []
    for l in langs.split("+"):
        name = LANG_DICTS.get(l)
        if name and os.path.exists(os.path.join(dict_dir(), name + ".dic")):
            out.append(l)
    return out


def _load(lang):
    with _lock:
        if lang in _dicts:
            return _dicts[lang]
        name = LANG_DICTS.get(lang)
        d = None
        if name:
            base = os.path.join(dict_dir(), name)
            if os.path.exists(base + ".dic"):
                try:
                    from spylls.hunspell import Dictionary
                    d = Dictionary.from_files(base)
                except Exception:  # noqa: BLE001
                    d = None
        _dicts[lang] = d
        return d


def _user_words():
    global _user
    with _lock:
        if _user is None:
            _user = set()
            try:
                with open(user_dict_path(), encoding="utf-8") as f:
                    _user = {ln.strip().lower() for ln in f if ln.strip()}
            except OSError:
                pass
        return _user


def add_user_word(word: str):
    w = clean(word)
    if not w:
        return
    words = _user_words()
    if w.lower() in words:
        return
    words.add(w.lower())
    os.makedirs(os.path.dirname(user_dict_path()), exist_ok=True)
    with open(user_dict_path(), "a", encoding="utf-8") as f:
        f.write(w + "\n")


def clean(word: str) -> str:
    w = _TRIM.sub("", word.replace("­", ""))
    return w.replace("ё", "е").replace("Ё", "Е") if False else w


def _script(w):
    lw = w.lower()
    c = sum(ch in CYR for ch in lw)
    l = sum(ch in LAT for ch in lw)
    if c and not l:
        return "cyr"
    if l and not c:
        return "lat"
    return "mixed" if (c and l) else ""


class Checker:
    def __init__(self, langs: str):
        self.langs = [l for l in langs.split("+") if l in LANG_DICTS]
        self.cyr = [l for l in self.langs if l in ("rus", "ukr")]
        self.lat = [l for l in self.langs if l == "eng"]

    def enabled(self) -> bool:
        return bool(available("+".join(self.langs)))

    def _dicts_for(self, w):
        sc = _script(w)
        langs = self.cyr if sc == "cyr" else self.lat if sc == "lat" else []
        return [d for d in (_load(l) for l in langs) if d is not None]

    def skip(self, w: str) -> bool:
        """Не проверяем: числа, коды, аббревиатуры, одиночные буквы, слова со смешанным алфавитом."""
        if len(w) < 2 or any(ch.isdigit() for ch in w):
            return True
        if "@" in w or "/" in w or "." in w or "_" in w:      # адреса сайтов и почты, имена файлов
            return True
        if w.isupper() and len(w) <= 5:
            return True
        return _script(w) in ("", "mixed")

    def check(self, word: str) -> bool:
        w = clean(word)
        if not w or self.skip(w):
            return True
        if w.lower() in _user_words():
            return True
        ds = self._dicts_for(w)
        if not ds:
            return True
        parts = [p for p in re.split(r"[-‐‑]", w) if p] if "-" in w else [w]
        with _lock:
            if any(d.lookup(w) for d in ds):
                return True
            # составные через дефис: каждая часть должна быть словом
            if len(parts) > 1 and all(any(d.lookup(p) for d in ds) for p in parts):
                return True
            # слово с заглавной в начале предложения / ВСЕ ЗАГЛАВНЫЕ
            if w.isupper() and any(d.lookup(w.capitalize()) or d.lookup(w.lower()) for d in ds):
                return True
        return False

    def suggest(self, word: str, limit: int = 6) -> list:
        w = clean(word)
        if not w:
            return []
        ds = self._dicts_for(w)
        out = []
        with _lock:
            for d in ds:
                try:
                    for s in d.suggest(w):
                        if s not in out:
                            out.append(s)
                        if len(out) >= limit:
                            break
                except Exception:  # noqa: BLE001
                    pass
        return out[:limit]

    def near_fix(self, word: str):
        """Единственное словарное слово на расстоянии одной замены/вставки/удаления буквы —
        типичная ошибка распознавания («иноормации» → «информации»). Иначе None."""
        w = clean(word)
        if not w or self.skip(w) or len(w) < 5:
            return None
        ds = self._dicts_for(w)
        if not ds:
            return None
        alpha = CYR[:33] if _script(w) == "cyr" else LAT
        low = w.lower()
        cands = set()
        for i in range(len(low)):
            for ch in alpha:
                if ch != low[i]:
                    cands.add(low[:i] + ch + low[i + 1:])
            cands.add(low[:i] + low[i + 1:])
        for i in range(len(low) + 1):
            for ch in alpha:
                cands.add(low[:i] + ch + low[i:])
        found = []
        with _lock:
            for c in cands:
                if any(d.lookup(c) for d in ds):
                    found.append(c)
                    if len(found) > 1:
                        return None
        if len(found) != 1:
            return None
        fix = found[0]
        # восстанавливаем регистр
        if w.isupper():
            fix = fix.upper()
        elif w[0].isupper():
            fix = fix[0].upper() + fix[1:]
        return word.replace(w, fix)


def check_words(data, langs: str, autofix: bool = True, max_fix: int = 40):
    """Отмечает слова не из словаря (w.in_dict) и исправляет явные ошибки распознавания."""
    ch = Checker(langs)
    if not ch.enabled():
        return 0
    fixed = 0
    for w in data.words:
        ok = ch.check(w.text)
        if not ok and autofix and fixed < max_fix and w.conf < 75:
            fx = ch.near_fix(w.text)
            if fx:
                w.text = fx
                w.conf = max(w.conf, 70.0)
                fixed += 1
                ok = True
        w.in_dict = ok
    return fixed
