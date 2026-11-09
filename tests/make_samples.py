"""Генерация тестовых «сканов»: ровный, перекошенный, перевёрнутый, тёмный фон, мелкий."""
import os
import random
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

TEXT = [
    "Федеральная служба по техническому и экспортному контролю",
    "Операционная система специального назначения Astra Linux",
    "Распознавание текста выполняется полностью без интернета.",
    "Съешь же ещё этих мягких французских булок, да выпей чаю.",
    "The quick brown fox jumps over the lazy dog 1234567890.",
    "Номер документа: 47/2026-ОД от 08.11.2026 г.",
]

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"


def page(size=34, w=2480, h=1400):
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(FONT, size)
    y = 120
    for line in TEXT:
        d.text((150, y), line, font=f, fill=0)
        y += int(size * 1.9)
    return img


def main(out):
    os.makedirs(out, exist_ok=True)
    random.seed(1)
    page().save(os.path.join(out, "01_clean.png"), dpi=(300, 300))
    p = page().rotate(3.2, expand=True, fillcolor=255, resample=Image.BICUBIC)
    p.save(os.path.join(out, "02_skew.png"))
    page().rotate(90, expand=True).save(os.path.join(out, "03_rotated90.png"))
    page().rotate(180).save(os.path.join(out, "04_upside_down.png"))
    # «фото» с тенью и шумом
    p = page().convert("RGB")
    px = p.load()
    W, H = p.size
    for y in range(0, H, 2):
        for x in range(0, W, 2):
            shade = int(90 * x / W)
            r, g, b = px[x, y]
            v = max(0, r - shade - random.randint(0, 25))
            for dx in (0, 1):
                for dy in (0, 1):
                    if x + dx < W and y + dy < H:
                        rr = px[x + dx, y + dy][0]
                        vv = max(0, rr - shade - random.randint(0, 25))
                        px[x + dx, y + dy] = (vv, vv, int(vv * 0.95))
    p.filter(ImageFilter.GaussianBlur(1.2)).save(os.path.join(out, "05_photo_shadow.jpg"), quality=70)
    # мелкий текст (как скриншот 96 dpi)
    small = page(size=34).resize((620, 350), Image.LANCZOS)
    small.save(os.path.join(out, "06_small.png"))
    # реалистичный снимок экрана: шрифт ~13 px без сглаживания по субпикселям
    scr = Image.new("L", (900, 170), 245)
    d = ImageDraw.Draw(scr)
    f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
    for i, line in enumerate(TEXT):
        d.text((10, 8 + i * 26), line, font=f, fill=30)
    scr.save(os.path.join(out, "08_screen13.png"))
    # белый текст на тёмном фоне
    from PIL import ImageOps
    ImageOps.invert(page(size=30).resize((1240, 700), Image.LANCZOS)).save(os.path.join(out, "07_dark.png"))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "samples")


def photo_of_page(src, dst):
    """Фото листа на столе: перспектива, тёмный фон, неравномерный свет, JPEG."""
    import cv2
    import numpy as np
    page = cv2.imread(src)
    page = cv2.resize(page, None, fx=0.75, fy=0.75, interpolation=cv2.INTER_AREA)
    h, w = page.shape[:2]
    W, H = int(w * 1.35), int(h * 1.2)
    bg = np.zeros((H, W, 3), np.uint8)
    bg[:] = (40, 60, 85)
    noise = np.random.default_rng(3).integers(0, 25, (H, W, 1), dtype=np.uint8)
    bg = cv2.add(bg, np.repeat(noise, 3, axis=2))
    src_q = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst_q = np.float32([[W * 0.18, H * 0.06], [W * 0.86, H * 0.03], [W * 0.95, H * 0.95], [W * 0.08, H * 0.9]])
    m = cv2.getPerspectiveTransform(src_q, dst_q)
    warped = cv2.warpPerspective(page, m, (W, H))
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), m, (W, H))
    out = bg.copy()
    out[mask > 0] = warped[mask > 0]
    xs = np.linspace(1.0, 0.72, W, dtype=np.float32)[None, :, None]
    out = np.clip(out.astype(np.float32) * xs, 0, 255).astype(np.uint8)
    out = cv2.GaussianBlur(out, (3, 3), 0)
    cv2.imwrite(dst, out, [cv2.IMWRITE_JPEG_QUALITY, 80])
