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
does not depend on color or hover. Initial HTML totals are 10 built, 1 in progress, 2 designed and 16
exploring; the script recomputes those totals from the chart. Evidence panels and section badges are not
additional initiatives.

## Evidence snapshot

The roadmap's latest verified work panel is **built**: the authority work (#57), required executor mode for Exo OS (#70) and the portable ledger authority (#80) are merged to main at `a0fc85e`. Local verification at that revision: root suite 1,485 passed and 65 skipped; six plugin suites 751 passed; core 15,836 lines under the 16,000 budget. Hosted CI was unavailable on 2026-10-13, so these are local runs; the panel links no CI run or private repository page.

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
