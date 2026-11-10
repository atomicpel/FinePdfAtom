"""Экспорт: PDF с текстовым слоем, PDF/A, DOCX, ODT, RTF, XLSX, CSV, PPTX, HTML, TXT
и форматы LibreOffice (DOC, ODS, XLS, ODP, PPT, EPUB, FB2), если он установлен."""
from __future__ import annotations

import io
import os
from dataclasses import dataclass

import numpy as np
from PIL import Image

from . import paths

FORMATS = {
    "pdf": "PDF с поиском по тексту (*.pdf)",
    "pdfa": "PDF/A-2b для архива (*.pdf)",
    "docx": "Документ Word (*.docx)",
    "odt": "Документ LibreOffice Writer (*.odt)",
    "rtf": "Документ RTF (*.rtf)",
    "xlsx": "Таблица Excel (*.xlsx)",
    "csv": "Таблицы CSV (*.csv)",
    "pptx": "Презентация PowerPoint (*.pptx)",
    "html": "Веб-страница (*.html)",
    "txt": "Текст (*.txt)",
}

# Форматы через установленный LibreOffice: расширение → (из чего делаем, название)
LO_FORMATS = {
    "doc": ("docx", "Документ Word 97–2003 (*.doc)"),
    "ods": ("xlsx", "Таблица LibreOffice Calc (*.ods)"),
    "xls": ("xlsx", "Таблица Excel 97–2003 (*.xls)"),
    "odp": ("pptx", "Презентация LibreOffice Impress (*.odp)"),
    "ppt": ("pptx", "Презентация PowerPoint 97–2003 (*.ppt)"),
    "epub": ("docx", "Электронная книга EPUB (*.epub)"),
}


def ext_of(fmt: str) -> str:
    return "pdf" if fmt == "pdfa" else fmt


def soffice_path():
    import shutil
    for name in ("soffice", "libreoffice", "loffice"):
        p = shutil.which(name)
        if p:
            return p
    for p in ("/opt/libreoffice/program/soffice", "/usr/lib/libreoffice/program/soffice"):
        if os.path.exists(p):
            return p
    return None


def all_formats() -> dict:
    """Все доступные форматы (с LibreOffice — больше)."""
    out = dict(FORMATS)
    if soffice_path():
        out.update({k: v[1] for k, v in LO_FORMATS.items()})
    return out


PDF_QUALITY = {
    "color": "Цветной, высокое качество",
    "mrc": "Цветной компактный (MRC): в 4–8 раз меньше",
    "gray": "Оттенки серого, компактный",
    "bw": "Чёрно-белый, минимальный размер",
}


@dataclass
class ExportPage:
    result: object            # ocr.PageResult
    text: str                 # итоговый (возможно, отредактированный) текст
    ref: object = None        # sources.PageRef — для PDF-источников


def _words(p):
    from . import docmodel
    return docmodel.page_data(p.result, p.text).words


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


def _encode_mrc(rgb: np.ndarray):
    """MRC: фон (без текста, уменьшен) + чернила (цвет низкого разрешения) с чёткой маской текста.

    Возвращает [(stream, mask_stream|None)] — слои для вставки снизу вверх."""
    import cv2
    from .preprocess import normalize_background
    H, W = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    norm = normalize_background(gray)
    mask = cv2.adaptiveThreshold(norm, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 12)
    # фотографии и заливки оставляем в фоне: из маски убираем крупные сплошные области
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    big = np.zeros(n, bool)
    if n > 1:
        dens = st[1:, 4] / np.maximum(1, st[1:, 2] * st[1:, 3])
        big[1:] = (st[1:, 2] > W * 0.15) & (st[1:, 3] > H * 0.08) & (dens > 0.35)
        mask[big[lab]] = 0
    f = 2
    bgm = cv2.dilate(mask, np.ones((5, 5), np.uint8))
    small = cv2.resize(rgb, (max(1, W // f), max(1, H // f)), interpolation=cv2.INTER_AREA)
    sm = cv2.resize(bgm, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
    bg = cv2.inpaint(small, sm, 3, cv2.INPAINT_TELEA)
    fg = cv2.resize(rgb, (max(1, W // 6), max(1, H // 6)), interpolation=cv2.INTER_AREA)
    fg = cv2.erode(fg, np.ones((3, 3), np.uint8))

    def jpg(a, q):
        b = io.BytesIO()
        if _is_grayscale(a):
            Image.fromarray(a[:, :, 0]).save(b, "JPEG", quality=q, optimize=True)
        else:
            Image.fromarray(a).save(b, "JPEG", quality=q, optimize=True)
        return b.getvalue()
    mb = io.BytesIO()
    Image.fromarray(mask).convert("1").save(mb, "PNG", optimize=True)
    return [(jpg(bg, 50), None), (jpg(fg, 60), mb.getvalue())]


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


def save_pdf(pages, path, quality="color", title="", progress=None, pdfa=False):
    import fitz
    font = fitz.Font(fontfile=_font_path())
    out = fitz.open()
    for i, p in enumerate(pages):
        r = p.result
        ref = p.ref
        same_geometry = (ref is not None and getattr(ref, "kind", "") == "pdf" and r.dpi
                         and "повёрнуто" not in r.notes and "перекос" not in r.notes)
        # для PDF/A исходные страницы не переносим: в них может быть что угодно (шрифты, прозрачность)
        if not pdfa and ref is not None and getattr(ref, "kind", "") == "pdf" and \
                (r.from_text_layer or same_geometry):
            # Исходная PDF-страница сохраняется как есть (векторы, качество), добавляется текст
            with fitz.open(ref.path) as src:
                out.insert_pdf(src, from_page=ref.index, to_page=ref.index)
            page = out[-1]
            if not r.from_text_layer:
                k = 72.0 / r.dpi
                page.insert_font(fontname="OCR", fontfile=_font_path())
                # координаты отрисовки → систему страницы с учётом поворота /Rotate
                words = _unrotate_words(_words(p), page, k)
                _add_text_layer(page, words, 1.0, "OCR", font)
        else:
            dpi = r.dpi or 300
            h, w = r.display.shape[:2]
            k = 72.0 / dpi
            page = out.new_page(width=w * k, height=h * k)
            if quality == "mrc":
                for stream, mask in _encode_mrc(r.display):
                    page.insert_image(page.rect, stream=stream, mask=mask)
            else:
                page.insert_image(page.rect, stream=_encode_image(r.display, quality))
            page.insert_font(fontname="OCR", fontfile=_font_path())
            _add_text_layer(page, _words(p), k, "OCR", font)
        if progress:
            progress(i + 1, len(pages))
    title = title or os.path.splitext(os.path.basename(path))[0]
    out.subset_fonts()
    if pdfa:
        _make_pdfa(out, title)
    else:
        out.set_metadata({"title": title, "creator": "FinePdfAtom", "producer": "FinePdfAtom (Tesseract OCR)"})
    out.save(path, garbage=4, deflate=True)
    out.close()


def _srgb_icc() -> bytes:
    from PIL import ImageCms
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _make_pdfa(doc, title):
    """Оформляет документ как PDF/A-2b: цветовой профиль sRGB, метаданные XMP, согласованные с Info."""
    import time
    from xml.sax.saxutils import escape
    now = time.localtime()
    off = time.strftime("%z", now) or "+0000"
    pdf_date = time.strftime("D:%Y%m%d%H%M%S", now) + "%s'%s'" % (off[:3], off[3:])
    xmp_date = time.strftime("%Y-%m-%dT%H:%M:%S", now) + "%s:%s" % (off[:3], off[3:])
    creator, producer = "FinePdfAtom", "FinePdfAtom (Tesseract OCR)"
    doc.set_metadata({"title": title, "creator": creator, "producer": producer,
                      "creationDate": pdf_date, "modDate": pdf_date})
    # PDF/A требует у встроенных шрифтов CIDFontType2 явной таблицы CID → глиф
    for x in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(x, "Subtype")[1] == "/CIDFontType2" and \
                    doc.xref_get_key(x, "CIDToGIDMap")[0] == "null":
                doc.xref_set_key(x, "CIDToGIDMap", "/Identity")
        except Exception:  # noqa: BLE001
            pass
    icc = doc.get_new_xref()
    doc.update_object(icc, "<</N 3>>")
    doc.update_stream(icc, _srgb_icc())
    oi = doc.get_new_xref()
    doc.update_object(oi, "<</Type/OutputIntent/S/GTS_PDFA1/OutputConditionIdentifier(sRGB IEC61966-2.1)"
                          "/Info(sRGB IEC61966-2.1)/DestOutputProfile %d 0 R>>" % icc)
    doc.xref_set_key(doc.pdf_catalog(), "OutputIntents", "[%d 0 R]" % oi)
    xmp = """<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">
   <pdfaid:part>2</pdfaid:part><pdfaid:conformance>B</pdfaid:conformance>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">
   <dc:format>application/pdf</dc:format>
   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">%(title)s</rdf:li></rdf:Alt></dc:title>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">
   <xmp:CreatorTool>%(creator)s</xmp:CreatorTool>
   <xmp:CreateDate>%(date)s</xmp:CreateDate><xmp:ModifyDate>%(date)s</xmp:ModifyDate>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:pdf="http://ns.adobe.com/pdf/1.3/">
   <pdf:Producer>%(producer)s</pdf:Producer>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>""" % dict(title=escape(title), creator=escape(creator), producer=escape(producer), date=xmp_date)
    doc.set_xml_metadata(xmp)


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


# --- документы с оформлением -------------------------------------------------

def build_doc(pages, mode="formatted", dehyphenate=True, title=""):
    """Страницы → документ целиком (колонтитулы, сноски, ссылки, единые стили)."""
    from . import docanalysis, docmodel
    models = [docmodel.build(p.result, p.text, plain=(mode == "plain"), dehyphenate=dehyphenate) for p in pages]
    return docanalysis.plain(models, title) if mode == "plain" else docanalysis.analyze(models, title)


def _convert_lo(src, dst):
    """Преобразование через LibreOffice (без окна)."""
    import shutil
    import subprocess
    import tempfile
    exe = soffice_path()
    if not exe:
        raise RuntimeError("Для этого формата нужен LibreOffice (команда soffice не найдена).")
    ext = os.path.splitext(dst)[1].lstrip(".")
    with tempfile.TemporaryDirectory(prefix="finepdfatom-lo-") as tmp:
        # отдельный профиль: не мешаем открытому LibreOffice пользователя
        r = subprocess.run([exe, "-env:UserInstallation=file://" + os.path.join(tmp, "profile"), "--headless",
                            "--convert-to", ext, "--outdir", tmp, src],
                           capture_output=True, text=True, timeout=600)
        out = os.path.join(tmp, os.path.splitext(os.path.basename(src))[0] + "." + ext)
        if not os.path.exists(out):
            raise RuntimeError("LibreOffice не смог сохранить %s: %s" % (ext.upper(), (r.stderr or r.stdout).strip()[:300]))
        shutil.move(out, dst)


def save(pages, path, fmt=None, pdf_quality="color", progress=None, mode="formatted"):
    fmt = (fmt or os.path.splitext(path)[1].lstrip(".")).lower()
    if fmt == "htm":
        fmt = "html"
    title = os.path.splitext(os.path.basename(path))[0]
    if fmt in ("pdf", "pdfa"):
        return save_pdf(pages, path, pdf_quality, progress=progress, pdfa=(fmt == "pdfa"))
    if fmt == "txt":
        return save_txt(pages, path)
    if fmt in LO_FORMATS:
        import tempfile
        base = LO_FORMATS[fmt][0]
        with tempfile.TemporaryDirectory(prefix="finepdfatom-") as tmp:
            mid = os.path.join(tmp, "document." + base)
            save(pages, mid, base, pdf_quality, None, mode)
            _convert_lo(mid, path)
        return None
    from . import richexport
    writers = {"docx": richexport.save_docx, "odt": richexport.save_odt, "rtf": richexport.save_rtf,
               "html": richexport.save_html, "xlsx": richexport.save_xlsx, "csv": richexport.save_csv,
               "pptx": richexport.save_pptx}
    if fmt not in writers:
        raise ValueError("Неизвестный формат: %s" % fmt)
    doc = build_doc(pages, mode, title=title)
    writers[fmt](doc, path, title)
    return doc
