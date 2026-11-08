"""Источники изображений: файлы, многостраничные TIFF/PDF, сканер (SANE)."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps, ImageSequence

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".jpe", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".pnm",
             ".pbm", ".pgm", ".ppm", ".jp2"}
PDF_EXT = {".pdf"}
SUPPORTED_EXT = IMAGE_EXT | PDF_EXT

PDF_RENDER_DPI = 300

Image.MAX_IMAGE_PIXELS = 400_000_000


@dataclass
class PageRef:
    """Ссылка на страницу-источник. Изображение загружается по требованию."""
    path: str | None
    index: int = 0
    kind: str = "image"           # image | pdf | memory
    label: str = ""
    hint_mode: str = ""           # подсказка типа изображения (например, "screen")
    _image: np.ndarray | None = None
    _dpi: int = 0

    def load(self):
        """Возвращает (RGB numpy, dpi)."""
        if self.kind == "memory":
            return self._image, self._dpi or 96
        if self.kind == "pdf":
            return render_pdf_page(self.path, self.index, PDF_RENDER_DPI)
        return load_image(self.path, self.index)

    def pdf_words(self, dpi=PDF_RENDER_DPI):
        """Текстовый слой PDF-страницы (если есть) в координатах отрисовки."""
        if self.kind != "pdf":
            return None
        return pdf_text_words(self.path, self.index, dpi)


def memory_ref(image: np.ndarray, label: str, dpi: int = 96, hint_mode: str = "") -> PageRef:
    return PageRef(None, 0, "memory", label, hint_mode, image, dpi)


def is_supported(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in SUPPORTED_EXT


def expand_paths(paths) -> list:
    """Файлы и папки → отсортированный список поддерживаемых файлов."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for f in sorted(files):
                    fp = os.path.join(root, f)
                    if is_supported(fp):
                        out.append(fp)
        elif os.path.isfile(p) and is_supported(p):
            out.append(p)
    return out


def page_refs(path: str) -> list:
    ext = os.path.splitext(path)[1].lower()
    base = os.path.basename(path)
    if ext in PDF_EXT:
        import fitz
        with fitz.open(path) as doc:
            n = doc.page_count
        return [PageRef(path, i, "pdf", "%s — стр. %d" % (base, i + 1) if n > 1 else base) for i in range(n)]
    with Image.open(path) as im:
        n = getattr(im, "n_frames", 1)
    if n > 1:
        return [PageRef(path, i, "image", "%s — стр. %d" % (base, i + 1)) for i in range(n)]
    return [PageRef(path, 0, "image", base)]


def pil_to_rgb_array(im: Image.Image) -> np.ndarray:
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg
    if im.mode != "RGB":
        if im.mode == "I;16" or im.mode == "I":
            arr = np.array(im, dtype=np.float32)
            arr = (arr / max(1.0, arr.max()) * 255).astype(np.uint8)
            im = Image.fromarray(arr)
        im = im.convert("RGB")
    return np.ascontiguousarray(np.array(im))


def load_image(path: str, index: int = 0):
    with Image.open(path) as im:
        if index:
            for i, frame in enumerate(ImageSequence.Iterator(im)):
                if i == index:
                    im = frame.copy()
                    break
        dpi = im.info.get("dpi", (0, 0))
        try:
            dpi = int(round(float(dpi[0])))
        except Exception:
            dpi = 0
        arr = pil_to_rgb_array(im)
    return arr, dpi


def render_pdf_page(path: str, index: int, dpi: int = PDF_RENDER_DPI):
    """Отрисовка страницы PDF → (RGB numpy, фактическое разрешение)."""
    import fitz
    with fitz.open(path) as doc:
        page = doc[index]
        # не раздуваем огромные страницы (чертежи)
        zoom = dpi / 72.0
        w, h = page.rect.width * zoom, page.rect.height * zoom
        if w * h > 80e6:
            zoom *= (80e6 / (w * h)) ** 0.5
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        return np.ascontiguousarray(arr), zoom * 72.0


def pdf_text_words(path: str, index: int, dpi: int = PDF_RENDER_DPI):
    """Слова из существующего текстового слоя PDF или None, если слоя нет."""
    import fitz
    with fitz.open(path) as doc:
        page = doc[index]
        words = page.get_text("words")
        if len(words) < 3:
            return None
        zoom = dpi / 72.0
        return [(w[4], (w[0] * zoom, w[1] * zoom, w[2] * zoom, w[3] * zoom), w[5], w[6], w[7])
                for w in words]


# --- Сканер ---------------------------------------------------------------

def scanimage_path():
    return shutil.which("scanimage")


def list_scanners(timeout=25) -> list:
    """[(device, описание)] через scanimage -L."""
    exe = scanimage_path()
    if not exe:
        raise RuntimeError("Не найдена утилита scanimage (пакет sane-utils).")
    p = subprocess.run([exe, "-f", "%d\t%v %m (%t)%n"], capture_output=True, text=True, timeout=timeout)
    out = []
    for line in p.stdout.splitlines():
        if "\t" in line:
            dev, desc = line.split("\t", 1)
            out.append((dev.strip(), desc.strip()))
    return out


def scan_command(device: str, dpi: int = 300, mode: str = "Gray", out_path: str | None = None,
                 source: str | None = None) -> list:
    exe = scanimage_path() or "scanimage"
    cmd = [exe, "-d", device, "--format=tiff", "--resolution", str(dpi), "--mode", mode]
    if source:
        cmd += ["--source", source]
    if out_path:
        cmd += ["-o", out_path]
    return cmd


def scan_to_file(device: str, dpi=300, mode="Gray", timeout=180) -> str:
    fd, out = tempfile.mkstemp(prefix="finepdfatom-scan-", suffix=".tiff")
    os.close(fd)
    cmd = scan_command(device, dpi, mode, out)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0 or os.path.getsize(out) == 0:
        # старые версии scanimage не знают -o
        with open(out, "wb") as f:
            p = subprocess.run(cmd[:-2], stdout=f, stderr=subprocess.PIPE, timeout=timeout)
        if p.returncode != 0:
            raise RuntimeError((p.stderr.decode() if isinstance(p.stderr, bytes) else p.stderr) or
                               "Ошибка сканирования")
    return out
