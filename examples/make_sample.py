# /// script
# requires-python = ">=3.10"
# dependencies = ["pymupdf>=1.24"]
# ///
"""Build examples/sample.pdf: a three-slide English deck to try the skill on.

usage: make_sample.py [out.pdf]
"""
import sys

import pymupdf as fitz

W, H = 720, 540
NAVY = (0.10, 0.16, 0.33)
GREY = (0.35, 0.35, 0.35)


def slide(doc, title, n):
    p = doc.new_page(width=W, height=H)
    p.draw_rect(fitz.Rect(0, 0, W, 16), color=None, fill=NAVY)
    p.insert_text((40, 78), title, fontname="hebo", fontsize=32, color=NAVY)
    p.draw_line((40, 92), (W - 40, 92), color=NAVY, width=1.2)
    p.insert_text((W - 60, H - 8), f"{n} / 3", fontname="helv", fontsize=9, color=GREY)
    return p


def bullets(p, items, y=140, size=20, gap=58):
    for text in items:
        p.insert_text((52, y), "•", fontname="helv", fontsize=size)
        p.insert_text((72, y), text, fontname="helv", fontsize=size)
        y += gap
    return y


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "sample.pdf"
    doc = fitz.open()

    p = slide(doc, "Binary Search", 1)
    bullets(p, [
        "Find a target value in a sorted array",
        "Compare the target with the middle element",
        "Discard the half that cannot contain the target",
        "Running time: O(log n) comparisons",
    ])

    p = slide(doc, "Example: search for 23", 2)
    vals = [2, 5, 8, 12, 16, 23, 38, 56, 72]
    x0, y0, cw, ch = 81, 150, 62, 44
    for i, v in enumerate(vals):
        r = fitz.Rect(x0 + i * cw, y0, x0 + (i + 1) * cw, y0 + ch)
        p.draw_rect(r, color=NAVY, fill=(1.0, 0.93, 0.80) if v == 23 else (0.93, 0.95, 1.0), width=1)
        p.insert_textbox(fitz.Rect(r.x0, r.y0 + 12, r.x1, r.y1), str(v), fontname="helv", fontsize=17, align=1)
        p.insert_textbox(fitz.Rect(r.x0, r.y1 + 3, r.x1, r.y1 + 18), str(i), fontname="helv", fontsize=10, align=1, color=GREY)
    bullets(p, [
        "Step 1: low = 0, high = 8, mid = 4",
        "Step 2: 16 < 23, so low = 5 and mid = 6",
        "Step 3: 38 > 23, so high = 5 and mid = 5: found",
    ], y=292, size=18, gap=56)

    p = slide(doc, "Implementation", 3)
    code = [
        "def binary_search(a, target):",
        "    low, high = 0, len(a) - 1",
        "    while low <= high:",
        "        mid = (low + high) // 2",
        "        if a[mid] == target:",
        "            return mid",
        "        if a[mid] < target:",
        "            low = mid + 1",
        "        else:",
        "            high = mid - 1",
        "    return -1",
    ]
    y = 132
    for line in code:
        p.insert_text((52, y), line, fontname="cour", fontsize=14)
        y += 19
    bullets(p, ["The loop invariant: the target, if present, lies in a[low..high]"], y=392, size=17)

    doc.save(out, garbage=4, deflate=True)
    print(out, len(doc), "pages")


if __name__ == "__main__":
    main()
