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
exploring. The script recomputes those totals from the chart. Evidence panels and section badges are not
additional initiatives.

## Evidence snapshot

The roadmap's latest verified work panel is **built**: the authority work (#57), required executor mode for Exo OS (#70) and the portable ledger authority (#80) are merged to main at `a0fc85e`.

Local verification at that revision: root suite 1,485 passed and 65 skipped, six plugin suites 751 passed, core 15,836 lines under the 16,000 budget. Hosted CI was unavailable on 2026-10-13, so these are local runs. The panel links no CI run or private repository page.

The panel explains application authority: local automation cannot speak as the user. Feedback, settings
and policy acceptance require the principal connection. Derived lessons still require applicable review
and evaluation. It makes no OS isolation claim.

House taste wave 1 means layering, privacy checks, review and export tooling. The shipped pack is an empty
`0.0.0` placeholder. The first curated pack is unreleased. Adaptation and controls in waves 2 and 3 remain
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
the hero content and wraps within its width. It keeps its absolute placement on desktop.

## Waitlist, founder section and contact

The home page has three places that give a visitor a next step. None of them adds a product claim.

- **Get Exo** (`#get-exo`, also a "Get Exo" button in the hero): a one-field waitlist form (email, submit, a
  honeypot field, labels for screen readers, keyboard operable) next to the quick start from `README.md`. No
  third-party script and no tracking.
- **Who is building this** (`#founder`): the user-zero story without a name. A serial founder who runs their
  company with a multi-agent team and built the system they could not buy. Exo is built by Aska Digital. The
  first principal is the project's author (D-004). A "Meet the founder" button appears only when a URL is set.
- **Contact** in the footer, next to the page links.

Both sections are in the section nav. `main.js` highlights them the same way as the others. With thirteen
links the home nav (`nav--long` in `style.css`) takes each fit step earlier than the subpages. The button tag
goes below 1600px. Compact links start below 1440px. Below 1260px the links tighten and the button goes. Below
1040px the phone's two-row layout starts and the button comes back. The brand and the button never shrink. A
Chromium sweep from 320px to 1920px shows no page overflow, with or without a classic scrollbar.

### Configuration at deploy time (`window.EXO_SITE`)

An inline script at the top of `site/index.html` declares `window.EXO_SITE = { waitlist: "", contact: "",
founder: "" }`. The comment above it says what goes where. `site/assets/main.js` reads it on load.

| Key | Set to | Effect when set | Effect when empty |
| --- | --- | --- | --- |
| `waitlist` | A form endpoint URL (a Formspree form, a Buttondown embed-subscribe endpoint, or your own) | The form POSTs only the `email` field, form-encoded, with `Accept: application/json` through `fetch` and shows a thank-you or error line inline. A filled honeypot sends nothing and shows the thank-you line | With a `mailto:` contact, submitting opens that address with the subject "Exo waitlist". With no contact either, the form is hidden and the section shows a "Write to us" link to the Aska Digital site |
| `contact` | A `mailto:` address or a page URL | The footer Contact link points there | The footer Contact link points to the Aska Digital site |
| `founder` | A URL | The "Meet the founder" button appears and points there | The button stays hidden |

Set the values in the deployed copy, never in the repository: `tests/test_privacy.py` scans every tracked file
for email addresses and, on the maintainer's machine, for the privacy deny-list. The repository keeps the
empty defaults and the one allowed company link, `https://digital.askaconsult.com`.

Without JavaScript the form shows only when the endpoint is also in the form's `action` attribute (`id="waitlist"`).
It then submits as a normal form with both fields, so the endpoint also sees the empty `website` honeypot.
With an empty `action` the form is hidden and the "Write to us" link shows.
The subpages share `main.js` but declare no `EXO_SITE`, so they keep their footers unchanged.

The endpoint must answer cross-origin requests from the site's origin (Formspree and Buttondown do). Check the
form on staging first: a missing CORS header shows as the inline error line.
