#!/usr/bin/env bash
# End-to-end check on the bundled sample: build the deck, dump the layout, render, verify.
# Also renders scaled copies (Beamer- and Keynote-sized pages) to exercise page normalisation.
# usage: tests/smoke.sh        (needs: pip install -r requirements.txt, or run inside `uv run --with-requirements requirements.txt`)
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
py="${PYTHON:-python3}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"

"$py" "$root/examples/make_sample.py" sample.pdf
mkdir -p spec && cp -R "$root/examples/spec/sample" spec/sample

run() {  # run <pdf>
  local name; name="$(basename "$1" .pdf)"
  "$py" "$root/scripts/prep.py" "$1"
  test -s "layout/$name.txt"
  "$py" "$root/scripts/render.py" "$1" --strict
  "$py" "$root/scripts/check.py" "$1" "out/${name}_주석.pdf"
}

run sample.pdf
"$py" "$root/scripts/sheets.py" prev/sample probe 1-3 2
"$py" "$root/scripts/render.py" sample.pdf --pages 2-3 --noprev
test -s out/_partial_sample_2-3.pdf

# the same deck on other page sizes must lay out identically (same spec, same ids)
"$py" - <<'PY'
import pymupdf as fitz
src = fitz.open("sample.pdf")
for name, (w, h) in {"beamer": (362.835, 272.126), "keynote": (1920, 1440)}.items():
    d = fitz.open()
    for i in range(len(src)):
        p = d.new_page(width=w, height=h)
        p.show_pdf_page(p.rect, src, i)
    d.save(f"{name}.pdf")
PY
for n in beamer keynote; do
  cp -R spec/sample "spec/$n"
  run "$n.pdf"
  diff <(tail -n +2 layout/sample.txt) <(tail -n +2 "layout/$n.txt") > /dev/null || { echo "layout of $n differs from the 720x540 original"; exit 1; }
done
echo "smoke test passed"
