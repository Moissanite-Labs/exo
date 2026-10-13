#!/usr/bin/env python3
"""Generate the Exo brand assets in site/brand/ (D-049, revised 2026-10-13; docs/BRAND.md).

    python3 scripts/brand_assets.py          SVG marks, wordmarks, terminal marks, the nav fragment in every page
    python3 scripts/brand_assets.py --png    also render the PNG icons and the og image with headless Chrome

Everything is computed from one construction (credit: aska-digital/incentre-lattice, origin.html, figure 1):
a square from (0,0) to (2,2), both diagonals, and in each of the four right triangles its incentre, the point
equally far from all three sides. Each incentre is joined to its triangle's two square corners (the figure's red
lines) and to the centre (the amber line); those three lines are the triangle's angle bisectors. The incentre sits
R = sqrt(2) - 1 = tan 22.5 deg above its side, and the bisector from a base corner rises at 22.5 deg. The incentres
are computed below with the incentre formula and checked against those closed forms, so nothing is eyeballed.

The wordmark is "3X0": the E drawn as a 3 (a mirrored E), the X as the square's diagonals with its top half orange
(the V), the O as a zero. Its edges run at 0, 45 and 90 deg, and 22.5 deg where a variant uses the bisector angle.
No third-party code; standard library only.
"""
from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
OUT = SITE / "brand"

R = math.sqrt(2) - 1          # incentre height in a square of side 2; also tan 22.5 deg
T22 = math.tan(math.radians(22.5))
BG, BG2, LINE2 = "#08090a", "#0e1012", "#343a41"
FG, FG2, FG3 = "#eceae4", "#b4b3ad", "#8b8b86"
ACC, ACC2, COLD = "#ff5b14", "#ff8a4c", "#9fb4c4"
AMBER = "#f2b25c"             # the figure's amber line (dot to centre), a soft amber beside the accent; mark only


def n(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def pts(poly) -> str:
    return " ".join(f"{n(x)},{n(y)}" for x, y in poly)


def clip(poly, a, b, c):
    """Keep the part of a convex polygon where a*x + b*y <= c (Sutherland-Hodgman, one half-plane)."""
    out = []
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        fp, fq = a * p[0] + b * p[1] - c, a * q[0] + b * q[1] - c
        if fp <= 0:
            out.append(p)
        if (fp < 0 < fq) or (fq < 0 < fp):
            t = fp / (fp - fq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def svg(w, h, body, extra="", vb=None):
    vb = vb or f"0 0 {n(w)} {n(h)}"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" width="{n(w)}" height="{n(h)}"{extra}>\n'
            f"{body}\n</svg>\n")


# ---------------------------------------------------------------- the construction (units: square side 2, y up)
def _d(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


def incentre(a, b, c):
    """Weighted by the opposite sides: (|BC| A + |CA| B + |AB| C) / perimeter."""
    la, lb, lc = _d(b, c), _d(c, a), _d(a, b)
    p = la + lb + lc
    return ((la * a[0] + lb * b[0] + lc * c[0]) / p, (la * a[1] + lb * b[1] + lc * c[1]) / p)


CORNERS = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
CENTRE = (1.0, 1.0)
SIDES = [(CORNERS[k], CORNERS[(k + 1) % 4]) for k in range(4)]      # bottom, right, top, left
DOTS = [incentre(a, b, CENTRE) for a, b in SIDES]
for _got, _want in zip(DOTS, [(1, R), (2 - R, 1), (1, 2 - R), (R, 1)]):
    assert _d(_got, _want) < 1e-12, (_got, _want)
assert abs(math.degrees(math.atan2(DOTS[0][1], DOTS[0][0])) - 22.5) < 1e-12   # the base-corner bisector rises at 22.5 deg
RED = [(DOTS[k], c) for k, (a, b) in enumerate(SIDES) for c in (a, b)]        # dot to its two square corners
AMB = [(DOTS[k], CENTRE) for k in range(4)]                                   # dot to the centre


def construction_table():
    """The exact coordinates as table rows, for the brand page (between <!-- construction -->) and docs/BRAND.md."""
    names = ["Bottom", "Right", "Top (in the V)", "Left"]
    exact = ["(1, √2 − 1)", "(3 − √2, 1)", "(1, 3 − √2)", "(√2 − 1, 1)"]
    g = lambda v: str(int(v)) if v == int(v) else f"{v:.4f}"  # noqa: E731
    f = lambda p: f"({g(p[0])}, {g(p[1])})"  # noqa: E731
    rows = []
    for k, (d, (a, b)) in enumerate(zip(DOTS, SIDES)):
        ang = math.degrees(math.atan2(abs(d[1] - a[1]) if k in (0, 2) else abs(d[0] - a[0]),
                                      abs(d[0] - a[0]) if k in (0, 2) else abs(d[1] - a[1])))
        rows.append(f"<tr><td>{names[k]}</td><td>{exact[k]}</td><td>{f(d)}</td><td>{f(a)}, {f(b)}</td>"
                    f"<td>{f(CENTRE)}</td><td>{ang:.1f}°</td></tr>")
    return "\n".join(rows)


# ---------------------------------------------------------------- the mark
def mark_body(size=100.0, inset=8.0, sq=1.2, xw=1.2, vw=2.4, rw=1.6, aw=1.6, cw=0.9, circles=True,
              square=FG3, legs=FG2, red=ACC, amber=AMBER, circle=AMBER, co=.6, cid="m"):
    """The figure in a size x size box, with no dots: each incentre is only where its triangle's three bisectors meet.

    circles=True (large sizes): the square, the X with its V, the twelve bisectors and the four incircles.
    circles=False (small sizes): the square, the X with its V and the twelve bisectors."""
    s = (size - 2 * inset) / 2
    P = lambda u, v: (inset + s * u, size - inset - s * v)  # noqa: E731  (v up)
    seg = lambda p, q, col, w, extra="": (  # noqa: E731
        f'<line x1="{n(P(*p)[0])}" y1="{n(P(*p)[1])}" x2="{n(P(*q)[0])}" y2="{n(P(*q)[1])}" stroke="{col}" stroke-width="{n(w)}"{extra}/>')
    out = []
    if circles:                                  # centre the incentre, radius R: tangent to all three sides
        for d in DOTS:
            dp = P(*d)
            out.append(f'<circle cx="{n(dp[0])}" cy="{n(dp[1])}" r="{n(R * s)}" fill="none" stroke="{circle}" '
                       f'stroke-opacity="{co}" stroke-width="{n(cw)}"/>')
    # the X, clipped to the square so its 45-degree ends stay inside: lower legs quiet, the V on top in orange
    out.append(f'<clipPath id="{cid}"><polygon points="{pts([P(*k) for k in CORNERS])}"/></clipPath><g clip-path="url(#{cid})">')
    for corner in CORNERS[:2]:
        out.append(seg(CENTRE, (2 * corner[0] - 1, 2 * corner[1] - 1), legs, xw))
    out.append("</g>")
    out.append(f'<polygon fill="none" stroke="{square}" stroke-width="{n(sq)}" points="{pts([P(*c) for c in CORNERS])}"/>')
    # the twelve bisectors, the focus: each ends at its incentre, so the three of a triangle meet there
    for p, q in AMB:
        out.append(seg(p, q, amber, aw, ' stroke-linecap="round"'))
    for p, q in RED:
        out.append(seg(p, q, red, rw, ' stroke-linecap="round"'))
    # the V owns the crossing: it starts half a stroke below the centre
    out.append(f'<g clip-path="url(#{cid})">')
    k = vw / 2 / s / math.sqrt(2)
    for corner in CORNERS[2:]:
        out.append(seg((1 - (corner[0] - 1) * k, 1 - k), (2 * corner[0] - 1, 2 * corner[1] - 1), ACC, vw))
    out.append("</g>")
    return "".join(out)


FULL = dict(inset=8, sq=1.2, xw=1.2, vw=2.8, rw=1.6, aw=1.6, cw=0.9, circles=True)
SMALL = dict(inset=9, sq=3.2, xw=3.2, vw=6.4, rw=3.4, aw=3.4, circles=False, red=ACC2, amber=ACC2)  # one tint: the V stays the strongest orange
LIGHT = dict(square=FG3, legs="#5a5a56", amber="#d98a2b", circle="#d98a2b")
SIZE16 = dict(inset=0.5, sq=1, xw=1, vw=1.8, rw=1, aw=1, circles=False, red=ACC2, amber=ACC2)
CIRCLES_MIN = 96                              # px: below this the incircles crowd the bisectors (actual-size renders)


def tile(size, inner, radius_frac=0.0):
    rx = f' rx="{n(size * radius_frac)}"' if radius_frac else ""
    return f'<rect width="{n(size)}" height="{n(size)}" fill="{BG}"{rx}/>' + inner


def favicon16():
    """16 px: the small mark with the square on the tile's outer pixel ring (one unit = 7.5 px), lines at 1 px."""
    return svg(16, 16, tile(16, mark_body(16, cid="f16", **SIZE16)))


def icon(size, radius_frac=0.0):
    """Icons: the large mark (with incircles) from CIRCLES_MIN px up, the small mark below."""
    k = size / 100
    if size >= CIRCLES_MIN:
        body = mark_body(100, cid=f"i{size}", **{**FULL, "inset": 12, "sq": 1.6, "xw": 1.6, "vw": 3.0, "rw": 2.0, "aw": 2.0, "cw": 1.1})
    else:
        body = mark_body(100, cid=f"i{size}", **SMALL)
    return svg(size, size, tile(size, f'<g transform="scale({n(k)})">{body}</g>', radius_frac))


# ---------------------------------------------------------------- the wordmark "3X0"
# Cap height HC, stroke T. The X is the drawing's X: two strokes at exactly 45 degrees with flat horizontal
# terminals; its top half, which owns the crossing, is orange and reads as a V.
HC, T = 28.0, 5.0
GAP = 4.0


def glyph_x(x0, h=HC, t=T, gap=0.0, colour_v=ACC, colour_rest=FG):
    """The X at left edge x0, height h, stroke t (measured square to the stroke). Returns (svg, width)."""
    hh = t / math.sqrt(2)                        # half the horizontal width of a 45-degree stroke
    box = [(x0 - 50, 0), (x0 + h + 80, 0), (x0 + h + 80, h), (x0 - 50, h)]
    u0 = x0 + hh
    d1 = clip(clip(box, 1, -1, u0 + hh), -1, 1, -u0 + hh)
    d2 = clip(clip(box, 1, 1, u0 + h + hh), -1, -1, -(u0 + h - hh))
    k = h + hh                                   # the V owns the crossing: cut along the far edge of the other band
    v1 = clip(d1, 1, 1, u0 + k)
    v2 = clip(d2, -1, 1, -u0 + hh)
    g = gap * math.sqrt(2)
    l1 = clip(d1, -1, -1, -(u0 + k + g))
    l2 = clip(d2, 1, -1, u0 - hh - g)
    return (f'<polygon fill="{colour_rest}" points="{pts(l1)}"/><polygon fill="{colour_rest}" points="{pts(l2)}"/>'
            f'<polygon fill="{colour_v}" points="{pts(v1)}"/><polygon fill="{colour_v}" points="{pts(v2)}"/>'), h + 2 * hh


def glyph_3(x0, style, h=HC, t=T, colour=FG):
    """The E drawn as a 3: a mirrored E, the spine on the right. style: facet | block | bisector.

    facet: the two right corners cut at 45 degrees, outside and inside, so the stroke keeps its width round them.
    bisector: as facet, and the middle arm ends in a point whose edges run at 22.5 degrees, the angle at which the
    figure's bisector leaves a base corner."""
    w = h * 0.70
    c = 0.0 if style == "block" else w * (1 - R) / 2   # outer 45-degree cut, the same as the zero's
    ci = c + t * math.sqrt(2)                    # the inner cut line lies one stroke width inside the outer one
    m = w * 0.30                                 # where the middle arm stops (shorter than the bars, so it reads as 3)
    ym = h / 2
    top_in = [(w - t, t)] if style == "block" else [(w - t, ci - t), (w - ci + t, t)]
    bot_in = [(w - t, h - t)] if style == "block" else [(w - ci + t, h - t), (w - t, h - ci + t)]
    outer = [(0, 0), (w, 0), (w, h), (0, h)] if style == "block" else [(0, 0), (w - c, 0), (w, c), (w, h - c), (w - c, h), (0, h)]
    if style == "bisector":
        tip = (t / 2) / T22                      # the taper length of a point with 22.5-degree edges
        arm = [(w - t, ym + t / 2), (m + tip, ym + t / 2), (m, ym), (m + tip, ym - t / 2), (w - t, ym - t / 2)]
    else:
        arm = [(w - t, ym + t / 2), (m, ym + t / 2), (m, ym - t / 2), (w - t, ym - t / 2)]
    poly = outer + [(0, h - t)] + bot_in + arm + top_in + [(0, t)]
    poly = [(x0 + x, y) for x, y in poly]
    return f'<polygon fill="{colour}" points="{pts(poly)}"/>', w


def _octa(x0, y0, w, h, c):
    return [(x0 + c, y0), (x0 + w - c, y0), (x0 + w, y0 + c), (x0 + w, y0 + h - c),
            (x0 + w - c, y0 + h), (x0 + c, y0 + h), (x0, y0 + h - c), (x0, y0 + c)]


def glyph_0(x0, style, h=HC, t=T, colour=FG):
    """The O drawn as a zero, narrower than it is tall. style: facet | block | incircle | bisector."""
    w = h * 0.70
    if style == "incircle":                      # a stadium: two half incircles joined by straight sides, a dotted zero
        r1, r2 = w / 2, w / 2 - t
        cx = x0 + w / 2
        d = (f"M{n(x0)},{n(r1)} A{n(r1)},{n(r1)} 0 0,1 {n(x0 + w)},{n(r1)} V{n(h - r1)} A{n(r1)},{n(r1)} 0 0,1 {n(x0)},{n(h - r1)}Z "
             f"M{n(cx - r2)},{n(r1)} V{n(h - r1)} A{n(r2)},{n(r2)} 0 0,0 {n(cx + r2)},{n(h - r1)} V{n(r1)} A{n(r2)},{n(r2)} 0 0,0 {n(cx - r2)},{n(r1)}Z")
        dot = f'<circle fill="{colour}" cx="{n(cx)}" cy="{n(h / 2)}" r="{n(t * 0.55)}"/>'
        return f'<path fill="{colour}" fill-rule="evenodd" d="{d}"/>{dot}', w
    c = 0.0 if style == "block" else w * (1 - R) / 2     # top flat = w * R, the incentre number again
    ci = max(0.0, c - t * (2 - math.sqrt(2)))            # the inner cut, one stroke width inside the outer one
    o, i = _octa(x0, 0, w, h, c), _octa(x0 + t, t, w - 2 * t, h - 2 * t, ci)
    d = "M" + " L".join(f"{n(x)},{n(y)}" for x, y in o) + "Z M" + " L".join(f"{n(x)},{n(y)}" for x, y in reversed(i)) + "Z"
    return f'<path fill="{colour}" fill-rule="evenodd" d="{d}"/>', w


VARIANTS = ("facet", "block", "incircle", "bisector")


def wordmark(variant, on="dark", pad=0.0, h=HC, t=T, gap=GAP):
    fg = FG if on == "dark" else BG
    parts, x = [], pad
    g3 = glyph_3(x, "facet" if variant == "incircle" else variant, h, t, fg)
    parts.append(g3[0]); x += g3[1] + gap
    gx = glyph_x(x, h, t, colour_rest=fg)
    parts.append(gx[0]); x += gx[1] + gap
    g0 = glyph_0(x, variant, h, t, fg)
    parts.append(g0[0]); x += g0[1] + pad
    return x, "".join(parts)


def glyph_xmark(x0, frame, size, h=HC, t=T, colour=FG, cid="xm"):
    """The X of 3X0 drawn as the mark. frame: True puts the square round it. size: "lg" adds the incircles,
    "sm" is the X and the twelve bisectors, "xs" falls back to the plain X with its V (glyph_x).

    The figure's square is the X's centre-line box: its corners are the centres of the X's four flat ends, so the
    bisectors start exactly where the strokes end. Returns (svg, width)."""
    if size == "xs":
        return glyph_x(x0, h, t, colour_rest=colour)
    xt = t * (0.42 if frame else 0.6)            # the X is thinner than the letters: the line work inside carries weight
    lw = t * (0.17 if size == "lg" else 0.24)    # the bisectors
    if frame:
        fw = t * 0.42
        side = h - fw                            # the frame's outer edge is the cap height
        ox, oy = x0 + fw / 2, fw / 2
        width = h
    else:
        hh = xt / math.sqrt(2)
        side, ox, oy, width = h, x0 + hh, 0.0, h + 2 * hh
    u = side / 2
    P = lambda a, b: (ox + a * u, oy + (2 - b) * u)  # noqa: E731
    seg = lambda p, q, col, w, cap="round": (  # noqa: E731
        f'<line x1="{n(P(*p)[0])}" y1="{n(P(*p)[1])}" x2="{n(P(*q)[0])}" y2="{n(P(*q)[1])}" stroke="{col}" stroke-width="{n(w)}" stroke-linecap="{cap}"/>')
    red, amber = (ACC, AMBER) if size == "lg" else (ACC2, ACC2)
    out = []
    if size == "lg":
        for d in DOTS:
            dp = P(*d)
            out.append(f'<circle cx="{n(dp[0])}" cy="{n(dp[1])}" r="{n(R * u)}" fill="none" stroke="{AMBER}" stroke-opacity=".7" stroke-width="{n(lw * .7)}"/>')
    if frame:
        out.append(f'<clipPath id="{cid}"><polygon points="{pts([P(*k) for k in CORNERS])}"/></clipPath><g clip-path="url(#{cid})">')
        for corner in CORNERS[:2]:
            out.append(seg(CENTRE, (2 * corner[0] - 1, 2 * corner[1] - 1), colour, xt, "butt"))
        out.append("</g>")
        out.append(f'<polygon fill="none" stroke="{colour}" stroke-width="{n(fw)}" points="{pts([P(*c) for c in CORNERS])}"/>')
    else:                                         # the letter's own lower legs, flat ends, 45 degrees
        g, _ = glyph_x(ox - xt / math.sqrt(2), h, xt, colour_v="none", colour_rest=colour)
        out.append(g)
    for p, q in AMB:
        out.append(seg(p, q, amber, lw))
    for p, q in RED:
        out.append(seg(p, q, red, lw))
    if frame:
        out.append(f'<g clip-path="url(#{cid})">')
        k = xt * 1.4 / 2 / u / math.sqrt(2)
        for corner in CORNERS[2:]:
            out.append(seg((1 - (corner[0] - 1) * k, 1 - k), (2 * corner[0] - 1, 2 * corner[1] - 1), ACC, xt * 1.4, "butt"))
        out.append("</g>")
    else:
        g, _ = glyph_x(ox - xt / math.sqrt(2), h, xt, colour_v=ACC, colour_rest="none")
        out.append(g)
    return "".join(out), width


MARKWORDS = ("framed", "open")


def markword(variant, size, on="dark", h=HC, t=T, gap=GAP, cid=None):
    """3 + the mark + 0 (facet 3 and 0). variant: framed | open. size: lg | sm | xs."""
    fg = FG if on == "dark" else BG
    parts, x = [], 0.0
    g3 = glyph_3(x, "facet", h, t, fg)
    parts.append(g3[0]); x += g3[1] + gap
    gm = glyph_xmark(x, variant == "framed", size, h, t, fg, cid or f"xm-{variant}-{size}-{on}")
    parts.append(gm[0]); x += gm[1] + gap
    g0 = glyph_0(x, "facet", h, t, fg)
    parts.append(g0[0]); x += g0[1]
    return x, "".join(parts)


def label(on="dark", h=20.0):
    """The spaced label form, "3 X 0", for the nav and the terminal: the facet glyphs, lighter, a wide gap."""
    t = h * 0.18
    fg = FG if on == "dark" else BG
    parts, x = [], 0.0
    for kind in ("3", "x", "0"):
        if kind == "3":
            g = glyph_3(x, "facet", h, t, fg)
        elif kind == "x":
            g = glyph_x(x, h, t, colour_rest=fg)
        else:
            g = glyph_0(x, "facet", h, t, fg)
        parts.append(g[0]); x += g[1] + h * 0.55
    return x - h * 0.55, h, "".join(parts)


# ---------------------------------------------------------------- the construction panels (brand page, section 1)
def panel(step, u=60, o=20):
    """Step 1..6 of the construction, in a (4u + 2o) square box. y down on screen, v up in the figure."""
    P = lambda a, b: (o + a * u, o + (2 - b) * u)  # noqa: E731
    out = []
    line = lambda p, q, col, w, extra="": out.append(  # noqa: E731
        f'<line x1="{n(P(*p)[0])}" y1="{n(P(*p)[1])}" x2="{n(P(*q)[0])}" y2="{n(P(*q)[1])}" stroke="{col}" stroke-width="{w}" stroke-linecap="round"{extra}/>')
    out.append(f'<polygon fill="none" stroke="{FG2}" stroke-width="1.4" points="{pts([P(*c) for c in CORNERS])}"/>')
    if step >= 2:
        for corner in CORNERS:
            top = corner[1] == 2
            col, w = (ACC, 3.2) if (step >= 6 and top) else (FG2, 1.4)
            line(CENTRE, corner, col, w)
    if step == 3 or step == 4:                   # one triangle: A = (0,0), B = (2,0), C = (1,1)
        tri = [P(0, 0), P(2, 0), P(1, 1)]
        out.insert(0, f'<polygon fill="{COLD}" fill-opacity=".10" points="{pts(tri)}"/>')
        d = DOTS[0]
        for p in ((0, 0), (2, 0), CENTRE):
            line(p, d, ACC2 if step == 3 else (ACC if p != CENTRE else AMBER), 1.4)
        if step == 3:                            # the 22.5-degree arcs at A
            ax, ay = P(0, 0)
            for rr, a1, a2 in ((u * .55, 0, 22.5), (u * .68, 22.5, 45)):
                x1, y1 = ax + rr * math.cos(math.radians(a1)), ay - rr * math.sin(math.radians(a1))
                x2, y2 = ax + rr * math.cos(math.radians(a2)), ay - rr * math.sin(math.radians(a2))
                out.append(f'<path d="M{n(x1)},{n(y1)} A{n(rr)},{n(rr)} 0 0,0 {n(x2)},{n(y2)}" fill="none" stroke="{COLD}" stroke-width="1"/>')
            out.append(f'<text x="{n(ax + u * .62)}" y="{n(ay - 4)}" fill="{COLD}" font-family="ui-monospace,Menlo,monospace" font-size="9">22.5°</text>')
        if step == 4:
            dp = P(*d)
            out.append(f'<circle cx="{n(dp[0])}" cy="{n(dp[1])}" r="{n(R * u)}" fill="none" stroke="{AMBER}" stroke-opacity=".8" stroke-width="1"/>')
            out.append(f'<line x1="{n(dp[0])}" y1="{n(dp[1])}" x2="{n(dp[0])}" y2="{n(P(1, 0)[1])}" stroke="{AMBER}" stroke-width="1" stroke-dasharray="2 2"/>')
            out.append(f'<text x="{n(dp[0] + 5)}" y="{n(dp[1] + R * u * .62)}" fill="{AMBER}" font-family="ui-monospace,Menlo,monospace" font-size="9">√2−1</text>')
    if step >= 5:                                # all four triangles: incircles, then the twelve bisectors
        for d in DOTS:
            dp = P(*d)
            out.append(f'<circle cx="{n(dp[0])}" cy="{n(dp[1])}" r="{n(R * u)}" fill="none" stroke="{AMBER}" stroke-opacity=".55" stroke-width="1"/>')
        for p, q in RED:
            line(p, q, ACC, 1.3)
        for p, q in AMB:
            line(p, q, AMBER, 1.3)
        if step >= 6:                            # redraw the V on top so it owns the crossing
            for corner in CORNERS[2:]:
                line(CENTRE, corner, ACC, 3.2)
    return svg(2 * u + 2 * o, 2 * u + 2 * o, "".join(out))


def lattice_panel():
    """The figure tiled: the incentres round each shared corner make a regular octagon (the 4.8.8 tiling)."""
    u2, body = 24, ""
    for i in range(4):
        for j in range(3):
            ox, oy = 8 + i * 2 * u2, 8 + j * 2 * u2
            P = lambda a, b: (ox + a * u2, oy + (2 - b) * u2)  # noqa: E731
            hl = i == 1 and j == 1
            col = FG3 if hl else LINE2
            body += f'<polygon fill="none" stroke="{col}" stroke-width="1" points="{pts([P(*c) for c in CORNERS])}"/>'
            for corner in CORNERS:
                a, b = P(*CENTRE), P(*corner)
                cc, w = (ACC, 2.4) if (hl and corner[1] == 2) else (col, 1)
                body += f'<line x1="{n(a[0])}" y1="{n(a[1])}" x2="{n(b[0])}" y2="{n(b[1])}" stroke="{cc}" stroke-width="{w}"/>'
            for p, q in RED + AMB:
                a, b = P(*p), P(*q)
                cc = (ACC if (p, q) in RED else AMBER) if hl else col
                body += f'<line x1="{n(a[0])}" y1="{n(a[1])}" x2="{n(b[0])}" y2="{n(b[1])}" stroke="{cc}" stroke-opacity="{.9 if hl else .8}" stroke-width=".9"/>'
    for cx, cy in [(8 + 2 * u2 * i, 8 + 2 * u2 * j) for i in (1, 2, 3) for j in (1, 2)]:
        rr = u2 * math.sqrt(1 + R * R)
        ring = [(cx + rr * math.cos(math.radians(45 * k) + math.atan(R)), cy + rr * math.sin(math.radians(45 * k) + math.atan(R))) for k in range(8)]
        body += f'<polygon fill="none" stroke="{COLD}" stroke-opacity=".95" stroke-width="1.6" points="{pts(ring)}"/>'
    return svg(8 * u2 + 16, 6 * u2 + 16, body)


# ---------------------------------------------------------------- the terminal (braille)
def braille(segments, dots_pts, cols, rows, scale, ox, oy):
    """Rasterise segments and points into a braille grid of cols x rows cells (2 x 4 dots per cell)."""
    W2, H4 = cols * 2, rows * 4
    grid = [[0] * W2 for _ in range(H4)]
    hot = [[0] * W2 for _ in range(H4)]

    def plot(x, y, h):
        i, j = int(round(x)), int(round(y))
        if 0 <= i < W2 and 0 <= j < H4:
            grid[j][i] = 1
            hot[j][i] = hot[j][i] or h
    for (x1, y1), (x2, y2), h in segments:
        x1, y1, x2, y2 = ox + x1 * scale, oy + y1 * scale, ox + x2 * scale, oy + y2 * scale
        steps = int(max(abs(x2 - x1), abs(y2 - y1)) * 2) + 1
        for t in range(steps + 1):
            plot(x1 + (x2 - x1) * t / steps, y1 + (y2 - y1) * t / steps, h)
    for (x, y), h in dots_pts:
        for dx in (0, 1):
            for dy in (0, 1):
                plot(ox + x * scale + dx - .5, oy + y * scale + dy - .5, h)
    bits = [(0, 0, 0x01), (0, 1, 0x02), (0, 2, 0x04), (1, 0, 0x08), (1, 1, 0x10), (1, 2, 0x20), (0, 3, 0x40), (1, 3, 0x80)]
    lines, hots = [], []
    for r in range(rows):
        s, hs = "", ""
        for c in range(cols):
            v, h = 0, 0
            for dx, dy, bit in bits:
                if grid[r * 4 + dy][c * 2 + dx]:
                    v |= bit
                    h = h or hot[r * 4 + dy][c * 2 + dx]
            s += chr(0x2800 + v) if v else " "
            hs += "^" if h else "."
        lines.append(s.rstrip())
        hots.append(hs)
    return lines, hots


def tui_mark():
    """The small mark in braille, 12 x 6 cells: the square, the X with its V (orange cells) and the twelve bisectors."""
    flip = lambda p: (p[0], 2 - p[1])  # noqa: E731  (y down in the grid)
    segs = [(flip(a), flip(b), 0) for a, b in SIDES]
    segs += [(flip(CENTRE), flip(CORNERS[0]), 0), (flip(CENTRE), flip(CORNERS[1]), 0)]
    segs += [(flip(p), flip(q), 0) for p, q in RED + AMB]
    segs += [(flip(CENTRE), flip(CORNERS[2]), 1), (flip(CENTRE), flip(CORNERS[3]), 1)]
    return braille(segs, [], cols=12, rows=6, scale=11.0, ox=1, oy=1)


def tui_egg():
    # stage 0 of the egg, still: the centre cell's six incentre balls (an octahedron) inside an egg shell
    k = 2 - math.sqrt(2)
    ax, ay = 0.55, 0.42

    def pr(p):
        x, y, z = p
        x, z = x * math.cos(ax) + z * math.sin(ax), -x * math.sin(ax) + z * math.cos(ax)
        y, z = y * math.cos(ay) - z * math.sin(ay), y * math.sin(ay) + z * math.cos(ay)
        return (x, y)
    six = [(k, 0, 0), (-k, 0, 0), (0, k, 0), (0, -k, 0), (0, 0, k), (0, 0, -k)]
    segs = []
    for i in range(6):
        for j in range(i + 1, 6):
            if abs(sum(a * b for a, b in zip(six[i], six[j]))) < 1e-9:
                segs.append((pr(six[i]), pr(six[j]), 1))
    shell = []
    m = 40
    for i in range(m):
        t1, t2 = 2 * math.pi * i / m, 2 * math.pi * (i + 1) / m
        e = lambda t: (1.2 * math.cos(t) * (1 + 0.12 * math.sin(t)), 1.45 * math.sin(t))  # noqa: E731  (wider at the bottom)
        if i % 3 != 2:
            shell.append((e(t1), e(t2), 0))
    return braille(shell + segs, [(pr(p), 1) for p in six], cols=14, rows=7, scale=8.6, ox=14, oy=14)


def write_tui(name, lines, hots):
    (OUT / f"{name}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # an HTML twin that wraps the orange cells (the V) in <b>, for the brand page and the terminal client
    html = []
    for s, h in zip(lines, hots):
        row = ""
        for ch, f in zip(s.ljust(len(h)), h):
            row += f'<b>{ch}</b>' if f == "^" and ch != " " else ch
        html.append(row.rstrip())
    (OUT / f"{name}.html").write_text("\n".join(html) + "\n", encoding="utf-8")


def tui_wordmark():
    """The label form in the terminal: one line, "3 X 0". A cell cannot be half orange, so the whole X is."""
    return ["3 X 0"], ["..^.."]


# ---------------------------------------------------------------- the nav fragment (every page)
NAV_VARIANT = "open"
WORD_LG_MIN = 96                              # px tall: from here the wordmark's X shows the incircles
WORD_SM_MIN = 24                              # px tall: below this the twelve lines blur and the X is the plain X


def nav_fragment(i=0, variant=None):
    """Inline SVG for the site nav: the wordmark 3X0 with the mark as its X, 24 px tall (the "sm" form).
    Written between <!-- exo-brand --> and <!-- /exo-brand --> in every page, and into site/brand/nav-brand.html
    for the whitepaper builder."""
    w, body = markword(variant or NAV_VARIANT, "sm", "dark", cid=f"exo-nav-clip-{i}")   # one clip id per copy
    return f'<svg class="brand__word" viewBox="0 0 {n(w)} {n(HC)}" aria-hidden="true">{body}</svg>'


def splice(text, name, frag):
    """Replace what lies between <!-- name --> and <!-- /name -->. frag is a string or a function of the copy's index."""
    a, b = f"<!-- {name} -->", f"<!-- /{name} -->"
    out, i, count = [], 0, 0
    while True:
        j = text.find(a, i)
        if j < 0:
            out.append(text[i:])
            return "".join(out)
        k = text.index(b, j)
        out.append(text[i:j + len(a)] + (frag(count) if callable(frag) else frag))
        i, count = k, count + 1


# ---------------------------------------------------------------- output
def main(png=False):
    OUT.mkdir(parents=True, exist_ok=True)
    files = {}
    for variant in VARIANTS:
        for on in ("dark", "light"):
            wdt, body = wordmark(variant, on)
            files[f"wordmark-{variant}-{on}.svg"] = svg(wdt, HC, body)
    for variant in MARKWORDS:
        for size in ("lg", "sm"):
            for on in ("dark", "light"):
                wdt, body = markword(variant, size, on)
                files[f"wordmark-{variant}-{size}-{on}.svg"] = svg(wdt, HC, body)
    for on in ("dark", "light"):
        w, h, body = label(on)
        files[f"wordmark-label-{on}.svg"] = svg(w, h, body)
    files["mark.svg"] = svg(100, 100, mark_body(100, cid="mf", **FULL))
    files["mark-light.svg"] = svg(100, 100, mark_body(100, cid="mfl", **FULL, **LIGHT))
    files["mark-small.svg"] = svg(100, 100, mark_body(100, cid="ms", **SMALL))
    files["mark-small-light.svg"] = svg(100, 100, mark_body(100, cid="msl", **SMALL, **{k: v for k, v in LIGHT.items() if k in ("square", "legs")}))
    files["favicon-16.svg"] = favicon16()
    files["favicon.svg"] = icon(32)
    files["app-icon.svg"] = icon(1024)
    files["app-icon-round.svg"] = icon(1024, radius_frac=0.225)
    for i in range(1, 7):
        files[f"story-{i}.svg"] = panel(i)
    files["story-7.svg"] = lattice_panel()
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8")
    # the site root keeps a copy of the favicon so every page can link ../favicon.svg
    shutil.copyfile(OUT / "favicon.svg", SITE / "favicon.svg")
    write_tui("tui-mark", *tui_mark())
    write_tui("tui-egg", *tui_egg())
    write_tui("tui-wordmark", *tui_wordmark())
    (OUT / "nav-brand.html").write_text(nav_fragment() + "\n", encoding="utf-8")
    for page in sorted(SITE.rglob("*.html")):
        text = page.read_text(encoding="utf-8")
        new = splice(text, "exo-brand", nav_fragment)
        if page == OUT / "index.html":           # keep the brand page's terminal samples in step
            for name in ("tui-mark", "tui-egg", "tui-wordmark"):
                new = splice(new, name, (OUT / f"{name}.html").read_text(encoding="utf-8").rstrip("\n"))
            new = splice(new, "construction", "\n" + construction_table() + "\n")
        if new != text:
            page.write_text(new, encoding="utf-8")
    print(f"wrote {len(files)} svg files, 3 terminal marks and the nav fragment to {OUT.relative_to(ROOT)}")
    if png:
        render_pngs()


def chrome():
    for c in (os.environ.get("CHROME"), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              shutil.which("google-chrome"), shutil.which("chromium")):
        if c and Path(c).exists():
            return c
    sys.exit("brand_assets: no Chrome found (set CHROME)")


def shoot(page_html, w, h, out, tmp, tag):
    import time
    page = Path(tmp) / f"{tag}.html"
    page.write_text(page_html, encoding="utf-8")
    out.unlink(missing_ok=True)
    # headless Chrome sometimes stays up after the screenshot: wait for the file, then always stop it
    proc = subprocess.Popen([chrome(), "--headless=new", "--use-mock-keychain", "--password-store=basic", "--no-first-run",
                             "--no-default-browser-check", "--hide-scrollbars", "--force-device-scale-factor=1",
                             "--allow-file-access-from-files", f"--user-data-dir={tmp}/profile-{tag}",
                             f"--window-size={w},{h}", f"--screenshot={out}", page.as_uri()],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        if out.exists() and out.stat().st_size > 0:
            time.sleep(0.3)
            break
        time.sleep(0.25)
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    subprocess.run(["pkill", "-f", f"{tmp}/profile-{tag}"], check=False)
    if not out.exists():
        sys.exit(f"brand_assets: no screenshot for {out.name}")


def og_html():
    """The 1200 x 630 social card: the headline, the large mark, and the 3X0 wordmark."""
    mk = mark_body(100, cid="og", **{**FULL, "sq": 1.0, "xw": 1.0, "vw": 2.2, "rw": 1.3, "aw": 1.3, "cw": .8})
    wdt, wm = markword(NAV_VARIANT, "sm", "dark", cid="ogw")
    css = (SITE / "assets" / "style.css").as_uri()
    return f"""<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="{css}"><style>
body{{margin:0;width:1200px;height:630px;background:{BG};position:relative;overflow:hidden}}
.bar{{position:absolute;inset:0 0 auto 0;height:6px;background:{ACC}}}
.grid{{position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px);background-size:60px 60px}}
.eyebrow{{position:absolute;left:72px;top:70px;font:500 18px/1 var(--mono);letter-spacing:.2em;color:{FG2};display:flex;gap:14px;align-items:center}}
.eyebrow i{{width:12px;height:12px;background:{ACC};display:block}}
h1{{position:absolute;left:72px;top:138px;margin:0;font:800 92px/1.02 var(--sans);letter-spacing:-.045em;color:{FG}}}
p{{position:absolute;left:72px;top:350px;margin:0;width:580px;font:400 23px/1.5 var(--sans);color:{FG2}}}
.mark{{position:absolute;left:760px;top:92px;width:350px;height:350px}}
.rule{{position:absolute;left:72px;right:72px;top:526px;border-top:1px solid #24282d}}
.foot{{position:absolute;left:72px;right:72px;top:552px;display:flex;justify-content:space-between;align-items:center;font:500 18px/1 var(--mono);letter-spacing:.16em;color:{FG3}}}
.foot b{{color:{ACC};font-weight:500}}
</style></head><body><div class="grid"></div><div class="bar"></div>
<div class="eyebrow"><i></i>PROJECT EXO / ASKA DIGITAL</div>
<h1>Every model,<br>fitted to you.</h1>
<p>One system that knows you, remembers, learns your taste and grows with you.</p>
<svg class="mark" viewBox="0 0 100 100">{mk}</svg>
<div class="rule"></div>
<div class="foot"><svg viewBox="0 0 {n(wdt)} {n(HC)}" height="30">{wm}</svg><span>EXO.ASKACONSULT.COM &nbsp;/&nbsp; <b>IN ACTIVE BUILD</b></span></div>
</body></html>"""


def render_pngs():
    jobs = [("favicon-16.svg", 16, "favicon-16.png"), ("favicon.svg", 32, "favicon-32.png"),
            ("app-icon.svg", 180, "apple-touch-icon.png"), ("app-icon.svg", 192, "icon-192.png"),
            ("app-icon.svg", 512, "icon-512.png"), ("app-icon.svg", 1024, "app-icon-1024.png")]
    with tempfile.TemporaryDirectory() as tmp:
        for src, px, dst in jobs:
            shoot(f'<html><body style="margin:0;background:{BG}"><img src="{(OUT / src).as_uri()}" '
                  f'width="{px}" height="{px}" style="display:block"></body></html>', px, px, OUT / dst, tmp, f"i{px}")
        shoot(og_html(), 1200, 630, SITE / "og.png", tmp, "og")
    for name in ("favicon-32.png", "apple-touch-icon.png"):
        shutil.copyfile(OUT / name, SITE / name)
    print("rendered", ", ".join(j[2] for j in jobs), "and og.png; copied favicon-32.png and apple-touch-icon.png to site/")


if __name__ == "__main__":
    main(png="--png" in sys.argv)
