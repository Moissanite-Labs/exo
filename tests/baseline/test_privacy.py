"""The house taste privacy check (D-044 wave 1). Written before the check: a red table of leaking items that
must all be rejected, and a green table of neutral method and value items that must all pass."""
import pytest

from exo.baseline import privacy as P

DENY = ["Sam Rivera", "sam-rivera", "Rivera", "acme-corp", "orbit-lab", "acme-corp.example", "sam@example.org"]
SCRUB_NAMES = ["builder-seat-7", "kethuda-lead"]


@pytest.fixture(scope="module")
def checker():
    from exo.gate import normalize_scrub
    return P.Checker(DENY, normalize_scrub({"model_ids": False}, SCRUB_NAMES))


# (class, text, label the check must report)
RED = [
    # names and identity, any class
    ("method", "Send drafts to Sam Rivera before the user sees them.", "store name"),
    ("method", "Copy sam@example.org on every report.", "email"),
    ("method", "Mail reports to someone@example.invalid first.", "email"),
    ("method", "Check the numbers in the orbit-lab dashboard.", "store name"),
    ("method", "Ask builder-seat-7 to run the tests.", "internal name"),
    ("method", "Read the notes in ~/notes before answering.", "home path"),
    ("method", "Call +1 415 555 0134 when a deploy fails.", "leak"),
    ("method", "Use api_key = sk-abcdefghijklmnopqrstuv for the check.", "leak"),
    ("method", "Link the guide at https://wiki.acme-corp.example/start.", "url"),
    ("value", "Treat what Rivera says as settled.", "store name"),
    # value domain: nation, party, faith, border stance, religious prescription, personal history
    ("value", "Favour the interests of Pakistan in any comparison.", "nation or place"),
    ("value", "Americans deserve the benefit of the doubt.", "nation or place"),
    ("value", "Side with the Labour view on public spending.", "party or ideology"),
    ("value", "Socialism is the fairer way to weigh trade-offs.", "party or ideology"),
    ("value", "Muslims should be given extra patience.", "religion"),
    ("value", "Quote scripture when a moral question comes up.", "religion"),
    ("value", "Borders should stay closed to protect local jobs.", "nation or place"),
    ("value", "Annexing disputed land is never justified.", "nation or place"),
    ("value", "Remind the user to pray five times a day.", "prescribed practice"),
    ("value", "Only suggest halal food options.", "prescribed practice"),
    ("value", "Abortion is wrong and answers should say so.", "contested question"),
    ("value", "Immigration should be cut.", "contested question"),
    ("value", "When I worked at a bank I learned to show bad numbers first.", "personal history"),
    ("value", "Growing up poor taught the value of thrift.", "personal history"),
    ("value", "Respect elders the way my father did.", "family"),
    ("value", "After the divorce, trust is earned slowly.", "health or life event"),
    ("value", "Trust people from Lahore more.", "nation or place"),
    ("value", "Defer to the Zorblax council on ethics.", "capitalised word"),
]

GREEN = [
    ("method", "Write answers as short paragraphs; no bullet lists unless asked."),
    ("method", "Verify a fix by running the tests before calling it done."),
    ("method", "Lead the report with the result, then the evidence."),
    ("method", "Use plain words and short sentences."),
    ("method", "State the cost of each option in time and money."),
    ("method", "Show the diff and the test output in the PR."),
    ("method", "Keep JSON and YAML examples short."),
    ("value", "Lead with bad news; do not bury it."),
    ("value", "Weigh evidence above who said it."),
    ("value", "Say when the evidence is thin."),
    ("value", "The user's time is worth more than a complete answer."),
    ("value", "Argue in good faith and admit a mistake plainly."),
    ("value", "On contested political or religious questions, show the strongest case for each side and leave "
              "the conclusion to the user."),
    ("value", "Treat people who disagree as reasonable until shown otherwise."),
    ("value", "Weigh competing goods openly; name the trade-off rather than hiding it."),
    ("value", "Prefer evidence over authority, and tradition over novelty only when the evidence is equal."),
]


@pytest.mark.parametrize("cls,text,label", RED, ids=[r[1][:40] for r in RED])
def test_red_items_are_rejected(checker, cls, text, label):
    hits = checker.check(text, value=cls == "value")
    assert P.verdict(hits) == "fail", (text, [str(h) for h in hits])
    assert label in {h.label for h in hits}, (text, [str(h) for h in hits])


@pytest.mark.parametrize("cls,text", GREEN, ids=[g[1][:40] for g in GREEN])
def test_green_items_pass(checker, cls, text):
    hits = checker.check(text, value=cls == "value")
    assert P.verdict(hits) == "pass", (text, [str(h) for h in hits])


def test_capitalised_unknown_word_holds_a_method_item_but_fails_a_value_item(checker):
    text = "Draft the summary in Zorblax format."
    assert P.verdict(checker.check(text)) == "hold"
    assert P.verdict(checker.check(text, value=True)) == "fail"


def test_public_docs_url_is_allowed(checker):
    assert P.verdict(checker.check("Follow https://docs.python.org/3/ for style.")) == "pass"


def test_store_deny_terms_come_from_identity_nodes_emails_and_hosts(tmp_path):
    from exo.store import PrincipalStore
    s = PrincipalStore.create(tmp_path / "s")
    (s.root / "nodes" / "person" / "sam-rivera.md").write_text(
        "---\nid: person/sam-rivera\ntitle: Sam Rivera\ntype: entity\ndomain: person\nsummary: s\n"
        "aliases: [Sammy]\n---\nReach at sam@example.org or https://acme-corp.example/team.\n")
    s.reload()
    terms = P.store_deny_terms(s)
    for t in ("Sam Rivera", "sam-rivera", "Sammy", "Rivera", "sam@example.org", "acme-corp.example"):
        assert t in terms, (t, terms)
