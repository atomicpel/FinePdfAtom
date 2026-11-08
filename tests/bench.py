"""Проверка качества: точность по символам для тестовых образцов."""
import difflib
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finepdfatom import ocr, preprocess, sources  # noqa: E402
from tests.make_samples import TEXT  # noqa: E402

REF = " ".join(" ".join(TEXT).split())


def accuracy(text):
    t = " ".join(text.split())
    return difflib.SequenceMatcher(None, REF, t).ratio() * 100


def main(folder, raw=False):
    s = ocr.Settings()
    if raw:
        s.prep = preprocess.Options(auto_rotate=False, deskew=False, rescale=False, clean_background=False)
    rec = ocr.Recognizer(s)
    total = []
    for f in sorted(os.listdir(folder)):
        ref = sources.page_refs(os.path.join(folder, f))[0]
        t = time.time()
        r = rec.recognize_ref(ref)
        a = accuracy(r.text)
        total.append(a)
        print("%-22s %5.1f%%  conf %4.0f  %.1fs  [%s]" % (f, a, r.mean_conf, time.time() - t, r.notes))
    print("СРЕДНЕЕ: %.1f%%" % (sum(total) / len(total)))
    rec.close()


if __name__ == "__main__":
    main(sys.argv[1], raw="--raw" in sys.argv)
