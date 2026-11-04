# Repository work routing

Before opening or changing a PR, read [the repository workflow](docs/REPOSITORY-WORKFLOW.md) (D-089).

Shared core, plugins and web UI are implemented once in `Moissanite-Labs/exo`. Native desktop shell and packaging work belongs in `Moissanite-Labs/exo-desktop`. Do not open a matching desktop implementation PR for shared code. Desktop receives merged upstream changes through reviewed sync PRs.

`Moissanite-Labs/exo-cli` currently contains a fork snapshot. Its extraction is proposed, not complete. Until a reviewed migration changes ownership, shared changes stay here. Preserve existing terminal PR ownership and link related work across repositories.

Preserve existing author ownership and unique work during the transition. A feature-branch merge, main integration, downstream sync and live installation are separate states. Follow the existing exact-head review and verification requirements. Do not treat this policy as authorization to change an active installation or credentials.
