#!/usr/bin/env python3
"""draft_pipeline.py — one command per GitHub draft, same scaffolding every time.

    draft_pipeline.py --src <draft.md> --slug <slug> --tab "pull request draft" \
        --title "<draft title>" --owners {{github_owner}} \
        --verify-against "origin/main:hermes_cli/auth_nous.py" \
        --verify-against "<patched-file>" [--open]

What it does (all gates from the github-draft-review-html SOP, in order):
  1. prose gate            check-prose.py must exit 0 (a finding, an error or a missing
                           check-prose.py blocks)
  2. visual value gate     visual-value-gate.py decides if content warrants a visual
                           (advisory only: never blocks; --skip-visual-gate opts out)
  2b. mermaid gate         check-mermaid.py runs iff a ```mermaid fence is present
                           (blocks on invalid fences, or when check-mermaid.py is missing)
  3. identifier gate       no /Users paths, usernames, profile/agent names, hostnames, emails;
                           --allow-identifier exempts one exact token, never a whole class
  4. verbatim gate         every ```python fence must appear in one --verify-against source
                           (git `REF:PATH` or a plain path), modulo uniform indentation
  5. render                render_draft_html.py into <drafts-root>/<batch>/<slug>.html
  6. fidelity gate         0 source lines missing from the rendered text
  7. palette gate          the seven core dark tokens + color-scheme meta present
  8. containment gate      no external asset fetch; repo/tab/badge chrome present
  9. banner gate           no false "no change" claim without a baseline

Exit 0 only when every gate passes. Prints the review path and the posting source path.

Attribution: external drafts pass --owners <poster handle>; internal drafts keep the
renderer default chain (role names, or $EXO_DRAFT_OWNERS).

Principal-specific settings come from the environment, never from this file:
  EXO_DRAFTS_ROOT       where review batches are written (default ./drafts)
  EXO_INTERNAL_NAMES    file with one internal name per line (agent, seat, host, user);
                        each matches as a whole name, even one that starts or ends with
                        '.', '~' or '@'. Keep it in the principal store, for example
                        policies/internal-names.txt.
"""
from __future__ import annotations

import argparse
import bisect
import os
import html as _html
import re
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
RENDERER = Path(__file__).resolve().parent / "render_draft_html.py"
CHARTS_SCRIPTS = SKILL_DIR.parent / "charts-in-posts" / "scripts"
VISUAL_GATE = CHARTS_SCRIPTS / "visual-value-gate.py"
MERMAID_CHECK = CHARTS_SCRIPTS / "check-mermaid.py"
DEFAULT_DRAFTS_ROOT = Path(os.environ.get("EXO_DRAFTS_ROOT", "drafts"))
DEFAULT_IDENTS = {
    "abs-path": r"/Users/|/home/[A-Za-z]|/root/",
    "tmp-path": r"/var/folders|/private/tmp",
    "localhost": r"127\.0\.0\.1|localhost",
    "email": r"[\w.+-]+@[\w-]+\.[a-z]{2,}",
}


def _load_internal_names() -> None:
    """Add principal-specific names from $EXO_INTERNAL_NAMES as one identifier pattern."""
    path = os.environ.get("EXO_INTERNAL_NAMES")
    if not path or not Path(path).is_file():
        return
    names = [ln.strip() for ln in Path(path).read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    if names:
        DEFAULT_IDENTS["internal-name"] = names_pattern(names)


def names_pattern(names: list[str]) -> str:
    """Whole names: a word edge must not touch another word character, a non-word edge
    ('.agents', '@bot', 'seat.') needs no boundary. Longest first, so the full name is reported."""
    def one(n: str) -> str:
        return ((r"(?<!\w)" if re.match(r"\w", n) else "") + re.escape(n)
                + (r"(?!\w)" if re.search(r"\w\Z", n) else ""))
    return "|".join(one(n) for n in sorted(names, key=len, reverse=True))


_load_internal_names()
PALETTE = ["#0d1117", "#c9d1d9", "#f0f6fc", "#8b949e", "#58a6ff", "#161b22", "#30363d",
           "#f85149", "#3d1d20", 'color-scheme" content="dark"']
OVERSIGHT_LINE = "> Automated posting by agentic team with human oversight."


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def resolve_source(ref: str) -> str | None:
    """`REF:PATH` via git show, or a plain file path."""
    if ":" in ref and not ref.startswith("/"):
        repo, path = ref.split(":", 1)
        p = run(["git", "-C", str(Path.cwd()), "show", f"{repo}:{path}"])
        return p.stdout if p.returncode == 0 else None
    f = Path(ref)
    return f.read_text(encoding="utf-8") if f.is_file() else None


def fence_bodies(text: str, langs: tuple[str, ...] = ("python", "py")) -> list[str]:
    pat = "|".join(langs)
    return re.findall(rf"```(?:{pat})\n(.*?)```", text, re.S)


def normalize_evidence(text: str) -> list[str]:
    """Evidence lines without runner decoration (box rules, `N |` gutters, trailing space)."""
    out = []
    for raw in text.splitlines():
        line = re.sub(r"^\s*(?:\d+\s*[|│]\s?|[║┃│]\s?)", "", raw).rstrip()
        if line.strip():
            out.append(line.strip())
    return out


def _mask_timing(s: str) -> str:
    """Run durations vary per machine; mask them so the gate compares content, not speed."""
    return re.sub(r"in \d+\.\d+s \(\d+ workers\)", "in <t> (N workers)", s)


def text_fence_gate(text: str, evidence: list[str]) -> tuple[bool, list[str]]:
    """Every non-empty line of a ```text fence must appear in an evidence file (timings masked)."""
    evidence = [_mask_timing(ev) for ev in evidence]
    bad = []
    for blk in fence_bodies(text, ("text",)):
        for line in blk.splitlines():
            probe = _mask_timing(line.strip())
            if len(probe) < 8:
                continue
            if not any(probe in ev or ev.endswith(probe) for ev in evidence):
                bad.append(probe[:80])
    return (not bad), bad


def verbatim_gate(text: str, sources: list[tuple[str, str | None]]) -> tuple[bool, list[str]]:
    notes, ok = [], True
    blocks = fence_bodies(text)
    for i, blk in enumerate(blocks, 1):
        lines = [l for l in blk.splitlines() if not l.lstrip().startswith("# ")]
        hit = None
        for name, body in sources:
            if body is None:
                continue
            for indent in (0, 4, 8, 12, 16):
                cand = "\n".join((" " * indent + l) if l.strip() else l for l in lines) + "\n"
                if cand in body:
                    hit = name
                    break
            if hit:
                break
        notes.append(f"fence {i}: {'verbatim in ' + hit if hit else 'NO VERBATIM SOURCE'}")
        ok &= hit is not None
    return ok, notes


_TOKEN_STOP = "`\"'<>()[]{}*"
_TOKEN_TRIM = ".,;:!?"
_TOKEN_RUN = re.compile(r"[^\s" + re.escape(_TOKEN_STOP) + r"]+")


def _token_spans(text: str) -> tuple[list[int], list[int]]:
    """Starts and ends of the runs between whitespace, quotes, brackets and emphasis, found once per text."""
    runs = [m.span() for m in _TOKEN_RUN.finditer(text)]
    return [a for a, _ in runs], [b for _, b in runs]


def _token_at(spans: tuple[list[int], list[int]], start: int, end: int) -> tuple[int, int]:
    """The whole token around a match, so '/Users/' in '/Users/sam/.ssh/id' gives the path: the match
    grows over the run that touches its start and the run that touches its end."""
    starts, ends = spans
    i = bisect.bisect_right(starts, start - 1) - 1
    if i >= 0 and ends[i] > start - 1 >= 0:
        start = starts[i]
    i = bisect.bisect_right(starts, end) - 1
    if i >= 0 and ends[i] > end:
        end = ends[i]
    return start, end


def identifier_gate(text: str, allowed: list[str]) -> tuple[bool, dict[str, list[str]]]:
    """Denylisted identifiers by class. An allowed literal exempts only a token equal to it."""
    allow = {a.rstrip(_TOKEN_TRIM).lower() for a in allowed}
    spans, hits = _token_spans(text), {}
    for key, pat in DEFAULT_IDENTS.items():
        found_at = {_token_at(spans, m.start(), m.end()) for m in re.finditer(pat, text, re.I)}
        tokens = {text[a:b].rstrip(_TOKEN_TRIM) for a, b in found_at}  # trailing punctuation is not the token
        found = sorted(t for t in tokens if t.lower() not in allow)
        if found:
            hits[key] = found
    return (not hits), hits


def prose_gate(md: Path, python: str) -> tuple[bool, str]:
    cp = SKILL_DIR.parent / "clean-writing" / "scripts" / "check-prose.py"
    if not cp.is_file():  # no checker is no check: block, so a partial install never posts unchecked text
        return False, f"check-prose.py not found at {cp}; install the clean-writing skill"
    # "--" ends the options, so a draft named --help is checked rather than printing usage.
    p = run([python, str(cp), "--", str(md)])
    out = (p.stdout + p.stderr).strip()
    # Only exit 0 is clean: 1 is findings, 3 a usage or policy error. Never read the text,
    # because every finding line quotes the draft path and the flagged words.
    return p.returncode == 0, out.splitlines()[0] if out else "no output"


def visual_gate(md: Path, python: str) -> tuple[bool, str]:
    """Advisory visual-value gate — ALWAYS passes; exit 1 surfaces as a note.

    Returns (True, note). Exit 0/2/3 surface the gate's verdict line as a
    plain note; exit 1 (WARRANTED) prefixes ADVISORY so the missing visual
    is visible without blocking the run.
    """
    if not VISUAL_GATE.is_file():
        return True, "visual-value-gate.py not found (skipped)"
    p = run([python, str(VISUAL_GATE), str(md)])
    lines = (p.stdout or "").strip().splitlines()
    reason = next(
        (l.strip() for l in lines[1:] if l.strip().startswith("reason:")),
        lines[-1] if lines else "no output",
    )
    if p.returncode == 1:
        return True, f"ADVISORY visual missing — {reason}"
    return True, lines[0].strip() if lines else "no output"


def mermaid_gate(md: Path, python: str) -> tuple[bool, str] | None:
    """Run check-mermaid.py iff the draft holds a ```mermaid fence.

    Returns None when no fence is present (no behavior change for
    non-diagram drafts); otherwise (passed, note) — a failing fence,
    or a missing check-mermaid.py, BLOCKS the pipeline.
    """
    try:
        text = md.read_text(encoding="utf-8")
    except OSError:
        return None
    if not re.search(r"^\s*```mermaid\s*$", text, re.M):
        return None
    if not MERMAID_CHECK.is_file():  # a fence with no checker blocks, as a failing fence does
        return False, f"check-mermaid.py not found at {MERMAID_CHECK}; install the charts-in-posts skill"
    p = run([python, str(MERMAID_CHECK), str(md)])
    out = (p.stdout + p.stderr).strip()
    last = out.splitlines()[-1] if out else "no output"
    return (p.returncode == 0), last


def _flat_body(html: str) -> str:
    body = html.split("<body", 1)[1] if "<body" in html else html
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S)
    body = _html.unescape(re.sub(r"<[^>]+>", "", body))
    return re.sub(r"\s+", " ", body).strip()


def fidelity_gate(md_text: str, html: str) -> tuple[bool, list[str]]:
    flat, missing, fence = _flat_body(html), [], False
    for raw in md_text.splitlines():
        if raw.startswith("```"):
            fence = not fence
            continue
        line = raw.strip()
        if re.fullmatch(r"\|[\s\-|]+\|", line):
            continue
        # A table row is a leading pipe with more than one column (so a separator survives).
        # Inside a fence a table is literal text, not markup, so it is compared literally.
        table_row = (not fence) and line.startswith("|") and "|" in line[1:]
        # strip only markers that markdown renders instead of printing: leading list/quote/table
        # markers, heading hashes, backticks/emphasis; keep inner '|', '#' and '->' (log text)
        line = re.sub(r"^\s*(?:\||>|\d+\.|[-*])\s*", "", line)
        line = re.sub(r"^#+\s*", "", line)
        if table_row:
            # inside a table region a pipe is column syntax, not log text: the renderer emits
            # cells separated by whitespace, so normalize the separators before probing
            line = re.sub(r"\s*\|\s*", " ", line)
        if not fence:  # fence content is rendered literally, so backticks/emphasis stay in the probe
            line = re.sub(r"[`*]", "", line)
        probe = re.sub(r"\s+", " ", line).strip()
        if len(probe) < 3:
            continue
        if probe not in flat and probe[:40] not in flat:
            missing.append(raw[:80])
    return (not missing), missing


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="draft_pipeline.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, type=Path, help="draft markdown (the postable text)")
    ap.add_argument("--slug", required=True, help="output file stem, e.g. pr-nous-grant-selfheal")
    ap.add_argument("--tab", required=True, choices=["issue draft", "pull request draft"])
    ap.add_argument("--title", required=True)
    ap.add_argument("--repo", default="NousResearch/hermes-agent")
    ap.add_argument("--badge", default="DRAFT — NOT POSTED")
    ap.add_argument("--owners", default=None, help="header attribution (external: poster handle)")
    ap.add_argument("--state", default=None, help="header state line")
    ap.add_argument("--drafts-root", type=Path, default=DEFAULT_DRAFTS_ROOT)
    ap.add_argument("--batch", default=None, help="batch folder, e.g. '2026-09-16 - 22h48m'")
    ap.add_argument("--verify-against", action="append", default=[],
                    help="REF:PATH or path a quoted fence must appear in (repeatable)")
    ap.add_argument("--evidence", action="append", default=[], type=Path,
                    help="file whose quoted ```text lines must appear verbatim (repeatable)")
    ap.add_argument("--allow-identifier", action="append", default=[],
                    help="literal allowed despite the denylist (repeatable)")
    ap.add_argument("--skip-visual-gate", action="store_true",
                    help="skip the advisory visual-value gate (default: run it)")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--diff-base", type=Path, default=None,
                    help="previous revision of the same draft: renderer highlights the change")
    ap.add_argument("--rev-dir", type=Path, default=None,
                    help="dir with <slug>.prev.md; the renderer diffs against it automatically")
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args(argv)

    if not args.src.is_file():
        print(f"ERROR: --src not found: {args.src}")
        return 2
    md_text = args.src.read_text(encoding="utf-8")
    results: list[tuple[str, bool, str]] = []

    ok, out = prose_gate(args.src, args.python)
    results.append(("prose (check-prose.py)", ok, out))

    if args.skip_visual_gate:
        results.append(("visual value (advisory)", True, "skipped via --skip-visual-gate"))
    else:
        ok, out = visual_gate(args.src, args.python)
        results.append(("visual value (advisory)", ok, out))
        mg = mermaid_gate(args.src, args.python)
        if mg is not None:
            ok, out = mg
            results.append(("mermaid fences (check-mermaid.py)", ok, out))

    ok, hits = identifier_gate(md_text, args.allow_identifier)
    results.append(("identifier sweep", ok, "clean" if ok else str(hits)))

    srcs = [(r, resolve_source(r)) for r in args.verify_against]
    if srcs:
        ok, notes = verbatim_gate(md_text, srcs)
        results.append(("verbatim fences", ok, "; ".join(notes)))
    else:
        results.append(("verbatim fences", True, "no --verify-against given (skipped)"))

    last = [l for l in md_text.strip().splitlines() if l.strip()][-1].strip()
    results.append(("oversight blockquote", last == OVERSIGHT_LINE, last[:70]))

    if args.evidence:
        ev: list[str] = []
        for f in args.evidence:
            ev.extend(normalize_evidence(f.read_text(encoding="utf-8")))
        ok, bad = text_fence_gate(md_text, ev)
        results.append((f"quoted log lines ({len(ev)} evidence lines)", ok,
                        "every quoted line found" if ok else f"NOT IN EVIDENCE: {bad[:3]}"))

    batch = args.batch or datetime.now().strftime("%Y-%m-%d - %Hh%Mm")
    out_dir = args.drafts_root / batch
    out_dir.mkdir(parents=True, exist_ok=True)
    out_html = out_dir / f"{args.slug}.html"

    from shutil import copy2
    prev_html = out_html.with_name(f"{args.slug}.prev.html")
    if out_html.is_file():
        copy2(out_html, prev_html)  # SOP: keep the previous render until the new one passes the gates
    cmd = [args.python, str(RENDERER), "--in", str(args.src), "--out", str(out_html),
           "--repo", args.repo, "--tab", args.tab, "--title", args.title, "--badge", args.badge]
    if args.owners:
        cmd += ["--owners", args.owners]
    if args.state:
        cmd += ["--state", args.state]
    if args.diff_base:
        cmd += ["--diff-base", str(args.diff_base)]
    elif args.rev_dir:
        cmd += ["--rev-dir", str(args.rev_dir)]
    elif prev_html.is_file():
        cmd += ["--from-html-baseline", str(prev_html),
                "--baseline-label", "previous review render"]
    r = run(cmd)
    if r.returncode != 0:
        results.append(("render", False, (r.stderr or r.stdout).strip()[:200]))
    else:
        results.append(("render", True, r.stdout.strip().splitlines()[-1][:160]))

    if out_html.is_file():
        page = out_html.read_text(encoding="utf-8")
        ok, missing = fidelity_gate(md_text, page)
        results.append(("text fidelity", ok, "0 lines missing" if ok else f"MISSING {missing[:5]}"))
        missing_pal = [t for t in PALETTE if t.lower() not in page.lower()]
        results.append(("palette", not missing_pal, "all tokens present" if not missing_pal else str(missing_pal)))
        external = re.search(r'<link[^>]+href="http|<script[^>]+src="http|@import\s+url\(http', page)
        results.append(("self-contained", not external, "no external assets" if not external else "EXTERNAL ASSET"))
        chrome = (args.repo in page) and (args.tab in page) and ("NOT POSTED" in page)
        results.append(("chrome", chrome, "repo + tab + badge" if chrome else "chrome incomplete"))
        banner = re.search(r"(no content change|added.{0,40}removed.{0,60}|no baseline)", _flat_body(page), re.I)
        results.append(("change banner", banner is not None, banner.group(0)[:70] if banner else "none"))

    width = max(len(n) for n, _, _ in results)
    print(f"\ndraft pipeline — {args.slug}")
    print(f"  source : {args.src}")
    print(f"  review : {out_html}")
    print(f"  batch  : {batch}")
    for name, passed, note in results:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name.ljust(width)}  {note}")
    failed = [n for n, p, _ in results if not p]
    print(f"  {'ALL GATES PASS — review the HTML before posting' if not failed else 'BLOCKED: ' + ', '.join(failed)}")
    if args.open and out_html.is_file():
        webbrowser.open(out_html.resolve().as_uri())
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
