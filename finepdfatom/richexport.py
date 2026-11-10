"""Экспорт с сохранением оформления: DOCX, ODT, RTF, HTML, XLSX, CSV, PPTX.

Принимает docanalysis.DocModel — документ целиком: страницы, колонтитулы, сноски, ссылки.
Режимы (как в FineReader):
  formatted — «Редактируемая копия»: заголовки, шрифты, колонки, таблицы, рисунки,
              колонтитулы, номера страниц, сноски, гиперссылки;
  plain     — «Простой текст»: абзацы и таблицы без оформления.
"""
from __future__ import annotations

import base64
import csv
import html
import io

from .docanalysis import PAGE
from .docmodel import (ColumnBreak, Columns, ColumnsEnd, ImageItem, Para, TableItem, is_number,
                       to_number)

BODY_FONT = "Times New Roman"
HEAD_FONT = "Arial"
LINK_COLOR = "0563C1"

MODES = {
    "formatted": "Редактируемая копия (оформление, колонтитулы, сноски, таблицы, рисунки)",
    "plain": "Простой текст (абзацы и таблицы)",
}


def _grid(t: TableItem):
    """(ячейки по координатам, множество накрытых объединением позиций)."""
    grid, covered = {}, set()
    for c in t.cells:
        grid[(c.row, c.col)] = c
        for r in range(c.row, c.row + c.rowspan):
            for k in range(c.col, c.col + c.colspan):
                if (r, k) != (c.row, c.col):
                    covered.add((r, k))
    return grid, covered


# ============================================================================ DOCX

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_FN_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"
_FN_RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes"


def save_docx(doc, path, title=""):
    import docx
    from docx.enum.section import WD_SECTION
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    models = doc.pages
    AL = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER,
          "right": WD_ALIGN_PARAGRAPH.RIGHT, "justify": WD_ALIGN_PARAGRAPH.JUSTIFY}
    d = docx.Document()
    st = d.styles["Normal"]
    st.font.name = BODY_FONT
    st.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    st.paragraph_format.space_after = Pt(0)
    for lvl in (1, 2, 3):
        hs = d.styles["Heading %d" % lvl]
        hs.font.color.rgb = None
        hs.font.name = HEAD_FONT if lvl < 3 else BODY_FONT
    body = d.element.body
    for p in list(body):
        if p.tag == qn("w:p"):
            body.remove(p)
    notes = []                       # тексты сносок по порядку (id = индекс + 1)

    def set_cols(section, n, gap):
        sp = section._sectPr
        cols = sp.find(qn("w:cols"))
        if cols is None:
            cols = OxmlElement("w:cols")
            sp.append(cols)
        cols.set(qn("w:num"), str(n))
        cols.set(qn("w:space"), str(int(gap * 20)))

    def geometry(section, m):
        section.page_width = Pt(m.width)
        section.page_height = Pt(m.height)
        section.left_margin = Pt(m.margins[0])
        section.top_margin = Pt(m.margins[1])
        section.right_margin = Pt(m.margins[2])
        section.bottom_margin = Pt(m.margins[3])

    def add_link(par, r, pt):
        rid = par.part.relate_to(r.href, RT.HYPERLINK, is_external=True)
        h = OxmlElement("w:hyperlink")
        h.set(qn("r:id"), rid)
        run = par.add_run(r.text)
        run.font.size = Pt(pt)
        run.font.underline = True
        run.font.color.rgb = RGBColor.from_string(LINK_COLOR)
        run.bold = r.bold or None
        run.italic = r.italic or None
        par._p.remove(run._r)
        h.append(run._r)
        par._p.append(h)

    def add_note_ref(par, r):
        notes.append(r.note)
        run = par.add_run()
        run.font.superscript = True
        ref = OxmlElement("w:footnoteReference")
        ref.set(qn("w:id"), str(len(notes)))
        run._r.append(ref)

    def add_runs(par, p, cell=False):
        for r in p.runs:
            if r.note is not None and not cell:
                add_note_ref(par, r)
                continue
            if r.href:
                add_link(par, r, p.pt)
                continue
            run = par.add_run(r.text)
            run.font.size = Pt(p.pt)
            run.bold = r.bold or None
            run.italic = r.italic or None
            if r.sup:
                run.font.superscript = True
            if p.heading and not cell:
                run.font.color.rgb = None

    pending = {"col": False}

    def add_para(container, p: Para, cell=False):
        if p.heading and not cell:
            par = container.add_paragraph(style="Heading %d" % min(3, p.heading))
        else:
            par = container.add_paragraph()
        if pending["col"] and not cell:
            par.add_run().add_break(WD_BREAK.COLUMN)
            pending["col"] = False
        pf = par.paragraph_format
        pf.alignment = AL.get(p.align, WD_ALIGN_PARAGRAPH.LEFT)
        if p.indent:
            pf.first_line_indent = Pt(p.indent)
        if p.left:
            pf.left_indent = Pt(p.left)
        if p.space_before and not cell:
            pf.space_before = Pt(min(24, p.space_before))
        pf.space_after = Pt(0)
        add_runs(par, p, cell)
        return par

    def add_table(t: TableItem):
        tab = d.add_table(rows=t.rows, cols=t.cols)
        if t.ruled:
            tab.style = "Table Grid"
        tab.alignment = WD_TABLE_ALIGNMENT.CENTER
        tab.autofit = False
        for ci, w in enumerate(t.col_widths):
            for cell in tab.columns[ci].cells:
                cell.width = Pt(w)
        for c in t.cells:
            cell = tab.cell(c.row, c.col)
            if c.rowspan > 1 or c.colspan > 1:
                r1 = min(t.rows - 1, c.row + c.rowspan - 1)
                c1 = min(t.cols - 1, c.col + c.colspan - 1)
                cell = cell.merge(tab.cell(r1, c1))
            first = True
            for p in c.paras or []:
                if first:
                    par = cell.paragraphs[0]
                    first = False
                    par.paragraph_format.alignment = AL.get(c.align)
                    add_runs(par, p, cell=True)
                else:
                    par = add_para(cell, p, cell=True)
                    par.paragraph_format.alignment = AL.get(c.align)
        d.add_paragraph()

    def fill_hf(target, lines):
        target.is_linked_to_previous = False
        par = target.paragraphs[0]
        for li, line in enumerate(lines):
            if li:
                par = target.add_paragraph()
            par.paragraph_format.alignment = AL.get(line.align, WD_ALIGN_PARAGRAPH.CENTER)
            for part in line.parts:
                if part is PAGE:
                    fld = OxmlElement("w:fldSimple")
                    fld.set(qn("w:instr"), "PAGE")
                    r = OxmlElement("w:r")
                    t = OxmlElement("w:t")
                    t.text = "1"
                    r.append(t)
                    fld.append(r)
                    par._p.append(fld)
                else:
                    run = par.add_run(part)
                    run.font.size = Pt(max(8.0, doc.body_pt - 2))

    sec = d.sections[0]
    for pi, m in enumerate(models):
        if pi == 0:
            geometry(sec, m)
            if doc.header:
                fill_hf(sec.header, doc.header)
            if doc.footer:
                fill_hf(sec.footer, doc.footer)
            if doc.first_page_plain:
                sec.different_first_page_header_footer = True
            if doc.page_start != 1 and (doc.header or doc.footer):
                pg = OxmlElement("w:pgNumType")
                pg.set(qn("w:start"), str(doc.page_start))
                mar = sec._sectPr.find(qn("w:pgMar"))
                if mar is not None:
                    mar.addnext(pg)
                else:
                    sec._sectPr.append(pg)
        elif not m.soft_start:
            sec = d.add_section(WD_SECTION.NEW_PAGE)
            geometry(sec, m)
            set_cols(sec, 1, 18)
        for it in m.items:
            if isinstance(it, Columns):
                sec = d.add_section(WD_SECTION.CONTINUOUS)
                geometry(sec, m)
                set_cols(sec, it.n, it.gap)
            elif isinstance(it, ColumnBreak):
                pending["col"] = True
            elif isinstance(it, ColumnsEnd):
                sec = d.add_section(WD_SECTION.CONTINUOUS)
                geometry(sec, m)
                set_cols(sec, 1, 18)
            elif isinstance(it, Para):
                add_para(d, it)
            elif isinstance(it, TableItem):
                add_table(it)
            elif isinstance(it, ImageItem):
                par = d.add_paragraph()
                par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
                par.add_run().add_picture(io.BytesIO(it.data), width=Pt(it.width))
    if notes:
        _docx_footnotes(d, notes, max(8.0, doc.body_pt - 2))
    d.core_properties.author = "FinePdfAtom"
    if title or doc.title:
        d.core_properties.title = title or doc.title
    d.save(path)


def _docx_footnotes(d, notes, pt):
    """Добавляет в документ часть footnotes.xml (python-docx сам сноски не умеет)."""
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part
    from xml.sax.saxutils import escape

    sz = int(round(pt * 2))
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<w:footnotes xmlns:w="%s">' % _W,
           '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>',
           '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p>'
           '</w:footnote>']
    for i, paras in enumerate(notes, 1):
        out.append('<w:footnote w:id="%d">' % i)
        for k, p in enumerate(paras):
            out.append("<w:p>")
            if k == 0:
                out.append('<w:r><w:rPr><w:vertAlign w:val="superscript"/><w:sz w:val="%d"/></w:rPr>'
                           "<w:footnoteRef/></w:r>" % sz)
            out.append('<w:r><w:rPr><w:sz w:val="%d"/><w:szCs w:val="%d"/></w:rPr>'
                       '<w:t xml:space="preserve">%s%s</w:t></w:r></w:p>' % (sz, sz, " " if k == 0 else "",
                                                                             escape(p.text)))
        out.append("</w:footnote>")
    out.append("</w:footnotes>")
    blob = "".join(out).encode("utf-8")
    for rel in list(d.part.rels.values()):
        if rel.reltype == _FN_RT:          # в шаблоне уже есть часть сносок — заменяем содержимое
            rel.target_part._blob = blob
            return
    part = Part(PackURI("/word/footnotes.xml"), _FN_CT, blob, d.part.package)
    d.part.relate_to(part, _FN_RT)


# ============================================================================= ODT

def save_odt(doc, path, title=""):
    from odf.draw import Frame
    from odf.draw import Image as DImage
    from odf.opendocument import OpenDocumentText
    from odf.style import (Column, Columns as SColumns, Footer, FooterStyle, GraphicProperties, Header,
                           HeaderFooterProperties, HeaderStyle, MasterPage, PageLayout,
                           PageLayoutProperties, ParagraphProperties, SectionProperties, Style,
                           TableCellProperties, TableColumnProperties, TableProperties, TextProperties)
    from odf.table import CoveredTableCell, Table, TableCell, TableColumn, TableRow
    from odf.text import A, H, Note, NoteBody, NoteCitation, P, PageNumber, Section, Span

    models = doc.pages
    od = OpenDocumentText()
    base = Style(name="Standard", family="paragraph")
    base.addElement(TextProperties(fontname=BODY_FONT, fontfamily=BODY_FONT, fontsize="11pt"))
    od.styles.addElement(base)
    cache = {}
    counter = [0]

    def pstyle(p: Para, page_break=False, col_break=False, cell=False, page_number=0):
        key = (round(p.pt, 1), p.align, p.heading if not cell else 0, round(p.indent), round(p.left),
               round(min(24, p.space_before)) if not cell else 0, page_break, col_break, page_number)
        if key in cache:
            return cache[key]
        counter[0] += 1
        s = Style(name="P%d" % counter[0], family="paragraph", parentstylename="Standard")
        props = dict(textalign={"justify": "justify", "center": "center", "right": "end"}.get(p.align, "start"),
                     marginbottom="0pt")
        if p.indent:
            props["textindent"] = "%.1fpt" % p.indent
        if p.left:
            props["marginleft"] = "%.1fpt" % p.left
        if p.space_before and not cell:
            props["margintop"] = "%.1fpt" % min(24, p.space_before)
        if page_break:
            props["breakbefore"] = "page"
        if col_break:
            props["breakbefore"] = "column"
        if page_number:
            props["pagenumber"] = page_number
            s.setAttribute("masterpagename", "Standard")
        s.addElement(ParagraphProperties(**props))
        tp = dict(fontsize="%.1fpt" % p.pt)
        if p.heading and not cell:
            tp.update(fontweight="bold", fontname=HEAD_FONT if p.heading < 3 else BODY_FONT,
                      fontfamily=HEAD_FONT if p.heading < 3 else BODY_FONT)
        s.addElement(TextProperties(**tp))
        od.automaticstyles.addElement(s)
        cache[key] = s
        return s

    def tstyle(bold=False, italic=False, sup=False, link=False):
        key = ("T", bold, italic, sup, link)
        if key in cache:
            return cache[key]
        counter[0] += 1
        s = Style(name="T%d" % counter[0], family="text")
        tp = {}
        if bold:
            tp["fontweight"] = "bold"
        if italic:
            tp["fontstyle"] = "italic"
        if sup:
            tp["textposition"] = "super 58%"
        if link:
            tp.update(color="#" + LINK_COLOR, textunderlinestyle="solid", textunderlinewidth="auto",
                      textunderlinecolor="font-color")
        s.addElement(TextProperties(**tp))
        od.automaticstyles.addElement(s)
        cache[key] = s
        return s

    note_n = [0]

    def add_runs(el, p, cell=False):
        for r in p.runs:
            if r.note is not None and not cell:
                note_n[0] += 1
                note = Note(noteclass="footnote", id="ftn%d" % note_n[0])
                cit = NoteCitation()
                cit.addText(str(note_n[0]))
                note.addElement(cit)
                nb = NoteBody()
                for q in r.note:
                    qp = P(stylename=pstyle(Para([], max(8.0, doc.body_pt - 2), "left"), cell=True))
                    qp.addText(q.text)
                    nb.addElement(qp)
                note.addElement(nb)
                el.addElement(note)
            elif r.href:
                a = A(href=r.href, type="simple", stylename=tstyle(r.bold, r.italic, link=True))
                a.addText(r.text)
                el.addElement(a)
            elif r.bold or r.italic or r.sup:
                sp = Span(stylename=tstyle(r.bold, r.italic, r.sup))
                sp.addText(r.text)
                el.addElement(sp)
            else:
                el.addText(r.text)

    def para_el(p: Para, **kw):
        st = pstyle(p, **kw)
        el = H(stylename=st, outlinelevel=p.heading) if (p.heading and not kw.get("cell")) else P(stylename=st)
        add_runs(el, p, kw.get("cell", False))
        return el

    def hf_el(cls, lines):
        box = cls()
        for line in lines:
            hp = P(stylename=pstyle(Para([], max(8.0, doc.body_pt - 2), line.align), cell=True))
            for part in line.parts:
                if part is PAGE:
                    num = PageNumber(selectpage="current")
                    num.addText("1")
                    hp.addElement(num)
                else:
                    hp.addText(part)
            box.addElement(hp)
        return box

    m0 = models[0] if models else None
    if m0 is not None:
        pl = PageLayout(name="Стр")
        pl.addElement(PageLayoutProperties(pagewidth="%.1fpt" % m0.width, pageheight="%.1fpt" % m0.height,
                                           marginleft="%.1fpt" % m0.margins[0], margintop="%.1fpt" % m0.margins[1],
                                           marginright="%.1fpt" % m0.margins[2],
                                           marginbottom="%.1fpt" % m0.margins[3], printorientation="portrait"))
        if doc.header:
            hs = HeaderStyle()
            hs.addElement(HeaderFooterProperties(minheight="0.6cm", marginbottom="0.3cm"))
            pl.addElement(hs)
        if doc.footer:
            fs = FooterStyle()
            fs.addElement(HeaderFooterProperties(minheight="0.6cm", margintop="0.3cm"))
            pl.addElement(fs)
        od.automaticstyles.addElement(pl)
        mp = MasterPage(name="Standard", pagelayoutname=pl)
        if doc.header:
            mp.addElement(hf_el(Header, doc.header))
        if doc.footer:
            mp.addElement(hf_el(Footer, doc.footer))
        od.masterstyles.addElement(mp)

    counter_t = [0]
    pending_col_break = False
    first_para = True
    for pi, m in enumerate(models):
        target = od.text
        pending_page_break = pi > 0 and not m.soft_start
        for it in m.items:
            if isinstance(it, Columns):
                counter[0] += 1
                ss = Style(name="S%d" % counter[0], family="section")
                sp = SectionProperties()
                cols = SColumns(columncount=it.n, columngap="%.1fpt" % it.gap)
                for _ in range(it.n):
                    cols.addElement(Column(relwidth="1*"))
                sp.addElement(cols)
                ss.addElement(sp)
                od.automaticstyles.addElement(ss)
                sec = Section(name="Колонки%d" % counter[0], stylename=ss)
                od.text.addElement(sec)
                target = sec
                pending_col_break = False
            elif isinstance(it, ColumnBreak):
                pending_col_break = True
            elif isinstance(it, ColumnsEnd):
                target = od.text
            elif isinstance(it, Para):
                start = doc.page_start if (first_para and doc.page_start != 1 and target is od.text) else 0
                target.addElement(para_el(it, page_break=pending_page_break, col_break=pending_col_break,
                                          page_number=start))
                first_para = False
                pending_page_break = pending_col_break = False
            elif isinstance(it, ImageItem):
                counter[0] += 1
                href = od.addPicture("Pictures/img%d.%s" % (counter[0], "jpg" if it.fmt == "jpeg" else "png"),
                                     "image/" + it.fmt, it.data)
                gs = Style(name="G%d" % counter[0], family="graphic")
                gs.addElement(GraphicProperties(wrap="none", horizontalpos="center", horizontalrel="paragraph"))
                od.automaticstyles.addElement(gs)
                holder = P(stylename=pstyle(Para([], 11, "center"), page_break=pending_page_break))
                pending_page_break = False
                fr = Frame(stylename=gs, width="%.1fpt" % it.width, height="%.1fpt" % it.height, anchortype="as-char")
                fr.addElement(DImage(href=href))
                holder.addElement(fr)
                target.addElement(holder)
            elif isinstance(it, TableItem):
                counter_t[0] += 1
                tname = "Таблица%d" % counter_t[0]
                ts = Style(name=tname + "s", family="table")
                ts.addElement(TableProperties(width="%.1fpt" % sum(it.col_widths), align="center"))
                od.automaticstyles.addElement(ts)
                cs = Style(name=tname + "c", family="table-cell")
                cs.addElement(TableCellProperties(border="0.5pt solid #000000" if it.ruled else "none",
                                                  padding="2pt"))
                od.automaticstyles.addElement(cs)
                if pending_page_break:
                    target.addElement(P(stylename=pstyle(Para([], 11), page_break=True)))
                    pending_page_break = False
                tab = Table(name=tname, stylename=ts)
                for ci, w in enumerate(it.col_widths):
                    col_s = Style(name="%s.%d" % (tname, ci), family="table-column")
                    col_s.addElement(TableColumnProperties(columnwidth="%.1fpt" % w))
                    od.automaticstyles.addElement(col_s)
                    tab.addElement(TableColumn(stylename=col_s))
                grid, covered = _grid(it)
                for r in range(it.rows):
                    tr = TableRow()
                    for k in range(it.cols):
                        if (r, k) in covered:
                            tr.addElement(CoveredTableCell())
                            continue
                        c = grid.get((r, k))
                        kw = dict(stylename=cs, valuetype="string")
                        if c is not None and (c.rowspan > 1 or c.colspan > 1):
                            kw["numberrowsspanned"] = c.rowspan
                            kw["numbercolumnsspanned"] = c.colspan
                        tc = TableCell(**kw)
                        paras = c.paras if c is not None and c.paras else [Para([], 11)]
                        for p in paras:
                            q = Para(p.runs, p.pt, c.align if c is not None else "left")
                            tc.addElement(para_el(q, cell=True))
                        tr.addElement(tc)
                    tab.addElement(tr)
                target.addElement(tab)
    od.save(path)


# ============================================================================= RTF

def _rtf(text: str) -> str:
    out = []
    for ch in text:
        o = ord(ch)
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\line ")
        elif ch == "\t":
            out.append("\\tab ")
        elif o < 128:
            out.append(ch)
        elif o <= 0xFFFF:
            out.append("\\u%d?" % (o if o < 32768 else o - 65536))
        else:
            o -= 0x10000
            for u in (0xD800 + (o >> 10), 0xDC00 + (o & 0x3FF)):
                out.append("\\u%d?" % (u - 65536))
    return "".join(out)


def _tw(pt: float) -> int:
    return int(round(pt * 20))


def save_rtf(doc, path, title=""):
    models = doc.pages
    QA = {"left": "\\ql", "center": "\\qc", "right": "\\qr", "justify": "\\qj"}
    o = ["{\\rtf1\\ansi\\ansicpg1251\\deff0\\uc1",
         "{\\fonttbl{\\f0\\froman\\fcharset204 %s;}{\\f1\\fswiss\\fcharset204 %s;}}" % (BODY_FONT, HEAD_FONT),
         "{\\colortbl;\\red5\\green99\\blue193;}",
         "{\\info{\\title %s}{\\author FinePdfAtom}}" % _rtf(title or doc.title or "")]
    if models:
        m = models[0]
        o.append("\\paperw%d\\paperh%d\\margl%d\\margt%d\\margr%d\\margb%d" % (
            _tw(m.width), _tw(m.height), _tw(m.margins[0]), _tw(m.margins[1]), _tw(m.margins[2]), _tw(m.margins[3])))
        if doc.page_start != 1:
            o.append("\\pgnstart%d" % doc.page_start)
    o.append("\\ftnbj\\aenddoc")
    sect_props = ""
    if doc.header or doc.footer:
        if doc.page_start != 1:
            sect_props += "\\pgnstarts%d\\pgnrestart" % doc.page_start
        if doc.first_page_plain:
            sect_props += "\\titlepg"
    hf_pt = max(8.0, doc.body_pt - 2)

    def hf(kind, lines):
        s = ["{\\%s" % kind]
        for line in lines:
            s.append("\\pard\\plain\\f0\\fs%d%s " % (int(hf_pt * 2), QA.get(line.align, "\\qc")))
            for part in line.parts:
                s.append("{\\field{\\*\\fldinst PAGE}{\\fldrslt 1}}" if part is PAGE else _rtf(part))
            s.append("\\par")
        s.append("}")
        return "".join(s)

    hf_text = (hf("header", doc.header) if doc.header else "") + (hf("footer", doc.footer) if doc.footer else "")
    o.append("\\sectd" + sect_props + hf_text)

    def runs(p, cell=False):
        s = []
        for r in p.runs:
            if r.note is not None and not cell:
                s.append("{\\super\\chftn}{\\footnote\\pard\\plain\\f0\\fs%d{\\super\\chftn} %s}" % (
                    int(hf_pt * 2), "\\par ".join(_rtf(q.text) for q in r.note)))
                continue
            t = _rtf(r.text)
            fmt = ("\\b" if r.bold else "") + ("\\i" if r.italic else "") + ("\\super" if r.sup else "")
            if r.href:
                s.append("{\\field{\\*\\fldinst{HYPERLINK \"%s\"}}{\\fldrslt{\\ul\\cf1%s %s}}}" % (
                    r.href.replace("\\", "/").replace('"', "%22"), fmt, t))
            elif fmt:
                s.append("{%s %s}" % (fmt, t))
            else:
                s.append(t)
        return "".join(s)

    def para(p, cell=False, align=None):
        f = "\\f1" if (p.heading and p.heading < 3 and not cell) else "\\f0"
        s = "\\pard\\plain%s%s\\fs%d%s" % ("\\intbl" if cell else "", f, int(round(p.pt * 2)),
                                           QA.get(align or p.align, "\\ql"))
        if p.heading and not cell:
            s += "\\b\\keepn\\outlinelevel%d" % (p.heading - 1)
        if not cell:
            if p.indent:
                s += "\\fi%d" % _tw(p.indent)
            if p.left:
                s += "\\li%d" % _tw(p.left)
            if p.space_before:
                s += "\\sb%d" % _tw(min(24, p.space_before))
        return s + " " + runs(p, cell)

    def table(t):
        grid, covered = _grid(t)
        owner = {}
        for c in t.cells:
            for r in range(c.row, c.row + c.rowspan):
                for k in range(c.col, c.col + c.colspan):
                    owner[(r, k)] = c
        s = []
        brd = "".join("\\clbrdr%s\\brdrs\\brdrw10" % e for e in "tlbr") if t.ruled else ""
        for r in range(t.rows):
            s.append("\\trowd\\trgaph60\\trqc")
            x = 0
            for k in range(t.cols):
                x += _tw(t.col_widths[k])
                c = owner.get((r, k))
                flags = ""
                if c is not None:
                    if c.colspan > 1:
                        flags += "\\clmgf" if k == c.col else "\\clmrg"
                    if c.rowspan > 1:
                        flags += "\\clvmgf" if r == c.row else "\\clvmrg"
                s.append("%s%s\\cellx%d" % (flags, brd, x))
            for k in range(t.cols):
                c = grid.get((r, k))
                if c is not None and c.paras:
                    s.append("\\par ".join(para(p, True, c.align) for p in c.paras) + "\\cell ")
                else:
                    s.append("\\pard\\intbl\\cell ")
            s.append("\\row\n")
        s.append("\\pard\\par\n")
        return "".join(s)

    for pi, m in enumerate(models):
        if pi > 0 and not m.soft_start:
            o.append("\\page\n")
        for it in m.items:
            if isinstance(it, Columns):
                o.append("\\sect\\sectd\\sbknone%s\\cols%d\\colsx%d%s\n" % (sect_props.replace("\\pgnrestart", ""),
                                                                      it.n, _tw(it.gap), hf_text))
            elif isinstance(it, ColumnBreak):
                o.append("\\column\n")
            elif isinstance(it, ColumnsEnd):
                o.append("\\sect\\sectd\\sbknone%s\\cols1%s\n" % (sect_props.replace("\\pgnrestart", ""), hf_text))
            elif isinstance(it, Para):
                o.append(para(it) + "\\par\n")
            elif isinstance(it, TableItem):
                o.append(table(it))
            elif isinstance(it, ImageItem):
                o.append("\\pard\\qc{\\pict\\%s\\picwgoal%d\\pichgoal%d\n%s}\\par\n" % (
                    "jpegblip" if it.fmt == "jpeg" else "pngblip", _tw(it.width), _tw(it.height), it.data.hex()))
    o.append("}")
    with open(path, "w", encoding="ascii", errors="replace", newline="\r\n") as f:
        f.write("\n".join(o))


# ============================================================================ HTML

def save_html(doc, path, title=""):
    models = doc.pages
    out = ['<!doctype html><html lang="ru"><head><meta charset="utf-8">',
           "<title>%s</title>" % html.escape(title or doc.title or "Документ"),
           "<style>body{font-family:'%s',serif;max-width:52em;margin:2em auto;padding:0 1em;line-height:1.35}"
           "table{border-collapse:collapse;margin:1em auto}td{border:1px solid #444;padding:3px 6px;vertical-align:top}"
           "table.plain td{border:none}.cols{display:grid;gap:1.5em}figure{text-align:center;margin:1em 0}"
           "img{max-width:100%%}hr.page{border:none;border-top:1px dashed #aaa;margin:2em 0}"
           ".notes{font-size:85%%;border-top:1px solid #999;margin-top:1.5em;padding-top:.5em}"
           ".hf{color:#666;font-size:85%%;text-align:center}</style></head><body>" % BODY_FONT]
    note_n = [0]
    for line in doc.header:
        out.append('<p class="hf">%s</p>' % html.escape("".join("" if x is PAGE else x for x in line.parts)))
    for pi, m in enumerate(models):
        if pi and not m.soft_start:
            out.append('<hr class="page">')
        col_open = False
        notes = []
        for it in m.items:
            if isinstance(it, Columns):
                out.append('<div class="cols" style="grid-template-columns:repeat(%d,1fr)"><div>' % it.n)
                col_open = True
            elif isinstance(it, ColumnBreak):
                out.append("</div><div>")
            elif isinstance(it, ColumnsEnd):
                if col_open:
                    out.append("</div></div>")
                col_open = False
            elif isinstance(it, Para):
                out.append(_html_para(it, m.body_pt, notes, note_n))
            elif isinstance(it, ImageItem):
                out.append('<figure><img alt="" style="width:%.0fpt" src="data:image/%s;base64,%s"></figure>'
                           % (it.width, it.fmt, base64.b64encode(it.data).decode()))
            elif isinstance(it, TableItem):
                out.append(_html_table(it, notes, note_n))
        if col_open:
            out.append("</div></div>")
        if notes:
            out.append('<div class="notes">%s</div>' % "".join(
                '<p id="fn%d"><sup>%d</sup> %s</p>' % (n, n, html.escape(t)) for n, t in notes))
    out.append("</body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def _html_runs(runs, notes=None, note_n=None):
    s = []
    for r in runs:
        if r.note is not None and notes is not None:
            note_n[0] += 1
            notes.append((note_n[0], " ".join(q.text for q in r.note)))
            s.append('<sup><a href="#fn%d">%d</a></sup>' % (note_n[0], note_n[0]))
            continue
        t = html.escape(r.text)
        if r.href:
            t = '<a href="%s">%s</a>' % (html.escape(r.href, quote=True), t)
        if r.sup:
            t = "<sup>%s</sup>" % t
        if r.bold:
            t = "<b>%s</b>" % t
        if r.italic:
            t = "<i>%s</i>" % t
        s.append(t)
    return "".join(s)


def _html_para(p, body_pt, notes=None, note_n=None):
    style = []
    if p.align != "left":
        style.append("text-align:%s" % p.align)
    if abs(p.pt - body_pt) >= 1:
        style.append("font-size:%.1fpt" % p.pt)
    if p.indent:
        style.append("text-indent:%.0fpt" % p.indent)
    if p.left:
        style.append("margin-left:%.0fpt" % p.left)
    tag = "h%d" % p.heading if p.heading else "p"
    st = ' style="%s"' % ";".join(style) if style else ""
    return "<%s%s>%s</%s>" % (tag, st, _html_runs(p.runs, notes, note_n), tag)


def _html_table(t, notes=None, note_n=None):
    grid, covered = _grid(t)
    rows = []
    for r in range(t.rows):
        tds = []
        for k in range(t.cols):
            if (r, k) in covered:
                continue
            c = grid.get((r, k))
            if c is None:
                tds.append("<td></td>")
                continue
            attrs = ""
            if c.rowspan > 1:
                attrs += ' rowspan="%d"' % c.rowspan
            if c.colspan > 1:
                attrs += ' colspan="%d"' % c.colspan
            if c.align != "left":
                attrs += ' style="text-align:%s"' % c.align
            tds.append("<td%s>%s</td>" % (attrs, "<br>".join(_html_runs(p.runs, notes, note_n) for p in c.paras)))
        rows.append("<tr>%s</tr>" % "".join(tds))
    return "<table%s>%s</table>" % ("" if t.ruled else ' class="plain"', "".join(rows))


# ====================================================================== XLSX / CSV

def save_xlsx(doc, path, title=""):
    """Таблицы — каждая на свой лист (числа становятся числами), остальной текст — на лист «Текст»."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    n_tab = 0
    text_rows = []
    for pi, m in enumerate(doc.pages):
        ti = 0
        for it in m.items:
            if isinstance(it, TableItem):
                ti += 1
                n_tab += 1
                ws = wb.create_sheet(("Стр%d Табл%d" % (pi + 1, ti))[:31])
                for ci, w in enumerate(it.col_widths):
                    ws.column_dimensions[get_column_letter(ci + 1)].width = max(4.0, min(80.0, w / 5.5))
                for c in it.cells:
                    txt = "\n".join(p.text for p in c.paras).strip()
                    cell = ws.cell(row=c.row + 1, column=c.col + 1)
                    num = to_number(txt) if txt and is_number(txt) else None
                    cell.value = num if num is not None else (txt or None)
                    bold = bool(c.paras) and all(r.bold for p in c.paras for r in p.runs if r.text.strip())
                    cell.font = Font(bold=bold)
                    cell.alignment = Alignment(wrap_text=True, vertical="top",
                                               horizontal={"center": "center", "right": "right"}.get(
                                                   c.align, "right" if num is not None else "left"))
                    if c.rowspan > 1 or c.colspan > 1:
                        ws.merge_cells(start_row=c.row + 1, start_column=c.col + 1,
                                       end_row=c.row + c.rowspan, end_column=c.col + c.colspan)
                if it.ruled:
                    for r in range(1, it.rows + 1):
                        for k in range(1, it.cols + 1):
                            ws.cell(row=r, column=k).border = border
            elif isinstance(it, Para):
                text_rows.append((it.text, bool(it.heading)))
        text_rows.append(("", False))
    if text_rows and any(t for t, _b in text_rows):
        ws = wb.create_sheet("Текст", 0 if n_tab == 0 else len(wb.sheetnames))
        ws.column_dimensions["A"].width = 120
        for i, (t, b) in enumerate(text_rows, 1):
            c = ws.cell(row=i, column=1, value=t or None)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            if b:
                c.font = Font(bold=True)
    if not wb.sheetnames:
        wb.create_sheet("Лист1")
    wb.save(path)


def save_csv(doc, path, title=""):
    """Таблицы документа подряд (между таблицами — пустая строка). Разделитель «;», кодировка UTF-8 с BOM —
    так файл сразу открывается в Excel и LibreOffice Calc. Если таблиц нет — по абзацу в строке."""
    tables = [it for m in doc.pages for it in m.items if isinstance(it, TableItem)]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f, delimiter=";")
        if tables:
            for ti, t in enumerate(tables):
                if ti:
                    wr.writerow([])
                grid, _cov = _grid(t)
                for r in range(t.rows):
                    wr.writerow([" ".join(p.text for p in grid[(r, k)].paras).strip() if (r, k) in grid else ""
                                 for k in range(t.cols)])
        else:
            for m in doc.pages:
                for it in m.items:
                    if isinstance(it, Para) and it.text.strip():
                        wr.writerow([it.text])


# ============================================================================ PPTX

def save_pptx(doc, path, title=""):
    """Каждая страница — слайд; блоки текста, таблицы и рисунки стоят на своих местах."""
    from pptx import Presentation
    from pptx.enum.text import MSO_AUTO_SIZE, PP_ALIGN
    from pptx.util import Emu, Pt

    AL = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT, "justify": PP_ALIGN.JUSTIFY}
    prs = Presentation()
    m0 = doc.pages[0] if doc.pages else None
    W = Pt(m0.width if m0 else 595)
    Hh = Pt(m0.height if m0 else 842)
    prs.slide_width, prs.slide_height = W, Hh
    blank = prs.slide_layouts[6]

    def fill_runs(par, p):
        par.alignment = AL.get(p.align, PP_ALIGN.LEFT)
        for r in p.runs:
            run = par.add_run()
            run.text = r.text if r.note is None else r.text
            run.font.size = Pt(p.pt)
            run.font.name = HEAD_FONT if (p.heading and p.heading < 3) else BODY_FONT
            run.font.bold = bool(r.bold or p.heading) or None
            run.font.italic = r.italic or None
            if r.href:
                run.hyperlink.address = r.href

    for m in doc.pages:
        slide = prs.slides.add_slide(blank)
        sw, sh = Pt(m.width), Pt(m.height)
        fx, fy = W / float(sw), Hh / float(sh)
        for it in m.items:
            if not getattr(it, "fbox", None):
                continue
            x0, y0, x1, y1 = it.fbox
            left, top = int(x0 * sw * fx), int(y0 * sh * fy)
            width, height = max(1, int((x1 - x0) * sw * fx)), max(1, int((y1 - y0) * sh * fy))
            if isinstance(it, Para):
                pad = int(Pt(it.pt * 0.6))
                # абзац по ширине занимает всю колонку; однострочному даём запас справа
                box = slide.shapes.add_textbox(Emu(max(0, left - pad // 2)), Emu(max(0, top - pad // 2)),
                                               Emu(width + pad * 2), Emu(height + pad))
                tf = box.text_frame
                tf.word_wrap = it.nlines > 1
                tf.auto_size = MSO_AUTO_SIZE.NONE
                tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
                fill_runs(tf.paragraphs[0], it)
            elif isinstance(it, ImageItem):
                slide.shapes.add_picture(io.BytesIO(it.data), Emu(left), Emu(top), Emu(width), Emu(height))
            elif isinstance(it, TableItem):
                gf = slide.shapes.add_table(it.rows, it.cols, Emu(left), Emu(top), Emu(width), Emu(height))
                tab = gf.table
                total = sum(it.col_widths) or 1.0
                for ci, cw in enumerate(it.col_widths):
                    tab.columns[ci].width = Emu(int(width * cw / total))
                for ri in range(it.rows):
                    tab.rows[ri].height = Emu(int(height / it.rows))
                for c in it.cells:
                    cell = tab.cell(c.row, c.col)
                    if c.rowspan > 1 or c.colspan > 1:
                        cell.merge(tab.cell(min(it.rows - 1, c.row + c.rowspan - 1),
                                            min(it.cols - 1, c.col + c.colspan - 1)))
                    tf = cell.text_frame
                    tf.margin_left = tf.margin_right = Pt(3)
                    tf.margin_top = tf.margin_bottom = Pt(1)
                    for k, p in enumerate(c.paras):
                        par = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
                        q = Para(p.runs, p.pt, c.align)
                        fill_runs(par, q)
    prs.core_properties.author = "FinePdfAtom"
    prs.core_properties.title = title or doc.title or ""
    prs.save(path)
