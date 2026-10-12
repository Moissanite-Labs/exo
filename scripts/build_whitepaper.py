#!/usr/bin/env python3
"""Build site/whitepaper/index.html from docs/WHITEPAPER.md. Figures are generated inline SVG."""
import html
import re
import sys
from pathlib import Path

REPO = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent)
SRC = REPO / "docs/WHITEPAPER.md"
OUT = REPO / "site/whitepaper/index.html"

# ------------------------------------------------------------------ svg helpers


def esc(t):
    return html.escape(t, quote=True)


def box(x, y, w, h, cls="bx", rx=0):
    return f'<rect class="{cls}" x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}"/>'


def cut(x, y, w, h, cls="bx", c=8):
    """Rectangle with two cut corners (the site's angular look)."""
    pts = [(x, y), (x + w - c, y), (x + w, y + c), (x + w, y + h), (x + c, y + h), (x, y + h - c)]
    return f'<polygon class="{cls}" points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in pts)}"/>'


def text(x, y, s, cls="tx", anchor="middle"):
    return f'<text class="{cls}" x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}">{esc(s)}</text>'


def line(x1, y1, x2, y2, cls="ln", arrow=None):
    m = f' marker-end="url(#{arrow})"' if arrow else ""
    return f'<line class="{cls}" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"{m}/>'


def path(d, cls="ln", arrow=None):
    m = f' marker-end="url(#{arrow})"' if arrow else ""
    return f'<path class="{cls}" d="{d}"{m}/>'


def marker(mid, cls="ah"):
    return (f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path class="{cls}" d="M0,0 L10,5 L0,10 z"/></marker>')


def svg(fid, w, h, title, desc, body, extra_defs=""):
    return (f'<svg class="wp-svg" viewBox="0 0 {w} {h}" role="img" aria-labelledby="{fid}-t {fid}-d" '
            f'xmlns="http://www.w3.org/2000/svg">'
            f'<title id="{fid}-t">{esc(title)}</title><desc id="{fid}-d">{esc(desc)}</desc>'
            f'<defs>{marker(fid + "-a")}{marker(fid + "-o", "ah ah--acc")}{extra_defs}</defs>'
            + "".join(body) + "</svg>")


def legend(x, y):
    return [box(x, y - 10, 22, 12, "bx"), text(x + 30, y, "built and tested", "mu", "start"),
            box(x + 160, y - 10, 22, 12, "bx bx--plan"), text(x + 190, y, "designed, not built", "mu", "start")]


# ------------------------------------------------------------------ figures


def fig_layers():
    W, H = 820, 500
    rows = [
        ("CLIENTS", [("Line client", 1), ("Terminal", 1), ("Web", 1), ("Chat channels", 0), ("Device", 0)]),
        ("PROTOCOL", [("One typed, numbered, replayable event stream", 1)]),
        ("CORE  exod", [("Turn loop", 1), ("Router", 1), ("Tools + gate", 1), ("Memory", 1),
                        ("Ingress", 1), ("Trace", 1)]),
        ("LEARNING", [("Capture", 1), ("Reviewer", 1), ("Proposals", 1), ("n=1 eval", 1), ("Held-out gate", 1)]),
        ("STORE", [("Pack", 1), ("Router", 1), ("Belief nodes", 1), ("Items", 0), ("Indexes", 0)]),
        ("LEDGER", [("Signed, hash-chained, append-only", 2)]),
    ]
    b = []
    x0, xw, top, rh, gap = 178, 622, 16, 54, 18
    for i, (label, chips) in enumerate(rows):
        y = top + i * (rh + gap)
        b.append(text(20, y + rh / 2 + 5, label, "lb", "start"))
        n = len(chips)
        cw = (xw - (n - 1) * 10) / n
        for j, (name, state) in enumerate(chips):
            cx = x0 + j * (cw + 10)
            cls = {0: "bx bx--plan", 1: "bx", 2: "bx bx--acc"}[state]
            b.append(cut(cx, y, cw, rh, cls))
            b.append(text(cx + cw / 2, y + rh / 2 + 5, name, "tx tx--acc" if state == 2 else "tx"))
        if i < len(rows) - 1:
            b.append(line(x0 + xw / 2, y + rh + 2, x0 + xw / 2, y + rh + gap - 2, "ln ln--mu"))
    b += legend(x0, H - 14)
    return svg("f1", W, H, "Exo's layers",
               "Clients on top, then the protocol, the core daemon, the learning loop, the store, and the signed "
               "ledger at the bottom. Solid boxes are built; dashed boxes are designed but not built.", b)


def fig_tree():
    W, H = 820, 330
    x0, x1 = 70, 800
    sc = (x1 - x0) / 52

    def X(m):
        return x0 + (m - 1) * sc

    b = []
    lv = {2: 34, 1: 104, 0: 174}
    for k, y in lv.items():
        b.append(text(22, y + 26, f"L{k}", "lb", "start"))
    nodes = [(2, 1, 32, "view"), (1, 1, 16, "kid"), (1, 17, 32, "kid"),
             (0, 1, 8, "kid"), (0, 9, 16, "kid"), (0, 17, 24, "kid"), (0, 25, 32, "kid"), (0, 33, 40, "view")]
    pos = {}
    for lvl, a, z, role in nodes:
        x, w, y = X(a) + 2, X(z + 1) - X(a) - 4, lv[lvl]
        cls = "bx bx--view" if role == "view" else "bx bx--dim"
        b.append(cut(x, y, w, 40, cls, 7))
        b.append(text(x + w / 2, y + 25, f"{a} to {z}", "tx" if role == "view" else "mu"))
        pos[(lvl, a)] = (x + w / 2, y)
    for (pl, pa, pz) in [(2, 1, 32), (1, 1, 16), (1, 17, 32)]:
        px, py = pos[(pl, pa)]
        half = (pz - pa + 1) // 2
        for ca in (pa, pa + half):
            cx, cy = pos[(pl - 1, ca)]
            b.append(line(px, py + 40, cx, cy, "ln ln--mu"))
    tx, tw = X(41) + 2, X(53) - X(41) - 4
    b.append(cut(tx, lv[0], tw, 40, "bx bx--acc", 7))
    b.append(text(tx + tw / 2, lv[0] + 25, "41 to 52 verbatim", "tx tx--acc"))
    b.append(text(tx + tw / 2, lv[1] + 25, "tail", "lb"))
    # axis
    ay = 252
    b.append(line(x0, ay, x1, ay, "ln ln--mu"))
    for m in (1, 9, 17, 25, 33, 41, 52):
        xx = X(m) if m != 52 else X(53)
        b.append(line(xx, ay - 4, xx, ay + 4, "ln ln--mu"))
        b.append(text(xx, ay + 18, str(m), "mu"))
    b.append(text(x0, ay + 40, "messages, oldest to newest", "mu", "start"))
    b.append(box(470, ay + 30, 14, 12, "bx bx--view"))
    b.append(text(490, ay + 40, "sent to the model this turn", "mu", "start"))
    b.append(box(690, ay + 30, 14, 12, "bx bx--dim"))
    b.append(text(710, ay + 40, "kept, not sent", "mu", "start"))
    return svg("f2", W, H, "Summary tree plus verbatim tail",
               "Leaves summarize 8 messages each. Two leaves merge into a level 1 node, two level 1 nodes into a "
               "level 2 node. This turn the model sees the level 2 summary of messages 1 to 32, the leaf for 33 to "
               "40, and messages 41 to 52 word for word.", b)


def fig_chain():
    W, H = 820, 250
    b = []
    bw, bh, y = 220, 150, 30
    xs = [20, 300, 580]
    prevs = ["000...000 (genesis)", "chain(1)", "chain(2)"]
    for i, x in enumerate(xs):
        b.append(cut(x, y, bw, bh, "bx" if i else "bx", 10))
        b.append(text(x + 16, y + 28, f"EVENT {i + 1}", "lb", "start"))
        rows = [("prev", prevs[i]), ("id", "sha256(event)"), ("body", "kind, author, refs ..."), ("sig", "Ed25519")]
        for k, (a, v) in enumerate(rows):
            yy = y + 58 + k * 24
            b.append(text(x + 16, yy, a, "mu", "start"))
            b.append(text(x + 70, yy, v, "tx tx--acc" if a == "prev" else "tx", "start"))
    for i in range(2):
        xa = xs[i] + bw
        b.append(path(f"M{xa:.0f},{y + bh / 2:.0f} C{xa + 30:.0f},{y + bh / 2:.0f} {xs[i + 1] - 40:.0f},{y + 52:.0f} "
                      f"{xs[i + 1] - 4:.0f},{y + 52:.0f}", "ln ln--acc", "f3-o"))
    b.append(text(W / 2, y + bh + 40, "chain(n) = sha256(prev + id). Change any event and every later link breaks.",
                  "mu"))
    return svg("f3", W, H, "The hash chain",
               "Three ledger events. Each event's prev field holds the chain hash of the event before it, and each "
               "event is signed with Ed25519.", b)


def fig_retrieval():
    W, H = 820, 610
    b = []
    stages = [
        ("1", "Pack", "always loaded, prompt-cached, outside the budget", 1),
        ("2", "Router pins", "load rules whose trigger matches the query enter first, in full", 0),
        ("3", "Candidates", "", 0),
        ("4", "Filters", "validity dates, privacy scope, never raw private text or archives", 0),
        ("5", "Graph expansion", "one hop from the top 8, weighted by link type", 0),
        ("6", "Pack under budget", "pinned first, then about 70% nodes and 30% items (default 1,500 tokens)", 1),
        ("7", "Log", "a retrieval event with the ids and token count", 0),
    ]
    y, x, w = 20, 120, 680
    for i, (n, name, sub, state) in enumerate(stages):
        h = 120 if n == "3" else 58
        b.append(text(60, y + 34, n, "num"))
        cls = "bx" if state else "bx bx--plan"
        if n == "3":
            b.append(cut(x, y, w, h, "bx bx--plan", 10))
            b.append(text(x + 18, y + 28, name.upper(), "lb", "start"))
            b.append(cut(x + 18, y + 44, 200, 52, "bx bx--plan", 7))
            b.append(text(x + 118, y + 66, "BM25 keywords", "tx"))
            b.append(text(x + 118, y + 84, "top 50", "mu"))
            b.append(cut(x + 238, y + 44, 200, 52, "bx bx--plan", 7))
            b.append(text(x + 338, y + 66, "Local embeddings", "tx"))
            b.append(text(x + 338, y + 84, "top 50", "mu"))
            b.append(line(x + 438, y + 70, x + 474, y + 70, "ln", "f4-a"))
            b.append(cut(x + 478, y + 44, 184, 52, "bx bx--plan", 7))
            b.append(text(x + 570, y + 66, "Rank fusion", "tx"))
            b.append(text(x + 570, y + 84, "sum of 1 / (60 + rank)", "mu"))
        else:
            b.append(cut(x, y, w, h, cls, 10))
            b.append(text(x + 18, y + 25, name.upper(), "lb", "start"))
            b.append(text(x + 18, y + 45, sub, "tx", "start"))
        if i < len(stages) - 1:
            b.append(line(x + w / 2, y + h + 2, x + w / 2, y + h + 18, "ln", "f4-a"))
        y += h + 20
    b += legend(x, H - 12)
    return svg("f4", W, H, "The retrieval pipeline",
               "Seven stages: pack, router pins, candidates from BM25 and embeddings merged by reciprocal rank "
               "fusion, filters, graph expansion, packing under a token budget, and a log entry.", b)


def fig_loop():
    W, H = 820, 640
    b = []

    def node(x, y, w, h, title, sub="", state=1):
        cls = {1: "bx", 0: "bx bx--plan", 2: "bx bx--acc"}[state]
        b.append(cut(x, y, w, h, cls, 8))
        b.append(text(x + w / 2, y + (h / 2 + 5 if not sub else 24), title, "tx tx--acc" if state == 2 else "tx"))
        if sub:
            b.append(text(x + w / 2, y + 42, sub, "mu"))

    # top row
    node(20, 20, 170, 56, "Live turn", "your words, verdicts")
    node(240, 20, 170, 56, "Capture", "rules, corrections")
    node(460, 20, 170, 56, "Ledger", "signed, append-only", 2)
    b.append(line(190, 48, 236, 48, "ln", "f5-a"))
    b.append(line(410, 48, 456, 48, "ln", "f5-a"))
    node(460, 120, 340, 56, "Reviewer", "off the main turn, propose only")
    b.append(line(545, 76, 545, 116, "ln", "f5-a"))
    node(240, 220, 560, 56, "Proposals", "typed lesson, evidence ids, confidence, critic note")
    b.append(line(630, 176, 630, 216, "ln", "f5-a"))
    # three branches
    bx = [(240, "Auto-promote", "unguarded, strong", 1), (430, "Eval gate", "beats incumbent", 1),
          (620, "Ask the person", "one clear question", 1)]
    for x, t, s, st in bx:
        node(x, 320, 180, 56, t, s, st)
        b.append(line(x + 90, 276, x + 90, 316, "ln", "f5-a"))
        b.append(line(x + 90, 376, x + 90, 416, "ln", "f5-a"))
    node(240, 420, 560, 56, "Accepted lessons", "pack, taste rules, skills, facts, retrieval")
    # slow loop back to proposals
    b.append(path("M240,448 C150,448 150,248 236,248", "ln ln--acc", "f5-o"))
    b.append(text(120, 352, "slow loop", "lb"))
    b.append(text(120, 370, "decay, merge,", "mu"))
    b.append(text(120, 386, "re-eval, revert", "mu"))
    # data split bar
    y = 520
    b.append(text(20, y - 8, "YOUR EVAL SET, SPLIT BY TIME", "lb", "start"))
    parts = [(20, 330, "train", "the reviewer may read it", "bx bx--dim"),
             (354, 230, "dev", "candidates are scored here", "bx"),
             (588, 212, "held out", "only for keep or drop", "bx bx--acc")]
    for x, w, t, s, cls in parts:
        b.append(cut(x, y, w, 50, cls, 7))
        b.append(text(x + w / 2, y + 22, t, "tx tx--acc" if "acc" in cls else "tx"))
        b.append(text(x + w / 2, y + 40, s, "mu"))
    b.append(text(20, y + 74, "oldest", "mu", "start"))
    b.append(text(800, y + 74, "newest", "mu", "end"))
    b += legend(240, H - 8)
    return svg("f5", W, H, "The learning loop",
               "Live turns are captured to the ledger. A reviewer turns events into proposals. Proposals are "
               "auto-promoted, sent through the eval gate, or turned into a question. Accepted lessons feed the "
               "pack, taste rules, skills and facts. The eval set is split by time into train, dev and held out.", b)


def fig_turn():
    rows = [
        ("c>s", "turn.send", ""),
        ("s>c", "response {accepted}", ""),
        ("s>c", "message.completed", "user message stored, numbered, sent"),
        ("s>c", "turn.started", ""),
        ("s", "", "pack + recall, settle summaries, tree view + tail"),
        ("s", "", "router picks a route that is not cooling down"),
        ("s>m", "stream (main role)", ""),
        ("m>s", "text deltas, tool_call edit_file", ""),
        ("s>c", "message.delta ...", ""),
        ("s>c", "usage.updated", ""),
        ("s>c", "message.completed", "assistant message"),
        ("s>c", "tool.started (medium)", ""),
        ("s>m", "edit_file", "gate: local, reversible, allow"),
        ("s>c", "tool.completed", ""),
        ("s>m", "stream (main role)", "fresh input"),
        ("s>c", "tool.started (high)", "bash: git push"),
        ("s>c", "approval.requested", "pre_tool_call asks"),
        ("c>s", "approval.respond allow", ""),
        ("s>c", "approval.resolved", ""),
        ("s>c", "tool.output ...", ""),
        ("s>c", "tool.completed", ""),
        ("s>c", "turn.completed", "final reply streamed before"),
        ("s>c", "session.updated", ""),
    ]
    W = 820
    top, step = 70, 27
    H = top + len(rows) * step + 30
    xc, xs, xm = 80, 400, 720
    b = []
    for x, name in ((xc, "CLIENT"), (xs, "EXOD"), (xm, "MODEL / TOOLS")):
        b.append(cut(x - 70, 16, 140, 34, "bx", 7))
        b.append(text(x, 38, name, "lb"))
        b.append(line(x, 50, x, H - 10, "ln ln--life"))
    for i, (d, label, note) in enumerate(rows):
        y = top + i * step
        if d == "s":
            b.append(text(xs, y + 4, note, "mu"))
            continue
        a, z = {"c>s": (xc, xs), "s>c": (xs, xc), "s>m": (xs, xm), "m>s": (xm, xs)}[d]
        acc = d == "c>s"
        b.append(line(a, y, z + (-6 if z > a else 6), y, "ln ln--acc" if acc else "ln", "f6-o" if acc else "f6-a"))
        b.append(text((a + z) / 2, y - 6, label, "tx tx--acc" if acc else "tx"))
        if note:
            nx = xm - 10 if d in ("s>c", "c>s") else xs + 10
            if d in ("s>m", "m>s"):
                b.append(text((xs + xm) / 2, y + 14, note, "mu"))
            else:
                b.append(text(xs + 16, y + 4, note, "mu", "start"))
    return svg("f6", W, H, "One turn as the client sees it",
               "A sequence diagram of one turn: turn.send, then numbered events from exod for the user message, "
               "turn start, streamed text, usage, the assistant message, a file edit tool, a shell tool that asks "
               "for approval, the approval answer, tool output, turn completion and session update.", b)


FIGS = {"layers": fig_layers, "tree": fig_tree, "chain": fig_chain, "retrieval": fig_retrieval,
        "loop": fig_loop, "turn": fig_turn}

# ------------------------------------------------------------------ markdown


def slug(t):
    t = t.lower()
    t = re.sub(r"[^\w\s-]", "", t)
    return re.sub(r"\s", "-", t.strip())


BADGES = [
    ("**[designed, not built]**", '<span class="wp-badge wp-badge--plan">designed, not built</span>'),
    ("**[built]**", '<span class="wp-badge wp-badge--built">built</span>'),
    ("**[not measured]**", '<span class="wp-badge wp-badge--plan">not measured</span>'),
]


def inline(t):
    codes = []

    def keep(m):
        codes.append("<code>" + esc(m.group(1)) + "</code>")
        return f"\x00{len(codes) - 1}\x00"

    t = re.sub(r"`([^`]+)`", keep, t)
    for a, bdg in BADGES:
        t = t.replace(a, f"\x01{BADGES.index((a, bdg))}\x01")
    t = esc(t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*([^*\s][^*]*?)\*(?![\w*])", r"<em>\1</em>", t)
    t = re.sub(r"\x01(\d+)\x01", lambda m: BADGES[int(m.group(1))][1], t)
    t = re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], t)
    return t


def convert(md):
    lines = md.split("\n")
    out, toc = [], []
    i, n = 0, len(lines)
    pending_fig = None
    fig_no = 0
    while i < n:
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        m = re.match(r"<!-- fig:(\w+) -->", ln)
        if m:
            pending_fig = m.group(1)
            i += 1
            continue
        if ln.startswith("```"):
            j = i + 1
            while j < n and not lines[j].startswith("```"):
                j += 1
            body = "\n".join(lines[i + 1:j])
            i = j + 1
            if pending_fig:
                # caption is the next non-empty line starting with *Figure
                while i < n and not lines[i].strip():
                    i += 1
                cap = ""
                if i < n and lines[i].startswith("*Figure"):
                    capl = [lines[i]]
                    while not capl[-1].rstrip().endswith("*") or len(capl[-1].strip()) < 2:
                        i += 1
                        capl.append(lines[i])
                    i += 1
                    cap = " ".join(c.strip() for c in capl).strip("*")
                fig_no += 1
                wide = " wp-fig--wide" if pending_fig == "turn" else ""
                fid = f"fig-{fig_no}"
                cap = re.sub(r"^Figure (\d+)\. ", "", cap)
                out.append(f'<figure class="wp-fig{wide}" id="{fid}"><div class="wp-fig__art">'
                           f'{FIGS[pending_fig]()}</div><figcaption><span class="wp-fig__no">Figure {fig_no}</span> '
                           f'{inline(cap)}</figcaption></figure>')
                pending_fig = None
            else:
                out.append(f"<pre><code>{esc(body)}</code></pre>")
            continue
        h = re.match(r"(#{1,4}) (.*)", ln)
        if h:
            lvl, title = len(h.group(1)), h.group(2).strip()
            sid = slug(title)
            cls = ' class="wp-part"' if title.startswith("Part ") else ""
            num = re.match(r"(\d+(?:\.\d+)*)\.? (.*)", title)
            label = (f'<span class="wp-num">{num.group(1)}</span> {inline(num.group(2))}' if num
                     else inline(title))
            out.append(f'<h{lvl} id="{sid}"{cls}>{label}<a class="wp-anchor" href="#{sid}" '
                       f'aria-label="Link to this section">#</a></h{lvl}>')
            if lvl in (2, 3):
                toc.append((lvl, sid, title))
            i += 1
            continue
        if ln.strip() == "---":
            out.append('<hr class="wp-sep">')
            i += 1
            continue
        if ln.startswith("|"):
            rows = []
            while i < n and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, body = rows[0], [r for r in rows[2:]]
            t = ['<div class="wp-table"><table><thead><tr>']
            t += [f"<th>{inline(c)}</th>" for c in head]
            t.append("</tr></thead><tbody>")
            for r in body:
                t.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
            t.append("</tbody></table></div>")
            out.append("".join(t))
            continue
        lm = re.match(r"(\s*)(- |\d+\. )(.*)", ln)
        if lm:
            ordered = lm.group(2) != "- "
            items = []
            while i < n:
                m2 = re.match(r"(- |\d+\. )(.*)", lines[i])
                if m2 and ((m2.group(1) != "- ") == ordered):
                    items.append(m2.group(2))
                    i += 1
                    while i < n and lines[i].startswith(" ") and lines[i].strip() and not re.match(
                            r"\s+(- |\d+\. )", lines[i]):
                        items[-1] += " " + lines[i].strip()
                        i += 1
                    continue
                break
            tag = "ol" if ordered else "ul"
            start = ""
            if ordered:
                s0 = int(re.match(r"\s*(\d+)", ln).group(1))
                start = f' start="{s0}"' if s0 != 1 else ""
            out.append(f"<{tag}{start}>" + "".join(f"<li>{inline(it)}</li>" for it in items) + f"</{tag}>")
            continue
        para = [ln.strip()]
        i += 1
        while i < n and lines[i].strip() and not re.match(r"(#{1,4} |\||```|<!--|- |\d+\. |---$)", lines[i]):
            para.append(lines[i].strip())
            i += 1
        cls = ' class="wp-caption"' if para[0].startswith("*Figure") else ""
        out.append(f"<p{cls}>{inline(' '.join(para))}</p>")
    return "\n".join(out), toc


def toc_html(toc):
    parts, open_sub = [], False
    parts.append("<ol class=\"wp-toc__list\">")
    for lvl, sid, title in toc:
        num = re.match(r"(\d+)\. (.*)", title)
        lab = (f'<span class="wp-toc__n">{num.group(1)}</span>{esc(num.group(2))}' if num else esc(title))
        if lvl == 2:
            if open_sub:
                parts.append("</ol></li>")
                open_sub = False
            cls = "wp-toc__part" if title.startswith("Part ") else "wp-toc__top"
            if title.startswith("Part "):
                parts.append(f'<li class="{cls}"><a href="#{sid}">{lab}</a><ol>')
                open_sub = True
            else:
                parts.append(f'<li class="{cls}"><a href="#{sid}">{lab}</a></li>')
        else:
            parts.append(f'<li><a href="#{sid}">{lab}</a></li>')
    if open_sub:
        parts.append("</ol></li>")
    parts.append("</ol>")
    return "".join(parts)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Project Exo whitepaper</title>
<meta name="description" content="{desc}">
<meta name="theme-color" content="#08090a">
<link rel="canonical" href="https://exo.askaconsult.com/whitepaper/">
<meta property="og:type" content="article">
<meta property="og:site_name" content="Project Exo">
<meta property="og:url" content="https://exo.askaconsult.com/whitepaper/">
<meta property="og:title" content="Project Exo whitepaper">
<meta property="og:description" content="{desc}">
<meta property="og:image" content="https://exo.askaconsult.com/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="Project Exo">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Project Exo whitepaper">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image" content="https://exo.askaconsult.com/og.png">
<meta name="twitter:image:alt" content="Project Exo">
<link rel="icon" href="../favicon.svg" type="image/svg+xml">
<link rel="icon" href="../favicon-32.png" type="image/png" sizes="32x32">
<link rel="apple-touch-icon" href="../apple-touch-icon.png">
<link rel="stylesheet" href="../assets/style.css">
<link rel="stylesheet" href="paper.css">
</head>
<body class="wp">
<a class="wp-skip" href="#wp-main">Skip to content</a>
<header class="wp-bar">
  <div class="wp-bar__in">
    <a class="wp-brand" href="../" aria-label="Project Exo home">
      <svg class="wp-brand__mark" viewBox="0 0 32 32" aria-hidden="true"><path d="M16 2 29 9.5v13L16 30 3 22.5v-13z" fill="none" stroke="currentColor" stroke-width="2"/><path d="M16 9.5 23 13.5v5L16 22.5 9 18.5v-5z" fill="currentColor"/></svg>
      <span>EXO</span>
    </a>
    <span class="wp-bar__doc">Whitepaper v0.2</span>
    <a class="wp-bar__back" href="../">&larr; Back to the site</a>
  </div>
</header>

<div class="wp-layout">
  <nav class="wp-toc" aria-label="Contents">
    <p class="wp-toc__h">Contents</p>
    {toc}
  </nav>

  <main id="wp-main" class="wp-main">
    <header class="wp-title">
      <p class="wp-eyebrow"><span class="wp-dot"></span>Project Exo / Aska Digital / Technical whitepaper</p>
      <h1>Project Exo</h1>
      <p class="wp-sub">A personal system for working with models: a signed record, a portable store, one continuous memory, consent bound to exact text, lessons kept only when they prove themselves, and a harness that measures itself.</p>
      <dl class="wp-meta">
        <div><dt>Version</dt><dd>0.2</dd></div>
        <div><dt>Date</dt><dd>October 2026</dd></div>
        <div><dt>Status</dt><dd>Early, in active build</dd></div>
        <div><dt>Tests</dt><dd>900 passed, 62 skipped</dd></div>
      </dl>
      <div class="wp-note">
        <p>This paper keeps a strict line between what exists and what is planned. <span class="wp-badge wp-badge--built">built</span> means the code exists and is covered by tests that pass today. <span class="wp-badge wp-badge--plan">designed, not built</span> means a reviewed design with no working code yet. Part III lists both in full.</p>
      </div>
      <details class="wp-toc-m">
        <summary>Contents</summary>
        {toc}
      </details>
      <nav class="wp-toc-p" aria-hidden="true">
        <p class="wp-toc__h">Contents</p>
        {toc}
      </nav>
    </header>

    <article class="wp-article">
{body}
    </article>

    <footer class="wp-foot">
      <p>Project Exo whitepaper, version 0.2, October 2026. Aska Digital.</p>
      <p><a href="../">&larr; Back to the Project Exo site</a> &nbsp;/&nbsp; <a href="#top-of-page" onclick="window.scrollTo(0,0);return false;">Top</a></p>
    </footer>
  </main>
</div>
</body>
</html>
"""


def main():
    md = SRC.read_text()
    start = md.index("## Part I. Overview")
    body, toc = convert(md[start:])
    desc = ("How Project Exo works: a signed, hash-chained ledger, a portable personal store, fresh context every "
            "turn from one continuous memory, a learning loop that keeps a lesson only if it scores better on "
            "held-out cases, approvals bound to exact text, a harness that traces and re-verifies itself, and one "
            "core daemon behind every screen. With an honest status of what is built.")
    page = PAGE.replace("{desc}", esc(desc)).replace("{toc}", toc_html(toc)).replace("{body}", body)
    page = page.replace('<a href="#top-of-page" onclick="window.scrollTo(0,0);return false;">Top</a>',
                        '<a href="#wp-main">Top</a>')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page)
    print(OUT, len(page))


if __name__ == "__main__":
    main()
