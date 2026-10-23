# Budgeted skill discovery

**Status:** Implemented for review (D-083, [#504](https://github.com/aska-digital/exo/issues/504)).

Skill discovery selects relevant skill descriptions for each turn. It uses local text matching,
with no model calls, network service or extra dependencies. Full instructions still load through
the existing `skill` tool. Selection is off by default.

## Enable it

```sh
exo config set skill_discovery '{"enabled":true,"max_chars":2048,"top_k":6}'
exo stop
exo
```

The `skills` capability must be enabled. For a lean profile, first run
`exo harness enable skills`. Existing profiles do not enable discovery automatically.
Restart the shared core after changing these settings.

| Setting | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Replace the full skill catalog in prompts with a per-turn shortlist. |
| `max_chars` | `2048` | Maximum characters contributed by the block, including its heading and joining blank line. Allowed range: 0–32768. |
| `top_k` | `6` | Maximum skill rows. Allowed range: 0–50. |

`max_chars` is a hard character ceiling, not an exact token limit. It covers discovery text,
not tool schemas, loaded skill bodies or the rest of the conversation. A zero budget, or one
too small for the heading, contributes no discovery text. `top_k: 0` omits skill rows but can
retain the short instruction explaining how to load a skill by name.

To restore the existing catalog, set `skill_discovery.enabled` to `false` and restart.
Removing the `skill_discovery` object does the same. Missing settings retain existing behavior.
An explicitly disabled `skills` capability suppresses discovery and the skill tool together.

## What is selected

Exact skill names take priority. Other matches use BM25, which scores shared words in the
request, skill names and descriptions. Ties use the skill name. There is no stemming, synonym
expansion or model reranking. A request with no matches gets the short access instruction.

Only names already present in the engine's enabled skill index are considered. Seat allowlists,
including an empty list, remain in force. Folder skills retain precedence over plugin skills.
Explicit plugin descriptions apply only when that plugin supplied the selected file.

The selector reads at most 16 KiB from each indexed file and uses only its frontmatter description.
It keeps at most 300 description characters for ranking. Missing or nonregular files are omitted.
On supported Unix systems, final file symlinks are refused. Oversized or absent frontmatter has
no usable description, but the existing name can still match.

Descriptions are reread for every selection, so same-size edits with unchanged timestamps still
take effect. No query or result cache is shared between sessions. Adding or renaming skills
requires a restart. The existing client skill listing remains the startup index.

The starting request contributes at most its first 8192 characters to ranking. Selection runs
at the start of each turn's tool loop. A steer handled inside that loop does not rerank it.
Queued input starting another turn does. Scheduled tasks select from their own request.
Delegated children receive no shortlist because their existing toolset excludes skills.

## Explicit access and boundaries

The shortlist does not restrict the skill loader. Ask for an enabled skill by its full name,
or its unique plugin suffix, even when it was not selected. Normal skill lookup and approval
rules still apply. A disabled skill cannot be loaded through this mechanism.

The full stable catalog is removed only for this opt-in mode. Other stable system instructions
stay before the shortlist. Updating a seat's `SOUL.md` does not restore the full catalog.
Metadata is local guidance and cannot become principal approval or a pinned ruling.

Required-executor mode rejects enabled discovery before plugin loading. Metadata refresh uses
host reads today. An executor-backed reader must be implemented before that combination is supported.
Turning discovery off creates no selector, file-refresh work, worker or network activity.

## Extension and verification

`exo.skill_discovery` owns settings validation, bounded metadata reads and ranking. Composition
passes `skills_prompt=False` to the runtime, preserving its enabled index and loader. The typed
`Loop.turn_extra(session, query)` callback supplies the block after stable system text.
The scheduler copies that callback to its own loop. It is synchronous and must stay bounded.

`SkillDiscovery.select(query)` returns text, selected names, considered-file count and unavailable
names. It does not mutate the index or remember a session's selection. Use it for local diagnostics.
`render(session, query)` is the loop callback. No protocol command or dependency was added.

Tests exercise actual composed model requests, exact default prompt preservation, explicit loading
outside the shortlist, scheduled and concurrent turns, edited metadata, SOUL refresh, seat filtering,
plugin precedence, invalid configuration, special files and character ceilings. They use fake models
and temporary stores. Reduced prompt text is not evidence of improved task success.

The design draws on the [Hermes Skill Retrieval example](HERMES-PLUGIN-OPPORTUNITIES.md#11-budgeted-skill-discovery).
This implementation uses standard BM25 scoring and copies no upstream implementation.
