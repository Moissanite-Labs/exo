# Privacy and data handling

Record here, with a date, the principal's consent to ingest each source. Consent covers the
principal's own data. It does not extend to third parties who appear in it.

## Tiers

| Tier | Where | Rule |
|---|---|---|
| private raw | `corpus/private/`, `ledger.sqlite` | Mode 700/600, git-ignored. Verbatim messages, transcripts, reports. Never published, never sent to an external service, never committed. |
| curated | `nodes/`, `timeline/`, `PRINCIPAL.md`, `policies/` | Distilled knowledge. May be committed to a private repository. No secrets, contact details, health or third-party private details. |
| derived | `training/` | Regenerated from the two tiers above. Third-party text and secrets stripped by the exporter. Treat as private. |

## Hard exclusions everywhere outside `corpus/private/`

Passwords, tokens, API keys, `.env` contents, phone numbers, personal email addresses other
than the principal's own published ones, health information, and messages written by third
parties. Add any further exclusions the principal names.

## Before anything leaves this machine

Pushing this store anywhere, uploading datasets for fine-tuning, or sharing an export is an
outward action. It needs the principal's explicit approval each time, and a redaction pass
must run first.

## Sources never ingested

List here the folders, accounts and files the principal has excluded. Credential files are
always excluded.
