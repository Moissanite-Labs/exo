# Fluid chat width pilot

Capability ID: `exo.web.chat-pane-width`.

Chat messages now fill the available pane. This pilot applies exo-razi's reviewed one-line CSS proposal to alpha base `26f996f7a58ba7f408f5ead340229bccd8e3a9ad`: `.xc-msg` changes from `max-width: 76ch` to `max-width: none`.

The message width cap limited prose, tables and code on wide screens. Existing wrapping and local table/code scrolling remain in place. The composer is unchanged.

A local Chrome check served the full web UI with a fake source and real markdown rendering. At 1600 pixels, message width grew from 766 to 940 pixels and filled the pane. At 1280 and 390 pixels, width stayed within the pane. Long prose, unbroken text, wide tables and code caused no page or log overflow. Wide tables and code retained local scrolling. Composer widths were identical before and after.

The existing layout, workspace chat, chat rendering and chat view suites passed 63 tests with Chrome 156. The isolated Python 3.11.15 environment uses frozen offline resolution from unchanged `uv.lock`.

The change belongs in shared Exo web code. A separate canonical leaf carries only the CSS line. No Swarm, goal, Decisions, pad or resource-grant changes are included.

This receipt records local source and browser checks. Live refresh and review remain separate evidence. The intended refresh replaces only the web client while retaining the current core, CLI and session state.
