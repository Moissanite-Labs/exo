# Posting rules

Rules for anything an agent sends outside the principal's private space: comments, pull
requests, issues, reviews, social posts, emails, client messages. These are defaults.
Replace them with the principal's own rulings and cite those rulings in a node.

## Identity

- Say whose account the post goes out from. Agents do not sign posts with their own names
  unless the principal allows it.
- If a disclosure line is required, write the exact line here and use only that line.

## Writing

- Plain, short sentences. No jargon the reader would not use.
- On spaces the principal does not own, recommend; do not command.
- Check for duplicates before posting.
- Fix a posted mistake by editing it, not by a new message that notifies everyone again.

## Gate

- Draft, check against these rules, show the final text to the principal with links, and
  wait for approval when `policies/gates.yaml` says "ask".
- Post the approved text exactly. Read back the posted URL and log it as an `artifact`
  event.

## Checks

The gate reads the block below and checks outbound text (GitHub posts, messages, emails,
social posts) before it goes out. Any violation turns the action into an ask that
lists what is wrong. Leave a value empty to switch its check off.

```yaml
posting:
  # Exact line the text must end with, for example a disclosure line. Empty = none.
  # Example: footer: "> Automated posting by agentic team with human oversight."
  footer: ""
  # Exposures the footer is required on: internal (the principal's team) and public.
  footer_exposures: [internal, public]
  # Patterns that must not appear. Each is a regex with a label, for example
  # - {pattern: "\u2014", label: "em dash"}
  forbid: []
  # Words or names that must not appear (whole word, any case), for example internal
  # project names or the principal's own handle.
  forbid_words: []
  # Also forbid the names of the principal's own agents listed under agents/.
  forbid_agent_names: true
  # Categories the checks apply to.
  applies_to: [github_post, message_send, email_send, social_post]
  # Internal identifiers that must not leave (on by default, public targets only). The gate
  # builds the list itself: seat names ($EXO_HOME/seats and the bus registry), agent names,
  # home paths (/Users/<name>, /home/<name>, ~/), model ids from the catalog and internal
  # task and thread ids. Add names one per line in policies/internal-names.txt, or below.
  scrub:
    enabled: true
    exposures: [public]
    names: []          # extra internal names: repos, hosts, project code names
    patterns: []       # extra regexes, for example {pattern: '\blrn-\d+\b', label: "lesson id"}
    allow: []          # exact strings never flagged, for example a seat name that is a common word
```
