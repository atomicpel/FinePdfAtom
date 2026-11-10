"""Тестовый многостраничный документ для проверки анализа «документ целиком»:
колонтитул, номера страниц (с 5-й), сноска со знаком в тексте, ссылка и почта,
абзац, разорванный границей страницы, таблица без линий, английский текст."""
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(__file__))
from make_layout_sample import SANS, SERIF, SERIF_B, H, W, font, para, px  # noqa: E402

HEADER = "Инструкция по эксплуатации изделия"
P1 = ("Настоящая инструкция определяет порядок установки, настройки и эксплуатации изделия. "
      "Перед началом работы необходимо внимательно изучить все разделы документа.")
P_LINK = ("Исходные тексты размещены по адресу https://github.com/atomicpel/FinePdfAtom, "
          "вопросы направляйте на support@example.org в рабочее время.")
P_SPLIT_A = ("Изделие устанавливается в сухом отапливаемом помещении. Допустимая температура воздуха "
             "составляет от пяти до сорока градусов, а относительная влажность не должна превышать")
P_SPLIT_B = ("восьмидесяти процентов при любых условиях работы. Запрещается эксплуатация изделия "
             "вблизи источников сильных электромагнитных полей.")
P_EN = ("The device must be connected to a grounded power outlet. Do not open the housing while "
        "the power cord is plugged in. Contact the service center if the indicator stays red.")
P3 = ("Гарантийный срок эксплуатации составляет двадцать четыре месяца со дня продажи. "
      "В течение этого срока неисправности устраняются бесплатно.")
NOTE = "Сведения приведены по состоянию на начало текущего года."
TABLE = [["Параметр", "Значение", "Единица"],
         ["Напряжение питания", "220", "В"],
         ["Потребляемая мощность", "45", "Вт"],
         ["Масса изделия", "3,2", "кг"],
         ["Срок службы", "10", "лет"]]


def new_page(n):
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    left, right = px(72), W - px(54)
    para(d, left, px(26), right - left, HEADER, font(SANS, 9), "center")
    para(d, left, H - px(44), right - left, "— %d —" % n, font(SERIF, 10), "center")
    return img, d, left, right - left


def main(out):
    os.makedirs(out, exist_ok=True)
    f12, fb = font(SERIF, 12), font(SERIF_B, 12)
    pages = []
    # ---- страница 1
    img, d, left, width = new_page(5)
    y = px(70)
    y = para(d, left, y, width, "ОБЩЕЕ ОПИСАНИЕ ИЗДЕЛИЯ", font(SERIF_B, 16), "center") + px(14)
    y = para(d, left, y, width, P1, f12, "justify", indent=px(30)) + px(10)
    # строка со знаком сноски
    seg1 = "Изделие служит для защиты информации"
    d.text((left, y), seg1, font=f12, fill=0)
    x = left + d.textlength(seg1, font=f12)
    fs = font(SERIF, 7.5)
    d.text((x + 2, y - px(2)), "1", font=fs, fill=0)
    x += d.textlength("1", font=fs) + 4
    d.text((x, y), " в системах связи.", font=f12, fill=0)
    y += int(f12.size * 1.35) + px(10)
    y = para(d, left, y, width, "1. Общие сведения", fb, "left") + px(8)
    y = para(d, left, y, width, P_LINK, f12, "justify", indent=px(30)) + px(10)
    y = para(d, left, y, width, "2. Условия эксплуатации", fb, "left") + px(8)
    # последний абзац прижат к низу страницы и обрывается на полуслове
    yb = H - px(150)
    yy = para(d, left, yb - px(62), width, P_SPLIT_A, f12, "justify", indent=px(30))
    # сноска
    ny = H - px(82)
    d.line([(left, ny - px(6)), (left + px(120), ny - px(6))], fill=0, width=2)
    f9 = font(SERIF, 9)
    d.text((left, ny - px(1)), "1", font=font(SERIF, 6), fill=0)
    d.text((left + px(6), ny), NOTE, font=f9, fill=0)
    del yy
    pages.append(img)
    # ---- страница 2
    img, d, left, width = new_page(6)
    y = px(70)
    y = para(d, left, y, width, P_SPLIT_B, f12, "justify") + px(12)
    y = para(d, left, y, width, "3. Технические характеристики", fb, "left") + px(10)
    cols = [left + px(10), left + px(250), left + px(370)]
    for r, row in enumerate(TABLE):
        for c, txt in enumerate(row):
            d.text((cols[c], y), txt, font=fb if r == 0 else f12, fill=0)
        y += px(22)
    y += px(12)
    y = para(d, left, y, width, P3, f12, "justify", indent=px(30))
    pages.append(img)
    # ---- страница 3
    img, d, left, width = new_page(7)
    y = px(70)
    y = para(d, left, y, width, "4. Safety instructions", fb, "left") + px(8)
    y = para(d, left, y, width, P_EN, f12, "justify", indent=px(30)) + px(10)
    y = para(d, left, y, width, P3, f12, "justify", indent=px(30))
    pages.append(img)
    for i, p in enumerate(pages):
        p.save(os.path.join(out, "doc_%d.png" % (i + 1)), dpi=(300, 300))
    Image.init()
    rgb = [p.convert("RGB") for p in pages]
    rgb[0].save(os.path.join(out, "doc.pdf"), save_all=True, append_images=rgb[1:], resolution=300)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "samples_doc")
