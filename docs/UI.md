# Exo terminal UI

This is the look of `exo-tui` (D-031). The language is not new: it comes from the project site
(`site/index.html`, the "surface" section with `figure.term`, plus `site/assets/style.css`). The terminal
client was built to look like that figure, and the site stays the source of truth for the tokens. A test
(`tests/clients/tui/test_ui.py::test_tokens_match_the_site`) reads `:root` in `style.css` and fails if the
palette drifts.

## Principles

- Instruments, not chrome. Every line on screen reports something: the model that served, how full memory is,
  how much of the last call came from the cache, what it cost, how many findings are open.
- Orange is the signal. The prompt mark, the live dot, a fallback model, an approval gate, an open finding.
  Steel blue is system knowledge: memory events, taste, findings panels, peers, `/context`. Never a rainbow.
- Hairlines, not boxes. Panels are a tinted strip with a coloured left rule and a header line that runs to the
  edge and ends in a clipped-corner mark.
- Motion only reports state. A packet moves along a wire while data is in flight; a spinner turns while a tool
  runs; the live dot pulses while a turn runs; panels fade in when they arrive; the caret blinks. Nothing moves
  at rest.

## Tokens

| Role | Hex | Use |
|---|---|---|
| bg | `#08090a` | screen, brand bar, composer |
| bg-2 | `#0e1012` | panels, status strip |
| bg-3 | `#15181b` | inputs |
| deep | `#050606` | transcript (the `.term` background) |
| line / line-2 | `#24282d` / `#343a41` | hairlines, separators, unfilled gauge |
| fg / fg-2 / fg-3 | `#eceae4` / `#b4b3ad` / `#8b8b86` | text, agent prose and labels, system lines |
| accent / accent-2 | `#ff5b14` / `#ff8a4c` | signal, warm warning |
| cold | `#9fb4c4` | system knowledge |
| ok | `#4ade80` | a check that passed |
| err | `#e5534b` | the one colour not on the site, for errors |

`clients/tui/exo_tui/theme.py` holds them, a Textual `Theme` named `exo` (so `$accent`, `$cold`, `$line-2`,
`$fg-3` work in CSS) and the drawing helpers (`header_rule`, `chips`, `gauge`). Mono, uppercase labels, and
the middle dot as separator come straight from the site.

## What is on screen

1. Brand bar: `E X O`, agent, thread, and `LIVE` with a pulsing dot while a turn runs (`IDLE` otherwise).
2. Transcript: `›` and your text for a prompt (`steer ›` when sent mid-turn); agent text in fg-2 as Markdown;
   one line per tool with state glyph, name, short args, line count and elapsed time (ctrl+o expands); system
   lines dim; tips as `tip · ...` in dim italic; `memory · log 1-16 folded · 1781 → 153 tok` as a cold line.
3. Panels: approval (orange rule, `APPROVAL · <risk>`, key chips `[o] once [a] always [d] deny`), taste,
   finding and `/context` report (cold rule), peer message (double cold rule, sender and trust in the header,
   never styled as you). The side panel for `ui.panel` uses the same header and rule.
4. Activity: a wire with a travelling packet and `waiting for <model> 12s`, `running tool`.
5. Composer: block caret in the signal colour, placeholder hint, `steer ›` while busy, `answer ›` for a question.
6. Status strip: `MODEL` (the route that served, in orange with `← requested` when it fell back), `MEM` gauge,
   `CACHE` share of the last call, `COST`, open findings.
7. Start: a boot sequence (under a second, any key skips it) that marks `core`, `agent`, `memory`, `model` as
   each real step finishes, then an empty-start card with the keys.

Where the numbers come from, all from events the core already sends: `model.call` (`requested`, `route`,
`usage.cache_read_tokens`), `usage.updated` (context tokens, session cost), `memory.summarized`,
`peer.message`, `taste.updated`, `tool.completed` (`duration_ms`), and the findings count the client polls.

## Switches

- `EXO_REDUCED_MOTION=1`: no boot sequence, no spinner, packet, pulse, fade or caret blink. The state is still
  shown, as still glyphs.
- `NO_COLOR`: the `exo-plain` theme paints nothing (the terminal's own colours); orange becomes bold, dim stays
  dim, so the shape still reads.
- 256-colour terminals: Textual and Rich downgrade the hex values; the palette was chosen so orange, blue and
  grey stay distinct. The app paints its own background, so light terminals are not blinded.

## Screenshots

Made by `scripts/ui_shots.py` (Textual pilot, `save_screenshot`, in-process core with the scripted model) and
converted by `scripts/svg_to_png.sh` (rsvg-convert, else headless Chrome, else qlmanage).

| State | Image |
|---|---|
| Boot sequence | `docs/ui/01b-boot.png` |
| Empty start | `docs/ui/01-empty.png` |
| A turn with tools running, fallback and cache in the strip | `docs/ui/02-tools.png` |
| Approval panel | `docs/ui/03-approval.png` |
| Finished answer with tips, taste panel and a memory event | `docs/ui/04-answer.png` |
| `/context` report | `docs/ui/05-context.png` |
| 80x24 terminal | `docs/ui/06-narrow-80x24.png` |

SVG sources sit beside the PNGs.

## Credit

The design language, colours, type choices and the terminal figure belong to the Exo site. This client follows
them; it adds no borrowed branding.
