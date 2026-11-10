"""Анализ документа целиком (а не по страницам).

После постраничного распознавания страницы рассматриваются вместе:
  * колонтитулы — текст, повторяющийся вверху/внизу страниц, выносится в настоящие колонтитулы;
  * номера страниц — заменяются полем «номер страницы»;
  * сноски — мелкий текст внизу страницы со знаком сноски привязывается к месту ссылки;
  * гиперссылки и адреса почты становятся ссылками;
  * единые стили: основной кегль и уровни заголовков одинаковы во всём документе;
  * абзац, разорванный границей страницы, склеивается;
  * единые поля страниц.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from .docmodel import ColumnBreak, Columns, ColumnsEnd, Para, Run, TableItem

PAGE = object()            # поле «номер страницы» в колонтитуле

TOP_ZONE = 0.115
BOTTOM_ZONE = 0.885

_PAGE_RE = re.compile(r"^[\s\-–—~|.]*(?:(?:стр(?:аница|\.)?|page|с\.|p\.)\s*)?(\d{1,4})"
                      r"(?:\s*(?:/|из|of)\s*\d{1,4})?[\s\-–—~|.]*$", re.I)
_NOTE_RE = re.compile(r"^\s*(\d{1,2}|\*{1,3}|[⁰¹²³⁴⁵⁶⁷⁸⁹]{1,2}|[†‡])\s*[).]?\s+(\S.*)$", re.S)
_UNSUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_SUP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
URL_RE = re.compile(r"((?:https?://|ftp://|www\.)[^\s<>«»\"']+[^\s<>«»\"'.,;:!?)\]]"
                    r"|[\w.+-]+@[\w-]+(?:\.[\w-]+)+[\w])", re.I)
_END_SENT = ".!?:;…»\")"


@dataclass
class HFLine:
    parts: list                   # строки и PAGE
    align: str = "center"


@dataclass
class DocModel:
    pages: list
    header: list = field(default_factory=list)     # [HFLine]
    footer: list = field(default_factory=list)
    first_page_plain: bool = False                 # на первой странице колонтитулов нет
    page_start: int = 1                            # номер первой страницы
    body_pt: float = 11.0
    notes: list = field(default_factory=list)      # что найдено — для сообщения пользователю
    title: str = ""

    def summary(self) -> str:
        return ", ".join(self.notes)


def _key(text: str) -> str:
    return re.sub(r"\d+", "#", " ".join(text.lower().split()))


def _zone(p: Para) -> str:
    if not p.fbox:
        return ""
    if p.fbox[3] < TOP_ZONE:
        return "top"
    if p.fbox[1] > BOTTOM_ZONE:
        return "bottom"
    return ""


def _align_of(p: Para) -> str:
    if p.align in ("center", "right"):
        return p.align
    if p.fbox:
        cx = (p.fbox[0] + p.fbox[2]) / 2
        w = p.fbox[2] - p.fbox[0]
        if w < 0.5:
            if cx > 0.62:
                return "right"
            if 0.4 <= cx <= 0.6:
                return "center"
    return "left"


def _body_paras(page):
    return [it for it in page.items if isinstance(it, Para)]


# ----------------------------------------------------------------- колонтитулы

def _find_headers_footers(doc: DocModel):
    pages = doc.pages
    n = len(pages)
    cands = []                                    # (page_index, zone, para)
    for pi, pg in enumerate(pages):
        for p in _body_paras(pg):
            z = _zone(p)
            if z and p.nlines <= 2 and len(p.text) <= 140 and p.text.strip():
                cands.append((pi, z, p))
    if not cands:
        return
    # --- номера страниц
    nums = {}
    for pi, z, p in cands:
        m = _PAGE_RE.match(p.text.strip())
        if m:
            nums.setdefault(z, []).append((pi, int(m.group(1)), p))
    pagenum_zone = None
    for z, lst in nums.items():
        if n == 1:
            ok = len(lst) == 1 and 0 < lst[0][1] < 2000
            start = lst[0][1] if ok else 1
        else:
            offs = [v - pi for pi, v, _p in lst]
            try:
                off = statistics.mode(offs)
            except statistics.StatisticsError:
                off = offs[0]
            good = [t for t in lst if t[1] - t[0] == off]
            ok = len(good) >= 2 and len(good) >= len(lst) * 0.6 and off >= 0
            lst[:] = good
            start = off
        if ok:
            pagenum_zone = z
            doc.page_start = max(1, start)
            for _pi, _v, p in lst:
                p.role = "pagenum"
            tmpl = lst[0][2]
            txt = tmpl.text.strip()
            m = _PAGE_RE.match(txt)
            parts = [txt[:m.start(1)], PAGE, txt[m.end(1):]]
            parts = [x for x in parts if x is PAGE or x.strip()]
            line = HFLine(parts, _align_of(tmpl))
            (doc.header if z == "top" else doc.footer).append(line)
            doc.notes.append("номера страниц")
            break
    # --- повторяющийся текст
    if n >= 2:
        groups = {}
        for pi, z, p in cands:
            if p.role:
                continue
            groups.setdefault((z, _key(p.text)), []).append((pi, p))
        need = max(2, int(n * 0.3 + 0.999))
        found = False
        for (z, key), lst in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            if len({pi for pi, _p in lst}) < need:
                continue
            tmpl = lst[0][1]
            parts = [tmpl.text.strip()]
            # число, растущее вместе со страницей, — это номер страницы внутри колонтитула
            if "#" in key and pagenum_zone is None:
                seq = []
                for pi, p in lst:
                    ms = list(re.finditer(r"\d+", p.text))
                    seq.append((pi, int(ms[-1].group()), ms[-1]))
                offs = {v - pi for pi, v, _m in seq}
                if len(offs) == 1 and len(seq) >= 2:
                    m = seq[0][2]
                    t = lst[0][1].text
                    parts = [x for x in (t[:m.start()], PAGE, t[m.end():]) if x is PAGE or x.strip()]
                    doc.page_start = max(1, seq[0][1] - seq[0][0])
            elif "#" in key and len({p.text.strip() for _pi, p in lst}) > 1:
                continue        # числа меняются без системы — это не колонтитул
            line = HFLine(parts, _align_of(tmpl))
            # одинаковые по смыслу колонтитулы (чётные/нечётные) не дублируем
            target = doc.header if z == "top" else doc.footer
            if not any(_hf_text(l) == _hf_text(line) for l in target):
                if z == "top":
                    target.insert(0, line) if pagenum_zone == "top" else target.append(line)
                else:
                    target.append(line)
            for _pi, p in lst:
                p.role = "header" if z == "top" else "footer"
            found = True
        if found:
            doc.notes.append("колонтитулы")
    had_first = any(p.role for p in _body_paras(pages[0]))
    for pg in pages:
        pg.items = [it for it in pg.items if not (isinstance(it, Para) and it.role)]
    # на титульной странице колонтитулов нет, а на остальных есть
    doc.first_page_plain = bool(n > 1 and (doc.header or doc.footer) and not had_first)


def _hf_text(line: HFLine) -> str:
    return "".join("#" if x is PAGE else x for x in line.parts).strip().lower()


# ---------------------------------------------------------------------- сноски

def _find_footnotes(doc: DocModel):
    total = 0
    for pg in doc.pages:
        body = pg.body_pt or doc.body_pt
        items = pg.items
        notes = []
        i = len(items) - 1
        while i >= 0:
            it = items[i]
            if not isinstance(it, Para) or not it.fbox:
                break
            m = _NOTE_RE.match(it.text)
            small = it.pt <= body - 1.0
            if not (small and it.fbox[1] > 0.45):
                break
            if m:
                notes.append((i, m.group(1).translate(_UNSUP), it, m))
            elif notes or i == len(items) - 1:
                # продолжение сноски отдельным абзацем — допускаем только одно подряд
                notes.append((i, None, it, None))
            else:
                break
            i -= 1
        notes.reverse()
        # убираем «продолжения» без начала
        while notes and notes[0][1] is None:
            notes.pop(0)
        if not notes:
            continue
        first_idx = notes[0][0]
        body_items = items[:first_idx]
        merged = []                       # (marker, [Para])
        for _idx, mark, para, m in notes:
            if mark is None:
                merged[-1][1].append(para)
            else:
                txt = m.group(2)
                clean = Para([Run(txt)], para.pt, "left")
                merged.append((mark, [clean]))
        attached = set()
        for mark, paras in merged:
            if _attach_note(body_items, mark, paras):
                attached.add(mark)
                total += 1
        # сноски, для которых ссылка в тексте не нашлась, остаются обычным мелким текстом
        keep_tail = [para for _idx, mark, para, _m in notes if mark is not None and mark not in attached]
        pg.items = body_items + keep_tail
    if total:
        doc.notes.append("сноски: %d" % total)


def _attach_note(items, mark: str, note_paras) -> bool:
    """Ищет в тексте страницы знак сноски (слово1, слово*, слово¹) и вешает на него сноску."""
    if mark.isdigit():
        pat = re.compile(r"(?<=[^\W\d_])[»\")]?\s?(%s|%s)(?=[\s.,;:!?)»]|$)" % (
            re.escape(mark.translate(_SUP)), re.escape(mark)))
    else:
        pat = re.compile(r"(?<=\S)(%s)(?=[\s.,;:!?)»]|$)" % re.escape(mark))
    for it in reversed(items):
        paras = [it] if isinstance(it, Para) else \
            [p for c in it.cells for p in c.paras] if isinstance(it, TableItem) else []
        for p in paras:
            for ri, r in enumerate(p.runs):
                if r.href or r.note is not None:
                    continue
                ms = list(pat.finditer(r.text))
                if not ms:
                    continue
                m = ms[-1]
                before, after = r.text[:m.start(1)], r.text[m.end(1):]
                new = []
                if before:
                    new.append(Run(before, r.bold, r.italic))
                new.append(Run(mark, False, False, sup=True, note=note_paras))
                if after:
                    new.append(Run(after, r.bold, r.italic))
                p.runs[ri:ri + 1] = new
                return True
    return False


# ------------------------------------------------------------------ гиперссылки

def link_runs(runs):
    out = []
    n = 0
    for r in runs:
        if r.href or r.note is not None or not URL_RE.search(r.text):
            out.append(r)
            continue
        pos = 0
        for m in URL_RE.finditer(r.text):
            if m.start() > pos:
                out.append(Run(r.text[pos:m.start()], r.bold, r.italic))
            t = m.group(1)
            if "@" in t and "://" not in t:
                href = "mailto:" + t
            elif t.lower().startswith("www."):
                href = "http://" + t
            else:
                href = t
            out.append(Run(t, r.bold, r.italic, href=href))
            n += 1
            pos = m.end()
        if pos < len(r.text):
            out.append(Run(r.text[pos:], r.bold, r.italic))
    return out, n


def _all_paras(pg):
    for it in pg.items:
        if isinstance(it, Para):
            yield it
        elif isinstance(it, TableItem):
            for c in it.cells:
                for p in c.paras:
                    yield p


def _find_links(doc: DocModel):
    total = 0
    for pg in doc.pages:
        for p in _all_paras(pg):
            p.runs, k = link_runs(p.runs)
            total += k
            for r in p.runs:
                if r.note:
                    for q in r.note:
                        q.runs, k2 = link_runs(q.runs)
                        total += k2
    if total:
        doc.notes.append("ссылки: %d" % total)


# ------------------------------------------------------------------------ стили

def _unify_styles(doc: DocModel):
    weights = {}
    for pg in doc.pages:
        for p in _all_paras(pg):
            if not p.heading:
                weights[p.pt] = weights.get(p.pt, 0) + len(p.text)
    if weights:
        doc.body_pt = max(weights, key=weights.get)
    body = doc.body_pt
    head_sizes = set()
    for pg in doc.pages:
        pg.body_pt = body
        for p in _all_paras(pg):
            if not p.heading and abs(p.pt - body) <= 1.5:
                p.pt = body
            if p.heading:
                head_sizes.add(p.pt)
    big = sorted((s for s in head_sizes if s > body + 1), reverse=True)
    # близкие размеры заголовков — один уровень
    levels = {}
    lvl = 0
    prev = None
    for s in big:
        if prev is None or prev - s > 1.5:
            lvl += 1
        levels[s] = min(lvl, 3)
        prev = s
    base = min(3, (max(levels.values()) + 1) if levels else 2)
    canon = {}
    for pg in doc.pages:
        for p in _all_paras(pg):
            if p.heading:
                p.heading = levels.get(p.pt, base)
                canon.setdefault(p.heading, []).append(p.pt)
    # у заголовков одного уровня — один кегль
    for lv, sizes in canon.items():
        size = statistics.median_high(sizes)
        for pg in doc.pages:
            for p in _all_paras(pg):
                if p.heading == lv:
                    p.pt = size
    if len(doc.pages) > 1:
        for i in (0, 2):
            m = statistics.median(pg.margins[i] for pg in doc.pages)
            for pg in doc.pages:
                mm = list(pg.margins)
                mm[i] = m
                pg.margins = tuple(mm)
        top = statistics.median(pg.margins[1] for pg in doc.pages)
        for pg in doc.pages:
            pg.margins = (pg.margins[0], top, pg.margins[2], pg.margins[3])


# ------------------------------------------------------- абзацы через страницу

def _join_across_pages(doc: DocModel):
    joined = 0
    for a, b in zip(doc.pages, doc.pages[1:]):
        if not a.items or not b.items:
            continue
        last = a.items[-1]
        first_i = 0
        while first_i < len(b.items) and isinstance(b.items[first_i], (Columns, ColumnBreak, ColumnsEnd)):
            first_i += 1
        if first_i != 0 or first_i >= len(b.items):
            continue                  # страница начинается с колонок — не трогаем
        first = b.items[first_i]
        if not isinstance(last, Para) or not isinstance(first, Para) or last.heading or first.heading:
            continue
        if isinstance(a.items[-1], (Columns, ColumnsEnd)):
            continue
        lt, ft = last.text.rstrip(), first.text.lstrip()
        if not lt or not ft or abs(last.pt - first.pt) > 1:
            continue
        hyph = lt[-1] in "-‐‑¬" and len(lt) > 1 and lt[-2].isalpha() and ft[0].islower()
        cont = lt[-1] not in _END_SENT and ft[0].islower() and not first.list_item
        if not (hyph or cont):
            continue
        if hyph:
            last.runs[-1].text = last.runs[-1].text.rstrip()[:-1]
        else:
            last.runs[-1].text = last.runs[-1].text.rstrip() + " "
        first.runs[0].text = first.runs[0].text.lstrip()
        last.runs.extend(first.runs)
        del b.items[first_i]
        b.soft_start = True
        joined += 1
    if joined:
        doc.notes.append("абзацы через страницу: %d" % joined)


def analyze(pages, title: str = "") -> DocModel:
    """pages — список docmodel.PageModel (в режиме с оформлением)."""
    doc = DocModel(list(pages), title=title)
    for i, pg in enumerate(doc.pages):
        pg.index = i
    if doc.pages:
        doc.body_pt = statistics.median(pg.body_pt for pg in doc.pages)
    _find_headers_footers(doc)
    _unify_styles(doc)
    _find_footnotes(doc)
    _find_links(doc)
    _join_across_pages(doc)
    return doc


def plain(pages, title: str = "") -> DocModel:
    """Без анализа — только гиперссылки не трогаем, страницы как есть."""
    return DocModel(list(pages), title=title)
