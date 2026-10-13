# Site status and delivery order

The public site describes the current local terminal and browser clients. The delivery order is **core
engine → browser experience → Electron desktop**. Electron is the chosen packaging direction, not a built
client. Packaging starts only after the core review and browser workflow checks hold. The roadmap lists
concrete exit criteria for each stage.

## Status model

The existing four statuses remain in use. No status enum was added.

| Status | Meaning |
| --- | --- |
| `built` | Tested and in daily use on main |
| `progress` | Being built or reviewed, including unmerged work |
| `designed` | Written and reviewed, with no implementation yet |
| `exploring` | A research direction or idea under test |

The tally counts only chips in the roadmap chart. Visible status text accompanies each chip so status
does not depend on color or hover. Initial HTML totals are 8 built, 2 in progress, 3 designed and 16
exploring; the script recomputes those totals from the chart. Evidence panels and section badges are not
additional initiatives.

## Evidence snapshot

The roadmap's latest verified work panel is explicitly **in review**, not built on main. Core revision
[`1532fa9`](https://github.com/aska-digital/exo/commit/1532fa9) passed CI, but [draft PR #57](https://github.com/aska-digital/exo/pull/57) is unmerged.
Reported verification: root suite 1,311 to 1,312 passed and 73 to 74 skipped by platform; combined plugins 721 passed and 1 skipped (figures read from the CI run, not from a local run); all four CI
test jobs (macOS/Linux, Python 3.11/3.13) and lint green.
[CI evidence](https://github.com/aska-digital/exo/actions/runs/38344045444).

The panel explains application authority: local automation cannot speak as the user; feedback, settings
and policy acceptance require the principal connection; derived lessons still require applicable review
and evaluation. It makes no OS isolation claim.

House taste wave 1 means layering, privacy checks, review and export tooling. The shipped pack is an empty
`0.0.0` placeholder. The first curated pack is unreleased; adaptation and controls in waves 2 and 3 remain
designs. The site no longer promises that every correction changes behavior the same day.

## Research and navigation

The roadmap adds four exploring directions: frozen delegated permissions, isolated tools with a
credential broker, durable steerable work, and admission against a shared task budget. These are not built
capabilities. [eumemic/AIOS](https://github.com/eumemic/aios) is the primary public source reference, with
[eumemic/AI Legion](https://github.com/eumemic/ai-legion) as an additional reference. Neither is a new
dependency and no code or branding was imported.

The long roadmap has section jump links. Phone navigation remains accessible in a scrollable page row,
anchor targets clear the sticky header, and the decorative floating egg is hidden on narrow screens to
keep text unobscured. The egg asset and brand link resolve from the shared script location, so
client subpages use the same site-level renderer. On narrow screens, the egg caption flows below
the hero content and wraps within its width; it keeps its absolute placement on desktop.
