"""Подготовка изображения к распознаванию.

Конвейер (по мотивам OCRmyPDF и рекомендаций Tesseract):
  1. выравнивание фона (тени, неравномерное освещение на фото);
  2. инверсия светлого текста на тёмном фоне;
  3. поворот на 90/180/270° по модели osd;
  4. устранение перекоса (поиск угла по проекционному профилю);
  5. масштабирование до оптимальной высоты символов (~28 px);
  6. шумоподавление (по необходимости).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

MODE_AUTO = "auto"
MODE_SCAN = "scan"
MODE_PHOTO = "photo"
MODE_SCREEN = "screen"

MODES = {
    MODE_AUTO: "Авто",
    MODE_SCAN: "Скан документа",
    MODE_PHOTO: "Фото документа",
    MODE_SCREEN: "Снимок экрана",
}

TARGET_CHAR_HEIGHT = 28.0


@dataclass
class Options:
    mode: str = MODE_AUTO
    auto_rotate: bool = True
    deskew: bool = True
    rescale: bool = True
    clean_background: bool = True
    denoise: bool = False


@dataclass
class Prepared:
    display: np.ndarray            # RGB после поворота/выравнивания, исходный масштаб
    ocr: np.ndarray                # полутоновое изображение для Tesseract
    scale: float = 1.0             # ocr = display * scale
    rotation: int = 0              # применённый поворот (против часовой, кратно 90)
    skew: float = 0.0              # исправленный угол перекоса, градусы
    inverted: bool = False
    background_fixed: bool = False
    char_height: float = 0.0
    notes: list = field(default_factory=list)

    def summary(self) -> str:
        return ", ".join(self.notes) if self.notes else "без изменений"


def to_rgb(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)
    return img


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_RGBA2GRAY)
    return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)


def _downscale(gray, max_side=1600):
    h, w = gray.shape
    f = min(1.0, max_side / float(max(h, w)))
    if f < 1.0:
        gray = cv2.resize(gray, (int(w * f), int(h * f)), interpolation=cv2.INTER_AREA)
    return gray, f


def is_dark_background(gray) -> bool:
    small, _ = _downscale(gray, 600)
    return float(np.median(small)) < 110


def background_unevenness(gray) -> float:
    """Насколько неравномерен фон (0 — ровный скан, >25 — тени, фото)."""
    small, _ = _downscale(gray, 600)
    k = max(15, (min(small.shape) // 12) | 1)
    bg = cv2.morphologyEx(small, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    bg = cv2.medianBlur(bg, 5)
    lo, hi = np.percentile(bg, [5, 95])
    return float(hi - lo)


def normalize_background(gray) -> np.ndarray:
    """Делит изображение на оценку фона — убирает тени и градиенты освещения."""
    h, w = gray.shape
    small, f = _downscale(gray, 1000)
    k = max(15, (min(small.shape) // 25) | 1)
    bg = cv2.morphologyEx(small, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    bg = cv2.GaussianBlur(bg, (0, 0), k / 3.0)
    bg = cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR)
    norm = cv2.divide(gray, np.maximum(bg, 1), scale=255)
    # растягиваем контраст
    lo, hi = np.percentile(norm, [1, 99.5])
    if hi - lo > 10:
        norm = np.clip((norm.astype(np.float32) - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)
    return norm


def _binary_ink(gray) -> np.ndarray:
    _, b = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return b


def estimate_char_height(gray) -> float:
    """Медианная высота символов по связным компонентам (в пикселях)."""
    b = _binary_ink(gray)
    n, _, stats, _ = cv2.connectedComponentsWithStats(b, connectivity=8)
    if n < 10:
        return 0.0
    hs = stats[1:, cv2.CC_STAT_HEIGHT]
    ws = stats[1:, cv2.CC_STAT_WIDTH]
    areas = stats[1:, cv2.CC_STAT_AREA]
    H, W = gray.shape
    ok = (hs >= 4) & (hs < H / 8) & (ws < W / 5) & (areas >= 6) & (ws <= hs * 3)
    hs = hs[ok]
    if hs.size < 10:
        return 0.0
    return float(np.median(hs))


def estimate_skew(gray, limit=15.0) -> float:
    """Угол перекоса строк в градусах (положительный — текст наклонён против часовой)."""
    small, _ = _downscale(gray, 1400)
    b = _binary_ink(small)
    # убираем крупные пятна и рамки, оставляем текст
    ys, xs = np.nonzero(b)
    if xs.size < 200:
        return 0.0
    if xs.size > 300000:
        idx = np.random.default_rng(0).choice(xs.size, 300000, replace=False)
        xs, ys = xs[idx], ys[idx]
    xs = xs.astype(np.float32) - small.shape[1] / 2.0
    ys = ys.astype(np.float32)
    nbins = small.shape[0] * 2

    def score(a):
        r = np.deg2rad(a)
        yy = ys * np.cos(r) - xs * np.sin(r)
        yy -= yy.min()
        hist = np.bincount(yy.astype(np.int32), minlength=1)
        return float(np.sum(hist.astype(np.float64) ** 2))

    best = max(np.arange(-limit, limit + 0.01, 0.5), key=score)
    fine = np.arange(best - 0.5, best + 0.501, 0.05)
    best = max(fine, key=score)
    base = score(0.0)
    if score(best) < base * 1.02:  # нет уверенного улучшения — не трогаем
        return 0.0
    # best — угол, на который надо «повернуть» точки; наклон текста — обратный
    return float(-best)


def rotate_bound(img, angle, border):
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2.0 - w / 2.0
    m[1, 2] += nh / 2.0 - h / 2.0
    return cv2.warpAffine(img, m, (nw, nh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
                          borderValue=border)


def prepare(image: np.ndarray, opts: Options | None = None, osd=None) -> Prepared:
    """image — RGB/RGBA/GRAY numpy. osd — tess.OsdEngine (необязательно)."""
    opts = opts or Options()
    rgb = to_rgb(image)
    gray = to_gray(rgb)
    notes = []
    mode = opts.mode

    if mode == MODE_AUTO:
        if gray.shape[0] * gray.shape[1] < 1.2e6 and estimate_char_height(gray) < 18:
            mode = MODE_SCREEN
        elif background_unevenness(gray) > 35:
            mode = MODE_PHOTO
        else:
            mode = MODE_SCAN

    inverted = False
    if is_dark_background(gray):
        gray = 255 - gray
        inverted = True
        notes.append("инвертирован тёмный фон")

    bg_fixed = False
    if opts.clean_background and (mode == MODE_PHOTO or background_unevenness(gray) > 35):
        gray = normalize_background(gray)
        bg_fixed = True
        notes.append("выровнен фон")

    if opts.denoise or mode == MODE_PHOTO:
        gray = cv2.fastNlMeansDenoising(gray, None, h=12, templateWindowSize=7, searchWindowSize=21) \
            if gray.size < 12e6 else cv2.medianBlur(gray, 3)
        if opts.denoise:
            notes.append("шумоподавление")

    # Поворот на 90/180/270
    rotation = 0
    if opts.auto_rotate and osd is not None and mode != MODE_SCREEN:
        ch = estimate_char_height(gray) or TARGET_CHAR_HEIGHT
        f = min(2.5, max(0.4, TARGET_CHAR_HEIGHT / ch))
        probe = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC)
        try:
            deg, conf = osd.detect(probe)
        except Exception:
            deg, conf = 0, 0.0
        if deg in (90, 180, 270) and conf >= 2.0:
            k = deg // 90
            gray = np.ascontiguousarray(np.rot90(gray, k))
            rgb = np.ascontiguousarray(np.rot90(rgb, k))
            rotation = deg
            notes.append("повёрнуто на %d°" % (360 - deg))

    # Перекос
    skew = 0.0
    if opts.deskew and mode != MODE_SCREEN:
        skew = estimate_skew(gray)
        if abs(skew) >= 0.15:
            gray = rotate_bound(gray, -skew, 255)
            rgb = rotate_bound(rgb, -skew, (255, 255, 255))
            notes.append("исправлен перекос %.1f°" % skew)
        else:
            skew = 0.0

    # Масштаб
    scale = 1.0
    ch = estimate_char_height(gray)
    if opts.rescale and ch > 0:
        f = TARGET_CHAR_HEIGHT / ch
        if f > 1.25 or f < 0.55:
            f = float(min(4.0, max(0.5, f)))
            # не раздуваем гигантские страницы
            while gray.shape[0] * gray.shape[1] * f * f > 60e6 and f > 0.5:
                f *= 0.9
            scale = f
    if scale != 1.0:
        interp = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
        ocr = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=interp)
        notes.append("масштаб ×%.1f" % scale)
    else:
        ocr = gray

    return Prepared(display=rgb, ocr=np.ascontiguousarray(ocr), scale=scale, rotation=rotation,
                    skew=skew, inverted=inverted, background_fixed=bg_fixed, char_height=ch, notes=notes)
