#!/usr/bin/env bash
# ── Generate all icon variants from a single SVG source ──
# Requires: librsvg (`brew install librsvg`) or ImageMagick (`magick`/`convert`)
#
# Produces:
#   src-tauri/icons/icon.{32x32,128x128,128,512x512,512}.png   (Tauri reqs)
#   src-tauri/icons/icon.icns   (macOS bundle)
#   src-tauri/icons/icon.ico    (Windows bundle)

set -euo pipefail
cd "$(dirname "$0")"
SRC="icon.svg"
OUTDIR="."

if ! command -v rsvg-convert >/dev/null 2>&1 && ! command -v magick >/dev/null 2>&1 && ! command -v convert >/dev/null 2>&1; then
  echo "❌ Need rsvg-convert (brew install librsvg) OR ImageMagick to render icons."
  exit 1
fi

# Convert any size $1 -> $2 output filename
render() {
  local size="$1" out="$2"
  if command -v rsvg-convert >/dev/null 2>&1; then
    rsvg-convert -w "$size" -h "$size" "$SRC" -o "$out"
  elif command -v magick >/dev/null 2>&1; then
    magick -background none -density 1024 "$SRC" -resize "${size}x${size}" "$out"
  else
    convert -background none -density 1024 "$SRC" -resize "${size}x${size}" "$out"
  fi
}

render 32   "$OUTDIR/icon_32x32.png"
render 128  "$OUTDIR/icon_128x128.png"
render 128  "$OUTDIR/icon.png"
render 256  "$OUTDIR/icon_256x256.png"
render 512  "$OUTDIR/icon_512x512.png"
cp -f "$OUTDIR/icon_512x512.png" "$OUTDIR/icon_512.png"

# macOS .icns (if iconutil exists)
if command -v iconutil >/dev/null 2>&1; then
  SETDIR="$(mktemp -d)/icon.iconset"
  mkdir -p "$SETDIR"
  render 16    "$SETDIR/icon_16x16.png"
  render 32    "$SETDIR/icon_16x16@2x.png"
  render 32    "$SETDIR/icon_32x32.png"
  render 64    "$SETDIR/icon_32x32@2x.png"
  render 128   "$SETDIR/icon_128x128.png"
  render 256   "$SETDIR/icon_128x128@2x.png"
  render 256   "$SETDIR/icon_256x256.png"
  render 512   "$SETDIR/icon_256x256@2x.png"
  render 512   "$SETDIR/icon_512x512.png"
  render 1024  "$SETDIR/icon_512x512@2x.png"
  iconutil -c icns "$SETDIR" -o "$OUTDIR/icon.icns"
  rm -rf "$(dirname "$SETDIR")"
  echo "✅ macOS icon.icns ready"
fi

# Windows .ico (if magick/convert exists)
if command -v magick >/dev/null 2>&1; then
  magick "$OUTDIR/icon_16x16.png" "$OUTDIR/icon_32x32.png" \
         "$OUTDIR/icon_128x128.png" "$OUTDIR/icon_256x256.png" \
         "$OUTDIR/icon.ico"
  echo "✅ Windows icon.ico ready"
elif command -v convert >/dev/null 2>&1; then
  convert "$OUTDIR/icon_16x16.png" "$OUTDIR/icon_32x32.png" \
          "$OUTDIR/icon_128x128.png" "$OUTDIR/icon_256x256.png" \
          "$OUTDIR/icon.ico"
  echo "✅ Windows icon.ico ready"
fi

echo "🎉 All icon variants generated under $OUTDIR"
