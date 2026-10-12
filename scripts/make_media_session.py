#!/usr/bin/env python3
"""Write docs/media/exo-session.svg: an animated terminal session (16 s loop).

Pure CSS keyframes, one master duration; stdlib only.
Usage: python3 scripts/make_media_session.py
"""
import pathlib

W, H, T = 960, 540, 16.0
MONO = 'ui-monospace, "SF Mono", Menlo, Consolas, "DejaVu Sans Mono", monospace'
BG, PANEL = "#08090a", "#0e1012"
css, uid = [], [0]


def pct(t):
    return f"{t / T * 100:.2f}%"


def show(on, dy=0):
    """Return a class name that fades an element in at time `on` and holds."""
    uid[0] += 1
    n = f"a{uid[0]}"
    tr0 = f"transform:translateY({dy}px);" if dy else ""
    tr1 = "transform:translateY(0);" if dy else ""
    css.append(f"@keyframes {n}{{0%,{pct(on)}{{opacity:0;{tr0}}}{pct(on + 0.25)},100%{{opacity:1;{tr1}}}}}"
               f".{n}{{opacity:0;animation:{n} {T}s linear infinite}}")
    return n


def el(tag, on, attrs, body="", dy=0):
    return f'<{tag} class="{show(on, dy)} s" {attrs}>{body}</{tag}>'


PROMPT = "fix the failing test in tests/test_slugify.py"
CW = 9.0
TW = len(PROMPT) * CW
TX = 92
t0, t1 = 0.9, 3.5
css.append(
    f"@keyframes typ{{0%,{pct(t0)}{{transform:translateX(0)}}{pct(t0)}{{animation-timing-function:steps({len(PROMPT)},end)}}"
    f"{pct(t1)}{{transform:translateX({TW + 14:.0f}px);opacity:1}}{pct(t1+.01)},100%{{transform:translateX({TW + 14:.0f}px);opacity:0}}}}"
    f".cover{{animation:typ {T}s linear infinite}}"
    f"@keyframes cur{{0%,{pct(t0 - .1)}{{opacity:0;transform:translateX(0)}}{pct(t0)}{{opacity:1;animation-timing-function:steps({len(PROMPT)},end)}}"
    f"{pct(t1)}{{opacity:1;transform:translateX({TW:.0f}px)}}{pct(t1 + .6)},100%{{opacity:0;transform:translateX({TW:.0f}px)}}}}"
    f".cursor{{opacity:0;animation:cur {T}s linear infinite}}")
# whole-scene fade, spinner, key press, gauge
css.append(
    f"@keyframes scene{{0%{{opacity:0}}{pct(.35)}{{opacity:1}}{pct(15.1)}{{opacity:1}}{pct(15.85)},100%{{opacity:0}}}}"
    f".scene{{animation:scene {T}s linear infinite}}"
    f"@keyframes spin{{to{{transform:rotate(360deg)}}}}"
    f".spin{{transform-box:fill-box;transform-origin:center;animation:spin .8s linear infinite}}"
    f"@keyframes spinv{{0%,{pct(6.5)}{{opacity:0}}{pct(6.7)},{pct(8.1)}{{opacity:1}}{pct(8.2)},100%{{opacity:0}}}}"
    f".spinv{{opacity:0;animation:spinv {T}s linear infinite}}"
    f"@keyframes key{{0%,{pct(12.0)}{{opacity:0}}{pct(12.1)},100%{{opacity:1}}}}"
    f".key{{opacity:0;animation:key {T}s linear infinite}}"
    f"@keyframes gauge{{0%,{pct(13.9)}{{transform:scaleX(0)}}{pct(15.0)},100%{{transform:scaleX(1)}}}}"
    f".gauge{{transform-box:fill-box;transform-origin:left center;transform:scaleX(0);animation:gauge {T}s cubic-bezier(.2,.7,.1,1) infinite}}"
    f"@keyframes blink{{0%,49%{{opacity:1}}50%,100%{{opacity:0}}}}")
css.append("@media (prefers-reduced-motion: reduce){"
           ".scene,.cover,.cursor,.spinv,.spin,.s,.key,.gauge{animation:none!important}"
           ".s,.key{opacity:1!important;transform:none!important}"
           ".gauge{transform:none}.cover,.cursor,.spinv{display:none}.scene{opacity:1}}")

L = []
x0, y0, x1, y1 = 40, 28, 920, 512
body_y = y0 + 44
L.append(f'<rect x="{x0}" y="{y0}" width="{x1-x0}" height="{y1-y0}" fill="{PANEL}" stroke="#343a41"/>')
L.append(f'<path d="M{x0} {body_y}H{x1}" stroke="#24282d"/>')
for i in range(3):
    L.append(f'<circle cx="{x0+22+i*18}" cy="{y0+22}" r="5" fill="none" stroke="#343a41"/>')
L.append(f'<text x="{x0+88}" y="{y0+26}" font-size="12" letter-spacing="1.8" fill="#8b8b86">EXO · ATLAS</text>')

Y = 112
L.append(f'<text x="{TX-22}" y="{Y}" font-size="16" font-weight="700" fill="#ff5b14">›</text>')
L.append(f'<text x="{TX}" y="{Y}" font-size="15" fill="#eceae4" textLength="{TW:.0f}" lengthAdjust="spacingAndGlyphs">{PROMPT}</text>')
L.append(f'<rect class="cover" x="{TX-1}" y="{Y-18}" width="{TW+16:.0f}" height="26" fill="{PANEL}"/>')
L.append(f'<rect class="cursor" x="{TX}" y="{Y-14}" width="8" height="18" fill="#ff5b14"/>')

right = x1 - 24


def tool(y, on, name, arg, ms=None, spinner=False):
    s = [f'<rect x="{TX-22}" y="{y-9}" width="6" height="6" fill="#9fb4c4"/>',
         f'<text x="{TX-6}" y="{y}" font-size="14" fill="#eceae4">{name}</text>',
         f'<text x="{TX+110}" y="{y}" font-size="14" fill="#8b8b86">{arg}</text>']
    if ms:
        s.append(f'<text x="{right}" y="{y}" font-size="12" text-anchor="end" fill="#8b8b86">{ms}</text>')
    return el("g", on, "", "".join(s), dy=4)


L.append(tool(158, 4.2, "read_file", "src/slugify/core.py", "31ms"))
L.append(tool(184, 4.9, "code_find", "unicode normalise", "640ms"))
L.append(tool(210, 5.6, "edit_file", "src/slugify/core.py +4 −1", "8ms"))
L.append(tool(236, 6.3, "bash", "pytest -q tests/test_slugify.py"))
L.append(f'<g class="spinv"><circle class="spin" cx="{right-6}" cy="231" r="5" fill="none" stroke="#9fb4c4" stroke-width="1.5" stroke-dasharray="9 23"/></g>')
L.append(el("text", 8.2, f'x="{right}" y="236" font-size="12" text-anchor="end" fill="#4ade80"', "ok · 14 passed · 2.1s"))

# approval panel
ay = 270
panel = (f'<rect x="{TX-22}" y="{ay}" width="{right-TX+22}" height="116" fill="#ff5b14" fill-opacity=".06" stroke="#ff5b14"/>'
         f'<text x="{TX-2}" y="{ay+28}" font-size="12" letter-spacing="2.4" fill="#ff5b14">APPROVAL · HIGH RISK</text>'
         f'<text x="{TX-2}" y="{ay+58}" font-size="16" fill="#eceae4">git push origin fix/unicode-slugs</text>')
chips = [("y", "allow", TX - 2), ("n", "deny", TX + 130), ("a", "allow always", TX + 250)]
for k, lab, cx in chips:
    panel += (f'<rect x="{cx}" y="{ay+76}" width="26" height="24" fill="none" stroke="#343a41"/>'
              + (f'<rect class="key" x="{cx}" y="{ay+76}" width="26" height="24" fill="#ff5b14"/>' if k == "y" else "")
              + f'<text x="{cx+13}" y="{ay+93}" font-size="13" text-anchor="middle" fill="{"#08090a" if k=="y" else "#eceae4"}"'
              + (' class="keyt"' if k == "y" else "") + f'>{k}</text>'
              f'<text x="{cx+36}" y="{ay+93}" font-size="13" fill="#b4b3ad">{lab}</text>')
L.append(el("g", 9.0, "", panel, dy=6))
# key-press text must stay legible over the highlight: redraw y on top
css.append(f"@keyframes keyt{{0%,{pct(12.0)}{{fill:#eceae4}}{pct(12.1)},100%{{fill:#08090a}}}}.keyt{{animation:keyt {T}s linear infinite}}")
css.append("@media (prefers-reduced-motion: reduce){.keyt{animation:none!important;fill:#08090a}}")

L.append(el("text", 12.8, f'x="{TX-22}" y="414" font-size="13" fill="#4ade80"',
            "✓ approved · seat-signed · exact text 3f9c…", dy=4))

# status strip
sy = 448
strip = [f'<path d="M{x0} {sy-22}H{x1}" stroke="#24282d"/>']
segs = [("model", "claude-sonnet-4-5", 64), ("memory", "18k/200k", 330), ("cache", "82%", 520), ("cost", "$0.14", 760)]
for lab, val, x in segs:
    strip.append(f'<text x="{x}" y="{sy+16}" font-size="12" fill="#8b8b86" letter-spacing="1.2">{lab.upper()}</text>'
                 f'<text x="{x + len(lab)*8.6 + 8:.0f}" y="{sy+16}" font-size="12" fill="#eceae4">{val}</text>')
strip.append(f'<rect x="618" y="{sy+7}" width="100" height="10" fill="none" stroke="#343a41"/>')
L.append(el("g", 13.6, "", "".join(strip), dy=4))
L.append(f'<rect class="gauge" x="618" y="{sy+7}" width="82" height="10" fill="#9fb4c4"/>')
# gauge fill must show only after strip; its own animation starts at 13.9 (strip shown at 13.6)

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="t d">
<title id="t">Exo session</title>
<desc id="d">An animated terminal session. The user asks Exo to fix a failing test. Exo reads a file, searches code, edits and runs the tests, then stops at an approval gate for a git push. The push is approved and signed, and a status strip shows model, memory, cache and cost.</desc>
<style>
text{{font-family:{MONO}}}
{chr(10).join(css)}
</style>
<rect width="{W}" height="{H}" fill="{BG}"/>
<g class="scene">
{chr(10).join(L)}
</g>
</svg>
'''
out = pathlib.Path(__file__).resolve().parent.parent / "docs" / "media" / "exo-session.svg"
out.write_text(svg, encoding="utf-8")
print(out.name, len(svg.encode()), "bytes")
