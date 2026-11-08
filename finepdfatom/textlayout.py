"""Сборка текста из слов: строки, абзацы, склейка переносов.

Помимо текста возвращает «карту» — какие символы текста соответствуют какому слову
на изображении. По ней интерфейс подсвечивает сомнительные слова и связывает
текст с рамками на картинке.
"""
from __future__ import annotations

import re

_HYPHENS = ("-", "‐", "‑", "¬")


def layout(data, keep_lines: bool = True, dehyphenate: bool = True):
    """Возвращает (text, spans), spans — список (start, end, word_index)."""
    # группируем индексы слов: абзацы → строки
    paras = []
    cur_key = None
    idx = 0
    for block, para, _line, ws in data.lines():
        key = (block, para)
        ids = list(range(idx, idx + len(ws)))
        idx += len(ws)
        if key != cur_key:
            paras.append([])
            cur_key = key
        paras[-1].append(ids)

    words = data.words
    out = []
    spans = []
    pos = 0

    def emit(s, wi=None):
        nonlocal pos
        if wi is not None:
            spans.append((pos, pos + len(s), wi))
        out.append(s)
        pos += len(s)

    first_para = True
    for lines in paras:
        if not any(lines):
            continue
        if not first_para:
            emit("\n\n")
        first_para = False
        for li, ids in enumerate(lines):
            if li > 0:
                prev_last = words[lines[li - 1][-1]].text if lines[li - 1] else ""
                nxt = words[ids[0]].text if ids else ""
                hyph = (dehyphenate and len(prev_last) > 1 and prev_last.endswith(_HYPHENS)
                        and prev_last[-2].isalpha() and nxt[:1].islower())
                if hyph:
                    # убираем дефис у предыдущего слова
                    out[-1] = out[-1][:-1]
                    pos -= 1
                    s, e, wi = spans[-1]
                    spans[-1] = (s, e - 1, wi)
                elif keep_lines:
                    emit("\n")
                else:
                    emit(" ")
            for k, wi in enumerate(ids):
                if k > 0:
                    emit(" ")
                emit(words[wi].text, wi)
    return "".join(out), spans


def page_text(data, keep_lines: bool = True, dehyphenate: bool = True) -> str:
    return layout(data, keep_lines, dehyphenate)[0]


def normalize_spaces(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ([,.;:!?»)])", r"\1", text)
    text = re.sub(r"([«(]) ", r"\1", text)
    return text
