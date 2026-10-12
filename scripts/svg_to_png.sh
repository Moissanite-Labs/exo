#!/bin/sh
# svg_to_png.sh <in.svg> <out.png>: rsvg-convert if present, else headless Chrome, else qlmanage.
set -e
in="$1"; out="$2"
if command -v rsvg-convert >/dev/null 2>&1; then rsvg-convert -w 1600 "$in" -o "$out"; exit 0; fi
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if [ -x "$CHROME" ]; then
  w=$(sed -n 's/.*viewBox="0 0 \([0-9.]*\) \([0-9.]*\)".*/\1/p' "$in" | head -1)
  h=$(sed -n 's/.*viewBox="0 0 \([0-9.]*\) \([0-9.]*\)".*/\2/p' "$in" | head -1)
  "$CHROME" --headless=new --use-mock-keychain --password-store=basic --no-first-run --no-default-browser-check --disable-gpu --hide-scrollbars --force-device-scale-factor=1.4 \
    --window-size="${w%.*},${h%.*}" --screenshot="$out" "file://$(cd "$(dirname "$in")" && pwd)/$(basename "$in")" >/dev/null 2>&1
  exit 0
fi
qlmanage -t -s 1600 -o "$(dirname "$out")" "$in" >/dev/null 2>&1
mv "$in.png" "$out"
