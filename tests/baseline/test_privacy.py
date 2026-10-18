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


# --- the principal's own name, organisation and email domain (from where onboarding writes them) ---------------

def _verdicts(checker, *texts, value=False):
    return {t: P.verdict(checker.check(t, value=value)) for t in texts}


def test_heading_name_and_email_domain_fail_and_onboarding_words_hold(tmp_path):
    from .conftest import ORG, build_onboarded_store
    s = build_onboarded_store(tmp_path / "principals" / "quill", heading='Quill Varden ("Quill")')
    fail, hold = P.store_terms(s)[:2]
    for t in ("Quill Varden", "Quill", "Varden", f"quill@{ORG}.io", f"{ORG}.io"):
        assert t in fail, (t, fail)
    assert ORG in hold, hold
    assert P.store_terms(s).handle == ["qvarden"] and "qvarden" not in fail
    c = P.Checker.for_store(s)
    assert _verdicts(c, "Copy Varden on releases.", "varden reviews the draft.", "Ask quill before a release.",
                     f"Keep drafts in the {ORG}.io shared drive.", f"Sign commits as {ORG}.",
                     "Run the tests before a release.", "Keep chat replies short.") == {
        "Copy Varden on releases.": "fail", "varden reviews the draft.": "fail", "Ask quill before a release.": "fail",
        f"Keep drafts in the {ORG}.io shared drive.": "fail", f"Sign commits as {ORG}.": "hold",
        "Run the tests before a release.": "pass", "Keep chat replies short.": "pass"}
    assert P.verdict(c.check(f"Sign commits as {ORG}.", value=True)) == "hold"


def test_onboarding_answers_alone_keep_the_name_and_org_out(tmp_path):
    """No curated heading yet (the template's placeholder): the answers in person/identity still hold."""
    from .conftest import ORG, build_onboarded_store
    s = build_onboarded_store(tmp_path / "principals" / "quill")
    fail, hold = P.store_terms(s)[:2]
    assert not {"name", "short name", "<name>"} & {t.lower() for t in fail}
    assert {"Quill", "Varden", ORG} <= set(hold) and f"{ORG}.io" in fail, (hold, fail)
    assert not {"chat", "call", "run", "build", "tools"} & {t.lower() for t in hold}   # question and plain words
    c = P.Checker.for_store(s)
    assert _verdicts(c, "Ask quill before a release.", "VARDEN signs off.", f"Sign commits as {ORG}.",
                     "Run the tests first.", "Keep chat replies short.", "Build small tools.") == {
        "Ask quill before a release.": "hold", "VARDEN signs off.": "hold", f"Sign commits as {ORG}.": "hold",
        "Run the tests first.": "pass", "Keep chat replies short.": "pass", "Build small tools.": "pass"}
    assert P.verdict(c.check("Ask quill before a release.", value=True)) == "hold"


def test_answers_beside_a_curated_identity_node_count_too(tmp_path):
    from exo import onboard
    s = onboard.create_store("quill", tmp_path / "principals" / "quill")
    (s.nodes_dir / "person" / "identity.md").write_text(
        "---\nid: person/identity\ntitle: Who\ntype: fact\ndomain: person\nsummary: s\nload_when: w\n"
        "confidence: high\nstability: durable\nvalid_from: 2026-01-01\nsources: []\nlinks: []\ntags: []\n"
        "---\n\nBody.\n")
    s.reload()
    onboard.run_interview(s, "quill", onboard.file_asker({"identity.name": "Quill Varden"}), count_skips=False, seed=0)
    assert "person/identity-said" in s.nodes
    assert {"Quill", "Varden"} <= set(P.store_terms(s)[1])


def test_a_plain_word_name_holds_only_when_capitalised_mid_sentence(tmp_path):
    from exo.store import PrincipalStore
    s = PrincipalStore.create(tmp_path / "s")
    (s.root / "PRINCIPAL.md").write_text('# PRINCIPAL: Mark Varden ("Mark")\n', encoding="utf-8")
    t = P.store_terms(s)
    assert "Mark Varden" in t.fail and "Mark" not in t.fail and t.cased == ["Mark"]
    c = P.Checker.for_store(s)
    assert _verdicts(c, "Ask Mark before a release goes out.", "Mark the PR as a draft.", "Done. Mark it closed.",
                     "mark the item done.", "Copy Varden on releases.") == {
        "Ask Mark before a release goes out.": "hold", "Mark the PR as a draft.": "pass",
        "Done. Mark it closed.": "pass", "mark the item done.": "pass", "Copy Varden on releases.": "fail"}


def test_the_template_heading_adds_nothing(tmp_path):
    from exo.store import PrincipalStore
    s = PrincipalStore.create(tmp_path / "s")
    assert (s.root / "PRINCIPAL.md").read_text().startswith("# PRINCIPAL: <name>")
    fail, hold, cased, own, handle = P.store_terms(s)
    assert not {"name", "short name", "<name>", '"<short name>"'} & {t.lower() for t in fail}
    assert hold == [] and cased == [] and own == [] and handle == []
    assert P.store_deny_terms(s) == fail


def test_the_domain_of_every_store_email_is_denied(tmp_path):
    from exo.store import PrincipalStore
    s = PrincipalStore.create(tmp_path / "s")
    (s.root / "nodes" / "work").mkdir(parents=True, exist_ok=True)
    (s.root / "nodes" / "work" / "inbox.md").write_text(
        "---\nid: work/inbox\ntitle: inbox\ntype: rule\ndomain: work\nsummary: s\n---\nWrite to sam@example.org.\n")
    s.reload()
    assert "example.org" in P.store_deny_terms(s)
    c = P.Checker.for_store(s)
    assert P.verdict(c.check("Keep drafts in the example.org shared drive.")) == "fail"
    assert P.verdict(c.check("Use a short example in each answer.")) == "pass"     # the plain label is not a term


def test_a_half_filled_heading_keeps_the_part_that_is_filled_in(tmp_path):
    """No onboarding name answer, so the heading is the only place the name lives."""
    from exo.store import PrincipalStore
    s = PrincipalStore.create(tmp_path / "s")
    lines = ("Ask quill before a release goes out.", "varden reviews drafts.")
    for heading, named, verdicts in (
            ('Quill Varden ("<short name>")', {"Quill Varden", "Quill", "Varden"}, ("fail", "fail")),
            ('<name> ("Quill")', {"Quill"}, ("fail", "pass"))):
        (s.root / "PRINCIPAL.md").write_text(f"# PRINCIPAL: {heading}\n", encoding="utf-8")
        s.reload()
        fail = P.store_deny_terms(s)
        assert named <= set(fail) and not {"name", "short name"} & {t.lower() for t in fail}, fail
        assert _verdicts(P.Checker.for_store(s), *lines) == dict(zip(lines, verdicts))


def test_contractions_and_plain_compounds_in_an_answer_hold_nothing(tmp_path):
    from exo import onboard
    s = onboard.create_store("quill", tmp_path / "principals" / "quill")
    onboard.run_interview(s, "quill", onboard.file_asker({
        "identity.name": "Quill Varden. I don't use a nickname; it isn't, doesn't, didn't, wasn't and won't matter.",
        "identity.role": "Full-time open-source work at Varden's shop"}), count_skips=False, seed=0)
    s.reload()
    hold = {t.lower() for t in P.store_terms(s).hold}
    assert {"quill", "varden"} <= hold
    assert not {"don", "isn", "doesn", "didn", "wasn", "won", "full-time", "open-source"} & hold
    c = P.Checker.for_store(s)
    plain = ["Verify, don't assume.", "It isn't done until the tests pass.", "Ask, don't guess; it doesn't help.",
             "Work full-time on open-source tools."]
    assert _verdicts(c, *plain) == dict.fromkeys(plain, "pass")
    assert _verdicts(c, *plain, value=True) == dict.fromkeys(plain, "pass")
    assert P.verdict(c.check("Copy varden's notes into the PR.")) == "hold"


def test_the_principal_terms_match_inside_a_handle_or_a_repo_name(tmp_path):
    from .conftest import ORG, build_onboarded_store
    s = build_onboarded_store(tmp_path / "principals" / "quill", heading='Quill Varden ("Quill")')
    c = P.Checker.for_store(s)
    assert _verdicts(c, "quill-approved drafts only.", "Use quill_bot for releases.", "Push to github.com/qvarden.",
                     f"Use the {ORG}-tools repo.", f"Use {ORG}_tools.", "Keep chat replies short.") == {
        "quill-approved drafts only.": "fail", "Use quill_bot for releases.": "fail",
        "Push to github.com/qvarden.": "fail", f"Use the {ORG}-tools repo.": "hold", f"Use {ORG}_tools.": "hold",
        "Keep chat replies short.": "pass"}
    assert P.verdict(c.check("Use the identity-check helper.")) == "pass"     # older store terms: the gate's boundary


def test_an_answer_word_that_a_person_node_also_names_keeps_the_wide_match(tmp_path):
    from .conftest import build_onboarded_store, write_node
    s = build_onboarded_store(tmp_path / "principals" / "quill")
    write_node(s, "person/quill-varden", "fact", "The principal.")
    s.reload()
    t = P.store_terms(s)
    assert "quill" in {x.lower() for x in t.fail} and "quill" in {x.lower() for x in t.own}
    assert "quill" not in {x.lower() for x in t.hold}
    c = P.Checker.for_store(s)
    assert _verdicts(c, "quill-approved drafts only.", "Use the identity-check helper.") == {
        "quill-approved drafts only.": "fail", "Use the identity-check helper.": "pass"}


def test_only_the_name_answer_gives_plain_word_names(tmp_path):
    """A capitalised plain word in the role answer ("Python") is no name; a plain-word name answer still is."""
    from exo import onboard
    from .conftest import ORG
    s = onboard.create_store("mark", tmp_path / "principals" / "mark")
    onboard.run_interview(s, "mark", onboard.file_asker({
        "identity.name": "Mark Varden",
        "identity.role": f"I am a Senior Software Engineer at {ORG.title()}. Mostly I write Python and Go"}),
        count_skips=False, seed=0)
    s.reload()
    t = P.store_terms(s)
    assert t.cased == ["Mark"] and ORG.title() in t.hold, t
    c = P.Checker.for_store(s)
    lines = ("Write Python tests first.", "Prefer Python for scripts.", "Mostly, keep it short.")
    assert _verdicts(c, *lines) == dict.fromkeys(lines, "pass")
    assert _verdicts(c, *lines, value=True) == dict.fromkeys(lines, "pass")
    assert _verdicts(c, "Ask Mark before a release goes out.", f"Sign commits as {ORG}.") == {
        "Ask Mark before a release goes out.": "hold", f"Sign commits as {ORG}.": "hold"}


def test_the_principal_terms_match_next_to_digits(tmp_path):
    from .conftest import ORG, build_onboarded_store
    s = build_onboarded_store(tmp_path / "principals" / "quill", heading='Quill Varden ("Quill")')
    c = P.Checker.for_store(s)
    assert _verdicts(c, "Push to github.com/quill42.", "Push to github.com/qvarden99.", "Sign as varden2024.",
                     f"Use the {ORG}2 repo.", f"Use the 2{ORG} repo.", "Keep 2 chat replies short.") == {
        "Push to github.com/quill42.": "fail", "Push to github.com/qvarden99.": "fail", "Sign as varden2024.": "fail",
        f"Use the {ORG}2 repo.": "hold", f"Use the 2{ORG} repo.": "hold", "Keep 2 chat replies short.": "pass"}


# --- an address matches as written, the initial plus surname only as a handle, a withheld answer still counts ---

def _contact_store(tmp_path, email):
    """A store whose only identity node is a supplier reached at `email`."""
    from exo.store import PrincipalStore
    from .conftest import write_node
    s = PrincipalStore.create(tmp_path / "s")
    write_node(s, "work/supplier", "entity", f"A supplier, reached at {email}.")
    s.reload()
    return s


@pytest.mark.parametrize("domain,line", [
    ("hey.com", "Check that they compile before review."),
    ("web.de", "Prefer web design patterns that degrade well."),
    ("pm.me", "Book the 5pm meeting only when asked."),
    ("me.com", "Show me commands before running them."),
    ("me.com", "Ask me compact questions, one at a time."),
    ("live.com", "Keep live comments short."),
    ("mail.com", "Use plain email composers."),
])
def test_an_email_domain_matches_only_as_written(tmp_path, domain, line):
    s = _contact_store(tmp_path, "kit@" + domain)
    c = P.Checker.for_store(s)
    assert domain in P.store_deny_terms(s)
    assert not [str(h) for h in c.check(line) if h.label.startswith("store")]
    assert P.verdict(c.check(f"Keep drafts on {domain} for now.")) == "fail"


def test_an_email_domain_fails_with_a_subdomain_but_not_inside_a_longer_name(tmp_path):
    from .conftest import ORG
    s = _contact_store(tmp_path, f"kit@{ORG}.io")
    c = P.Checker.for_store(s)
    hits = {t: {h.label for h in c.check(t) if not h.hold} for t in (
        f"Keep drafts in the {ORG}.io drive.", f"Use mail.{ORG}.io for drafts.", f"Use my{ORG}.io for drafts.",
        f"Use {ORG}.io.example for drafts.")}
    assert hits == {f"Keep drafts in the {ORG}.io drive.": {"store name"},
                    f"Use mail.{ORG}.io for drafts.": {"store name"},
                    f"Use my{ORG}.io for drafts.": set(), f"Use {ORG}.io.example for drafts.": set()}, hits
    assert P.verdict(c.check(f"Sign commits as {ORG}.")) == "hold"           # the org's label still holds


def test_a_domain_shaped_answer_word_holds_only_as_written():
    c = P.Checker([], hold=["web.de", "pm.me"])
    lines = ("Prefer web design patterns that degrade well.", "Book the 5pm meeting.", "Keep drafts on web.de.")
    assert {t: [str(h) for h in c.check(t) if h.label == "store word"] for t in lines} == {
        lines[0]: [], lines[1]: [], lines[2]: ["store word: web.de (held)"]}


def test_the_initial_plus_surname_fails_only_as_a_handle(tmp_path):
    from exo.store import PrincipalStore
    s = PrincipalStore.create(tmp_path / "s")
    (s.root / "PRINCIPAL.md").write_text('# PRINCIPAL: Pell Rice ("Pell")\n', encoding="utf-8")
    s.reload()
    t = P.store_terms(s)
    assert t.handle == ["price"] and "price" not in {x.lower() for x in t.fail + t.hold + t.own}
    c = P.Checker.for_store(s)
    words = ("Show the price in the report header.", "Keep the price tables in scripts/price/.")
    handles = ("Ask @price before a release.", "Push to github.com/price.", "Sign as price42.", "Use the 2price repo.")
    assert _verdicts(c, *words) == dict.fromkeys(words, "pass")
    assert _verdicts(c, *handles) == dict.fromkeys(handles, "fail")
    assert P.verdict(c.check("Ask pell before a release.")) == "fail"        # the name itself still fails


@pytest.mark.parametrize("role", [
    "I run {org} ({org}.io); call +1 555 010 4477 or mail pell@{org}.io",
    "I have run {org} since 2015 - 2024 as founder",
])
def test_an_answer_the_node_withholds_still_keeps_the_name_and_org_out(tmp_path, role):
    from exo import onboard
    from .conftest import ORG
    s = onboard.create_store("pell", tmp_path / "principals" / "pell")
    onboard.run_interview(s, "pell", onboard.file_asker({
        "identity.name": "Pell Marrow, call me pell; mobile +44 7700 900123", "identity.role": role.format(org=ORG)}),
        count_skips=False, seed=0)
    s.reload()
    body = s.nodes["person/identity"]["_body"]
    assert body.count("(withheld from this node") == 2 and ORG not in body and "Marrow" not in body
    t = P.store_terms(s)
    assert {"pell", "marrow", ORG} <= {x.lower() for x in t.hold}, t.hold
    assert not any(x.isdigit() for x in t.hold) and not {"call", "run"} & {x.lower() for x in t.hold}
    c = P.Checker.for_store(s)
    lines = ("Ask pell before a release goes out.", "Copy Marrow on releases.", f"Sign commits as {ORG}.")
    assert _verdicts(c, *lines) == dict.fromkeys(lines, "hold")
    assert _verdicts(c, *lines, value=True) == {lines[0]: "hold", lines[1]: "fail", lines[2]: "hold"}  # Marrow: cased
    assert P.verdict(c.check("Run the tests before a release.")) == "pass"
    assert (f"{ORG}.io" in t.fail) == ("@" in role)                            # the address's domain fails


# --- the name answer written as one word, and generic words of the role answer --------------------------------

def test_the_name_answer_written_as_one_word_never_ships(tmp_path):
    """No curated heading: the name answer alone gives the joined name ("quillvarden") and the handle."""
    from .conftest import build_onboarded_store
    s = build_onboarded_store(tmp_path / "principals" / "quill")
    t = P.store_terms(s)
    assert "quillvarden" in t.hold and "qvarden" in t.handle, t
    c = P.Checker.for_store(s)
    assert _verdicts(c, "Sign commits as quillvarden.", "Push to github.com/quillvarden.",
                     "Use the quillvarden42 key.", "Push to github.com/qvarden.", "Sign as qvarden2.",
                     "Sign as qvarden.", "Keep chat replies short.") == {
        "Sign commits as quillvarden.": "hold", "Push to github.com/quillvarden.": "hold",
        "Use the quillvarden42 key.": "hold", "Push to github.com/qvarden.": "fail", "Sign as qvarden2.": "fail",
        "Sign as qvarden.": "pass", "Keep chat replies short.": "pass"}
    assert P.verdict(c.check("Sign commits as quillvarden.", value=True)) == "hold"


@pytest.mark.parametrize("answer,joined,handle", [
    ("Brin Talloway; call me Brin", {"brintalloway"}, {"btalloway"}),
    ("brin talloway", {"brintalloway"}, {"btalloway"}),
    ("Brin K. Talloway", {"brintalloway", "ktalloway"}, {"btalloway", "ktalloway"}),
    ("Call me Brin, short name Tal", set(), set()),
])
def test_the_name_answer_gives_the_joined_name_and_the_handle(tmp_path, answer, joined, handle):
    from exo import onboard
    s = onboard.create_store("brin", tmp_path / "principals" / "brin")
    onboard.run_interview(s, "brin", onboard.file_asker({"identity.name": answer}), count_skips=False, seed=0)
    s.reload()
    t = P.store_terms(s)
    assert joined <= set(t.hold) and handle <= set(t.handle) and "brink" not in t.hold, t
    if not joined:
        assert not [w for w in t.hold if w.lower() not in ("brin", "tal")] and not t.handle, t


def test_a_generic_role_word_holds_a_value_item_and_never_fails(tmp_path):
    from exo import onboard
    s = onboard.create_store("brin", tmp_path / "principals" / "brin")
    onboard.run_interview(s, "brin", onboard.file_asker({
        "identity.name": "Brin Talloway",
        "identity.role": "Senior software engineer leading a small team; I also mentor junior developers"}),
        count_skips=False, seed=0)
    s.reload()
    c = P.Checker.for_store(s)
    lines = ("Put the team before personal credit.", "Treat junior people with patience.")
    assert _verdicts(c, *lines) == dict.fromkeys(lines, "hold")
    assert _verdicts(c, *lines, value=True) == dict.fromkeys(lines, "hold")
