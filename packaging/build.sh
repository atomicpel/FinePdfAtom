#!/usr/bin/env bash
# Сборка офлайн-пакета FinePdfAtom (.deb) для Astra Linux SE 1.6 / 1.7 / 1.8.
#
# Собирается на любом современном Linux x86_64 с интернетом (Ubuntu 22.04+, Debian 12, Astra 1.8):
#   sudo apt install git cmake make python3 python3-pip binutils dpkg-dev
#   ./packaging/build.sh
#
# Всё нужное попадает внутрь пакета: Python 3.11, Qt 5.15, Tesseract 5 (собирается здесь же
# компилятором zig под glibc 2.17), модели rus/eng/ukr (tessdata_best) и osd.
# Пакет не зависит от версий библиотек в репозиториях Astra и ставится без интернета.
set -euo pipefail

VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$(dirname "$0")/../finepdfatom/__init__.py")
TESS_VER=5.5.4
LEPT_VER=1.87.0
ZLIB_VER=v1.3.1
PNG_VER=v1.6.50
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20251014/cpython-3.11.14%2B20251014-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
ZIG_VER=0.13.0
LANGS_BEST="rus eng ukr"

REPO=$(cd "$(dirname "$0")/.." && pwd)
W=${BUILD_DIR:-$REPO/build}
SRC=$W/src
PFX=$W/prefix
STAGE=$W/stage
ROOT=$STAGE/opt/finepdfatom
DIST=$REPO/dist
mkdir -p "$SRC" "$PFX" "$DIST"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

clone() {  # clone <url> <tag> <dir>
    [ -d "$3" ] || git -c advice.detachedHead=false clone -q --depth 1 -b "$2" "$1" "$3"
}

# --------------------------------------------------------------------- zig
log "Компилятор zig $ZIG_VER (цель: glibc 2.17)"
python3 -m pip install -q --user "ziglang==$ZIG_VER" 2>/dev/null || python3 -m pip install -q --break-system-packages "ziglang==$ZIG_VER"
ZIG=$(python3 -c "import ziglang,os;print(os.path.join(os.path.dirname(ziglang.__file__),'zig'))")
mkdir -p "$W/bin"
for t in cc c++; do
    n=$([ "$t" = cc ] && echo zcc || echo zcxx)
    printf '#!/bin/sh\nexec "%s" %s -target x86_64-linux-gnu.2.17 "$@"\n' "$ZIG" "$t" > "$W/bin/$n"
done
printf '#!/bin/sh\nexec "%s" ar "$@"\n' "$ZIG" > "$W/bin/zar"
printf '#!/bin/sh\nexec "%s" ranlib "$@"\n' "$ZIG" > "$W/bin/zranlib"
chmod +x "$W/bin"/*
export CC=$W/bin/zcc CXX=$W/bin/zcxx
CM="-DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=$PFX -DCMAKE_PREFIX_PATH=$PFX -DCMAKE_AR=$W/bin/zar -DCMAKE_RANLIB=$W/bin/zranlib -DCMAKE_POSITION_INDEPENDENT_CODE=ON"

# ---------------------------------------------------------------- tesseract
if [ ! -f "$PFX/lib/libtesseract.so.$TESS_VER" ]; then
    log "Исходники zlib, libpng, Leptonica $LEPT_VER, Tesseract $TESS_VER"
    clone https://github.com/madler/zlib "$ZLIB_VER" "$SRC/zlib"
    clone https://github.com/pnggroup/libpng "$PNG_VER" "$SRC/libpng"
    clone https://github.com/DanBloomberg/leptonica "$LEPT_VER" "$SRC/leptonica"
    clone https://github.com/tesseract-ocr/tesseract "$TESS_VER" "$SRC/tesseract"
    # clang 18 (zig) требует evex512 для AVX-512
    sed -i 's/set(AVX512F_COMPILE_FLAGS "-mavx512f")/set(AVX512F_COMPILE_FLAGS "-mavx512f -mevex512")/' \
        "$SRC/tesseract/CMakeLists.txt"
    J=$(nproc)
    log "zlib"
    rm -rf "$W/b-zlib" && mkdir -p "$W/b-zlib" && cd "$W/b-zlib"
    cmake "$SRC/zlib" $CM -DZLIB_BUILD_SHARED=OFF -DZLIB_BUILD_TESTING=OFF -DBUILD_SHARED_LIBS=OFF >/dev/null
    make -j"$J" install >/dev/null && rm -f "$PFX"/lib/libz.so*
    log "libpng"
    rm -rf "$W/b-png" && mkdir -p "$W/b-png" && cd "$W/b-png"
    cmake "$SRC/libpng" $CM -DPNG_SHARED=OFF -DPNG_STATIC=ON -DPNG_TESTS=OFF -DPNG_TOOLS=OFF \
        -DZLIB_ROOT="$PFX" -DZLIB_USE_STATIC_LIBS=ON >/dev/null
    make -j"$J" install >/dev/null
    log "Leptonica"
    rm -rf "$W/b-lept" && mkdir -p "$W/b-lept" && cd "$W/b-lept"
    cmake "$SRC/leptonica" $CM -DBUILD_SHARED_LIBS=OFF -DSW_BUILD=OFF -DBUILD_PROG=OFF -DENABLE_ZLIB=ON \
        -DENABLE_PNG=ON -DENABLE_GIF=OFF -DENABLE_JPEG=OFF -DENABLE_TIFF=OFF -DENABLE_WEBP=OFF \
        -DENABLE_OPENJPEG=OFF -DZLIB_ROOT="$PFX" -DPNG_ROOT="$PFX" >/dev/null
    make -j"$J" install >/dev/null
    log "Tesseract (несколько минут)"
    rm -rf "$W/b-tess" && mkdir -p "$W/b-tess" && cd "$W/b-tess"
    cmake "$SRC/tesseract" $CM -DBUILD_SHARED_LIBS=ON -DSW_BUILD=OFF -DBUILD_TRAINING_TOOLS=OFF \
        -DDISABLE_ARCHIVE=ON -DDISABLE_CURL=ON -DGRAPHICS_DISABLED=ON -DOPENMP_BUILD=OFF -DENABLE_LTO=OFF \
        -DDISABLE_TIFF=ON -DBUILD_TESTS=OFF -DZLIB_ROOT="$PFX" -DPNG_ROOT="$PFX" -DZLIB_USE_STATIC_LIBS=ON >/dev/null
    make -j"$J" install >/dev/null
fi

# ------------------------------------------------------------------ модели
log "Модели: tessdata_best ($LANGS_BEST) и osd"
if [ ! -d "$SRC/tessdata_best" ]; then
    git clone -q --depth 1 --filter=blob:none --sparse https://github.com/tesseract-ocr/tessdata_best "$SRC/tessdata_best"
fi
(cd "$SRC/tessdata_best" && git sparse-checkout set --no-cone $(for l in $LANGS_BEST; do echo "/$l.traineddata"; done))
if [ ! -d "$SRC/tessdata" ]; then
    git clone -q --depth 1 --filter=blob:none --sparse https://github.com/tesseract-ocr/tessdata "$SRC/tessdata"
fi
(cd "$SRC/tessdata" && git sparse-checkout set --no-cone /osd.traineddata)

# ------------------------------------------------------------------ Python
log "Python 3.11 (python-build-standalone)"
[ -f "$SRC/python.tar.gz" ] || curl -fsSL -o "$SRC/python.tar.gz" "$PY_URL"
rm -rf "$STAGE" && mkdir -p "$ROOT"
tar -xzf "$SRC/python.tar.gz" -C "$ROOT"
PY=$ROOT/python/bin/python3

log "Колёса Python (manylinux2014 / glibc 2.17)"
WH=$W/wheels
mkdir -p "$WH"
python3 -m pip download -q --no-deps --only-binary=:all: --platform manylinux2014_x86_64 \
    --platform manylinux_2_17_x86_64 --platform manylinux_2_5_x86_64 --platform manylinux1_x86_64 \
    --python-version 3.11 --implementation cp -d "$WH" -r "$REPO/packaging/requirements.lock"
python3 -m pip wheel -q --no-deps -w "$WH" "odfpy==1.4.1"
"$PY" -m pip install -q --no-index --no-deps --no-compile "$WH"/*.whl

# --------------------------------------------------------------- сборка /opt
log "Сборка /opt/finepdfatom"
mkdir -p "$ROOT/lib" "$ROOT/tessdata" "$ROOT/app" "$ROOT/LICENSES"
cp -a "$PFX/lib/libtesseract.so.$TESS_VER" "$ROOT/lib/"
strip "$ROOT/lib/libtesseract.so.$TESS_VER"
ln -sf "libtesseract.so.$TESS_VER" "$ROOT/lib/libtesseract.so.5"
for l in $LANGS_BEST; do cp "$SRC/tessdata_best/$l.traineddata" "$ROOT/tessdata/"; done
cp "$SRC/tessdata/osd.traineddata" "$ROOT/tessdata/"
cp -r "$REPO/finepdfatom" "$ROOT/app/"
cp "$REPO/packaging/run.py" "$ROOT/app/run.py"
find "$ROOT/app" -name "__pycache__" -prune -exec rm -rf {} +
cp "$REPO/LICENSE" "$ROOT/LICENSES/FinePdfAtom.txt"
cp "$SRC/tesseract/LICENSE" "$ROOT/LICENSES/Tesseract-Apache-2.0.txt"
cp "$SRC/leptonica/leptonica-license.txt" "$ROOT/LICENSES/Leptonica.txt" 2>/dev/null || true
cp "$REPO/finepdfatom/resources/DejaVuSans-LICENSE.txt" "$ROOT/LICENSES/DejaVu.txt"
cat > "$ROOT/LICENSES/README.txt" <<'EOF'
Компоненты: Tesseract OCR (Apache-2.0), Leptonica (BSD-2), Python (PSF), Qt 5 / PyQt5 (GPL-3),
OpenCV (Apache-2.0), PyMuPDF / MuPDF (AGPL-3), NumPy (BSD), Pillow (MIT-CMU), python-docx (MIT),
odfpy (Apache-2.0/GPL), lxml (BSD), шрифт DejaVu (свободная лицензия Bitstream Vera).
Лицензии колёс Python лежат в python/lib/python3.11/site-packages/*.dist-info.
EOF

log "Удаление лишнего (Qt-модули, тесты Python)"
SP=$ROOT/python/lib/python3.11/site-packages
Q=$SP/PyQt5
keep_lib="Core|Gui|Widgets|DBus|XcbQpa|Svg"
for f in "$Q"/Qt5/lib/libQt5*.so.5; do
    b=$(basename "$f"); m=${b#libQt5}; m=${m%.so.5}
    echo "$m" | grep -qxE "$keep_lib" || rm -f "$f"
done
for d in "$Q"/Qt5/plugins/*; do
    case $(basename "$d") in platforms|imageformats|iconengines|xcbglintegrations|platforminputcontexts) ;; *) rm -rf "$d";; esac
done
rm -f "$Q"/Qt5/plugins/platforms/libqwayland* "$Q"/Qt5/plugins/platforms/libqeglfs* \
      "$Q"/Qt5/plugins/platforms/libqlinuxfb* "$Q"/Qt5/plugins/platforms/libqvnc* \
      "$Q"/Qt5/plugins/platforms/libqwebgl* "$Q"/Qt5/plugins/imageformats/libqpdf* \
      "$Q"/Qt5/plugins/platformthemes/libqxdgdesktopportal*
rm -rf "$Q"/Qt5/qml "$Q"/Qt5/qsci "$Q"/bindings "$Q"/uic "$Q"/pylupdate* "$Q"/pyrcc*
find "$Q"/Qt5/translations -type f ! -name "qtbase_ru.qm" -delete
for f in "$Q"/Qt*.abi3.so "$Q"/Qt*.pyi; do
    b=$(basename "$f"); m=${b%%.*}
    echo "$m" | grep -qxE "Qt|QtCore|QtGui|QtWidgets|QtSvg|QtDBus" || rm -f "$f"
done
rm -f "$Q"/_QOpenGLFunctions*
rm -rf "$ROOT"/python/lib/python3.11/{test,idlelib,tkinter,turtledemo,ensurepip,lib2to3,unittest/test}
rm -rf "$ROOT"/python/lib/tcl* "$ROOT"/python/lib/tk* "$ROOT"/python/lib/itcl* "$ROOT"/python/lib/libtcl* "$ROOT"/python/lib/libtk*
rm -f "$ROOT"/python/lib/python3.11/lib-dynload/_tkinter*
rm -rf "$SP"/cv2/data "$SP"/numpy/*/tests "$SP"/numpy/tests "$SP"/pymupdf/mupdf-devel 2>/dev/null || true
find "$ROOT/python" -name "*.a" -delete
find "$ROOT/python" -name "__pycache__" -prune -exec rm -rf {} +

log "Компиляция байт-кода"
"$PY" -m compileall -q -j0 "$ROOT/python/lib/python3.11" "$ROOT/app" >/dev/null || true

log "Проверка совместимости с glibc 2.17"
python3 "$REPO/packaging/glibcheck.py" "$ROOT" 2.17

log "Проверка работы"
FINEPDFATOM_HOME=$ROOT "$PY" -I "$ROOT/app/run.py" --version
QT_QPA_PLATFORM=offscreen FINEPDFATOM_HOME=$ROOT HOME=$W/home "$PY" -I -c "
import sys; sys.path.insert(0, '$ROOT/app')
from PyQt5.QtWidgets import QApplication; from PyQt5 import QtSvg; app = QApplication([])
import fitz, cv2, docx, odf; print('Qt OK')"

# -------------------------------------------------------------------- .deb
log "Пакет .deb"
DEB=$STAGE
install -Dm755 "$REPO/packaging/launcher.sh" "$DEB/usr/bin/finepdfatom"
install -Dm644 "$REPO/packaging/finepdfatom.desktop" "$DEB/usr/share/applications/finepdfatom.desktop"
install -Dm644 "$REPO/packaging/finepdfatom-screen.desktop" "$DEB/usr/share/applications/finepdfatom-screen.desktop"
install -Dm644 "$REPO/packaging/finepdfatom.svg" "$DEB/usr/share/icons/hicolor/scalable/apps/finepdfatom.svg"
for s in 32 48 64 128 256; do
    QT_QPA_PLATFORM=offscreen "$PY" -I -c "
from PyQt5.QtGui import QGuiApplication, QImage, QPainter; from PyQt5.QtCore import Qt
from PyQt5.QtSvg import QSvgRenderer; a = QGuiApplication([]); i = QImage($s, $s, QImage.Format_ARGB32); i.fill(Qt.transparent)
p = QPainter(i); QSvgRenderer('$REPO/packaging/finepdfatom.svg').render(p); p.end(); i.save('$W/icon$s.png')"
    install -Dm644 "$W/icon$s.png" "$DEB/usr/share/icons/hicolor/${s}x${s}/apps/finepdfatom.png"
done
install -Dm644 "$REPO/README.md" "$DEB/usr/share/doc/finepdfatom/README.md"
SIZE=$(du -sk --exclude=DEBIAN "$DEB" | cut -f1)
mkdir -p "$DEB/DEBIAN"
cat > "$DEB/DEBIAN/control" <<EOF
Package: finepdfatom
Version: $VERSION
Section: graphics
Priority: optional
Architecture: amd64
Maintainer: Pavel Dudin <atomicpel@yandex.ru>
Installed-Size: $SIZE
Depends: libc6 (>= 2.17)
Recommends: sane-utils
Homepage: https://github.com/atomicpel/FinePdfAtom
Description: Offline OCR for Astra Linux SE 1.6+ (Tesseract $TESS_VER)
 Распознавание текста без интернета: сканы, фото, PDF, область экрана, буфер обмена.
 Автоповорот, выравнивание, удаление теней, PDF с поиском по тексту, DOCX, ODT, TXT.
 Самодостаточный пакет: собственные Python, Qt, Tesseract и модели языков.
EOF
cat > "$DEB/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -f /usr/share/icons/hicolor || true
exit 0
EOF
cat > "$DEB/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "purge" ] || [ "$1" = "remove" ]; then
    rm -rf /opt/finepdfatom
fi
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
exit 0
EOF
chmod 755 "$DEB/DEBIAN/postinst" "$DEB/DEBIAN/postrm"
OUT=$DIST/finepdfatom_${VERSION}_amd64.deb
# xz — понимает dpkg 1.18 в Astra 1.6 (zstd не понимает)
dpkg-deb --root-owner-group -Zxz -z9 --build "$DEB" "$OUT" >/dev/null 2>&1 || \
    fakeroot dpkg-deb -Zxz -z9 --build "$DEB" "$OUT"
(cd "$DIST" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256")
log "Готово: $OUT ($(du -h "$OUT" | cut -f1))"
