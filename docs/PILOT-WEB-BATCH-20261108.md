# Web batch canary, November 8

Purpose: let the pilot work through long conversations, organize chats, attach images and maintain shared notes while preserving conversation continuity and existing controls.

Canonical implementation layers, in order:

| Responsibility | Canonical PR | Reviewed source |
| --- | --- | --- |
| Bounded web transport and transcript windowing | 1230 | 08f48e24 |
| Pinned chats and Flow mode | 1236 | c4b781da |
| Image input | 1237 | 2a697aee |
| Fluid chat width | 1228 | 3f67275b |
| Editable shared Pad and final integration fixes | 1238 | 35bd18cc |

Capability identities: `exo.web.transport`, `exo.web.chat-navigation`, `exo.web.image-input`, `exo.web.chat-pane-width` and `exo.shared-pad`. Preserve working/Stop/approval/connection visibility, strict request boundaries, draft ownership, Pad revision checks and all prior canary capabilities.

Review repaired Python 3.11 overflow lifecycle ownership, asynchronous image ownership, Pad reload/save races, valid escaped Pad transport, authorization probing and responsive navigation. Module preloads and test helpers now compose with Credentials, worker controls and the relative clock. All original author worktrees and commits remain preserved.

Composition evidence: 836 combined web and Pad tests passed on the final runtime source. Independent production-browser checks covered pins, Flow shortcuts, approval and model-picker Escape, real FileReaders, thread switching and Pad conflicts. Responsive checks at 320, 390, 768, 1024 and 1440 pixels preserve page bounds and controls. No model inference was used for these checks.

The previous canary's continuation, Full Access, unlimited-round and worker/model controls remain. The engine-only delta is a reviewed type annotation. The advisory end-turn change in PR1233 is excluded from this batch. Canonical architecture remains 39 inward imports and 8 private reads. The inherited alpha remains 44 and 8. No cap was increased.

The Pad defaults to off upstream. A explicitly authorized `shared_pad.enabled: true` for this local activation. Preserve every other configuration value. Store revision history privately. Clearing text does not erase earlier revisions. Do not import old notes automatically.

Prerequisite PR1240 pins a verified upstream tool asset consistently and passed actual hosted conformance. Its source and the contribution SOP from PR1232 are carried here. These records do not establish upstream merge or live installation. The private cutover receipt establishes the installed source, process, preserved settings and served assets.

Retained lessons: establish wakes from ingress records, invalidate attachment ownership at navigation, serialize Pad reloads and test navigation at intermediate widths. Next action: observe the installed pilot and reconcile each carry through independently reviewed upstream merges. Never reset shared work or restore an old database over new conversations.
