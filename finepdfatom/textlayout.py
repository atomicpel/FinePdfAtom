"""Сборка текста из слов: блоки, абзацы, строки, таблицы, склейка переносов.

Помимо текста возвращает «карту» — какие символы текста соответствуют какому слову
на изображении. По ней интерфейс подсвечивает сомнительные слова, связывает
текст с рамками на картинке и переносит ручные правки обратно в слова.
"""
from __future__ import annotations

import re

_HYPHENS = ("-", "‐", "‑", "¬")


class _Out:
    def __init__(self):
        self.parts = []
        self.spans = []
        self.pos = 0

    def emit(self, s, wi=None):
        if wi is not None:
            self.spans.append((self.pos, self.pos + len(s), wi))
        self.parts.append(s)
        self.pos += len(s)

    def drop_last_char(self):
        self.parts[-1] = self.parts[-1][:-1]
        self.pos -= 1
        s, e, wi = self.spans[-1]
        self.spans[-1] = (s, e - 1, wi)

    def text(self):
        return "".join(self.parts)


def _is_hyphen_break(prev_word: str, next_word: str) -> bool:
    return (len(prev_word) > 1 and prev_word.endswith(_HYPHENS) and prev_word[-2].isalpha()
            and next_word[:1].islower())


def _emit_lines(out, words, lines, keep_lines, dehyphenate):
    """lines — списки индексов слов по строкам."""
    for li, ids in enumerate(lines):
        if li > 0:
            prev_last = words[lines[li - 1][-1]].text if lines[li - 1] else ""
            nxt = words[ids[0]].text if ids else ""
            if dehyphenate and _is_hyphen_break(prev_last, nxt):
                out.drop_last_char()
            elif keep_lines:
                out.emit("\n")
            else:
                out.emit(" ")
        for k, wi in enumerate(ids):
            if k > 0:
                out.emit(" ")
            out.emit(words[wi].text, wi)


def _group(indices, words, key):
    groups = []
    for wi in indices:
        k = key(words[wi])
        if groups and groups[-1][0] == k:
            groups[-1][1].append(wi)
        else:
            groups.append((k, [wi]))
    return groups


def layout(data, keep_lines: bool = True, dehyphenate: bool = True):
    """Возвращает (text, spans), spans — список (start, end, word_index)."""
    words = data.words
    out = _Out()
    by_block = {}
    for i, w in enumerate(words):
        by_block.setdefault(w.block, []).append(i)
    order = [b for b in data.order if b in by_block] if data.order else []
    order += [b for b in by_block if b not in order]
    first = True
    for bid in order:
        idx = by_block[bid]
        blk = data.blocks.get(bid)
        if not first:
            out.emit("\n\n")
        first = False
        if blk is not None and blk.kind == "table" and blk.table is not None:
            _emit_table(out, words, idx, blk.table)
            continue
        paras = _group(idx, words, lambda w: w.para)
        for pi, (_p, pidx) in enumerate(paras):
            if pi > 0:
                out.emit("\n\n")
            lines = [ids for _l, ids in _group(pidx, words, lambda w: w.line)]
            _emit_lines(out, words, lines, keep_lines, dehyphenate)
    return out.text(), out.spans


def _emit_table(out, words, idx, table):
    """Строки таблицы — через перевод строки, ячейки — через табуляцию (удобно вставлять в таблицы)."""
    cells = {}
    for wi in idx:
        cells.setdefault(words[wi].cell, []).append(wi)
    starts = {(c.row, c.col) for c in table.cells}
    for r in range(table.rows):
        if r > 0:
            out.emit("\n")
        for c in range(table.cols):
            if c > 0:
                out.emit("\t")
            if (r, c) not in starts:
                continue
            ids = cells.get((r, c), [])
            lines = [g for _l, g in _group(ids, words, lambda w: w.line)]
            _emit_lines(out, words, lines, False, True)


def page_text(data, keep_lines: bool = True, dehyphenate: bool = True) -> str:
    return layout(data, keep_lines, dehyphenate)[0]


def normalize_spaces(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ([,.;:!?»)])", r"\1", text)
    text = re.sub(r"([«(]) ", r"\1", text)
    return text


# ------------------------------------------------------------------ правки пользователя

_TOKEN = re.compile(r"\S+|\s+")


def apply_edits(data, old_text: str, spans, new_text: str):
    """Переносит правки текста обратно в слова (для PDF-слоя и экспорта с оформлением).

    Изменённые слова получают новый текст; удалённые — пустой; вставки дописываются
    к соседнему слову. Разметка (абзацы, таблицы) сохраняется."""
    import difflib
    if old_text == new_text:
        return
    words = data.words
    # символ старого текста → индекс слова
    owner = [-1] * len(old_text)
    for s, e, wi in spans:
        for p in range(s, min(e, len(old_text))):
            owner[p] = wi
    a = [(m.start(), m.group()) for m in _TOKEN.finditer(old_text)]
    b = [(m.start(), m.group()) for m in _TOKEN.finditer(new_text)]
    sm = difflib.SequenceMatcher(None, [t for _p, t in a], [t for _p, t in b], autojunk=False)
    new_vals = {}
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        old_ws = []
        for p, t in a[i1:i2]:
            for q in range(p, p + len(t)):
                wi = owner[q] if q < len(owner) else -1
                if wi >= 0 and wi not in old_ws:
                    old_ws.append(wi)
        ins = " ".join(t for _p, t in b[j1:j2] if not t.isspace())
        if old_ws:
            new_vals[old_ws[0]] = (new_vals.get(old_ws[0], "") + " " + ins).strip() if old_ws[0] in new_vals \
                else ins
            for wi in old_ws[1:]:
                new_vals.setdefault(wi, "")
        elif ins:
            # чистая вставка: приклеиваем к предыдущему слову
            p = a[i1 - 1][0] if i1 > 0 else 0
            wi = -1
            q = min(p, len(owner) - 1)
            while q >= 0 and wi < 0:
                wi = owner[q]
                q -= 1
            if wi < 0 and words:
                wi = 0
            if wi >= 0:
                base = new_vals.get(wi, words[wi].text)
                new_vals[wi] = (base + " " + ins).strip() if i1 > 0 else (ins + " " + base).strip()
    for wi, val in new_vals.items():
        words[wi].text = val
        words[wi].conf = max(words[wi].conf, 99.0) if val else words[wi].conf
    data.words = [w for w in words if w.text]
