"""Экспорт с сохранением оформления: DOCX, ODT, HTML, XLSX.

Режимы (как в FineReader):
  formatted — «Редактируемая копия»: заголовки, шрифты, жирный/курсив, выравнивание,
              колонки, таблицы, рисунки;
  plain     — «Простой текст»: абзацы и таблицы без оформления.
"""
from __future__ import annotations

import base64
import html
import io

from .docmodel import (ColumnBreak, Columns, ColumnsEnd, ImageItem, Para, TableItem, is_number,
                       to_number)

BODY_FONT = "Times New Roman"
HEAD_FONT = "Arial"

MODES = {
    "formatted": "Редактируемая копия (оформление, колонки, таблицы, рисунки)",
    "plain": "Простой текст (абзацы и таблицы)",
}


# ============================================================================ DOCX

def save_docx(models, path, title=""):
    import docx
    from docx.enum.section import WD_SECTION
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt

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
    # убираем пустой абзац шаблона
    body = d.element.body
    for p in list(body):
        if p.tag == qn("w:p"):
            body.remove(p)

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
        for r in p.runs:
            run = par.add_run(r.text)
            run.font.size = Pt(p.pt)
            run.bold = r.bold or None
            run.italic = r.italic or None
            if p.heading and not cell:
                run.font.color.rgb = None
        return par

    def add_table(t: TableItem):
        tab = d.add_table(rows=t.rows, cols=t.cols)
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
            # первый пустой абзац ячейки используем под текст
            first = True
            for p in c.paras or []:
                if first:
                    par = cell.paragraphs[0]
                    first = False
                    par.paragraph_format.alignment = AL.get(c.align)
                    for r in p.runs:
                        run = par.add_run(r.text)
                        run.font.size = Pt(p.pt)
                        run.bold = r.bold or None
                        run.italic = r.italic or None
                else:
                    par = add_para(cell, p, cell=True)
                    par.paragraph_format.alignment = AL.get(c.align)
        d.add_paragraph()

    for pi, m in enumerate(models):
        if pi == 0:
            sec = d.sections[0]
            geometry(sec, m)
        else:
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
    d.core_properties.author = "FinePdfAtom"
    if title:
        d.core_properties.title = title
    d.save(path)


# ============================================================================= ODT

def save_odt(models, path, title=""):
    from odf.draw import Frame
    from odf.draw import Image as DImage
    from odf.opendocument import OpenDocumentText
    from odf.style import (Column, Columns as SColumns, GraphicProperties, MasterPage, PageLayout,
                           PageLayoutProperties, ParagraphProperties, SectionProperties, Style,
                           TableCellProperties, TableColumnProperties, TableProperties, TextProperties)
    from odf.table import CoveredTableCell, Table, TableCell, TableColumn, TableRow
    from odf.text import H, P, Section, Span

    doc = OpenDocumentText()
    m0 = models[0] if models else None
    if m0 is not None:
        pl = PageLayout(name="Стр")
        pl.addElement(PageLayoutProperties(pagewidth="%.1fpt" % m0.width, pageheight="%.1fpt" % m0.height,
                                           marginleft="%.1fpt" % m0.margins[0], margintop="%.1fpt" % m0.margins[1],
                                           marginright="%.1fpt" % m0.margins[2],
                                           marginbottom="%.1fpt" % m0.margins[3], printorientation="portrait"))
        doc.automaticstyles.addElement(pl)
        doc.masterstyles.addElement(MasterPage(name="Standard", pagelayoutname=pl))
    base = Style(name="Standard", family="paragraph")
    base.addElement(TextProperties(fontname=BODY_FONT, fontfamily=BODY_FONT, fontsize="11pt"))
    doc.styles.addElement(base)
    cache = {}
    counter = [0]

    def pstyle(p: Para, page_break=False, col_break=False, cell=False):
        key = (round(p.pt, 1), p.align, p.heading if not cell else 0, round(p.indent), round(p.left),
               round(min(24, p.space_before)) if not cell else 0, page_break, col_break)
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
        s.addElement(ParagraphProperties(**props))
        tp = dict(fontsize="%.1fpt" % p.pt)
        if p.heading and not cell:
            tp.update(fontweight="bold", fontname=HEAD_FONT if p.heading < 3 else BODY_FONT,
                      fontfamily=HEAD_FONT if p.heading < 3 else BODY_FONT)
        s.addElement(TextProperties(**tp))
        doc.automaticstyles.addElement(s)
        cache[key] = s
        return s

    def tstyle(bold, italic):
        key = ("T", bold, italic)
        if key in cache:
            return cache[key]
        counter[0] += 1
        s = Style(name="T%d" % counter[0], family="text")
        tp = {}
        if bold:
            tp["fontweight"] = "bold"
        if italic:
            tp["fontstyle"] = "italic"
        s.addElement(TextProperties(**tp))
        doc.automaticstyles.addElement(s)
        cache[key] = s
        return s

    def para_el(p: Para, **kw):
        st = pstyle(p, **kw)
        el = H(stylename=st, outlinelevel=p.heading) if (p.heading and not kw.get("cell")) else P(stylename=st)
        for r in p.runs:
            if r.bold or r.italic:
                sp = Span(stylename=tstyle(r.bold, r.italic))
                sp.addText(r.text)
                el.addElement(sp)
            else:
                el.addText(r.text)
        return el

    counter_t = [0]
    pending_page_break = False
    pending_col_break = False
    for pi, m in enumerate(models):
        target = doc.text
        pending_page_break = pi > 0
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
                doc.automaticstyles.addElement(ss)
                sec = Section(name="Колонки%d" % counter[0], stylename=ss)
                doc.text.addElement(sec)
                target = sec
                pending_col_break = False
            elif isinstance(it, ColumnBreak):
                pending_col_break = True
            elif isinstance(it, ColumnsEnd):
                target = doc.text
            elif isinstance(it, Para):
                target.addElement(para_el(it, page_break=pending_page_break, col_break=pending_col_break))
                pending_page_break = pending_col_break = False
            elif isinstance(it, ImageItem):
                counter[0] += 1
                href = doc.addPicture("Pictures/img%d.%s" % (counter[0], "jpg" if it.fmt == "jpeg" else "png"),
                                      "image/" + it.fmt, it.data)
                gs = Style(name="G%d" % counter[0], family="graphic")
                gs.addElement(GraphicProperties(wrap="none", horizontalpos="center", horizontalrel="paragraph"))
                doc.automaticstyles.addElement(gs)
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
                doc.automaticstyles.addElement(ts)
                cs = Style(name=tname + "c", family="table-cell")
                cs.addElement(TableCellProperties(border="0.5pt solid #000000", padding="2pt"))
                doc.automaticstyles.addElement(cs)
                if pending_page_break:
                    target.addElement(P(stylename=pstyle(Para([], 11), page_break=True)))
                    pending_page_break = False
                tab = Table(name=tname, stylename=ts)
                for ci, w in enumerate(it.col_widths):
                    col_s = Style(name="%s.%d" % (tname, ci), family="table-column")
                    col_s.addElement(TableColumnProperties(columnwidth="%.1fpt" % w))
                    doc.automaticstyles.addElement(col_s)
                    tab.addElement(TableColumn(stylename=col_s))
                grid = {}
                covered = set()
                for c in it.cells:
                    grid[(c.row, c.col)] = c
                    for r in range(c.row, c.row + c.rowspan):
                        for k in range(c.col, c.col + c.colspan):
                            if (r, k) != (c.row, c.col):
                                covered.add((r, k))
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
    doc.save(path)


# ============================================================================ HTML

def save_html(models, path, title=""):
    out = ['<!doctype html><html lang="ru"><head><meta charset="utf-8">',
           "<title>%s</title>" % html.escape(title or "Документ"),
           "<style>body{font-family:'%s',serif;max-width:52em;margin:2em auto;padding:0 1em;line-height:1.35}"
           "table{border-collapse:collapse;margin:1em auto}td{border:1px solid #444;padding:3px 6px;vertical-align:top}"
           ".cols{display:grid;gap:1.5em}figure{text-align:center;margin:1em 0}img{max-width:100%%}"
           "hr.page{border:none;border-top:1px dashed #aaa;margin:2em 0}</style></head><body>" % BODY_FONT]
    for pi, m in enumerate(models):
        if pi:
            out.append('<hr class="page">')
        col_open = False
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
                out.append(_html_para(it, m.body_pt))
            elif isinstance(it, ImageItem):
                out.append('<figure><img alt="" style="width:%.0fpt" src="data:image/%s;base64,%s"></figure>'
                           % (it.width, it.fmt, base64.b64encode(it.data).decode()))
            elif isinstance(it, TableItem):
                out.append(_html_table(it))
        if col_open:
            out.append("</div></div>")
    out.append("</body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def _html_runs(runs):
    s = []
    for r in runs:
        t = html.escape(r.text)
        if r.bold:
            t = "<b>%s</b>" % t
        if r.italic:
            t = "<i>%s</i>" % t
        s.append(t)
    return "".join(s)


def _html_para(p, body_pt):
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
    return "<%s%s>%s</%s>" % (tag, st, _html_runs(p.runs), tag)


def _html_table(t):
    covered = set()
    grid = {}
    for c in t.cells:
        grid[(c.row, c.col)] = c
        for r in range(c.row, c.row + c.rowspan):
            for k in range(c.col, c.col + c.colspan):
                if (r, k) != (c.row, c.col):
                    covered.add((r, k))
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
            tds.append("<td%s>%s</td>" % (attrs, "<br>".join(_html_runs(p.runs) for p in c.paras)))
        rows.append("<tr>%s</tr>" % "".join(tds))
    return "<table>%s</table>" % "".join(rows)


# ============================================================================ XLSX

def save_xlsx(models, path, title=""):
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
    for pi, m in enumerate(models):
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
                for r in range(1, it.rows + 1):
                    for k in range(1, it.cols + 1):
                        ws.cell(row=r, column=k).border = border
            elif isinstance(it, Para):
                text_rows.append((it.text, any(r.bold for r in it.runs) and it.heading))
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
