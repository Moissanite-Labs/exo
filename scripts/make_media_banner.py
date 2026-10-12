#!/usr/bin/env python3
"""Write docs/media/exo-banner.svg: a node shell around an orange core.

Deterministic, stdlib only. The shell is a Fibonacci sphere whose projected
points are precomputed for FRAMES rotation steps; SMIL interpolates between
them, so the SVG needs no script. Usage: python3 scripts/make_media_banner.py
"""
import math
import pathlib

W, H = 1280, 400
CX, CY, R = 960, 200, 160
N, FRAMES, PERIOD = 64, 16, 48
EDGE_D2 = 0.33
TILT = -0.35
MONO = 'ui-monospace, "SF Mono", Menlo, Consolas, "DejaVu Sans Mono", monospace'

ga = math.pi * (3 - math.sqrt(5))
pts = []
for k in range(N):
    y = 1 - k / (N - 1) * 2
    r = math.sqrt(1 - y * y)
    th = ga * k
    pts.append((math.cos(th) * r, y, math.sin(th) * r))
hot = set(range(5, N, 9))
edges = [(a, b) for a in range(N) for b in range(a + 1, N)
         if sum((pts[a][i] - pts[b][i]) ** 2 for i in range(3)) < EDGE_D2]


def project(f):
    ry = 0.6 + f / FRAMES * 2 * math.pi
    cy_, sy_, cx_, sx_ = math.cos(ry), math.sin(ry), math.cos(TILT), math.sin(TILT)
    out = []
    for x, y, z in pts:
        x1, z1 = x * cy_ + z * sy_, -x * sy_ + z * cy_
        y2, z2 = y * cx_ - z1 * sx_, y * sx_ + z1 * cx_
        s = 1.6 / (2.6 - z2)
        out.append((round(CX + x1 * R * s), round(CY + y2 * R * s)))
    return out


frames = [project(f) for f in range(FRAMES)]
frames.append(frames[0])


def edge_d(p):
    return "".join(f"M{p[a][0]} {p[a][1]}L{p[b][0]} {p[b][1]}" for a, b in edges)


def node_d(p, sel):
    return "".join(f"M{p[i][0]} {p[i][1]}h0" for i in sel)


cold_ids = [i for i in range(N) if i not in hot]


def anim(fn):
    vals = ";".join(fn(p) for p in frames)
    return (f'<animate attributeName="d" dur="{PERIOD}s" repeatCount="indefinite" '
            f'calcMode="linear" values="{vals}"/>')


def hexpts(r, a0):
    return " ".join(f"{r*math.cos(a0+i*math.pi/3):.1f},{r*math.sin(a0+i*math.pi/3):.1f}"
                    for i in range(6))


spoke_angles = [200, 245, 290, 335, 20, 70, 115, 160]
spokes, packets = [], []
for i, deg in enumerate(spoke_angles):
    a = math.radians(deg)
    sx, sy = CX + math.cos(a) * R * 1.02, CY + math.sin(a) * R * 1.02 * 0.92
    ex, ey = CX + math.cos(a) * 24, CY + math.sin(a) * 24
    pid = f"s{i}"
    spokes.append(f'<path id="{pid}" d="M{sx:.0f} {sy:.0f}L{ex:.0f} {ey:.0f}" stroke="url(#spk{i})" fill="none"/>'
                  f'<rect x="{sx-2.5:.0f}" y="{sy-2.5:.0f}" width="5" height="5" fill="#ff5b14" opacity=".8"/>')
    dur = 3.0 + (i * 0.37) % 1.6
    col = "#ff8a4c" if i % 3 else "#9fb4c4"
    packets.append(
        f'<circle r="2.6" fill="{col}" opacity="0">'
        f'<animateMotion dur="{dur:.2f}s" begin="{i*0.55:.2f}s" repeatCount="indefinite" '
        f'keyPoints="0;1" keyTimes="0;1" calcMode="linear"><mpath href="#{pid}"/></animateMotion>'
        f'<animate attributeName="opacity" dur="{dur:.2f}s" begin="{i*0.55:.2f}s" repeatCount="indefinite" '
        f'values="0;1;1;0" keyTimes="0;.12;.8;1"/></circle>')
grads = "".join(
    f'<linearGradient id="spk{i}" gradientUnits="userSpaceOnUse" '
    f'x1="{CX + math.cos(math.radians(d))*R*1.02:.0f}" y1="{CY + math.sin(math.radians(d))*R*1.02*0.92:.0f}" '
    f'x2="{CX}" y2="{CY}"><stop offset="0" stop-color="#ff5b14" stop-opacity=".45"/>'
    f'<stop offset="1" stop-color="#ff5b14" stop-opacity="0"/></linearGradient>'
    for i, d in enumerate(spoke_angles))

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="t d">
<title id="t">Exo</title>
<desc id="d">Exo: every model, fitted to you. A shell of linked nodes slowly rotates around an orange core while small packets travel inward along links to the core.</desc>
<style>
text{{font-family:{MONO}}}
.core{{transform-box:fill-box;transform-origin:center;animation:spin 24s linear infinite}}
.core2{{transform-box:fill-box;transform-origin:center;animation:spin 36s linear infinite reverse}}
.glow{{animation:pulse 3.6s ease-in-out infinite}}
.hotn{{animation:pulse 3.6s ease-in-out infinite}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
@keyframes pulse{{0%,100%{{opacity:.7}}50%{{opacity:1}}}}
@media (prefers-reduced-motion: reduce){{.core,.core2,.glow,.hotn{{animation:none}}}}
</style>
<defs>
<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke="#24282d" stroke-width="1" opacity=".55"/></pattern>
<radialGradient id="fade" cx="{CX/W}" cy="0.5" r="0.75"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="#fff" stop-opacity=".25"/></radialGradient>
<mask id="gm"><rect width="{W}" height="{H}" fill="url(#fade)"/></mask>
<radialGradient id="halo"><stop offset="0" stop-color="#ff5b14" stop-opacity=".22"/><stop offset="1" stop-color="#ff5b14" stop-opacity="0"/></radialGradient>
{grads}
</defs>
<rect width="{W}" height="{H}" fill="#08090a"/>
<rect width="{W}" height="{H}" fill="url(#grid)" mask="url(#gm)"/>
<rect x=".5" y=".5" width="{W-1}" height="{H-1}" fill="none" stroke="#24282d"/>
<circle cx="{CX}" cy="{CY}" r="{R*1.1:.0f}" fill="url(#halo)"/>
<g fill="none" stroke-linecap="round">
<path d="{edge_d(frames[0])}" stroke="#eceae4" stroke-opacity=".17" stroke-width="1">{anim(edge_d)}</path>
<path d="{node_d(frames[0], cold_ids)}" stroke="#b4b3ad" stroke-opacity=".75" stroke-width="3">{anim(lambda p: node_d(p, cold_ids))}</path>
<path d="{node_d(frames[0], sorted(hot))}" class="hotn" stroke="#ff5b14" stroke-width="5">{anim(lambda p: node_d(p, sorted(hot)))}</path>
{"".join(spokes)}
</g>
{"".join(packets)}
<g transform="translate({CX} {CY})">
<polygon class="core2" points="{hexpts(34,math.pi/6)}" fill="none" stroke="#ff5b14" stroke-opacity=".5"/>
<polygon class="core glow" points="{hexpts(18,math.pi/6)}" fill="#ff5b14"/>
</g>
<text x="72" y="178" font-size="76" font-weight="700" fill="#eceae4" letter-spacing="8">E X O</text>
<rect x="72" y="204" width="36" height="2" fill="#ff5b14"/>
<text x="72" y="244" font-size="26" fill="#b4b3ad">Every model, fitted to you.</text>
<text x="72" y="290" font-size="12" fill="#8b8b86" letter-spacing="2.2">ONE AGENT · ONE MEMORY · A HARNESS THAT WATCHES ITSELF</text>
</svg>
'''
out = pathlib.Path(__file__).resolve().parent.parent / "docs" / "media" / "exo-banner.svg"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(svg, encoding="utf-8")
print(out, len(svg.encode()), "bytes;", len(edges), "edges")
