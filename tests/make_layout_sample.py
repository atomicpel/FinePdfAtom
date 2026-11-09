"""Тестовая страница «как в жизни»: заголовок, абзацы по ширине, жирный текст,
две колонки, таблица с объединёнными ячейками, рисунок. 300 dpi, A4."""
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

DPI = 300
W, H = 2480, 3508
SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
SERIF_B = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def px(pt):
    return int(round(pt * DPI / 72.0))


def font(path, pt):
    return ImageFont.truetype(path, px(pt))


def wrap(d, text, f, width):
    words, lines, cur = text.split(), [], []
    for w in words:
        t = " ".join(cur + [w])
        if d.textlength(t, font=f) <= width or not cur:
            cur.append(w)
        else:
            lines.append(cur)
            cur = [w]
    if cur:
        lines.append(cur)
    return lines


def para(d, x, y, width, text, f, align="justify", indent=0, leading=1.35):
    lines = wrap(d, text, f, width - indent)
    lh = int(f.size * leading)
    for i, ws in enumerate(lines):
        xx = x + (indent if i == 0 else 0)
        avail = width - (indent if i == 0 else 0)
        if align == "justify" and i < len(lines) - 1 and len(ws) > 1:
            tw = sum(d.textlength(w, font=f) for w in ws)
            gap = (avail - tw) / (len(ws) - 1)
            for w in ws:
                d.text((xx, y), w, font=f, fill=0)
                xx += d.textlength(w, font=f) + gap
        else:
            t = " ".join(ws)
            tw = d.textlength(t, font=f)
            if align == "center":
                xx = x + (width - tw) / 2
            elif align == "right":
                xx = x + width - tw
            d.text((xx, y), t, font=f, fill=0)
        y += lh
    return y


LOREM = ("Настоящий документ определяет порядок применения средств защиты информации в "
         "автоматизированных системах и устанавливает требования к их настройке, эксплуатации и "
         "контролю. Требования распространяются на все подразделения организации без исключения.")
COL1 = ("Первая колонка содержит описание порядка работы. Пользователь обязан соблюдать правила "
        "парольной защиты и не передавать учётные данные третьим лицам. Нарушения фиксируются в журнале.")
COL2 = ("Вторая колонка продолжает изложение. Администратор безопасности проверяет журналы не реже "
        "одного раза в неделю и докладывает о выявленных нарушениях руководителю подразделения.")

TABLE = [
    ["№", "Наименование", "Количество", "Цена, руб."],
    ["1", "Системный блок", "12", "45 600,00"],
    ["2", "Монитор 24 дюйма", "12", "15 250,50"],
    ["3", "Источник питания", "4", "8 900,00"],
    ["", "Итого", "", "780 006,00"],
]


def main(out):
    os.makedirs(out, exist_ok=True)
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    left, right = px(72), W - px(54)
    width = right - left
    truth = {"paras": []}
    y = px(60)
    f = font(SERIF_B, 16)
    y = para(d, left, y, width, "ПОЛОЖЕНИЕ О ЗАЩИТЕ ИНФОРМАЦИИ", f, "center")
    truth["paras"].append({"text": "ПОЛОЖЕНИЕ О ЗАЩИТЕ ИНФОРМАЦИИ", "pt": 16, "bold": True, "align": "center"})
    y += px(14)
    f12 = font(SERIF, 12)
    y = para(d, left, y, width, LOREM, f12, "justify", indent=px(36))
    truth["paras"].append({"text": LOREM, "pt": 12, "bold": False, "align": "justify"})
    y += px(10)
    fb = font(SERIF_B, 12)
    y = para(d, left, y, width, "Раздел 1. Общие положения", fb, "left")
    truth["paras"].append({"text": "Раздел 1. Общие положения", "pt": 12, "bold": True, "align": "left"})
    y += px(10)
    # две колонки
    gap = px(28)
    cw = (width - gap) // 2
    y1 = para(d, left, y, cw, COL1, f12, "justify")
    y2 = para(d, left + cw + gap, y, cw, COL2, f12, "justify")
    truth["columns"] = [COL1, COL2]
    y = max(y1, y2) + px(18)
    # таблица
    colw = [px(40), px(220), px(110), px(120)]
    rowh = px(26)
    tx, ty = left, y
    tw = sum(colw)
    for r, row in enumerate(TABLE):
        cx = tx
        for c, txt in enumerate(row):
            if r == len(TABLE) - 1 and c == 0:
                # объединённая ячейка «Итого» на два столбца
                pass
            ff = fb if r == 0 or r == len(TABLE) - 1 else f12
            if txt:
                tl = d.textlength(txt, font=ff)
                if c == 1 and r == len(TABLE) - 1:
                    d.text((tx + (colw[0] + colw[1] - tl) / 2, ty + r * rowh + px(5)), txt, font=ff, fill=0)
                else:
                    d.text((cx + (colw[c] - tl) / 2, ty + r * rowh + px(5)), txt, font=ff, fill=0)
            cx += colw[c]
    lw = 3
    for r in range(len(TABLE) + 1):
        d.line([(tx, ty + r * rowh), (tx + tw, ty + r * rowh)], fill=0, width=lw)
    cx = tx
    for c in range(len(colw) + 1):
        if c == 1:
            # в последней строке нет вертикали между 0 и 1 столбцами
            d.line([(cx, ty), (cx, ty + (len(TABLE) - 1) * rowh)], fill=0, width=lw)
        else:
            d.line([(cx, ty), (cx, ty + len(TABLE) * rowh)], fill=0, width=lw)
        if c < len(colw):
            cx += colw[c]
    truth["table"] = TABLE
    y = ty + len(TABLE) * rowh + px(24)
    # рисунок (градиент с фигурами)
    pw, ph = px(200), px(110)
    pic = Image.new("L", (pw, ph))
    pd = ImageDraw.Draw(pic)
    for i in range(pw):
        pd.line([(i, 0), (i, ph)], fill=int(60 + 150 * i / pw))
    pd.ellipse([pw * 0.1, ph * 0.15, pw * 0.45, ph * 0.85], fill=30)
    pd.rectangle([pw * 0.55, ph * 0.2, pw * 0.9, ph * 0.8], fill=230)
    img.paste(pic, (left + (width - pw) // 2, y))
    y += ph + px(8)
    fc = font(SANS, 10)
    y = para(d, left, y, width, "Рисунок 1. Схема размещения оборудования", fc, "center")
    truth["paras"].append({"text": "Рисунок 1. Схема размещения оборудования", "pt": 10, "bold": False,
                           "align": "center"})
    y += px(14)
    y = para(d, left, y, width, "Начальник отдела                                        П. А. Дудин", f12, "left")
    img.save(os.path.join(out, "layout.png"), dpi=(DPI, DPI))
    with open(os.path.join(out, "layout.json"), "w") as fh:
        json.dump(truth, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "samples_layout")
