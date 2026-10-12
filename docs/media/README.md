# Media

Images and animations used by the README and the project site (`site/media/`). Everything here uses neutral
demo data: an agent called `atlas`, a public model id, and a scripted turn that fixes a failing test in a small
open-source library. Nothing is captured from a real home directory or account.

| File | What | How it is made |
|---|---|---|
| `exo-banner.svg` | Animated banner: a rotating shell of nodes around an orange core, packets travelling in. | `python3 scripts/make_media_banner.py` (stdlib only, deterministic) |
| `exo-session.svg` | Animated terminal session, 16 s loop: prompt, tools, approval, signed answer, status strip. | `python3 scripts/make_media_session.py` (stdlib only) |
| `tui-01-start.png` to `tui-05-job.png` | Terminal client: start, a turn, approval, `/context`, a background job. | `.venv/bin/python scripts/promo_shots.py` |
| `tui-06-slash.png` to `tui-09-guidance.png` | Completion (D-037): the `/co` menu with ghost text, `/model ` arguments, `@s` files, the guidance line. | `.venv/bin/python scripts/promo_shots.py` |
| `web-01-start.png` to `web-04-memory.png` | Web client: start, a turn, approval, memory column. | `.venv/bin/python scripts/promo_shots_web.py` |
| `web-05-guidance.png` to `web-07-files.png` | Web completion: the guidance line, the `/` menu, `@` files. The script types into a copy of the page, so the product has no screenshot hook. | same |
| `tui-10-composer.png` to `tui-12-classic-bottom.png` | D-046: the big composer pane, the inverse layout, a classic thread opening at the bottom. | `.venv/bin/python scripts/promo_shots.py <dir> [names]` |
| `web-08-inverse.png`, `web-09-classic-bottom.png` | D-046: the web inverse layout and a classic thread opening at the bottom. | `.venv/bin/python scripts/promo_shots_web.py layout` |

Both SVGs use only SMIL and CSS animation, no script and no external font, so GitHub shows them inside an
`<img>` tag. The CSS parts stop under `prefers-reduced-motion`.

## Regenerate

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[tui]" -e clients/tui
.venv/bin/python scripts/make_media_banner.py
.venv/bin/python scripts/make_media_session.py
.venv/bin/python scripts/promo_shots.py        # needs rsvg-convert, or headless Chrome (scripts/svg_to_png.sh; every Chrome launch here passes --use-mock-keychain --password-store=basic)
.venv/bin/python scripts/promo_shots_web.py    # needs headless Chrome; set CHROME to its path if not on macOS
```

The screenshot scripts use the in-process core with a scripted model and a stand-in source, so they need no
network and no keys. Keep each PNG under 400 KB. To publish on the site, copy the files into `site/media/`.
