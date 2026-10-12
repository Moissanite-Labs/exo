#!/usr/bin/env python3
"""Generate the Exo brand assets in site/brand/ (D-049, docs/BRAND.md).

    python3 scripts/brand_assets.py          SVG marks, wordmarks and the terminal (braille) proposals
    python3 scripts/brand_assets.py --png    also render the PNG icons with headless Chrome

The geometry comes from one drawing (credit: aska-digital/incentre-lattice): a square of side 2, its two
diagonals (the X), and in each of the four triangles the incentre, the point equally far from all three
sides. It sits at R = sqrt(2) - 1 = tan 22.5 deg above the side. Every number below is derived from that.
No third-party code; standard library only.
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site" / "brand"

R = math.sqrt(2) - 1          # incentre height in a square of side 2; also tan 22.5 deg
BG, BG2, LINE2 = "#08090a", "#0e1012", "#343a41"
FG, FG2, FG3 = "#eceae4", "#b4b3ad", "#8b8b86"
ACC, ACC2, COLD = "#ff5b14", "#ff8a4c", "#9fb4c4"


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


# ---------------------------------------------------------------- the wordmark
# x-height S, stroke W. The x is the drawing's X: two strokes at exactly 45 degrees with flat horizontal
# terminals. Its top half (the two upper arms, owning the crossing) is orange and reads as a V.
S, W = 20.0, 4.0
H = W / math.sqrt(2)          # half the horizontal width of a 45-degree stroke of width W
GAP = 3.2                     # space between letters


def glyph_x(x0, gap=0.0, colour_v=ACC, colour_rest=FG):
    """The x at left edge x0. gap > 0 opens a hairline between the V and the lower legs (display sizes)."""
    box = [(x0 - 50, 0), (x0 + 80, 0), (x0 + 80, S), (x0 - 50, S)]
    u0 = x0 + H                                  # centreline of d1 starts at (u0, 0)
    # d1: top-left to bottom-right, |(x - u0) - y| <= H ; d2: top-right to bottom-left, |(x - u0) + y - S| <= H
    d1 = clip(clip(box, 1, -1, u0 + H), -1, 1, -u0 + H)
    d2 = clip(clip(box, 1, 1, u0 + S + H), -1, -1, -(u0 + S - H))
    k = S + H                                    # the V owns the crossing: cut along the far edge of the other band
    v1 = clip(d1, 1, 1, u0 + k)                  # u + y <= S + H
    v2 = clip(d2, -1, 1, -u0 + H)                # u - y >= -H
    g = gap * math.sqrt(2)
    l1 = clip(d1, -1, -1, -(u0 + k + g))
    l2 = clip(d2, 1, -1, u0 - H - g)
    return (f'<polygon fill="{colour_rest}" points="{pts(l1)}"/><polygon fill="{colour_rest}" points="{pts(l2)}"/>'
            f'<polygon fill="{colour_v}" points="{pts(v1)}"/><polygon fill="{colour_v}" points="{pts(v2)}"/>'), S + 2 * H


def octagon(x0, y0, size):
    c = size * (1 - R) / 2                       # corner cut; the side is size * R (the incentre number again)
    return [(x0 + c, y0), (x0 + size - c, y0), (x0 + size, y0 + c), (x0 + size, y0 + size - c),
            (x0 + size - c, y0 + size), (x0 + c, y0 + size), (x0, y0 + size - c), (x0, y0 + c)]


def ring_path(x0, shape):
    if shape == "oct":
        o, i = octagon(x0, 0, S), octagon(x0 + W, W, S - 2 * W)
        return "M" + " L".join(f"{n(x)},{n(y)}" for x, y in o) + "Z M" + " L".join(f"{n(x)},{n(y)}" for x, y in reversed(i)) + "Z"
    r1, r2, cx, cy = S / 2, S / 2 - W, x0 + S / 2, S / 2
    return (f"M{n(cx - r1)},{n(cy)} a{n(r1)},{n(r1)} 0 1,0 {n(2 * r1)},0 a{n(r1)},{n(r1)} 0 1,0 {n(-2 * r1)},0Z "
            f"M{n(cx - r2)},{n(cy)} a{n(r2)},{n(r2)} 0 1,1 {n(2 * r2)},0 a{n(r2)},{n(r2)} 0 1,1 {n(-2 * r2)},0Z")


def glyph_o(x0, shape, colour=FG):
    return f'<path fill="{colour}" fill-rule="evenodd" d="{ring_path(x0, shape)}"/>', S


def glyph_e(x0, shape, colour=FG, cid="e"):
    a = S / 2 + W / 2                            # bottom of the bar
    # the aperture: below the bar and right of the 45-degree line x = y (a cut square to the stroke)
    keep = [(x0 - 1, -1), (x0 + S + 1, -1), (x0 + S + 1, a), (x0 + a, a), (x0 + S + 1, S + 1), (x0 - 1, S + 1)]
    bar = f'<rect fill="{colour}" x="{n(x0 + W / 2)}" y="{n(S / 2 - W / 2)}" width="{n(S - W)}" height="{n(W)}"/>'
    return (f'<clipPath id="{cid}"><polygon points="{pts(keep)}"/></clipPath>'
            f'<path fill="{colour}" fill-rule="evenodd" clip-path="url(#{cid})" d="{ring_path(x0, shape)}"/>{bar}'), S


def glyph_E(x0, colour=FG):
    w = S * 0.82
    return (f'<path fill="{colour}" d="M{n(x0)},0 h{n(w)} v{n(W)} h{n(-(w - W))} v{n(S / 2 - W * 1.5)} h{n(w * 0.86 - W)} v{n(W)} '
            f'h{n(-(w * 0.86 - W))} v{n(S / 2 - W * 1.5)} h{n(w - W)} v{n(W)} h{n(-w)}Z"/>'), w


def wordmark(variant, on="dark", pad=0.0):
    fg = FG if on == "dark" else BG
    parts, x = [], pad
    shape = "round" if variant == "round" else "oct"
    first = glyph_E(x, fg) if variant == "caps" else glyph_e(x, shape, fg, cid=f"e-{variant}-{on}")
    parts.append(first[0]); x += first[1] + (GAP * 2.2 if variant == "caps" else GAP)
    gx = x
    g = glyph_x(x, gap=0.9 if variant == "seed" else 0.0, colour_rest=fg)
    parts.append(g[0]); x += g[1] + (GAP * 2.2 if variant == "caps" else GAP)
    o = glyph_o(x, shape, fg)
    parts.append(o[0]); x += o[1] + pad
    if variant == "seed":                        # the incentre of the V's triangle, from its centrelines and the top line
        parts.append(f'<circle fill="{ACC}" cx="{n(gx + H + S / 2)}" cy="{n(S * R / 2)}" r="1.25"/>')
    return x, "".join(parts)


# ---------------------------------------------------------------- the mark
def mark_body(size=100.0, inset=10.0, sq=2.6, xw=7.0, lw=1.6, dot=3.6, circles=False, lines=True,
              others=FG2, square=FG3, structure=FG):
    s = (size - 2 * inset) / 2                   # one unit of the drawing (square side 2)
    P = lambda u, v: (inset + s * u, size - inset - s * v)  # noqa: E731  (v up)
    dots = [(1, R), (2 - R, 1), (1, 2 - R), (R, 1)]          # bottom, right, top, left
    corners = {0: [(0, 0), (2, 0)], 1: [(2, 0), (2, 2)], 2: [(2, 2), (0, 2)], 3: [(0, 2), (0, 0)]}
    out = []
    # the X: lower legs in the structure colour, the V (two upper half-diagonals) in orange. The V's arms start
    # half a stroke past the centre, so the V owns the crossing. Everything is clipped to the square.
    c = P(1, 1)
    cid = f"sq{int(size)}{int(xw * 10)}{int(inset)}"
    q0 = [P(0, 0), P(2, 0), P(2, 2), P(0, 2)]
    out.append(f'<clipPath id="{cid}"><polygon points="{pts(q0)}"/></clipPath><g clip-path="url(#{cid})">')
    for (u, v), col in (((0, 0), structure), ((2, 0), structure), ((0, 2), ACC), ((2, 2), ACC)):
        a = P(u, v)
        dx, dy = a[0] - c[0], a[1] - c[1]
        L = math.hypot(dx, dy)
        back = xw / 2 if col == ACC else 0
        sx, sy = c[0] - dx / L * back, c[1] - dy / L * back
        out.append(f'<line x1="{n(sx)}" y1="{n(sy)}" x2="{n(a[0])}" y2="{n(a[1])}" stroke="{col}" stroke-width="{n(xw)}"/>')
    out.append("</g>")
    q = [P(0, 0), P(2, 0), P(2, 2), P(0, 2)]
    out.insert(0, f'<polygon fill="none" stroke="{square}" stroke-width="{n(sq)}" points="{pts(q)}"/>')
    for k, d in enumerate(dots):
        col = ACC if k == 2 else others
        lcol = col if circles else others       # in the plain mark the seed's lines stay quiet; only the dot is orange
        dp = P(*d)
        if circles:
            out.append(f'<circle cx="{n(dp[0])}" cy="{n(dp[1])}" r="{n(R * s)}" fill="none" stroke="{col}" stroke-opacity=".45" stroke-width="{n(lw * .7)}"/>')
        if lines:
            for cu in corners[k] + [(1, 1)]:
                e = P(*cu)
                out.append(f'<line x1="{n(dp[0])}" y1="{n(dp[1])}" x2="{n(e[0])}" y2="{n(e[1])}" stroke="{lcol}" stroke-opacity="{".9" if lcol == ACC else ".55"}" stroke-width="{n(lw)}"/>')
        out.append(f'<circle cx="{n(dp[0])}" cy="{n(dp[1])}" r="{n(dot)}" fill="{col}"/>')
    return "".join(out)


def tile(size, inner, radius_frac=0.0):
    rx = f' rx="{n(size * radius_frac)}"' if radius_frac else ""
    return f'<rect width="{n(size)}" height="{n(size)}" fill="{BG}"{rx}/>' + inner


def favicon16():
    # 16 px: no room for the square and the dots. Keep the X, with its V in orange, on the dark tile.
    a, b, w = 3.0, 13.0, 2.6
    c = (a + b) / 2
    return svg(16, 16, tile(16,
        f'<line x1="{c}" y1="{c}" x2="{a}" y2="{b}" stroke="{FG}" stroke-width="{w}" stroke-linecap="square"/>'
        f'<line x1="{c}" y1="{c}" x2="{b}" y2="{b}" stroke="{FG}" stroke-width="{w}" stroke-linecap="square"/>'
        f'<line x1="{c}" y1="{c}" x2="{a}" y2="{a}" stroke="{ACC}" stroke-width="{w}" stroke-linecap="square"/>'
        f'<line x1="{c}" y1="{c}" x2="{b}" y2="{a}" stroke="{ACC}" stroke-width="{w}" stroke-linecap="square"/>'))


def icon(size, radius_frac=0.0):
    # 32 px and up: the square, the X with its V, and the four incentres; the top one is the seed, in orange.
    k = size / 100
    body = mark_body(100, inset=16, sq=3.2, xw=8.5, lw=1.8, dot=4.6, lines=size >= 120)
    return svg(size, size, tile(size, f'<g transform="scale({n(k)})">{body}</g>', radius_frac))


# ---------------------------------------------------------------- the story panels (brand page, section 1)
def cell_lines(ox, oy, u, hl=False, dim=False, dots=True, lines=True):
    """One cell of the drawing (square side 2 units of u px) at (ox, oy) = its top-left corner, y down."""
    P = lambda a, b: (ox + a * u, oy + (2 - b) * u)  # noqa: E731
    out = []
    sqc, xc, lc = (LINE2, LINE2, LINE2) if dim else (FG3, FG2, FG3)
    q = [P(0, 0), P(2, 0), P(2, 2), P(0, 2)]
    out.append(f'<polygon fill="none" stroke="{sqc}" stroke-width="1.2" points="{pts(q)}"/>')
    for (a, b), top in (((0, 0), 0), ((2, 0), 0), ((0, 2), 1), ((2, 2), 1)):
        c, e = P(1, 1), P(a, b)
        col, w = (ACC, 3.2) if (hl and top) else (xc, 1.6)
        out.append(f'<line x1="{n(c[0])}" y1="{n(c[1])}" x2="{n(e[0])}" y2="{n(e[1])}" stroke="{col}" stroke-width="{w}" stroke-linecap="round"/>')
    ds = [(1, R), (2 - R, 1), (1, 2 - R), (R, 1)]
    cs = {0: [(0, 0), (2, 0)], 1: [(2, 0), (2, 2)], 2: [(2, 2), (0, 2)], 3: [(0, 2), (0, 0)]}
    for k, d in enumerate(ds):
        dp = P(*d)
        seed = hl and k == 2
        if lines:
            for cu in cs[k] + [(1, 1)]:
                e = P(*cu)
                out.append(f'<line x1="{n(dp[0])}" y1="{n(dp[1])}" x2="{n(e[0])}" y2="{n(e[1])}" stroke="{ACC2 if seed else lc}" stroke-opacity="{.9 if seed else .7}" stroke-width="1"/>')
        if dots:
            out.append(f'<circle cx="{n(dp[0])}" cy="{n(dp[1])}" r="{n(u * .07 + 1)}" fill="{ACC if seed else FG2}"/>')
    return "".join(out)


def story():
    u, o = 60, 20
    files = {}
    files["story-1.svg"] = svg(160, 160, cell_lines(o, o, u, dots=False, lines=False))
    s2 = cell_lines(o, o, u)
    for k, d in enumerate([(1, R), (2 - R, 1), (1, 2 - R), (R, 1)]):
        s2 += f'<circle cx="{n(o + d[0] * u)}" cy="{n(o + (2 - d[1]) * u)}" r="{n(R * u)}" fill="none" stroke="{FG3}" stroke-opacity=".5" stroke-width="1"/>'
    files["story-2.svg"] = svg(160, 160, s2)
    files["story-3.svg"] = svg(160, 160, cell_lines(o, o, u, hl=True))
    # the tiling: dots of neighbouring cells make regular octagons (4.8.8); one cell carries the V
    u2, body = 24, ""
    for i in range(4):
        for j in range(3):
            body += cell_lines(8 + i * 2 * u2, 8 + j * 2 * u2, u2, hl=(i == 1 and j == 1), dim=not (i == 1 and j == 1))
    oct_c = [(8 + 2 * u2 * i, 8 + 2 * u2 * j) for i in (1, 2, 3) for j in (1, 2)]
    for cx, cy in oct_c:
        rr = u2 * math.sqrt(1 + R * R)
        ring = [(cx + rr * math.cos(math.radians(22.5 + 45 * k) + math.atan(R) - math.radians(22.5)),
                 cy + rr * math.sin(math.radians(22.5 + 45 * k) + math.atan(R) - math.radians(22.5))) for k in range(8)]
        body += f'<polygon fill="none" stroke="{COLD}" stroke-opacity=".95" stroke-width="1.8" points="{pts(ring)}"/>'
    files["story-4.svg"] = svg(8 * u2 + 16, 6 * u2 + 16, body)
    g, wdt = glyph_x(0)
    files["story-5.svg"] = svg(wdt + 8, S + 8, f'<g transform="translate(4,4)">{g}</g>')
    return files


# ---------------------------------------------------------------- the terminal proposals (braille)
def braille(segments, dots_pts, cols, rows, scale, ox, oy):
    """Rasterise segments and points into a braille grid of cols x rows cells (2 x 4 dots per cell)."""
    W2, H4 = cols * 2, rows * 4
    grid = [[0] * W2 for _ in range(H4)]
    hot = [[0] * W2 for _ in range(H4)]
    def plot(x, y, h):
        i, j = int(round(x)), int(round(y))
        if 0 <= i < W2 and 0 <= j < H4:
            grid[j][i] = 1; hot[j][i] = hot[j][i] or h
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
                    v |= bit; h = h or hot[r * 4 + dy][c * 2 + dx]
            s += chr(0x2800 + v) if v else " "
            hs += "^" if h else "."
        lines.append(s.rstrip()); hots.append(hs)
    return lines, hots


def tui_mark():
    # the drawing in a 2 x 1 unit-per-cell grid: square side 2 (y down here)
    segs = []
    sq = [(0, 0), (2, 0), (2, 2), (0, 2)]
    for i in range(4):
        segs.append((sq[i], sq[(i + 1) % 4], 0))
    segs += [((1, 1), (0, 2), 0), ((1, 1), (2, 2), 0), ((1, 1), (0, 0), 1), ((1, 1), (2, 0), 1)]
    top = (1, R)
    segs += [(top, (0, 0), 1), (top, (2, 0), 1)]
    dts = [((1, 2 - R), 0), ((2 - R, 1), 0), ((R, 1), 0), (top, 1)]
    return braille(segs, dts, cols=12, rows=6, scale=11.0, ox=1, oy=1)


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
    lines, hots = braille(shell + segs, [(pr(p), 1) for p in six], cols=14, rows=7, scale=8.6, ox=14, oy=14)
    return lines, hots


def write_tui(name, lines, hots):
    (OUT / f"{name}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # an HTML fragment that colours the cells that hold the V or the seed, for the brand page
    html = []
    for s, h in zip(lines, hots):
        row = ""
        for ch, f in zip(s.ljust(len(h)), h):
            row += f'<b>{ch}</b>' if f == "^" and ch != " " else ch
        html.append(row.rstrip())
    (OUT / f"{name}.html").write_text("\n".join(html) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- output
def main(png=False):
    OUT.mkdir(parents=True, exist_ok=True)
    files = {}
    for variant in ("facet", "round", "seed", "caps"):
        for on in ("dark", "light"):
            wdt, body = wordmark(variant, on)
            files[f"wordmark-{variant}-{on}.svg"] = svg(wdt, S, body)
    # lockup: mark + wordmark (the pick)
    wdt, body = wordmark("facet", "dark")
    files["lockup-dark.svg"] = svg(34 + wdt, 30, f'<g transform="translate(0,1) scale(.28)">{mark_body(100, 4, 3, 8, 1.6, 4.4, lines=False)}</g>'
                                   f'<g transform="translate(34,5)">{body}</g>')
    wdt, body = wordmark("facet", "light")
    files["lockup-light.svg"] = svg(34 + wdt, 30, f'<g transform="translate(0,1) scale(.28)">{mark_body(100, 4, 3, 8, 1.6, 4.4, lines=False, others=FG3, square=FG2, structure=BG)}</g>'
                                    f'<g transform="translate(34,5)">{body}</g>')
    files["mark.svg"] = svg(100, 100, mark_body())
    files["mark-light.svg"] = svg(100, 100, mark_body(others=FG3, square=FG2, structure=BG))
    files["mark-construction.svg"] = svg(100, 100, mark_body(xw=3.2, lw=1.1, dot=2.4, sq=1.4, circles=True))
    files["favicon-16.svg"] = favicon16()
    files["favicon.svg"] = icon(64)
    files["app-icon.svg"] = icon(1024)
    files["app-icon-round.svg"] = icon(1024, radius_frac=0.225)
    files.update(story())
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8")
    write_tui("tui-mark", *tui_mark())
    write_tui("tui-egg", *tui_egg())
    page = OUT / "index.html"                    # keep the brand page's terminal samples in step
    if page.exists():
        text = page.read_text(encoding="utf-8")
        for name in ("tui-mark", "tui-egg"):
            a, b = f"<!-- {name} -->", f"<!-- /{name} -->"
            if a in text and b in text:
                frag = (OUT / f"{name}.html").read_text(encoding="utf-8").rstrip("\n")
                text = text[:text.index(a) + len(a)] + frag + text[text.index(b):]
        page.write_text(text, encoding="utf-8")
    print(f"wrote {len(files)} svg files and 2 terminal proposals to {OUT.relative_to(ROOT)}")
    if png:
        render_pngs()


def chrome():
    for c in (os.environ.get("CHROME"), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              shutil.which("google-chrome"), shutil.which("chromium")):
        if c and Path(c).exists():
            return c
    sys.exit("brand_assets: no Chrome found (set CHROME)")


def render_pngs():
    import time
    jobs = [("favicon-16.svg", 16, "favicon-16.png"), ("favicon.svg", 32, "favicon-32.png"),
            ("app-icon.svg", 180, "apple-touch-icon.png"), ("app-icon.svg", 192, "icon-192.png"),
            ("app-icon.svg", 512, "icon-512.png"), ("app-icon.svg", 1024, "app-icon-1024.png")]
    with tempfile.TemporaryDirectory() as tmp:
        for src, px, dst in jobs:
            page, out = Path(tmp) / f"{dst}.html", OUT / dst
            page.write_text(f'<html><body style="margin:0;background:{BG}"><img src="{(OUT / src).as_uri()}" '
                            f'width="{px}" height="{px}" style="display:block"></body></html>', encoding="utf-8")
            out.unlink(missing_ok=True)
            # headless Chrome sometimes stays up after the screenshot: wait for the file, then always stop it
            proc = subprocess.Popen([chrome(), "--headless=new", "--use-mock-keychain", "--password-store=basic", "--no-first-run",
                                     "--no-default-browser-check", "--hide-scrollbars", "--force-device-scale-factor=1",
                                     "--allow-file-access-from-files", f"--user-data-dir={tmp}/profile-{px}",
                                     f"--window-size={px},{px}", f"--screenshot={out}", page.as_uri()],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(120):
                if out.exists() and out.stat().st_size > 0:
                    time.sleep(0.3); break
                time.sleep(0.25)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            subprocess.run(["pkill", "-f", f"{tmp}/profile-{px}"], check=False)
            if not out.exists():
                sys.exit(f"brand_assets: no screenshot for {dst}")
    print("rendered", ", ".join(j[2] for j in jobs))


if __name__ == "__main__":
    main(png="--png" in sys.argv)
