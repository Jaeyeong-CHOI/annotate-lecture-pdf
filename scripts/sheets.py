# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow"]
# ///
"""Tile preview pages into contact sheets for a quick visual check.

usage: sheets.py <dir with pNN.png> <outdir> <pages, e.g. 1-12 or 3,7,9-11> [cols=2]
Each sheet holds cols x 2 pages. Needs only Pillow.
"""
import os
import sys

from PIL import Image


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    src, out, spec = sys.argv[1:4]
    cols = int(sys.argv[4]) if len(sys.argv) > 4 else 2
    pages = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        pages += list(range(int(a), int(b or a) + 1))
    os.makedirs(out, exist_ok=True)
    per = cols * 2
    for k in range(0, len(pages), per):
        grp = pages[k:k + per]
        ims = [Image.open(f"{src}/p{p:02d}.png").convert("RGB") for p in grp]
        w = 1000
        h = round(w * ims[0].height / ims[0].width)
        sheet = Image.new("RGB", (w * cols, h * 2), "white")
        for i, im in enumerate(ims):
            sheet.paste(im.resize((w, h), Image.LANCZOS), ((i % cols) * w, (i // cols) * h))
        name = f"{out}/s{grp[0]:02d}-{grp[-1]:02d}.png"
        sheet.save(name)
        print(name)


if __name__ == "__main__":
    main()
