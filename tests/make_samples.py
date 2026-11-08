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
