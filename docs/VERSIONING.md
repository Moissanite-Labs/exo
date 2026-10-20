# Versioning and naming

Status: Accepted (D-056). This is how every version, tag, branch and decision in Exo is named, so each
iteration can be found again.

## 1. Exo, the product

Core, clients and plugins ship together under one version.

- **SemVer** `MAJOR.MINOR.PATCH`. Exo stays below 1.0 until the first public release.
- **MINOR** goes up per milestone: a wave of merged decisions, or a user-visible feature set.
- **PATCH** goes up for fixes only.
- **Git tag** `exo-vX.Y.Z`, annotated, with a one-line summary.
- **Pre-releases** are `X.Y.Z-rc.N`, tagged `exo-vX.Y.Z-rc.N`.
- **Codename per MAJOR**, from the exopet evolution line (`docs/EXOPET.md` on branch `design/exopet`; the egg
  stages are in `docs/BRAND.md`):

| MAJOR | Codename |
|---|---|
| 0.x | egg |
| 1.x | cell |
| 2.x | tile |
| 3.x | cube |
| 4.x | frame |
| 5 and later | 5D, 6D and so on |

- **Display form** `exo 0.2.0 (egg)`. `exo --version` prints that, plus the git sha for a checkout install:
  `exo 0.2.0 (egg), a1b2c3d`.
- **Where the number lives.** `pyproject.toml` is the source. `src/exo/__init__.py`, `clients/tui/pyproject.toml`,
  `plugins/*/plugin.yaml`, `plugins/exo-command/exo_command/__init__.py` and `uv.lock` carry the same number.
  `tests/test_versioning.py` fails if the first two or the changelog drift.
- **Starting point.** Main at the time of D-056 is 0.2.0 (egg). 0.1.0 was the scaffold.

## 2. The public site

- Versions are `site-vMAJOR.MINOR`. **MAJOR** goes up for a redesign. **MINOR** goes up with each production deploy.
- Staging candidates are `site-vX.Y-rc.N`.
- `site-v1.0` is commit `8d54b55`, the source of the live site. Its archive is tag `site-live-2026-10-13` and branch
  `archive/site-live-20261013`.
- `site-v2.0-rc.1` is commit `6b8800f`, the approved staging v2.
- `site-live-2026-10-13` and `site-staging-v2-2026-10-13` stay as aliases. They are never deleted.
- **Archive before every production deploy.** Branch `archive/site-vX.Y` for the outgoing version, plus a local
  tarball `~/Work/archive/exo-site-vX.Y.tar.gz`.
- **`scripts/deploy-site.sh`.**
  - `--prod` needs `--version site-vX.Y`. It refuses if that tag exists, then tags the deployed commit and pushes the tag.
  - Staging takes an optional `--version site-vX.Y-rc.N` and tags the same way.
  - Every deploy refuses when `site/` has untracked files, and runs a pinned Vercel CLI (never `@latest`).
  - Every deploy writes `version.json` at the site root: site version, exo version, commit, date. The footer can show it.
  - A failed copy or deploy exits non-zero and tags nothing, so the version stays free for the retry. It needs
    `rsync`. A versioned deploy refuses uncommitted changes under `site/`. An unversioned one deploys them and
    marks the commit `-dirty` in `version.json`.
  - The copy is still git-free and staging is still noindex.

## 3. Documents

- The whitepaper is `whitepaper-vX.Y`. It is v0.2 (commit `8d54b55`, tag `whitepaper-v0.2`). A new whitepaper
  version gets its tag when it is published.
- Design docs carry a status line: **Proposed**, **Accepted** or **Built**, with the decision number.

## 4. Decisions

Decisions are `D-NNN`. A number is never reused or renumbered once it is on main. D-055 is taken by the roadmap
page branch, so versioning is D-056.

## 5. The house taste pack

Versioned `house-vX.Y.Z`. The version is in `src/exo/baseline/house-taste/MANIFEST.json` (`"version"`). Tag a
release `house-vX.Y.Z`. The manifest currently says `0.0.0`, so no release exists yet.

## 6. Branches

| Prefix | For |
|---|---|
| `feat/` | a new capability |
| `fix/` | a bug fix |
| `design/` | a design that is not built yet |
| `docs/` | documentation only |
| `chore/` | housekeeping: versioning, tooling, dependencies |
| `review/` | a review or audit of someone else's change |
| `archive/` | a frozen copy, never merged (`archive/site-vX.Y`) |
| `<agent>/` | work by a named agent, such as `astra/` |

## 7. Cutting a release

1. Merge the work. Pick the next version by the rules above.
2. Bump the number in the files listed in section 1.
3. In `CHANGELOG.md`, move `[Unreleased]` entries under a new `[X.Y.Z] (codename) - date` heading.
4. Run the root suite and the plugin suites. `tests/test_versioning.py` must pass.
5. After the merge, tag main: `git tag -a exo-vX.Y.Z -m "one-line summary"` and push the tag.
