"""Проверка: какие максимальные версии GLIBC/GLIBCXX требуют бинарники пакета.

Использование: python3 glibcheck.py <папка> [макс_glibc]
Код возврата 1, если что-то требует glibc новее заданной (по умолчанию 2.17).
"""
import collections
import os
import re
import subprocess
import sys


def scan(root):
    mx = collections.defaultdict(lambda: (0,))
    who = {}
    for d, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            if os.path.islink(p) or not (".so" in f or os.access(p, os.X_OK)):
                continue
            with open(p, "rb") as fh:
                if fh.read(4) != b"\x7fELF":
                    continue
            out = subprocess.run(["objdump", "-T", p], capture_output=True, text=True).stdout
            for line in out.splitlines():
                if "*UND*" not in line:
                    continue
                for t, v in re.findall(r"\((GLIBCXX|GLIBC|CXXABI)_([0-9.]+)\)", line):
                    k = tuple(int(x) for x in v.split("."))
                    if k > mx[t]:
                        mx[t], who[t] = k, p
    return mx, who


if __name__ == "__main__":
    root = sys.argv[1]
    limit = tuple(int(x) for x in (sys.argv[2] if len(sys.argv) > 2 else "2.17").split("."))
    mx, who = scan(root)
    for t in sorted(mx):
        print("%-8s %-8s %s" % (t, ".".join(map(str, mx[t])), os.path.relpath(who[t], root)))
    sys.exit(1 if mx["GLIBC"] > limit else 0)
