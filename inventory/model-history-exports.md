# AI conversation history sources

Built 2026-10-11 by saga-divan. Read-only. Counts come from metadata only (JSON keys, SQLite counts, timestamps). No conversation content was printed or copied.

Searched: `~/Downloads`, `~/Documents`, `~/Desktop`, the iCloud backup, `~/Library/Application Support/*`, `~/.claude*`, `~/.codex`, `~/.gemini`, `~/.cursor`, `~/.hermes`, and Google Drive `My Drive/` (not `backup/`).

## 1. Sources found

| # | Path | Platform | Format | Size | Date range | Count |
|---|---|---|---|---|---|---|
| 1 | `/Users/kethuda/Downloads/8dc893b8-6bc3-4b06-b76a-1d71ad55887a.zip` (inner `ttl/30d/export_data/<uuid>/prod-grok-backend.json`) | Grok (xAI account export) | Zip, JSON `{conversations, projects, tasks, media_posts}` plus 84 asset files | 56 MB zip, 24.6 MB JSON | 2025-03-29 to 2026-08-23 | 211 conversations, 4,794 responses, 4 projects, 64 media posts |
| 2 | `/Users/kethuda/Documents/team6/arif/mats/ttl/` and iCloud `Documents/hermes/temp/ttl/30d/export_data/` | Grok | Unzipped copies of #1 | 73 MB each | same | same. Arif already ingested summaries via `arif/wrk/gc1/ingest_grok.py` (source `grok_conversations`, 500-char summaries) |
| 3 | `/Users/kethuda/.hermes/state.db` and `/Users/kethuda/.hermes/profiles/*/state.db` | Hermes agent (all Protean profiles: aetherean, frida, hazen, kindi, leo, mozi, orda, proteus, shaka, sina, plus root) | SQLite, `sessions` and `messages` tables | about 4.3 GB total (proteus 1.3 GB, orda 946 MB, mozi 689 MB) | 2026-08-12 to 2026-10-11 | 9,088 sessions, 584,276 messages |
| 4 | iCloud backup `Documents/hermes/agenticsystem/23-reoptimization/profiles/*/state.db` (22 earlier snapshots in sibling folders) | Hermes, Team6 era (lugia, kodekoot, sheikh-al-jabr, azaraki, halakukhan, shayba, aetherean, kurimasu) | SQLite | about 417 MB for the last snapshot | 2026-08-12 to 2026-08-29 | 464 sessions, 52,736 messages. The live `~/.hermes/profiles/lugia` has no state.db, so this backup is the only copy |
| 5 | `/Users/kethuda/Library/Application Support/Claude/local-agent-mode-sessions/` | Claude Desktop, Cowork (local agent mode) | Claude Code JSONL transcripts inside per-session folders | 155 MB, 33 transcripts, 14 sessions | 2026-06-09 to 2026-08-09 | 7,340 messages (3,019 user turns) |
| 6 | `/Users/kethuda/Library/Application Support/Claude/claude-code-sessions/` | Claude Desktop, Code tab | JSON session metadata only | 40 KB | 2026 | 9 session records, no transcript text |
| 7 | `/Users/kethuda/.claude/projects/-Users-kethuda/` | Claude Code CLI (this machine, saga-divan) | JSONL | 53 MB, 43 files | 2026-10-09 to 2026-10-11 | 2,414 messages (896 user) |
| 8 | `/Users/kethuda/.claude/projects/-Users-kethuda-work/`, `-Users-kethuda--hermes-team-skills/` | Claude Code CLI | JSONL | 7.5 MB and 1.6 MB | 2026-10-11 | about 1,290 and 197 messages |
| 9 | `/Users/kethuda/.claude/projects/-Users-kethuda-Documents-Aetherean-*` (7 dirs) | Claude Code CLI, imported from Cowork (paths point to the old `~/Documents/Aetherean/`, which no longer exists) | JSONL | about 15 MB | 2026-07-28 to 2026-08-09 | about 3,230 messages. Two pairs are duplicates (metrade CodeV1, M's Kitchen 3stepCode) |
| 10 | `/Users/kethuda/.codex/sessions/`, `state_5.sqlite`, `thread_history_1.sqlite` | OpenAI Codex | JSONL rollouts plus SQLite | 29 MB sessions, 13 MB history | 2026-08-02 to 2026-10-11 | 27 threads, 27 rollout files. `claude-cowork-import-history.json` shows 5 Cowork transcripts imported into Codex |
| 11 | `/Users/kethuda/Library/Application Support/com.openai.chat/conversations-v3-<uuid>/` | ChatGPT desktop app cache | 81 `.data` files, app-internal and likely encrypted | 1.6 MB | IDs start 2025-05; files dated 2025-06-01 to 2025-11-18 | 81 cached conversations. Partial, not a real export |
| 12 | `/Users/kethuda/Documents/team6/arif/mats/Google/ahrazarifuddin/Takeout 2/My Activity/Gemini Apps/MyActivity.html` and `.../AI Mode/MyActivity.html` | Google Gemini and Search AI Mode | Takeout HTML activity log (taken 2026-08-23) | 265 KB and 157 KB | Gemini 2025-10-11 to 2025-10-13; AI Mode 2025-07-21 to 2025-10-29 | 10 Gemini entries, 3 AI Mode entries. `Takeout 2/Gemini/` gems and scheduled actions files are empty |
| 13 | Google Drive `My Drive/Prompt buffer.gdoc`, `Output buffer.gdoc`, `pastebin team6.gdoc`, `Structure these meeting minutes from Grok...gdoc`, `Gemini Artifacts/` | Mixed, manual paste buffers | Google Docs pointers | n/a locally | unknown | Not counted. Needs Drive API read |
| 14 | Google Drive `My Drive/vaultofsouls/` | Inter-agent notes (FROM/FOR/INGEST files, some `.jsonl`) | Text and JSONL | not measured | 2026-09 to 2026-10 | Agent-to-agent messages, not human chats |

Not history, but present: `~/.gemini/` (76 KB, Gemini CLI config and tmp, no saved chats), `~/.cursor/` and `~/Library/Application Support/Cursor/` (about 1.6 MB global storage, no meaningful chat store), `~/Library/Application Support/Grok Bot/` (27 MB Electron cache, no export), `~/Library/Application Support/Claude/IndexedDB` (1.4 MB claude.ai web cache, not a history).

## 2. What is missing

1. **claude.ai account export.** None found anywhere (no `conversations.json`, `projects.json`, `users.json`). This is the biggest gap. All claude.ai chats and Projects before Cowork and Claude Code live only on Anthropic's servers.
   - How: claude.ai > Settings > Privacy > Export data. A download link arrives by email. Do it for each account A used (personal and a@askaconsult.com, if separate).
2. **ChatGPT export.** Only an 81-item desktop cache exists, likely encrypted.
   - How: chatgpt.com > Settings > Data controls > Export data. Emailed zip with `conversations.json` and `chat.html`.
3. **Grok after 2026-08-23.** The export is seven weeks old.
   - How: grok.com or accounts.x.ai > Settings > Data > Download your data. Also X > Settings > Your account > Download an archive, for Grok-in-X chats.
4. **Gemini conversations.** Takeout only has an activity log with 10 entries.
   - How: takeout.google.com > select "Gemini" and "My Activity" with Gemini Apps included, export as JSON not HTML. Repeat for every Google account A uses.
5. **Codex cloud and ChatGPT-side Codex tasks.** Only local rollouts are here.
   - How: covered by the ChatGPT export above.
6. **Claude Code history on other machines.** Only this Mac's `~/.claude/projects` was checked. charminar and any older laptop are not covered.
   - How: copy `~/.claude/projects/` from each machine into a dated folder.
7. **Older Claude Code transcripts on this Mac.** Nothing before 2026-07-28. Claude Code prunes transcripts after about 30 days by default.
   - How: set `cleanupPeriodDays` high in `~/.claude/settings.json` so future transcripts are kept.
8. **Perplexity, Mistral, DeepSeek, Venice, Nous Portal.** Nothing found. Hermes used Nous and Venice as providers, so those turns are in the Hermes state.db files, not separate exports.

## 3. Suggested next steps for Exo

- Ask A to run the claude.ai, ChatGPT, Grok and Gemini exports now. Drop each zip in one new folder, for example `~/Work/exo/raw-exports/<platform>-<date>/`, and treat as untrusted data.
- Freeze a read-only copy of the Team6-era state.db files from the iCloud backup (#4) before that backup ages out.
- Build one normaliser into a common schema (platform, conversation id, turn, role, timestamp, model, text). Sources #1, #3, #4, #5, #7 to #10 are parseable today.
