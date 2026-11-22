# /// script
# requires-python = ">=3.10"
# dependencies = ["pymupdf>=1.24"]
# ///
"""Verify that an annotated PDF still contains the untouched original.

usage: check.py <original.pdf> <annotated.pdf>
Checks page count, page sizes, and that every word of each original page is still extractable
from the annotated page. Exits with status 1 if anything differs.
"""
import sys
from collections import Counter

import pymupdf as fitz


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    a, b = fitz.open(sys.argv[1]), fitz.open(sys.argv[2])
    problems = []
    if len(a) != len(b):
        problems.append(f"page count {len(a)} -> {len(b)}")
    added = 0
    for i in range(min(len(a), len(b))):
        pa, pb = a[i], b[i]
        if abs(pa.rect.width - pb.rect.width) > 0.01 or abs(pa.rect.height - pb.rect.height) > 0.01:
            problems.append(f"p{i + 1}: size {pa.rect.width:.1f}x{pa.rect.height:.1f} -> {pb.rect.width:.1f}x{pb.rect.height:.1f}")
        wa = Counter(w[4] for w in pa.get_text("words"))
        wb = Counter(w[4] for w in pb.get_text("words"))
        lost = wa - wb
        if lost:
            sample = ", ".join(list(lost)[:5])
            problems.append(f"p{i + 1}: {sum(lost.values())} original word(s) no longer extractable ({sample})")
        added += sum((wb - wa).values())
    if problems:
        print("FAILED")
        for p in problems:
            print("  " + p)
        sys.exit(1)
    print(f"OK: {len(a)} pages, sizes unchanged, original text intact, {added} annotation words added")


if __name__ == "__main__":
    main()
