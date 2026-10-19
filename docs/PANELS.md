# Panels

A panel is a view the model opens beside the chat with the `ui` tool (D-014). The core sends it to every
client as a `ui.panel` event. Each client draws what it can. Every panel carries `text`, a plain-words
version, so a client that cannot draw the panel (a phone notice, a voice device) still says the same thing.

## Data by kind

| kind | data |
|---|---|
| markdown | `{markdown}` (else `text` is shown) |
| diff | `{files: [{path, diff}]}` where `diff` is unified diff text |
| table | `{columns: [str], rows: [[cell, ...]]}` |
| tasks | `{tasks: [{title, status}]}` status: queued, running, done, failed |
| choice | `{prompt, options: [{id, label}]}` |
| approval | `{request_id, summary}` |
| taste | `{rules: [{rule_id, text, confidence}]}` |
| file | `{path, content, language}` |
| chart | `{labels: [str], values: [number]}` |

Clients also accept looser shapes (plain string options, row objects), but the model is told the shapes above.

## What the user did: `ui.respond`

`action` is `opened`, `dismissed`, `acted` or `ignored` (the panel closed before the user looked).
For `acted`, `value` is:

| kind | value |
|---|---|
| choice | `{choice: id}` |
| approval | `{decision: "allow" or "deny"}` |
| taste | `{rule_id, verdict: "accept" or "reject"}` |
| other | free form, for example `{line, comment}` on a diff |

Every response is a learning signal: a dismissed or ignored panel teaches when not to open one. A response to a
panel that a private thread opened or updated is not recorded (D-028): `ui.respond` returns `recorded: false`.
