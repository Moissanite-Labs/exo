"""Shared paths and a sample principal store for the exo-gate tests."""
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
for p in (str(PLUGIN), str(REPO / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

TEMPLATE = REPO / "templates" / "principal-store"

# A sample principal's rules, written the way a real store would hold them. The names are
# made up; the point is that the schema can express a real person's approval line.
EXAMPLE_GATES = """
version: 1
matrix:
  reversible: {private: act, internal: act, public: ask}
  costly: {private: act, internal: report, public: ask}
  irreversible: {private: ask, internal: ask, public: ask}
scopes:
  private: ["telegram:own-*"]
  internal: ["acme-org/*"]
categories:
  vcs_merge: act
rules:
  - match: {category: message_send, target: "telegram:own-*"}
    decision: act
    reason: messages to the principal's own channel
  - match: {category: web_post}
    decision: act
    reason: tries to lower a public post
escalate:
  cost_over_budget: true
  third_party: true
  categories: [credentials, account]
reserved:
  - credentials
  - account
  - payment
  - github_close
  - delete
  - force_push
  - naming, branding and pricing stay with the principal   # prose, ignored by code
deny:
  - 'command:push\\s.*--force.*\\b(main|master)\\b'
"""

EXAMPLE_BUDGET = """
version: 1
currency: USD
per_action_max: 10
per_day_max: 20
per_month_ceiling: 100
"""

EXAMPLE_POSTING = '''# Posting rules

Prose for humans.

```yaml
posting:
  footer: "> Posted with agent help, reviewed by a person."
  forbid:
    - {pattern: "\\u2014", label: "em dash"}
    - {pattern: '(?i)\\bthe cto (said|commented)\\b', label: "internal direction"}
  forbid_words: ["@sample-principal"]
  forbid_agent_names: true
```
'''


def write_store(root: Path) -> Path:
    (root / "policies").mkdir(parents=True)
    (root / "agents").mkdir()
    (root / "policies" / "gates.yaml").write_text(EXAMPLE_GATES)
    (root / "policies" / "budget.yaml").write_text(EXAMPLE_BUDGET)
    (root / "policies" / "posting.md").write_text(EXAMPLE_POSTING)
    (root / "agents" / "scout.md").write_text("---\nname: Scout Prime\n---\nresearch agent\n")
    return root

