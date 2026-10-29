# Exopet in the workspace

Exopet is a small lattice that shows the selected conversation's activity and the growth supported by the
principal's signed ledger. It does not speak, send notifications, ask for care or lose progress during time away.
This is the first local implementation of [D-048](../EXOPET.md), built on the redesigned workspace.

## Controls

The web client's Exopet choice offers **Mini**, **Full** and **Off**. Mini is the default: a small glyph in the
brand bar. Full adds a growth card and a larger drawing in an empty conversation. Off removes the companion and
stops its status reads. The choice is local to this browser; it does not change the model or execution permissions.

The companion is still at rest. Motion follows observed activity and respects reduced motion. Its state describes
what the client has seen, such as thinking, answering, using a tool or waiting for approval. A turn ending does not
prove that the work succeeded. Switching conversations or losing the connection clears stale activity.

## Growth and trust

`/pet` reads a counts-only status through the existing command protocol. It does not call a model, create a store
or write to the ledger. The web route uses the same authenticated core connection as the rest of the workspace.
Conversation text, lesson text and other memory contents do not reach the renderer.
The terminal command prints a short summary of banked XP, recorded promotions and verification state.

Growth requires valid signatures, pinned author keys and a verified anchor. If verification fails, the current
core process keeps its last verified result and labels it frozen. Without a previous verified result, growth is
unavailable. A browser preference or cache file cannot establish growth.

Completed principal turns and teaching events earn personal XP under the versioned scoring rules. Repeated turns
within 24 hours count once, sessions receive one bonus after three completed turns, and the daily curve caps growth.
The level ladder runs from 1 to 99. Learned points count distinct historically accepted proposals, including
proposals later reverted. They do not claim that every lesson is currently active or that the full taste profile
has been measured. Unlinked promotion notes and delegated actions without completion evidence earn no XP.

Legacy ledgers require pinned signer keys and matching anchors. Stores configured for the separate ledger authority
use its authenticated checkpoint chain against the same read snapshot. That verifies a signed prefix, not a fresh
independent witness. Anchors and checkpoints are captured before the read snapshot opens, so an ordinary concurrent
append cannot appear to be a missing tail. Authority growth includes only the captured authenticated prefix; newer
events wait for the next read. A changing ledger without a confirmed prefix reports temporary unavailability rather
than an integrity alarm. Actual truncation or invalid evidence still fails verification.

An unavailable authority does not fall back to legacy verification. Last-good values are scoped
to the store and trust configuration. Full verification runs in one background worker. On a synthetic ledger of
100,000 signed events, scoring took 0.301 seconds and verification plus scoring took 58.698 seconds. The proposed
two-second full-replay target is not met. The client allows 90 seconds for a read; Off still cancels the client
request immediately. A timed-out read does not retry automatically. Incremental verification remains follow-up work.

## The shared hatch is pending

The drawing stays an egg while personal XP is banked. The community hatch has no verified date or live feed in this
implementation. There are no requests to the project site and no install reports. The future hatch must retain
this boundary: repository activity grows x0; an install's own signed history grows its personal Exo.

The renderer includes the ten planned forms so their appearance can be reviewed with synthetic data. Seeing a form
in a preview does not mean the community has hatched. The terminal glyph, native floating window, repository feed,
gallery, import/export and pet-creation skill remain separate work.

## Implementation and checks

Shared source lives in Exo. Exo Desktop receives it through the reviewed upstream sync process; this PR does not
install or restart the desktop client. The core projection, client state reducer and renderer are tested separately,
then together through the authenticated web interface. Fixtures use synthetic conversations and signed test ledgers.

Run `PYTHONPATH=src:plugins/exo-command python scripts/shots_exopet.py OUTDIR` to reproduce the synthetic previews:
[dark workspace](exopet-dark.png), [light workspace](exopet-light.png), [mobile](exopet-mobile.png) and
[form studies](exopet-forms.png). The samples do not read a real principal store or call a model.
