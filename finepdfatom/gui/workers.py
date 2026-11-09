"""Фоновые задачи интерфейса."""
from __future__ import annotations

import traceback

from PyQt5.QtCore import QThread, pyqtSignal

from .. import export, ocr, sources


class OcrWorker(QThread):
    pageDone = pyqtSignal(int, object)      # индекс в списке задач, PageResult | Exception
    progress = pyqtSignal(int, int)
    failed = pyqtSignal(str)

    def __init__(self, recognizer: ocr.Recognizer, jobs, regions=None, parent=None):
        """jobs — список PageRef или (numpy, dpi); regions — список (x,y,w,h)|None."""
        super().__init__(parent)
        self.rec = recognizer
        self.jobs = jobs
        self.regions = regions or [None] * len(jobs)
        self._done = 0

    def run(self):
        try:
            def on_done(i, r):
                self._done += 1
                self.pageDone.emit(i, r)
                self.progress.emit(self._done, len(self.jobs))
            self.rec.map(self.jobs, on_done, region_for=lambda i: self.regions[i])
        except Exception:  # noqa: BLE001
            self.failed.emit(traceback.format_exc())

    def cancel(self):
        self.rec.cancel()


class LoadWorker(QThread):
    """Разбор файлов на страницы (PDF могут быть большими)."""
    loaded = pyqtSignal(list, list)          # refs, ошибки

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths

    def run(self):
        refs, errors = [], []
        for p in sources.expand_paths(self.paths):
            try:
                refs.extend(sources.page_refs(p))
            except Exception as e:  # noqa: BLE001
                errors.append("%s: %s" % (p, e))
        self.loaded.emit(refs, errors)


class ExportWorker(QThread):
    progress = pyqtSignal(int, int)
    finishedOk = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, pages, path, fmt, quality, parent=None, mode="formatted"):
        super().__init__(parent)
        self.pages, self.path, self.fmt, self.quality = pages, path, fmt, quality
        self.mode = mode

    def run(self):
        try:
            export.save(self.pages, self.path, self.fmt, self.quality,
                        progress=lambda a, b: self.progress.emit(a, b), mode=self.mode)
            self.finishedOk.emit(self.path)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class ScanWorker(QThread):
    scanned = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, device, dpi, mode, parent=None):
        super().__init__(parent)
        self.device, self.dpi, self.mode = device, dpi, mode

    def run(self):
        try:
            self.scanned.emit(sources.scan_to_file(self.device, self.dpi, self.mode))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class ListScannersWorker(QThread):
    found = pyqtSignal(list)
    failed = pyqtSignal(str)

    def run(self):
        try:
            self.found.emit(sources.list_scanners())
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
