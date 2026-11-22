# /// script
# requires-python = ">=3.10"
# dependencies = ["pymupdf>=1.24", "numpy", "fonttools"]
# ///
"""Dump the layout of each page (paragraph ids, figure regions) and page images for whoever writes the spec.

usage: prep.py <deck.pdf> [more.pdf ...]
out:   layout/<name>.txt, layout/<name>.json, img/<name>/pNN.png   (under the current directory)
"""
import json
import os
import sys

import pymupdf as fitz
from common import Deck, PX_PER_PT, TITLE_SIZE, bands, name_of, paragraphs, regions
from render import Mask


def main():
    pdfs = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not pdfs:
        sys.exit(__doc__)
    for path in pdfs:
        lec = name_of(path)
        deck = Deck(path)
        os.makedirs(f"img/{lec}", exist_ok=True)
        os.makedirs("layout", exist_ok=True)
        out, js = [], {}
        for i in range(len(deck)):
            page, k = deck.layout_page(i)
            top, bot = bands(page)
            ps = paragraphs(page, top, bot)
            rs = regions(page, top)
            mk = Mask(page, top, bot)
            for p in ps:
                p["hidden"] = mk.ink_frac(p["bbox"]) < 0.004
            js[str(i + 1)] = dict(paras=ps, regions=rs, size=[page.rect.width, page.rect.height], scale=k)
            out.append(f"\n===== page {i + 1} =====")
            for p in ps:
                b = [round(v) for v in p["bbox"]]
                kind = "code" if p["mono"] else ("title" if p["size"] >= TITLE_SIZE else "")
                if p["hidden"]:
                    kind += " HIDDEN(invisible in original; do not annotate)"
                txt = p["text"].replace("\n", " ⏎ ")
                out.append(f"{p['id']} [{b[0]},{b[1]},{b[2]},{b[3]}] sz{p['size']:.0f} {kind} | {txt}")
            for r in rs:
                b = [round(v) for v in r["bbox"]]
                out.append(f"{r['id']} (figure region) [{b[0]},{b[1]},{b[2]},{b[3]}]")
            page.get_pixmap(matrix=fitz.Matrix(PX_PER_PT, PX_PER_PT)).save(f"img/{lec}/p{i + 1:02d}.png")
        page, k = deck.layout_page(0)
        head = f"# {lec}: {len(deck)} pages, layout space {page.rect.width:.0f} x {page.rect.height:.0f} pt"
        if k != 1.0:
            head += f" (original pages scaled x{k:.3f})"
        with open(f"layout/{lec}.txt", "w", encoding="utf-8") as f:
            f.write(head + "\n" + "\n".join(out) + "\n")
        with open(f"layout/{lec}.json", "w", encoding="utf-8") as f:
            json.dump(js, f, ensure_ascii=False)
        print(lec, len(deck), "pages")


if __name__ == "__main__":
    main()
