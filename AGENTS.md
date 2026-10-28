# Repository work routing

Before opening or changing a PR, read [the repository workflow](docs/REPOSITORY-WORKFLOW.md) (D-089).

Shared core, plugins and web UI are implemented once in this repository. Native desktop shell and packaging work belongs in `aska-digital/exo-desktop`. Do not open a matching desktop implementation PR for shared code. Desktop receives merged upstream changes through reviewed sync PRs.

Preserve existing author ownership and unique work during the transition. A feature-branch merge, main integration, downstream sync and live installation are separate states. Follow the existing exact-head review and verification requirements. Do not treat this policy as authorization to change an active installation or credentials.
