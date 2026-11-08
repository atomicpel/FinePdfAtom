"""Оформление: светлая и тёмная темы на базе Fusion."""
from __future__ import annotations

from PyQt5.QtGui import QColor, QPalette

THEMES = {
    "light": dict(
        window="#f3f5f9", panel="#ffffff", panel2="#f7f8fb", border="#dfe3ea", text="#1d2330",
        muted="#6b7385", accent="#3a6df0", accent_hover="#2f5fd8", accent_text="#ffffff",
        selection="#dbe6ff", canvas="#e9ecf2", icon="#465062", warn="#f2a516", lowconf="#ffe9b0",
        lowconf_text="#6a4a00", ok="#1f9d61", box="#3a6df0", input="#ffffff"),
    "dark": dict(
        window="#16191f", panel="#1e222a", panel2="#232833", border="#303644", text="#e6e9ef",
        muted="#9099ab", accent="#5b8cff", accent_hover="#7aa2ff", accent_text="#0b1020",
        selection="#2c3d66", canvas="#111318", icon="#c3c9d5", warn="#f5b83d", lowconf="#5a4413",
        lowconf_text="#ffe4a3", ok="#37c483", box="#7aa2ff", input="#191d24"),
}


def colors(name: str) -> dict:
    return THEMES.get(name, THEMES["light"])


def palette(name: str) -> QPalette:
    c = colors(name)
    p = QPalette()
    p.setColor(QPalette.Window, QColor(c["window"]))
    p.setColor(QPalette.WindowText, QColor(c["text"]))
    p.setColor(QPalette.Base, QColor(c["input"]))
    p.setColor(QPalette.AlternateBase, QColor(c["panel2"]))
    p.setColor(QPalette.Text, QColor(c["text"]))
    p.setColor(QPalette.Button, QColor(c["panel"]))
    p.setColor(QPalette.ButtonText, QColor(c["text"]))
    p.setColor(QPalette.Highlight, QColor(c["accent"]))
    p.setColor(QPalette.HighlightedText, QColor(c["accent_text"]))
    p.setColor(QPalette.ToolTipBase, QColor(c["panel"]))
    p.setColor(QPalette.ToolTipText, QColor(c["text"]))
    p.setColor(QPalette.PlaceholderText, QColor(c["muted"]))
    p.setColor(QPalette.Link, QColor(c["accent"]))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(c["muted"]))
    return p


def _chevron(color: str) -> str:
    """Стрелка для выпадающих списков (QSS принимает только файлы)."""
    import os
    d = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "finepdfatom")
    p = os.path.join(d, "chevron-%s.svg" % color.strip("#"))
    if not os.path.exists(p):
        try:
            os.makedirs(d, exist_ok=True)
            with open(p, "w") as f:
                f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 12"><path d="M3 4.5l3 3 3-3" '
                        'fill="none" stroke="%s" stroke-width="1.6" stroke-linecap="round" '
                        'stroke-linejoin="round"/></svg>' % color)
        except OSError:
            return ""
    return p


def stylesheet(name: str) -> str:
    c = dict(colors(name))
    c["chevron"] = _chevron(c["muted"]).replace("\\", "/")
    c["chevron_w"] = _chevron("#ffffff")
    return """
QMainWindow, QDialog {{ background: {window}; }}
QWidget {{ font-size: 10pt; }}
QToolTip {{ background: {panel}; color: {text}; border: 1px solid {border}; padding: 6px 8px; border-radius: 6px; }}
#TopBar {{ background: {panel}; border-bottom: 1px solid {border}; }}
#TopBar QToolButton {{ border: none; border-radius: 8px; padding: 6px 10px; color: {text}; }}
#TopBar QToolButton:hover {{ background: {panel2}; }}
#TopBar QToolButton:pressed {{ background: {selection}; }}
#TopBar QToolButton::menu-indicator {{ image: none; width: 0; }}
#TopBar #Primary {{ background: {accent}; color: {accent_text}; font-weight: 600; padding: 7px 16px; }}
#TopBar #Primary:hover {{ background: {accent_hover}; }}
#TopBar #Primary:disabled {{ background: {border}; color: {muted}; }}
#Brand {{ font-size: 13pt; font-weight: 700; color: {text}; padding: 0 10px 0 4px; }}
#BrandSub {{ color: {muted}; font-size: 8.5pt; }}
#Sep {{ background: {border}; min-width: 1px; max-width: 1px; margin: 8px 6px; }}
#Card {{ background: {panel}; border: 1px solid {border}; border-radius: 12px; }}
#CardTitle {{ font-weight: 600; color: {text}; font-size: 10.5pt; }}
#Muted {{ color: {muted}; }}
#Chip {{ background: {panel2}; border: 1px solid {border}; border-radius: 10px; padding: 0px; color: {muted}; font-size: 9pt; }}
#ChipOk {{ background: {panel2}; border: 1px solid {ok}; border-radius: 10px; padding: 0px; color: {ok}; font-size: 9pt; }}
#ChipWarn {{ background: {panel2}; border: 1px solid {warn}; border-radius: 10px; padding: 0px; color: {warn}; font-size: 9pt; }}
QListWidget#Pages {{ background: transparent; border: none; outline: none; }}
QListWidget#Pages::item {{ border-radius: 10px; padding: 6px; margin: 3px; color: {text}; }}
QListWidget#Pages::item:hover {{ background: {panel2}; }}
QListWidget#Pages::item:selected {{ background: {selection}; color: {text}; }}
QGraphicsView {{ background: {canvas}; border: none; border-radius: 10px; }}
QPlainTextEdit, QTextEdit {{ background: {input}; border: none; selection-background-color: {selection};
    selection-color: {text}; padding: 8px; font-size: 11pt; }}
QLineEdit, QSpinBox, QComboBox {{ background: {input}; border: 1px solid {border}; border-radius: 7px; padding: 5px 8px; color: {text}; }}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover {{ border-color: {accent}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url({chevron}); width: 12px; height: 12px; }}
#TopBar #Primary::menu-button {{ border: none; border-left: 1px solid rgba(255,255,255,0.35); width: 20px;
    border-top-right-radius: 8px; border-bottom-right-radius: 8px; }}
#TopBar #Primary::menu-arrow {{ image: url({chevron_w}); width: 12px; height: 12px; }}
#TopBar QToolButton::menu-button {{ border: none; width: 16px; }}
#TopBar QToolButton::menu-arrow {{ image: url({chevron}); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{ background: {panel}; border: 1px solid {border}; selection-background-color: {selection}; selection-color: {text}; }}
QPushButton {{ background: {panel}; border: 1px solid {border}; border-radius: 8px; padding: 6px 14px; color: {text}; }}
QPushButton:hover {{ border-color: {accent}; }}
QPushButton:default, QPushButton#Primary {{ background: {accent}; color: {accent_text}; border-color: {accent}; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {accent_hover}; }}
QCheckBox {{ spacing: 8px; }}
QGroupBox {{ border: 1px solid {border}; border-radius: 10px; margin-top: 14px; padding: 10px 10px 8px 10px; background: {panel}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {muted}; font-weight: 600; }}
QProgressBar {{ background: {panel2}; border: none; border-radius: 4px; height: 8px; max-height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 4px; }}
QStatusBar {{ background: {panel}; border-top: 1px solid {border}; color: {muted}; }}
QStatusBar::item {{ border: none; }}
QMenu {{ background: {panel}; border: 1px solid {border}; border-radius: 8px; padding: 5px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 6px; color: {text}; }}
QMenu::item:selected {{ background: {selection}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 8px; }}
QSplitter::handle {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {border}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ padding: 6px 14px; border: none; color: {muted}; }}
QTabBar::tab:selected {{ color: {text}; border-bottom: 2px solid {accent}; }}
#ViewToggle QToolButton {{ border: 1px solid {border}; padding: 4px 10px; background: {panel}; color: {muted}; }}
#ViewToggle QToolButton:checked {{ background: {selection}; color: {text}; border-color: {accent}; }}
#SmallTool {{ border: none; border-radius: 6px; padding: 4px; }}
#SmallTool:hover {{ background: {panel2}; }}
#Empty {{ color: {muted}; font-size: 11pt; }}
#EmptyTitle {{ color: {text}; font-size: 16pt; font-weight: 700; }}
""".format(**c)
