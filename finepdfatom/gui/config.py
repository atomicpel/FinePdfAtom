"""Сохранение настроек между запусками (~/.config/FinePdfAtom/FinePdfAtom.conf)."""
from __future__ import annotations

from PyQt5.QtCore import QSettings

from .. import ocr, preprocess


class Config:
    def __init__(self):
        self.s = QSettings("FinePdfAtom", "FinePdfAtom")

    def _b(self, key, default):
        v = self.s.value(key, default)
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes")
        return bool(v)

    def get(self, key, default=None):
        return self.s.value(key, default)

    def set(self, key, value):
        self.s.setValue(key, value)

    def ocr_settings(self) -> ocr.Settings:
        prep = preprocess.Options(
            mode=self.get("prep/mode", preprocess.MODE_AUTO),
            auto_rotate=self._b("prep/auto_rotate", True),
            deskew=self._b("prep/deskew", True),
            rescale=self._b("prep/rescale", True),
            clean_background=self._b("prep/clean_background", True),
            denoise=self._b("prep/denoise", False),
        )
        return ocr.Settings(
            langs=self.get("ocr/langs", "auto"),
            layout=self.get("ocr/layout", "auto"),
            prep=prep,
            use_pdf_text=self._b("ocr/use_pdf_text", True),
            keep_lines=self._b("ocr/keep_lines", True),
            dehyphenate=self._b("ocr/dehyphenate", True),
            threads=int(self.get("ocr/threads", 0) or 0),
            spellcheck=self._b("ocr/spellcheck", True),
        )

    def save_ocr_settings(self, st: ocr.Settings):
        self.set("ocr/langs", st.langs)
        self.set("ocr/layout", st.layout)
        self.set("ocr/use_pdf_text", st.use_pdf_text)
        self.set("ocr/keep_lines", st.keep_lines)
        self.set("ocr/dehyphenate", st.dehyphenate)
        self.set("ocr/threads", st.threads)
        self.set("ocr/spellcheck", st.spellcheck)
        p = st.prep
        self.set("prep/mode", p.mode)
        self.set("prep/auto_rotate", p.auto_rotate)
        self.set("prep/deskew", p.deskew)
        self.set("prep/rescale", p.rescale)
        self.set("prep/clean_background", p.clean_background)
        self.set("prep/denoise", p.denoise)
        self.s.sync()

    @property
    def theme(self):
        return self.get("ui/theme", "light")

    @theme.setter
    def theme(self, v):
        self.set("ui/theme", v)

    @property
    def pdf_quality(self):
        return self.get("export/pdf_quality", "color")

    @pdf_quality.setter
    def pdf_quality(self, v):
        self.set("export/pdf_quality", v)
