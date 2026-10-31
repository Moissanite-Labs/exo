# Complete heredoc-family acceptance (#328)

Eight classifier-only tests cover the whitespace-preserving delimiter terminators, named descriptor stdin isolation, and direct/piped assignment tilde bodies reported in #328. Four spaced-delimiter cases fail against the issue's historical main `8f4e5179`; all eight pass on current main `f28934a3`.

Tilde scrubbing is checked with internal and public exposures enabled. A public-only scrub policy intentionally permits internal targets without a home-path violation; that exposure control must not be mistaken for a missing expansion repair. The tests execute no shell command or post. This draft provides full-family regression coverage, not a new runtime fix or a claim of issue closure.
