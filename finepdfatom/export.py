"""Экспорт результатов: TXT, PDF с текстовым слоем, DOCX, ODT."""
from __future__ import annotations

import io
import os
from dataclasses import dataclass

import numpy as np
from PIL import Image

from . import paths

FORMATS = {
    "pdf": "PDF с поиском по тексту (*.pdf)",
    "txt": "Текст (*.txt)",
    "docx": "Документ Word (*.docx)",
    "odt": "Документ LibreOffice (*.odt)",
}

PDF_QUALITY = {
    "color": "Цветной, высокое качество",
    "gray": "Оттенки серого, компактный",
    "bw": "Чёрно-белый, минимальный размер",
}


@dataclass
class ExportPage:
    result: object            # ocr.PageResult
    text: str                 # итоговый (возможно, отредактированный) текст
    ref: object = None        # sources.PageRef — для PDF-источников


def _font_path():
    return paths.resource("DejaVuSans.ttf")


# --- TXT --------------------------------------------------------------------

def save_txt(pages, path, page_separator="\n\n\f\n"):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(page_separator.join(p.text.rstrip() for p in pages) + "\n")


# --- PDF --------------------------------------------------------------------

def _encode_image(rgb: np.ndarray, quality: str):
    """Возвращает (bytes, тип) для вставки в PDF."""
    import cv2
    buf = io.BytesIO()
    if quality == "bw":
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        from .preprocess import normalize_background
        gray = normalize_background(gray)
        bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 15)
        Image.fromarray(bw).convert("1").save(buf, "PNG", optimize=True)
    elif quality == "gray":
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        Image.fromarray(gray).save(buf, "JPEG", quality=72, optimize=True)
    else:
        if _is_grayscale(rgb):
            Image.fromarray(rgb[:, :, 0]).save(buf, "JPEG", quality=85, optimize=True)
        else:
            Image.fromarray(rgb).save(buf, "JPEG", quality=85, optimize=True, subsampling=1)
    return buf.getvalue()


def _is_grayscale(rgb) -> bool:
    s = rgb[::8, ::8].astype(np.int16)
    return int(np.abs(s[:, :, 0] - s[:, :, 1]).max()) < 12 and int(np.abs(s[:, :, 1] - s[:, :, 2]).max()) < 12


def _add_text_layer(page, words, k, fontname, font):
    """Невидимый текст поверх изображения. k — пиксели → пункты."""
    import fitz
    for w in words:
        x0, y0, x1, y1 = w.box
        bw, bh = (x1 - x0) * k, (y1 - y0) * k
        if bw <= 0 or bh <= 0 or not w.text:
            continue
        fs = max(1.0, bh * 0.92)
        tl = font.text_length(w.text, fs)
        if tl <= 0:
            continue
        sx = bw / tl
        pt = fitz.Point(x0 * k, y1 * k - bh * 0.18)
        page.insert_text(pt, w.text + " ", fontsize=fs, fontname=fontname, render_mode=3,
                         morph=(pt, fitz.Matrix(sx, 1)))


def save_pdf(pages, path, quality="color", title="", progress=None):
    import fitz
    font = fitz.Font(fontfile=_font_path())
    out = fitz.open()
    for i, p in enumerate(pages):
        r = p.result
        ref = p.ref
        same_geometry = (ref is not None and getattr(ref, "kind", "") == "pdf" and r.dpi
                         and "повёрнуто" not in r.notes and "перекос" not in r.notes)
        if ref is not None and getattr(ref, "kind", "") == "pdf" and (r.from_text_layer or same_geometry):
            # Исходная PDF-страница сохраняется как есть (векторы, качество), добавляется текст
            with fitz.open(ref.path) as src:
                out.insert_pdf(src, from_page=ref.index, to_page=ref.index)
            page = out[-1]
            if not r.from_text_layer:
                k = 72.0 / r.dpi
                page.insert_font(fontname="OCR", fontfile=_font_path())
                # координаты отрисовки → систему страницы с учётом поворота /Rotate
                words = _unrotate_words(r.data.words, page, k)
                _add_text_layer(page, words, 1.0, "OCR", font)
        else:
            dpi = r.dpi or 300
            h, w = r.display.shape[:2]
            k = 72.0 / dpi
            page = out.new_page(width=w * k, height=h * k)
            page.insert_image(page.rect, stream=_encode_image(r.display, quality))
            page.insert_font(fontname="OCR", fontfile=_font_path())
            _add_text_layer(page, r.data.words, k, "OCR", font)
        if progress:
            progress(i + 1, len(pages))
    out.set_metadata({"title": title or os.path.splitext(os.path.basename(path))[0],
                      "creator": "FinePdfAtom", "producer": "FinePdfAtom (Tesseract OCR)"})
    out.subset_fonts()
    out.save(path, garbage=4, deflate=True)
    out.close()


class _W:
    __slots__ = ("text", "box")

    def __init__(self, text, box):
        self.text, self.box = text, box


def _unrotate_words(words, page, k):
    """Пересчитывает рамки слов из координат отрисованной картинки в координаты страницы."""
    import fitz
    m = page.rotation_matrix  # неповёрнутая → повёрнутая (отрисовка)
    inv = ~m
    out = []
    for w in words:
        x0, y0, x1, y1 = (v * k for v in w.box)
        r = fitz.Rect(x0, y0, x1, y1) * inv
        out.append(_W(w.text, (r.x0, r.y0, r.x1, r.y1)))
    return out


# --- DOCX -------------------------------------------------------------------

def save_docx(pages, path):
    import docx
    from docx.enum.text import WD_BREAK
    from docx.shared import Pt
    d = docx.Document()
    st = d.styles["Normal"]
    st.font.name = "Times New Roman"
    st.font.size = Pt(12)
    for i, p in enumerate(pages):
        paras = [x for x in p.text.split("\n\n")] if p.text.strip() else [""]
        for j, para in enumerate(paras):
            par = d.add_paragraph()
            lines = para.split("\n")
            for li, ln in enumerate(lines):
                run = par.add_run(ln)
                if li < len(lines) - 1:
                    run.add_break()
        if i < len(pages) - 1:
            d.paragraphs[-1].add_run().add_break(WD_BREAK.PAGE)
    d.core_properties.author = "FinePdfAtom"
    d.save(path)


# --- ODT --------------------------------------------------------------------

def save_odt(pages, path):
    from odf.opendocument import OpenDocumentText
    from odf.style import ParagraphProperties, Style, TextProperties
    from odf.text import LineBreak, P
    doc = OpenDocumentText()
    normal = Style(name="Текст", family="paragraph")
    normal.addElement(TextProperties(fontname="Times New Roman", fontsize="12pt"))
    normal.addElement(ParagraphProperties(marginbottom="0.2cm"))
    doc.styles.addElement(normal)
    brk = Style(name="РазрывСтраницы", family="paragraph", parentstylename=normal)
    brk.addElement(ParagraphProperties(breakbefore="page"))
    doc.automaticstyles.addElement(brk)
    for i, p in enumerate(pages):
        paras = p.text.split("\n\n") if p.text.strip() else [""]
        for j, para in enumerate(paras):
            style = brk if (i > 0 and j == 0) else normal
            el = P(stylename=style)
            lines = para.split("\n")
            for li, ln in enumerate(lines):
                el.addText(ln)
                if li < len(lines) - 1:
                    el.addElement(LineBreak())
            doc.text.addElement(el)
    doc.save(path)


def save(pages, path, fmt=None, pdf_quality="color", progress=None):
    fmt = (fmt or os.path.splitext(path)[1].lstrip(".")).lower()
    if fmt == "pdf":
        save_pdf(pages, path, pdf_quality, progress=progress)
    elif fmt == "txt":
        save_txt(pages, path)
    elif fmt == "docx":
        save_docx(pages, path)
    elif fmt == "odt":
        save_odt(pages, path)
    else:
        raise ValueError("Неизвестный формат: %s" % fmt)
