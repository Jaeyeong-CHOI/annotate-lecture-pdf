"""Shared helpers: fonts, page normalisation, layout extraction (paragraphs / figure regions).

All coordinates the other scripts read or write are in *layout space*: the original page for
ordinary slide decks, or a copy scaled to the reference slide height for unusual page sizes.
"""
import glob
import os
import re
import sys

import pymupdf as fitz

REF_H = float(os.environ.get("ANNOT_REF_HEIGHT", 540))   # landscape pages are laid out as if this tall; 0 = never rescale
TITLE_SIZE = float(os.environ.get("ANNOT_TITLE_SIZE", 30))  # paragraphs at least this large (pt, layout space) count as slide titles
SKIP_TEXT = {s.strip() for s in os.environ.get("ANNOT_SKIP_TEXT", "").split(",") if s.strip()}
PX_PER_PT = 1000 / 720   # resolution of the page images written by prep.py


def name_of(path):
    return os.path.splitext(os.path.basename(path))[0]


def parse_pages(spec):
    """'3', '3-10' or '1-4,9' -> sorted list of 1-based page numbers."""
    pages = set()
    for part in spec.split(","):
        a, _, b = part.strip().partition("-")
        pages.update(range(int(a), int(b or a) + 1))
    return sorted(pages)


# ---------------------------------------------------------------- page normalisation
def page_scale(page):
    """Factor that maps a page onto the reference slide height. 1.0 for ordinary decks and portrait pages."""
    w, h = page.rect.width, page.rect.height
    if REF_H <= 0 or w <= h:
        return 1.0
    k = REF_H / h
    return 1.0 if abs(k - 1) < 0.1 else k


def bands(page):
    """(top, bottom) y of the usable area in layout space. Nothing is placed in the header/footer bands outside it."""
    h = page.rect.height
    top = os.environ.get("ANNOT_TOP")
    bot = os.environ.get("ANNOT_BOT")
    return (float(top) if top else float(round(0.0352 * h)),
            float(bot) if bot else float(round(0.9648 * h)))


class Deck:
    """A source PDF plus, where needed, scaled copies of its pages to do the layout on."""

    def __init__(self, path):
        self.path = path
        self.doc = fitz.open(path)
        self._work = None

    def __len__(self):
        return len(self.doc)

    def layout_page(self, i):
        """-> (page to analyse, scale). The page is the original itself when scale == 1."""
        page = self.doc[i]
        if page.rotation:
            page.remove_rotation()
        k = page_scale(page)
        if k == 1.0:
            return page, 1.0
        if self._work is None:
            self._work = fitz.open()
        wp = self._work.new_page(width=page.rect.width * k, height=page.rect.height * k)
        wp.show_pdf_page(wp.rect, self.doc, i)
        return wp, k


# ---------------------------------------------------------------- fonts
FONT_DIRS = [
    "~/Library/Fonts", "/Library/Fonts", "/System/Library/Fonts", "/System/Library/Fonts/Supplemental",
    "~/.fonts", "~/.local/share/fonts", "/usr/share/fonts", "/usr/local/share/fonts", "C:/Windows/Fonts",
]
# (regular, bold) file patterns, best first. Bold may be None: the regular face is then used for bold too.
TEXT_FONTS = [
    ("NotoSansKR-Regular.[ot]tf", "NotoSansKR-Bold.[ot]tf"),
    ("NotoSansKR*-Regular.[ot]tf", "NotoSansKR*-Bold.[ot]tf"),
    ("Pretendard-Regular.[ot]tf", "Pretendard-Bold.[ot]tf"),
    ("NotoSansCJKkr-Regular.otf", "NotoSansCJKkr-Bold.otf"),
    ("NanumGothic.[ot]tf", "NanumGothicBold.[ot]tf"),
    ("NanumBarunGothic.[ot]tf", "NanumBarunGothicBold.[ot]tf"),
    ("malgun.ttf", "malgunbd.ttf"),
    ("NotoSansKR*.[ot]tf", None),
    ("AppleGothic.ttf", None),
]
SYMBOL_FONTS = ["Arial Unicode.ttf", "NotoSansMath-Regular.[ot]tf", "STIXTwoMath-Regular.[ot]tf", "DejaVuSans.ttf", "seguisym.ttf"]
BUILTIN = ("cjk", "builtin")


def _find(pattern):
    for d in FONT_DIRS:
        d = os.path.expanduser(d)
        if not os.path.isdir(d):
            continue
        hits = sorted(glob.glob(os.path.join(d, "**", pattern), recursive=True))
        if hits:
            return hits[0]
    return None


def _load(spec):
    if spec in BUILTIN:
        return fitz.Font("cjk")
    path = os.path.expanduser(spec)
    if not os.path.isfile(path):
        sys.exit(f"font file not found: {spec}")
    return fitz.Font(fontfile=path)


def load_fonts():
    """-> (regular, bold, symbol fallback or None, last-resort fallback).

    ANNOT_FONT / ANNOT_FONT_BOLD / ANNOT_FONT_MATH take a font file path; ANNOT_FONT=cjk forces
    the font built into PyMuPDF. Without them the usual system font folders are searched.
    """
    reg, bold = os.environ.get("ANNOT_FONT"), os.environ.get("ANNOT_FONT_BOLD")
    if not reg:
        for r_pat, b_pat in TEXT_FONTS:
            reg = _find(r_pat)
            if reg:
                bold = bold or (b_pat and _find(b_pat))
                break
    if not reg:
        reg = "cjk"
        print("note: no Korean font found; using PyMuPDF's built-in CJK font (no bold face). "
              "Install Noto Sans KR or set ANNOT_FONT / ANNOT_FONT_BOLD for better type.", file=sys.stderr)
    fr = _load(reg)
    fb = _load(bold) if bold else fr
    sym = os.environ.get("ANNOT_FONT_MATH")
    if not sym:
        for pat in SYMBOL_FONTS:
            sym = _find(pat)
            if sym:
                break
    fm = _load(sym) if sym else None
    return fr, fb, fm, fitz.Font("cjk")


# ---------------------------------------------------------------- layout extraction
# \uf0xx: Wingdings bullet glyphs as PowerPoint exports them
BULLET_RE = re.compile(
    r"^\s*(?:[▪•◦‣▸►■□●○◆◇➢➤✓\-–—*]|\uf0a7|\uf0d8|\uf0fc|\uf076|\d{1,2}[.)]|[a-zA-Z][.)]|\((?:[ivx]+|\d+|[a-z])\))\s*"
)


def _rows(page, top, bot):
    """Merge same-baseline line fragments into rows."""
    H = page.rect.height
    lines = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        for l in b["lines"]:
            spans = [s for s in l["spans"] if s["text"].strip()]
            if not spans:
                continue
            x0, y0, x1, y1 = l["bbox"]
            if y1 <= top + 12 and y0 < top:
                continue  # header band
            if y0 >= bot:
                continue  # footer band
            text = "".join(s["text"] for s in l["spans"])
            if text.strip() in SKIP_TEXT and (y0 < 0.07 * H or y1 > 0.93 * H):
                continue  # template text such as a logo or course code
            size = max(s["size"] for s in spans)
            mono = any("Courier" in s["font"] or "Mono" in s["font"] or "Consolas" in s["font"] for s in spans)
            lines.append(dict(bbox=[x0, y0, x1, y1], text=text, size=size, mono=mono))
    lines.sort(key=lambda l: (round(l["bbox"][1] / 2), l["bbox"][0]))
    rows = []
    for l in lines:
        placed = False
        for r in reversed(rows[-6:]):
            a, b = r["bbox"], l["bbox"]
            ov = min(a[3], b[3]) - max(a[1], b[1])
            if ov > 0.6 * min(a[3] - a[1], b[3] - b[1]) and 0 <= b[0] - a[2] < 30:
                r["text"] += (" " if not r["text"].endswith(" ") else "") + l["text"]
                r["bbox"] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                r["size"] = max(r["size"], l["size"])
                r["mono"] = r["mono"] or l["mono"]
                placed = True
                break
        if not placed:
            rows.append(dict(l))
    rows.sort(key=lambda r: (r["bbox"][1], r["bbox"][0]))
    return rows


def paragraphs(page, top=None, bot=None):
    """Text paragraphs of a layout page, each with an id (p1, p2, ...), bbox, rows and font size."""
    if top is None or bot is None:
        top, bot = bands(page)
    rows = _rows(page, top, bot)
    chars = []
    for b in page.get_text("rawdict")["blocks"]:
        if b["type"] != 0:
            continue
        for l in b["lines"]:
            for sp in l["spans"]:
                for c in sp["chars"]:
                    bb = c["bbox"]
                    chars.append((bb[0], bb[1], bb[2], bb[3], c["c"]))
    paras = []
    for r in rows:
        x0, y0, x1, y1 = r["bbox"]
        starts_bullet = bool(BULLET_RE.match(r["text"])) and not r["mono"]
        target = None
        if not starts_bullet:
            for p in reversed(paras[-8:]):
                lr = p["rows"][-1]
                gap = y0 - lr[3]
                same_size = abs(p["size"] - r["size"]) <= 0.16 * p["size"]
                xov = min(p["bbox"][2], x1) - max(p["bbox"][0], x0)
                if (-0.3 * r["size"] <= gap <= 0.55 * r["size"] and same_size and xov > 0
                        and x0 >= p["xt"] - 8 and p["mono"] == r["mono"]):
                    target = p
                    break
        if target is None:
            xt = x0
            if starts_bullet:
                cs = sorted([c for c in chars if c[1] < y1 - 2 and c[3] > y0 + 2 and c[0] >= x0 - 1 and c[2] <= x1 + 1],
                            key=lambda c: c[0])
                line = "".join(c[4] for c in cs)
                m = BULLET_RE.match(line)
                k = m.end() if m else 0
                while k < len(cs) and cs[k][4].isspace():
                    k += 1
                if k < len(cs):
                    xt = cs[k][0]
            paras.append(dict(rows=[[x0, y0, x1, y1]], bbox=[x0, y0, x1, y1], text=r["text"].strip(),
                              size=r["size"], xt=xt, mono=r["mono"], bullet=starts_bullet))
        else:
            p = target
            p["rows"].append([x0, y0, x1, y1])
            b = p["bbox"]
            p["bbox"] = [min(b[0], x0), min(b[1], y0), max(b[2], x1), max(b[3], y1)]
            sep = "\n" if p["mono"] else " "
            p["text"] = (p["text"] + sep + r["text"].strip()).strip()
    for i, p in enumerate(paras, 1):
        p["id"] = f"p{i}"
    return paras


def regions(page, top=None):
    """Figure regions (r1, r2, ...): images and clusters of vector drawings."""
    if top is None:
        top = bands(page)[0]
    W, H = page.rect.width, page.rect.height
    rects = []
    for im in page.get_image_info():
        r = fitz.Rect(im["bbox"]) & page.rect
        if r.width > 12 and r.height > 12 and r.get_area() < 0.9 * W * H and r.y1 > top + 4:
            rects.append(r)
    for g in page.get_drawings():
        r = fitz.Rect(g["rect"]) & page.rect
        if r.is_empty and r.width < 1 and r.height < 1:
            continue
        if r.get_area() > 0.55 * W * H:
            continue
        if r.y1 <= top + 1:
            continue
        if r.width < 2 and r.height < 2:
            continue
        # keep thin horizontal strokes (text underlines) as slim rects
        if r.height < 2.5 and r.width > 6 and g.get("items") is not None and len(g["items"]) <= 1 and g.get("type") == "f":
            rects.append(fitz.Rect(r.x0, r.y0 - 0.5, r.x1, r.y1 + 0.5))
            continue
        rects.append(r)
    changed = True
    cl = [fitz.Rect(r) for r in rects]
    while changed:
        changed = False
        out = []
        for r in cl:
            for j, o in enumerate(out):
                e = fitz.Rect(o.x0 - 7, o.y0 - 7, o.x1 + 7, o.y1 + 7)
                if e.intersects(r) or (r.is_empty and e.contains(r.tl)):
                    out[j] = o | r
                    changed = True
                    break
            else:
                out.append(fitz.Rect(r))
        cl = out
    cl = [r for r in cl if r.width >= 30 and r.height >= 18]
    cl.sort(key=lambda r: (r.y0, r.x0))
    return [dict(id=f"r{i}", bbox=[round(v, 1) for v in r]) for i, r in enumerate(cl, 1)]
