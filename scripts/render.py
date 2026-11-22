# /// script
# requires-python = ">=3.10"
# dependencies = ["pymupdf>=1.24", "numpy", "fonttools"]
# ///
"""Overlay study annotations (translation, notes, highlights) on the original slide PDF.

usage: render.py <deck.pdf> [--pages 3-10] [--noprev] [--strict]
spec:  spec/<name>/*.json  -> {"<page>": {"tr": [...], "hl": [...], "notes": [...], "sum": "..."}}
out:   out/<name>_주석.pdf, prev/<name>/pNN.png, report/<name>.json   (all under the current directory)
"""
import argparse, glob, json, math, os, re, sys
import numpy as np
import pymupdf as fitz
from common import Deck, bands, load_fonts, name_of, page_scale, paragraphs, regions, TITLE_SIZE

S = 2  # mask px per pt
K = float(os.environ.get("ANNOT_SCALE", 1.0))  # annotation font/width scale (roomy 16:9 decks: ~1.3)
SUFFIX = os.environ.get("ANNOT_SUFFIX", "_주석")
SUM_LABEL = os.environ.get("ANNOT_SUM_LABEL", "정리")
FR, FB, FM, FZ = load_fonts()


def rgb(h):
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))


TR = rgb("#1557D6")          # translation blue
RED = rgb("#D81E1E")           # handwritten-style notes
KIND = {k: (None, None, RED, RED) for k in ("key", "why", "ex", "warn", "exam")}
KIND["tr"] = (None, None, TR, TR)
KIND["sum"] = (SUM_LABEL, None, RED, RED)
HL = rgb("#FFE100")              # highlighter
CIRC = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


# ---------------------------------------------------------------- text layout
def segs_of(s, base_color, label=None, label_color=None):
    out = []
    if label:
        out.append((label + "  ", True, label_color))
    parts = re.split(r"(\*\*.+?\*\*)", s)
    for p in parts:
        if not p:
            continue
        if p.startswith("**") and p.endswith("**") and len(p) > 4:
            out.append((p[2:-2], True, base_color))
        else:
            out.append((p.replace("`", ""), False, base_color))
    return out


_GL = {}


def _font_for(ch, bold):
    """Font for one character: the text font, else the first fallback that has the glyph."""
    base = FB if bold else FR
    k = (ch, bold)
    if k not in _GL:
        f = base
        if not ch.isspace() and not base.has_glyph(ord(ch)):
            f = next((x for x in (FR, FM, FZ) if x is not None and x.has_glyph(ord(ch))), base)
        _GL[k] = f
    return _GL[k]


def body(d):
    """Annotation text of a spec item ("ko"; "text" is accepted as an alias)."""
    return (d.get("ko") or d.get("text") or "").strip()


def mparse(t):
    """Math markup: _{sub} ^{sup} ~{overline}. -> [(char, script, bar_ids)], {bar_id: height}"""
    out, stack, bars, nbar = [], [], {}, 0
    i = 0
    while i < len(t):
        c = t[i]
        if c in "_^~" and i + 1 < len(t) and t[i + 1] == "{":
            if c == "~":
                nbar += 1
                bars[nbar] = 1
                stack.append(("bar", nbar))
            else:
                stack.append(("sub" if c == "_" else "sup", 0))
            i += 2
            continue
        if c == "}" and stack:
            kind, bid = stack.pop()
            if kind == "bar":
                for k2, b2 in stack:
                    if k2 == "bar":
                        bars[b2] = max(bars[b2], bars[bid] + 1)
            i += 1
            continue
        sc = 0
        for k2, _ in stack:
            if k2 == "sub":
                sc = -1
            elif k2 == "sup":
                sc = 1
        out.append((" " if c == "\ue000" else c, sc, tuple(b for k2, b in stack if k2 == "bar")))
        i += 1
    return out, bars


def runs(t, bold, fs):
    """-> [(text, font, fontsize, dy, bar_ids)], bars"""
    chars, bars = mparse(t)
    out = []
    for c, sc, bids in chars:
        font = _font_for(c, bold)
        f = fs if sc == 0 else fs * 0.68
        dy = 0.0 if sc == 0 else (fs * 0.22 if sc < 0 else -fs * 0.36)
        if out and out[-1][1] is font and out[-1][2] == f and out[-1][3] == dy and out[-1][4] == bids:
            out[-1] = (out[-1][0] + c, font, f, dy, bids)
        else:
            out.append((c, font, f, dy, bids))
    return out, bars


def tlen(t, bold, fs):
    return sum(font.text_length(x, fontsize=f) for x, font, f, dy, b in runs(t, bold, fs)[0])


def _glue_bars(t):
    """Spaces inside ~{...} (nested) become U+E000 so an overline group never wraps or breaks."""
    out, depth, i = [], 0, 0
    while i < len(t):
        if t[i] == "~" and i + 1 < len(t) and t[i + 1] == "{":
            depth += 1; out.append("~{"); i += 2; continue
        if t[i] == "{" and depth:
            depth += 1
        elif t[i] == "}" and depth:
            depth -= 1
        out.append("\ue000" if (t[i] == " " and depth) else t[i])
        i += 1
    return "".join(out)


def _unspace(m):
    return m.group(0).replace(" ", "")


def wrap(segs, fs, maxw):
    lines, cur, curw = [], [], 0.0

    def flush():
        nonlocal cur, curw
        while cur and cur[-1][0].isspace():
            curw -= cur[-1][3]
            cur.pop()
        lines.append((cur, curw))
        cur, curw = [], 0.0

    for text, bold, color in segs:
        for part in re.split(r"(\n)", text):
            if part == "\n":
                flush()
                continue
            part = re.sub(r"[_^]\{[^{}]*\}", _unspace, part)
            part = _glue_bars(part)
            for tok in re.findall(r"\s+|\S+", part):
                w = tlen(tok, bold, fs)
                if tok.isspace():
                    if cur:
                        cur.append((tok, bold, color, w))
                        curw += w
                    continue
                if curw + w > maxw and cur:
                    flush()
                if w > maxw and not re.search(r"[_^~]\{", tok):  # hard split
                    buf = ""
                    for ch in tok:
                        if tlen(buf + ch, bold, fs) > maxw - curw and buf:
                            cur.append((buf, bold, color, tlen(buf, bold, fs)))
                            curw += tlen(buf, bold, fs)
                            flush()
                            buf = ch
                        else:
                            buf += ch
                    if buf:
                        w2 = tlen(buf, bold, fs)
                        cur.append((buf, bold, color, w2))
                        curw += w2
                    continue
                cur.append((tok, bold, color, w))
                curw += w
    if cur or not lines:
        flush()
    return lines


LH = 1.30


def block_dims(lines, fs):
    w = max((lw for _, lw in lines), default=0)
    h = fs * 1.14 + (len(lines) - 1) * fs * LH
    return w, h


class Painter:
    """Collects text per colour and writes with TextWriter at the end."""

    def __init__(self, page):
        self.page = page
        self.tw = {}
        self.lines = []

    def text(self, x, y, lines, fs):
        for i, (toks, _) in enumerate(lines):
            cx = x
            by = y + fs * 0.90 + i * fs * LH
            for t, bold, color, w in toks:
                if not t.isspace():
                    tw = self.tw.setdefault(color, fitz.TextWriter(self.page.rect, color=color))
                    rs, bars = runs(t, bold, fs)
                    rx = cx
                    ext = {}
                    for rt, font, f, dy, bids in rs:
                        tw.append((rx, by + dy), rt, font=font, fontsize=f)
                        rw = font.text_length(rt, fontsize=f)
                        for b in bids:
                            e = ext.setdefault(b, [rx, rx + rw])
                            e[0] = min(e[0], rx); e[1] = max(e[1], rx + rw)
                        rx += rw
                    for b, (a0, a1) in ext.items():
                        ly = by - fs * (0.80 + 0.13 * (bars[b] - 1))
                        self.lines.append((a0 + 0.2, a1 - 0.2, ly, color, max(0.5, fs * 0.055)))
                cx += w

    def flush(self):
        for tw in self.tw.values():
            tw.write_text(self.page)
        for a0, a1, ly, color, lw in self.lines:
            self.page.draw_line((a0, ly), (a1, ly), color=color, width=lw)


# ---------------------------------------------------------------- occupancy mask
def box_any(m, r):
    """True where any True within (2r+1) square window."""
    if r <= 0:
        return m.copy()
    H, W = m.shape
    p = np.pad(m.astype(np.int32), r)
    ii = np.zeros((H + 2 * r + 1, W + 2 * r + 1), np.int32)
    ii[1:, 1:] = p.cumsum(0).cumsum(1)
    k = 2 * r + 1
    s = ii[k:, k:] - ii[:-k, k:] - ii[k:, :-k] + ii[:-k, :-k]
    return s > 0


class Mask:
    def __init__(self, page, top=None, bot=None):
        if top is None or bot is None:
            top, bot = bands(page)
        pm = page.get_pixmap(matrix=fitz.Matrix(S, S), alpha=False)
        a = np.frombuffer(pm.samples, np.uint8).reshape(pm.h, pm.w, pm.n)[:, :, :3]
        ink = a.min(axis=2) < 236
        self.ink = ink
        self.H, self.W = ink.shape
        self.ink_ratio = float(ink.mean())
        raw = box_any(ink, 2)
        edge = np.zeros_like(raw)
        edge[: int(top * S) + 6, :] = True
        edge[int(bot * S):, :] = True
        edge[:, : 10 * S] = True
        edge[:, self.W - 10 * S:] = True
        raw |= edge
        mid = raw.copy()
        W, H = page.rect.width, page.rect.height
        for im in page.get_image_info():
            self._fill(mid, fitz.Rect(im["bbox"]), 2)
        for g in page.get_drawings():
            r = fitz.Rect(g["rect"])
            area = r.width * r.height
            if area > 0.12 * W * H or r.y1 <= top + 1:
                continue
            items = g.get("items", [])
            if items and all(it[0] == "l" for it in items) and len(items) <= 2 and r.width > 3 and r.height > 3:
                continue
            if r.width >= 8 and r.height >= 8:
                self._fill(mid, r, 1)
        closed = ~box_any(~box_any(mid, 9), 9)
        flt = closed | mid
        flt[: 42 * S, :] = True
        self.m = {"raw": raw, "mid": mid, "flt": flt}
        self._ii()

    @staticmethod
    def _fill(m, r, pad):
        H, W = m.shape
        x0 = max(0, int((r.x0 - pad) * S)); y0 = max(0, int((r.y0 - pad) * S))
        x1 = min(W, int(math.ceil((r.x1 + pad) * S))); y1 = min(H, int(math.ceil((r.y1 + pad) * S)))
        if x1 > x0 and y1 > y0:
            m[y0:y1, x0:x1] = True

    def _ii(self):
        self.ii = {}
        for k, m in self.m.items():
            ii = np.zeros((self.H + 1, self.W + 1), np.int32)
            ii[1:, 1:] = m.cumsum(0, dtype=np.int32).cumsum(1, dtype=np.int32)
            self.ii[k] = ii

    def ink_frac(self, r):
        x0 = max(0, int(r[0] * S)); y0 = max(0, int(r[1] * S))
        x1 = min(self.W, int(math.ceil(r[2] * S))); y1 = min(self.H, int(math.ceil(r[3] * S)))
        if x1 <= x0 or y1 <= y0:
            return 0.0
        return float(self.ink[y0:y1, x0:x1].mean())

    def snapshot(self):
        return ({k: v.copy() for k, v in self.m.items()}, dict(self.ii))

    def restore(self, snap):
        self.m, self.ii = snap

    def mark(self, rect, pad=2.5):
        for m in self.m.values():
            self._fill(m, fitz.Rect(rect), pad)
        self._ii()

    def free(self, which, x0, y0, x1, y1):
        X0, Y0 = int(math.floor(x0 * S)), int(math.floor(y0 * S))
        X1, Y1 = int(math.ceil(x1 * S)), int(math.ceil(y1 * S))
        if X0 < 0 or Y0 < 0 or X1 > self.W or Y1 > self.H or X1 <= X0 or Y1 <= Y0:
            return False
        ii = self.ii[which]
        return (ii[Y1, X1] - ii[Y0, X1] - ii[Y1, X0] + ii[Y0, X0]) == 0

    def free_positions(self, which, w, h, step=4):
        """All free top-left positions (pt arrays xs, ys) for a w x h pt box."""
        ww, wh = int(math.ceil(w * S)), int(math.ceil(h * S))
        if ww >= self.W or wh >= self.H:
            return np.array([]), np.array([])
        ii = self.ii[which]
        s = ii[wh:, ww:] - ii[:-wh, ww:] - ii[wh:, :-ww] + ii[:-wh, :-ww]
        sub = s[::step, ::step] == 0
        ys, xs = np.nonzero(sub)
        return xs * step / S, ys * step / S

    def ink_on_segment(self, a, b):
        n = max(2, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 2))
        cnt = 0
        raw = self.m["raw"]
        for i in range(1, n - 1):
            t = i / n
            x = int((a[0] + (b[0] - a[0]) * t) * S); y = int((a[1] + (b[1] - a[1]) * t) * S)
            if 0 <= x < self.W and 0 <= y < self.H and raw[y, x]:
                cnt += 1
        return cnt


# ---------------------------------------------------------------- drawing helpers
def rrect(shape, r, fill, border, width=0.7, rad=3.2, dashes=None, fill_opacity=1.0):
    m = min(r.width, r.height)
    shape.draw_rect(r, radius=min(0.5, rad / m) if m > 0 else None)
    shape.finish(color=border, fill=fill, width=width, dashes=dashes, fill_opacity=fill_opacity)


def clamp_pt(p, r):
    return (min(max(p[0], r.x0), r.x1), min(max(p[1], r.y0), r.y1))


def draw_arrow(shape, a, b, color):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    if L < 7:
        return
    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux
    bend = min(9.0, 0.11 * L)
    c = ((a[0] + b[0]) / 2 + nx * bend, (a[1] + b[1]) / 2 + ny * bend)
    tx, ty = b[0] - c[0], b[1] - c[1]
    tl = math.hypot(tx, ty) or 1
    tx, ty = tx / tl, ty / tl
    hb = (b[0] - tx * 5.2, b[1] - ty * 5.2)
    c1 = (a[0] + 2 / 3 * (c[0] - a[0]), a[1] + 2 / 3 * (c[1] - a[1]))
    c2 = (hb[0] + 2 / 3 * (c[0] - hb[0]), hb[1] + 2 / 3 * (c[1] - hb[1]))
    for col, wd, op in (((1, 1, 1), 2.8, 0.85), (color, 1.05, 1.0)):
        shape.draw_bezier(a, c1, c2, hb)
        shape.finish(color=col, width=wd, closePath=False, lineCap=1, stroke_opacity=op)
    px, py = -ty, tx
    shape.draw_polyline([b, (hb[0] + px * 2.5, hb[1] + py * 2.5), (hb[0] - px * 2.5, hb[1] - py * 2.5), b])
    shape.finish(color=color, fill=color, width=0.3)
    shape.draw_circle(a, 1.3)
    shape.finish(color=color, fill=color, width=0.2)


# ---------------------------------------------------------------- page renderer
class PageR:
    def __init__(self, page, pno, spec, issues, canvas=None):
        """page: the layout page to analyse. canvas: the page to draw on (defaults to the same page)."""
        self.page, self.pno, self.spec = page, pno, spec
        canvas = canvas or page
        self.issues = issues
        self.top, self.bot = bands(page)
        self.paras = {p["id"]: p for p in paragraphs(page, self.top, self.bot)}
        self.regs = {r["id"]: r for r in regions(page, self.top)}
        self.mask = Mask(page, self.top, self.bot)
        for p in self.paras.values():
            p["hidden"] = self.mask.ink_frac(p["bbox"]) < 0.004
        self.W, self.H = page.rect.width, page.rect.height
        self.RL = self.W - 14
        self.shape_under = canvas.new_shape()
        self.shape = canvas.new_shape()
        self.paint = Painter(canvas)
        self.badge_n = 0

    def warn(self, kind, msg):
        self.issues.append(dict(page=self.pno, kind=kind, msg=msg))

    # ---- anchors
    def get_par(self, pid):
        ids = pid if isinstance(pid, list) else [pid]
        ps = [self.paras[i] for i in ids if i in self.paras]
        if len(ps) != len(ids):
            self.warn("par_not_found", str(pid))
        if any(p.get("hidden") for p in ps):
            self.warn("hidden_par", f"{pid} is covered/invisible in the original; remove it from the spec")
            return None
        if not ps:
            return None
        if len(ps) == 1:
            return ps[0]
        rows = [r for p in ps for r in p["rows"]]
        return dict(rows=rows, bbox=[min(r[0] for r in rows), min(r[1] for r in rows), max(r[2] for r in rows), max(r[3] for r in rows)],
                    size=ps[0]["size"], xt=min(p["xt"] for p in ps), mono=ps[0]["mono"], text=" ".join(p["text"] for p in ps))

    def find_text(self, t, pid=None, n=None):
        clip = None
        if pid:
            p = self.get_par(pid)
            if p:
                b = p["bbox"]
                clip = fitz.Rect(b[0] - 3, b[1] - 3, b[2] + 3, b[3] + 3)
        rs = self.page.search_for(t, clip=clip)
        rs = [r for r in rs if r.y0 > self.top - 2 and self.mask.ink_frac(r) >= 0.004]
        if not pid:
            body = [r for r in rs if r.height < 34]
            if body:
                rs = body
        if not rs:
            return []
        # group consecutive rects into occurrences (wrapped hits are adjacent rows)
        if n is not None:
            # naive: nth rect
            return [rs[min(n, len(rs)) - 1]] if n >= 1 else rs
        return rs

    def resolve(self, at):
        """-> (list of Rect, type)"""
        if at is None:
            return [], None
        if isinstance(at, dict):
            if "r" in at and isinstance(at["r"], list):
                return [fitz.Rect(at["r"])], "rect"
            if "t" in at:
                rs = self.find_text(at["t"], at.get("p"), at.get("n"))
                if not rs:
                    self.warn("text_not_found", at["t"])
                    if at.get("p"):
                        return self.resolve(at["p"])
                    return [], None
                return rs[:3] if at.get("n") is None else rs, "text"
            if "p" in at:
                return self.resolve(at["p"])
        if isinstance(at, list) and len(at) == 4 and all(isinstance(v, (int, float)) for v in at):
            return [fitz.Rect(at)], "rect"
        if isinstance(at, str):
            if re.fullmatch(r"p\d+", at):
                p = self.get_par(at)
                return ([fitz.Rect(r) for r in p["rows"]], "par") if p else ([], None)
            if re.fullmatch(r"r\d+", at):
                if at in self.regs:
                    return [fitz.Rect(self.regs[at]["bbox"])], "rect"
                self.warn("region_not_found", at)
                return [], None
            rs = self.find_text(at)
            if not rs:
                self.warn("text_not_found", at)
                return [], None
            return rs[:1], "text"
        return [], None

    # ---- highlights
    def do_hl(self):
        for h in self.spec.get("hl", []):
            if isinstance(h, str):
                h = {"t": h}
            rs = self.find_text(h["t"], h.get("p"), h.get("n"))
            if not rs:
                self.warn("hl_not_found", h["t"])
                continue
            for r in rs:
                hh = r.height
                rr = fitz.Rect(r.x0 - 0.8, r.y0 + hh * 0.14, r.x1 + 0.8, r.y1 - hh * 0.06)
                self.shape_under.draw_rect(rr)
                self.shape_under.finish(color=None, fill=HL, fill_opacity=0.40, width=0)

    # ---- translations
    def cand_below(self, par, ko, fs, maxlines=2):
        size = par["size"]
        last = par["rows"][-1]
        xt = par["xt"]
        maxw = self.RL - xt
        if maxw < 60:
            return None
        lines = wrap(segs_of(ko, TR), fs, maxw)
        if len(lines) > maxlines:
            return None
        w, h = block_dims(lines, fs)
        y = last[3] - 0.24 * size
        yend = last[3] + 0.42 * size
        while y <= yend:
            if self.mask.free("mid", xt, y - 1.0, xt + w, y + h + 0.3):
                return (xt, y + 0.2, lines, fs, w, h)
            y += 0.5
        return None

    def cand_right(self, par, ko, fs, one_line=False):
        last = par["rows"][-1]
        x = last[2] + 10
        avail = self.RL - x
        if avail < 60:
            return None
        rowh = last[3] - last[1]
        widths = (avail,) if one_line else (avail, min(avail, 300 * K), min(avail, 220 * K))
        for maxw in widths:
            lines = wrap(segs_of(ko, TR), fs, maxw)
            if one_line and len(lines) > 1:
                return None
            if len(lines) > 1 and maxw < 120:
                continue
            w, h = block_dims(lines, fs)
            if h > rowh + 8 or len(lines) > 2:
                continue
            yc = (last[1] + last[3]) / 2 + 0.03 * par["size"]
            for dy in (0, -2, 2, -4, 4):
                y = yc - h / 2 + dy
                if self.mask.free("mid", x - 2, y - 0.5, x + w, y + h + 0.5):
                    return (x, y, lines, fs, w, h)
        return None

    def cand(self, par, ko, fs, pos=None):
        single_short = len(par["rows"]) == 1
        order = []
        if pos == "r":
            order = [("r", False), ("b", 2), ("b", 3)]
        elif pos == "b":
            order = [("b", 2), ("b", 3), ("r", False)]
        else:
            if single_short:
                order.append(("r", True))
            order += [("b", 2), ("r", False), ("b", 3)]
        for mode, arg in order:
            c = self.cand_right(par, ko, fs, arg) if mode == "r" else self.cand_below(par, ko, fs, arg)
            if c:
                return c
        return None

    def put_tr(self, c):
        x, y, lines, fs, w, h = c
        self.paint_tr.append((x, y, lines, fs))
        self.mask.mark(fitz.Rect(x, y, x + w, y + h), pad=1.5)

    def do_tr(self):
        self.paint_tr = []
        items = []
        for t in self.spec.get("tr", []):
            par = self.get_par(t["p"])
            if par is None:
                continue
            items.append((par, body(t), t))
        # manual boxes first
        rest = []
        for par, ko, t in items:
            if "box" in t:
                x, y, w = t["box"][:3]
                fs = t.get("fs", 8.5 * K)
                lines = wrap(segs_of(ko, TR), fs, w)
                bw, bh = block_dims(lines, fs)
                self.put_tr((x, y, lines, fs, bw, bh))
            else:
                rest.append((par, ko, t))

        def cap(par, t):
            if t.get("fs"):
                return t["fs"]
            size = par["size"]
            if size >= TITLE_SIZE:
                return 12.0 * K
            return K * min(10.0, max(7.5, math.floor(size * 0.46 * 2) / 2))

        def attempt(fs_page, commit):
            snap = self.mask.snapshot()
            n_ok, failed, placed = 0, [], []
            for par, ko, t in rest:
                c = None
                top = min(cap(par, t), fs_page if par["size"] < TITLE_SIZE else 12.0 * K)
                fs = top
                while fs >= max(7.5 * K, top - 1.0) - 1e-6 and c is None:
                    c = self.cand(par, ko, fs, t.get("pos"))
                    fs -= 0.5
                if c:
                    n_ok += 1
                    placed.append(c)
                    self.mask.mark(fitz.Rect(c[0], c[1], c[0] + c[4], c[1] + c[5]), pad=1.5)
                else:
                    failed.append((par, ko))
            if not commit:
                self.mask.restore(snap)
            return n_ok, failed, placed

        best = None
        for fs_page in (10.0 * K, 9.5 * K, 9.0 * K, 8.5 * K, 8.0 * K):
            n_ok, failed, _ = attempt(fs_page, False)
            sc = n_ok + (fs_page / K - 8.0) * 0.8
            if best is None or sc > best[0]:
                best = (sc, fs_page)
            if not failed:
                break
        pending = []
        if rest:
            _, pending, placed = attempt(best[1], True)
            for c in placed:
                self.paint_tr.append(c[:4])
        for x, y, lines, fs in self.paint_tr:
            self.paint.text(x, y, lines, fs)
        if pending:
            self.do_panel(pending)

    def do_panel(self, pending):
        """Numbered fallback: badge next to paragraph, translations in a boxed list."""
        if len(pending) > len(CIRC):
            self.warn("too_many_panel", str(len(pending)))
            pending = pending[: len(CIRC)]
        cx = float(np.mean([(p["bbox"][0] + p["bbox"][2]) / 2 for p, _ in pending]))
        cy = float(np.mean([(p["bbox"][1] + p["bbox"][3]) / 2 for p, _ in pending]))
        text = "\n".join(f"**{CIRC[i]}** {ko}" for i, (_, ko) in enumerate(pending))
        best = None
        pad = 4
        for fs in (8.5 * K, 8 * K, 7.5 * K):
            for w in (300 * K, 230 * K, 380 * K, 180 * K, 460 * K, 150 * K, 560 * K, 660 * K):
                lines = wrap(segs_of(text, TR), fs, w - 2 * pad)
                tw, th = block_dims(lines, fs)
                bw, bh = tw + 2 * pad, th + 2 * pad
                xs, ys = self.mask.free_positions("flt", bw + 4, bh + 4)
                if len(xs) == 0:
                    continue
                bx, by = xs + 2 + bw / 2, ys + 2 + bh / 2
                sc = np.hypot(bx - cx, (by - cy)) + len(lines) * 2
                i = int(sc.argmin())
                if best is None or sc[i] < best[0]:
                    best = (float(sc[i]), xs[i] + 2, ys[i] + 2, bw, bh, lines, fs)
            if best is not None:
                break
        if best is None:
            if len(pending) > 1:
                h = len(pending) // 2
                self.do_panel(pending[:h]); self.do_panel(pending[h:])
                return
            self.warn("unplaced_tr", pending[0][1][:40])
            return
        _, x, y, bw, bh, lines, fs = best
        r = fitz.Rect(x, y, x + bw, y + bh)
        self.paint.text(x + pad, y + pad, lines, fs)
        self.mask.mark(r)
        self.warn("info_tr_panel", f"{len(pending)} translations in numbered panel")
        for i, (par, _) in enumerate(pending):
            first, last = par["rows"][0], par["rows"][-1]
            fsb = 8.5 * K
            bwid = tlen(CIRC[i], True, fsb)
            cands = [(first[0] - bwid - 2.5, (first[1] + first[3]) / 2 - fsb * 0.55),
                     (last[2] + 3, (last[1] + last[3]) / 2 - fsb * 0.55),
                     (first[0] - bwid - 2.5, first[1] - fsb * 0.6),
                     (last[2] + 3, last[1] - 2)]
            pos = cands[1]
            for c in cands:
                if self.mask.free("raw", c[0], c[1] + 1, c[0] + bwid, c[1] + fsb):
                    pos = c
                    break
            self.paint.text(pos[0], pos[1], [([(CIRC[i], True, TR, bwid)], bwid)], fsb)
            self.mask.mark(fitz.Rect(pos[0], pos[1], pos[0] + bwid, pos[1] + fsb), pad=1)

    # ---- notes
    def note_layout(self, ko, kind, fs, w, label=None):
        lab, fill, border, tc = KIND[kind]
        if label is not None:
            lab = label
        pad = 2.0
        lines = wrap(segs_of(ko, tc, lab or None, border), fs, w - 2 * pad)
        tw, th = block_dims(lines, fs)
        return lines, tw + 2 * pad, th + 2 * pad, pad

    def draw_note(self, x, y, bw, bh, lines, fs, pad, kind):
        _, fill, border, _ = KIND[kind]
        r = fitz.Rect(x, y, x + bw, y + bh)
        self.paint.text(x + pad, y + pad, lines, fs)
        self.mask.mark(r, pad=3.5)
        return r

    def do_sum(self):
        s = self.spec.get("sum")
        if not s:
            return
        for fs in (9.0 * K, 8.5 * K, 8 * K, 7.5 * K):
            for w in (self.W - 60, 520 * K, 400 * K):
                lines, bw, bh, pad = self.note_layout(s, "sum", fs, w)
                if len(lines) > 2:
                    continue
                y = self.bot - bh - 3
                while y > self.bot - 70:
                    for x in (30, 14, 60, 120, 200, 300, self.W - bw - 14):
                        if x + bw > self.W - 12:
                            continue
                        if self.mask.free("flt", x - 2, y - 2, x + bw + 2, y + bh + 2):
                            self.draw_note(x, y, bw, bh, lines, fs, pad, "sum")
                            return
                    y -= 3
        # title-right area
        title = [p for p in self.paras.values() if p["size"] >= TITLE_SIZE]
        if title:
            tb = title[0]["bbox"]
            for fs in (8.5 * K, 8 * K, 7.5 * K, 7 * K):
                for w in (self.W - 14 - tb[2] - 12, 300 * K, 240 * K, 200 * K):
                    if w < 150:
                        continue
                    lines, bw, bh, pad = self.note_layout(s, "sum", fs, w)
                    x = self.W - 14 - bw
                    for y in (tb[1] + 2, tb[1] - 4, tb[1] + 8, self.top + 5, tb[3] - bh):
                        if x > tb[2] + 8 and self.mask.free("flt", x - 2, y - 2, x + bw + 2, y + bh + 2):
                            self.draw_note(x, y, bw, bh, lines, fs, pad, "sum")
                            return
        if not self.place_note(dict(ko=s, kind="sum", at=None, pos="b")):
            self.warn("unplaced_sum", s[:40])

    def place_note(self, n):
        kind = n.get("kind", "key")
        if kind not in KIND:
            self.warn("bad_kind", kind); kind = "key"
        _, fill, border, _ = KIND[kind]
        ats = n.get("at")
        multi = isinstance(ats, list) and not (len(ats) == 4 and all(isinstance(v, (int, float)) for v in ats))
        anchors = []
        for a in (ats if multi else [ats]):
            rs, typ = self.resolve(a)
            if rs:
                anchors.append((rs, typ))
        ko = body(n)
        label = n.get("label")
        if "box" in n:
            x, y, w = n["box"][:3]
            fs = n.get("fs", 8.5 * K)
            lines, bw, bh, pad = self.note_layout(ko, kind, fs, w, label)
            rr = fitz.Rect(x, y, x + bw, y + bh)
            info = self.arrow_info(rr, anchors, n)
            r = self.draw_note(x, y, bw, bh, lines, fs, pad, kind)
            self.arrows(r, anchors, border, n, info)
            return True
        pos = n.get("pos", "")
        arects = [r for rs, _ in anchors[:1] for r in rs]
        best = None
        fss = [n["fs"]] if n.get("fs") else [9.0 * K, 8.5 * K, 8.0 * K, 7.5 * K, 7.0 * K]
        for fs in fss:
            for w in (200 * K, 160 * K, 250 * K, 130 * K, 310 * K, 110 * K, 380 * K, 480 * K):
                lines, bw, bh, pad = self.note_layout(ko, kind, fs, w, label)
                if len(lines) > 9:
                    continue
                xs, ys = self.mask.free_positions("flt", bw + 5, bh + 5)
                if len(xs) == 0:
                    continue
                X0, Y0 = xs + 2.5, ys + 2.5
                X1, Y1 = X0 + bw, Y0 + bh
                cxs, cys = (X0 + X1) / 2, (Y0 + Y1) / 2
                if arects:
                    d = None
                    for a in arects:
                        dx = np.maximum(np.maximum(a.x0 - X1, X0 - a.x1), 0)
                        dy = np.maximum(np.maximum(a.y0 - Y1, Y0 - a.y1), 0)
                        dd = np.hypot(dx, dy)
                        d = dd if d is None else np.minimum(d, dd)
                    sc = d + np.where(d < 5, 40, 0)
                    ub = arects[0]
                    acx, acy = (ub.x0 + ub.x1) / 2, (ub.y0 + ub.y1) / 2
                    if "r" in pos: sc = sc + np.where(X0 < ub.x1, 90, 0)
                    if "l" in pos: sc = sc + np.where(X1 > ub.x0, 90, 0)
                    if "t" in pos: sc = sc + np.where(Y1 > ub.y0, 90, 0)
                    if "b" in pos: sc = sc + np.where(Y0 < ub.y1, 90, 0)
                else:
                    tx = self.W - 20 if "l" not in pos else 20
                    ty = 60 if "t" in pos else self.bot - 10
                    sc = np.hypot(cxs - tx, cys - ty) * 0.5
                sc = sc + (9.0 - fs / K) * 40 + max(0, len(lines) - 5) * 8 + (12 if len(lines) == 1 and bw > 330 * K else 0) + (35 if bh > bw * 0.75 else 0) + (15 if bw < 125 * K and len(lines) > 2 else 0)
                order = np.argsort(sc)[:24]
                for i in order:
                    s = float(sc[i])
                    r = fitz.Rect(X0[i], Y0[i], X1[i], Y1[i])
                    if arects and n.get("arrow", True):
                        a, b = self.arrow_pts(r, arects)
                        s += self.mask.ink_on_segment(a, b) * 3.5 + (20 if math.hypot(b[0] - a[0], b[1] - a[1]) > 95 else 0)
                    if best is None or s < best[0]:
                        best = (s, r, lines, fs, pad)
            if best is not None and best[0] < 60 + (9.0 - fs / K) * 40:
                break
        if best is None:
            return False
        _, r, lines, fs, pad = best
        info = self.arrow_info(r, anchors, n)
        self.draw_note(r.x0, r.y0, r.width, r.height, lines, fs, pad, kind)
        self.arrows(r, anchors, border, n, info)
        return True

    def arrow_info(self, box, anchors, n):
        out = []
        for rs, typ in anchors:
            s, t = self.arrow_pts(box, rs)
            L = math.hypot(t[0] - s[0], t[1] - s[1])
            ink = self.mask.ink_on_segment(s, t) if L >= 7 else 0
            out.append((s, t, L, ink))
        return out

    def badge(self, cx, cy, num, color):
        self.shape.draw_circle((cx, cy), 4.4)
        self.shape.finish(color=(1, 1, 1), fill=color, width=0.7)
        t = str(num)
        fs = 6.6
        w = tlen(t, True, fs)
        self.paint.text(cx - w / 2, cy - fs * 0.90 + fs * 0.36, [([(t, True, (1, 1, 1), w)], w)], fs)

    def badge_at_anchor(self, rs, typ, num, color, box):
        a = rs[0]
        bc = ((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
        if typ == "rect":
            # nearest corner of the dashed outline to the note
            cx = a.x0 - 2 if bc[0] < (a.x0 + a.x1) / 2 else a.x1 + 2
            cy = a.y0 - 2 if bc[1] < (a.y0 + a.y1) / 2 else a.y1 + 2
        else:
            cands = [(a.x0 - 6, (a.y0 + a.y1) / 2), (a.x1 + 6, (a.y0 + a.y1) / 2), (a.x0 + 2, a.y0 - 3), (a.x1 - 2, a.y0 - 3)]
            cx, cy = a.x1 + 5, a.y0 + 2
            for c in cands:
                if self.mask.free("raw", c[0] - 4.6, c[1] - 4.6, c[0] + 4.6, c[1] + 4.6):
                    cx, cy = c
                    break
        self.badge(cx, cy, num, color)
        self.mask.mark(fitz.Rect(cx - 4.5, cy - 4.5, cx + 4.5, cy + 4.5), pad=0.5)

    @staticmethod
    def arrow_pts(box, arects):
        bc = ((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
        bestp = None
        for a in arects:
            t0 = clamp_pt(bc, a)
            s = clamp_pt(t0, box)
            t = clamp_pt(s, a)
            d = math.hypot(t[0] - s[0], t[1] - s[1])
            if bestp is None or d < bestp[0]:
                bestp = (d, s, t, a)
        _, s, t, a = bestp
        # aim a bit inside long anchors rather than at the extreme corner
        if a.width > 30:
            t = (min(max(t[0], a.x0 + 6), a.x1 - 6), t[1])
        return s, t

    def arrows(self, box, anchors, color, n, info=None):
        if info is None:
            info = self.arrow_info(box, anchors, n)
        used_badge = False
        for (rs, typ), (s, t, L, ink) in zip(anchors, info):
            if typ == "rect":
                for r in rs:
                    rr = fitz.Rect(r.x0 - 2, r.y0 - 2, r.x1 + 2, r.y1 + 2)
                    rrect(self.shape, rr, None, color, width=0.9, rad=4, dashes="[3 2] 0")
            elif typ == "text":
                for r in rs:
                    y = r.y1 - r.height * 0.07
                    self.shape.draw_line((r.x0, y), (r.x1, y))
                    self.shape.finish(color=color, width=1.1)
            if not n.get("arrow", True) or L < 7:
                continue
            if L > 95 or ink > 5 or n.get("badge"):
                if not used_badge:
                    self.badge_n += 1
                    used_badge = True
                    self.badge(box.x0 - 4.5, box.y0 + 6.5, self.badge_n, color)
                self.badge_at_anchor(rs, typ, self.badge_n, color, box)
                continue
            ux, uy = (t[0] - s[0]) / L, (t[1] - s[1]) / L
            t = (t[0] - ux * 1.5, t[1] - uy * 1.5)
            draw_arrow(self.shape, s, t, color)

    def do_notes(self):
        for n in self.spec.get("notes", []):
            if not self.place_note(n):
                self.warn("unplaced_note", body(n)[:40])

    def run(self):
        if self.mask.ink_ratio > 0.5:
            self.warn("dark_page", f"ink {self.mask.ink_ratio:.2f}")
        self.do_hl()
        self.do_tr()
        self.do_sum()
        self.do_notes()
        self.shape_under.commit(overlay=True)
        self.shape.commit(overlay=True)
        self.paint.flush()


def load_spec(lec):
    spec = {}
    for f in sorted(glob.glob(f"spec/{lec}/*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception as e:
            sys.exit(f"!! JSON error in {f}: {e}")
        for k, v in d.items():
            if k in spec:
                print("!! duplicate page", k, "in", f)
            spec[k] = v
    return spec


def main():
    ap = argparse.ArgumentParser(description="Overlay study annotations on a slide PDF (run from the work folder).")
    ap.add_argument("pdf", help="original slide PDF")
    ap.add_argument("--pages", metavar="A-B", help="render only this page range into out/_partial_*.pdf")
    ap.add_argument("--noprev", action="store_true", help="skip the preview PNGs")
    ap.add_argument("--strict", action="store_true", help="exit with status 1 if any non-info issue remains")
    args = ap.parse_args()
    lec = name_of(args.pdf)
    pr = None
    if args.pages:
        a, _, b = args.pages.partition("-")
        pr = (int(a), int(b or a))
    spec = load_spec(lec)
    if not spec:
        sys.exit(f"no spec found: spec/{lec}/*.json (run this from the work folder)")
    deck = Deck(args.pdf)
    doc = deck.doc
    overlay, stamps = None, []
    issues = []
    for i in range(len(doc)):
        pno = i + 1
        if pr and not (pr[0] <= pno <= pr[1]):
            continue
        sp = spec.get(str(pno))
        if not sp:
            issues.append(dict(page=pno, kind="no_spec", msg=""))
            continue
        page, k = deck.layout_page(i)
        if k == 1.0:
            PageR(page, pno, sp, issues).run()
            continue
        # unusual page size: lay out on the scaled copy, draw on a blank page, stamp that onto the original
        if overlay is None:
            overlay = fitz.open()
        canvas = overlay.new_page(width=page.rect.width, height=page.rect.height)
        PageR(page, pno, sp, issues, canvas=canvas).run()
        stamps.append((i, canvas.number))
    for i, j in stamps:
        if overlay[j].get_contents():
            doc[i].show_pdf_page(doc[i].rect, overlay, j)
    os.makedirs("out", exist_ok=True)
    os.makedirs(f"prev/{lec}", exist_ok=True)
    os.makedirs("report", exist_ok=True)
    tag = f"_{pr[0]}-{pr[1]}" if pr else ""
    if pr:
        doc.select(list(range(pr[0] - 1, min(pr[1], len(doc)))))
        outp = f"out/_partial_{lec}{tag}.pdf"
    else:
        outp = f"out/{lec}{SUFFIX}.pdf"
    doc.subset_fonts()
    doc.save(outp, garbage=4, deflate=True)
    if not args.noprev:
        d2 = fitz.open(outp)
        base = pr[0] if pr else 1
        for j, page in enumerate(d2):
            z = 1200 / 720 * page_scale(page)
            page.get_pixmap(matrix=fitz.Matrix(z, z)).save(f"prev/{lec}/p{base + j:02d}.png")
    json.dump(issues, open(f"report/{lec}{tag}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    bad = [x for x in issues if not x["kind"].startswith("info")]
    print(f"{lec}: saved {outp} ({os.path.getsize(outp) // 1024} KB); issues {len(bad)} (+{len(issues) - len(bad)} info)")
    for x in issues:
        print(f"  p{x['page']:>2} {x['kind']}: {x['msg']}")
    if args.strict and bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
