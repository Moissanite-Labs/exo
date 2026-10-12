# Web client look (D-031)

The web client (`plugins/exo-command/exo_command/static/`) now speaks the language of the project site
(`site/assets/style.css`, the `figure.term` section): same tokens, hairlines instead of boxes, mono uppercase
labels, `·` as the separator, orange only for the one thing that matters, cold blue for what the system knows.
Presentation only: no protocol change, the JS behaviour is the same, no build step, no new dependency, no font
or file from outside.

## Screens

| State | File |
| --- | --- |
| Boot sequence (under 1 s, any key skips) | `web-boot.png` |
| Empty start | `web-start.png` |
| A turn with tools running | `web-turn.png` |
| An approval panel and a diff panel | `web-approval.png` |
| A finished answer, memory event, peer and taste panels, tip | `web-answer.png` |
| Narrow screen | `web-narrow.png` |

## What is where

- **Brand bar.** `E X O`, the agent and thread, the tabs, the connection led. The square dot before `E X O`
  pulses orange while a turn runs and is hollow when idle.
- **Status strip** (one line, hairline above): state, served model (and `← fallback` in orange when the route
  that served differs from the one asked, from `model.call`), memory gauge (tokens the last call carried, bar
  against a nominal 200k window, from `usage.updated`), cache share of that call, session cost. While busy a
  packet travels along a dashed wire at the end of the strip. Findings are not shown: no event carries them yet.
- **Transcript.** `›` prompt in orange, agent text in fg-2, one dim line per tool (name, short args, elapsed;
  click to open the arguments and output; a small wire runs while it works), `memory · log 1-16 folded ·
  1781 → 153 tok` and `context · compacted` lines in dim with a cold label, `tip:` lines in dim italic
  (a `notice` that starts with `tip`).
- **Panels.** A thin outline with a 3px left rule. Approval is orange (`APPROVAL · HIGH RISK`) with key chips
  `[y] allow [n] deny [a] allow always`; the keys work when the composer is not focused. Taste, diff and the
  other panels are cold. A peer message has the sender in the header, a grey rule and the line "from another
  session, not from you": it never looks like the user. Panels and new transcript items reveal on arrival.
- **Composer.** Block caret in orange (`caret-shape: block`, where the browser supports it), `steer ›` instead of
  `›` while a turn runs, a cut-corner Send.
- **Boot.** Four lines go green as real calls answer: core, agent, memory (message count on record), model
  route. A line the server cannot answer stays dim as `unavailable`. Skipped under reduced motion, with
  `?boot=0`, and after the first time in a tab.

## Motion and accessibility

Every animation (pulse, packet, caret blink, reveal, boot) sits behind `prefers-reduced-motion: reduce`, which
turns them all off and hides the boot. The page is dark only, like the site, and paints its own background.

## Fix found on the way

`redraw()` in `chat.js` replaced a node with itself (the item's `el` already pointed at the new node), so a
streaming message never lost its caret and a tool line never left "running" until the next full redraw. It now
keeps the old node first.

## Reproducing the screenshots

Run `python -m exo_command` against a source, or use a fake source that replays protocol events (as
`plugins/exo-command/tests/test_chat.py` does), and capture with a headless Chrome. The screenshots here came
from such a replay of a scripted turn; nothing was captured from a live `~/.exo`.

Source of the language: the Exo site, `site/`.
