"""Векторные иконки интерфейса (собственные, линейный стиль 24×24)."""
from __future__ import annotations

from PyQt5.QtCore import QByteArray, QRectF, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer

_HEAD = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" '
         'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">')

PATHS = {
    "open": '<path d="M3 7.5V18a1.5 1.5 0 0 0 1.5 1.5h15A1.5 1.5 0 0 0 21 18V9.5A1.5 1.5 0 0 0 19.5 8H12l-2-2.5H4.5A1.5 1.5 0 0 0 3 7z"/>',
    "scan": '<rect x="3" y="11" width="18" height="7" rx="1.5"/><path d="M6 11V5.5h9l3 3V11"/><path d="M6.5 14.5h4"/><circle cx="17" cy="14.5" r=".6" fill="{c}"/>',
    "screen": '<path d="M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8M20 16v2.5a1.5 1.5 0 0 1-1.5 1.5H16M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16"/><path d="M9 10h6M9 13h6M9 16h3.5"/>',
    "clipboard": '<rect x="5" y="4.5" width="14" height="16" rx="1.8"/><path d="M9 4.5V3.5h6v1M9 4.5h6v2H9z"/><path d="M8.5 11h7M8.5 14h7M8.5 17h4"/>',
    "recognize": '<path d="M4 7V5.5A1.5 1.5 0 0 1 5.5 4H7M17 4h1.5A1.5 1.5 0 0 1 20 5.5V7M20 17v1.5a1.5 1.5 0 0 1-1.5 1.5H17M7 20H5.5A1.5 1.5 0 0 1 4 18.5V17"/><path d="M8 16l4-9 4 9M9.5 13h5"/>',
    "area": '<rect x="4" y="4" width="16" height="16" rx="1.5" stroke-dasharray="3 2.6"/><path d="M9 15l3-7 3 7M10.2 12.8h3.6"/>',
    "save": '<path d="M5.5 4h10.2L20 8.3v10.2a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5v-13A1.5 1.5 0 0 1 5.5 4z"/><path d="M8 4v4.5h7V4M7.5 20v-6h9v6"/>',
    "copy": '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="1.6"/><path d="M15.5 8.5V5.5A1.5 1.5 0 0 0 14 4H5.5A1.5 1.5 0 0 0 4 5.5V14a1.5 1.5 0 0 0 1.5 1.5h3"/>',
    "settings": '<path d="M4 7h9M17 7h3M4 17h3M11 17h9M4 12h5M13 12h7"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/><circle cx="11" cy="12" r="2"/>',
    "zoom_in": '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5M8 10.5h5M10.5 8v5"/>',
    "zoom_out": '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5M8 10.5h5"/>',
    "fit": '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/><rect x="8" y="8" width="8" height="8" rx="1"/>',
    "boxes": '<rect x="3.5" y="6" width="7" height="4" rx=".8"/><rect x="12.5" y="6" width="8" height="4" rx=".8"/><rect x="3.5" y="13.5" width="10" height="4" rx=".8"/><rect x="15.5" y="13.5" width="5" height="4" rx=".8"/>',
    "delete": '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5a1.5 1.5 0 0 0 1.5 1.4h6a1.5 1.5 0 0 0 1.5-1.4l1-12.5M10.2 10.5v6.5M13.8 10.5v6.5"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "rotate_left": '<path d="M4.5 9.5A8 8 0 1 1 6 16.5"/><path d="M4 4.5v5h5"/>',
    "rotate_right": '<path d="M19.5 9.5A8 8 0 1 0 18 16.5"/><path d="M20 4.5v5h-5"/>',
    "lang": '<path d="M3.5 18l4-11 4 11M5 14h5"/><path d="M14 11.5c.9-1 2-1.5 3.2-1.5 1.9 0 3.3 1.2 3.3 3.2V18M20.5 14.5h-2.8c-1.6 0-2.7.7-2.7 1.9 0 1 .8 1.8 2 1.8 1.6 0 3.5-1.1 3.5-3.7"/>',
    "batch": '<rect x="7" y="7" width="13" height="13" rx="1.5"/><path d="M4 16.5V5.5A1.5 1.5 0 0 1 5.5 4h11"/><path d="M10.5 12h6M10.5 15.5h4"/>',
    "image": '<rect x="3.5" y="4.5" width="17" height="15" rx="1.6"/><circle cx="9" cy="9.5" r="1.6"/><path d="M20.5 15.5l-4.5-4.5-8.5 8.5"/>',
    "text": '<path d="M5 6h14M5 10.5h14M5 15h14M5 19.5h9"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "warn": '<path d="M12 4l9 16H3z"/><path d="M12 10v4.5M12 17.2v.2"/>',
    "info": '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.8v.2"/>',
    "add": '<path d="M12 5v14M5 12h14"/>',
    "up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "down": '<path d="M12 5v14M6 13l6 6 6-6"/>',
    "theme": '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/>',
}

_cache = {}


def svg(name: str, color: str) -> bytes:
    body = PATHS[name].replace("{c}", color)
    return (_HEAD.format(c=color) + body + "</svg>").encode()


def pixmap(name: str, color: str, size: int = 24, ratio: float = 2.0) -> QPixmap:
    px = QPixmap(int(size * ratio), int(size * ratio))
    px.fill(Qt.transparent)
    r = QSvgRenderer(QByteArray(svg(name, color)))
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, px.width(), px.height()))
    p.end()
    px.setDevicePixelRatio(ratio)
    return px


def icon(name: str, color: str = "#4b5565", disabled: str | None = None) -> QIcon:
    key = (name, color, disabled)
    if key in _cache:
        return _cache[key]
    ic = QIcon()
    for s in (16, 20, 24, 32):
        ic.addPixmap(pixmap(name, color, s), QIcon.Normal)
        ic.addPixmap(pixmap(name, disabled or _fade(color), s), QIcon.Disabled)
    _cache[key] = ic
    return ic


def _fade(color: str) -> str:
    c = QColor(color)
    g = QColor("#9aa1ad")
    return QColor((c.red() + g.red() * 2) // 3, (c.green() + g.green() * 2) // 3,
                  (c.blue() + g.blue() * 2) // 3).name()


APP_ICON_SVG = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#3a7bff"/><stop offset="1" stop-color="#6d3dff"/></linearGradient></defs>
<rect x="16" y="16" width="224" height="224" rx="52" fill="url(#g)"/>
<path d="M78 58h70l34 34v100a12 12 0 0 1-12 12H78a12 12 0 0 1-12-12V70a12 12 0 0 1 12-12z" fill="#fff" opacity=".96"/>
<path d="M148 58v26a8 8 0 0 0 8 8h26" fill="#dfe6ff"/>
<path d="M88 116h70M88 138h70M88 160h44" stroke="#9fb0d9" stroke-width="9" stroke-linecap="round"/>
<g fill="none" stroke="#ffd34d" stroke-width="7"><ellipse cx="170" cy="168" rx="44" ry="17"/>
<ellipse cx="170" cy="168" rx="44" ry="17" transform="rotate(60 170 168)"/>
<ellipse cx="170" cy="168" rx="44" ry="17" transform="rotate(-60 170 168)"/></g>
<circle cx="170" cy="168" r="10" fill="#ffd34d"/></svg>'''


def app_icon() -> QIcon:
    ic = QIcon()
    r = QSvgRenderer(QByteArray(APP_ICON_SVG))
    for s in (16, 24, 32, 48, 64, 128, 256):
        px = QPixmap(s, s)
        px.fill(Qt.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing)
        r.render(p)
        p.end()
        ic.addPixmap(px)
    return ic
