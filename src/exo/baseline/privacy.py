"""The house taste privacy check (D-044, docs/TASTE-BASELINE.md section 1, "The export", step 3).

Every line that would ship is checked here. A hit either **fails** (the export writes nothing) or **holds**
(the item stays out of the pack and is listed for review). Nothing here can pass an item that a rule fails.

General check, every item and the soul:

- the store's leak patterns (`exo.store.LEAKS`);
- the gate's internal-name sweep (`exo.gate.internal_names` and `normalize_scrub`): seat, registry and agent
  names, home paths, model ids, internal ids, `policies/internal-names.txt`;
- the deny list built from the source store (`store_deny_terms`): titles, ids and aliases of person,
  relationship, entity, client and project nodes, the principal's name from the `PRINCIPAL.md` heading, and
  every email address, email domain and URL host in the store and in the onboarding answers; an address,
  domain or host matches as written (`domain_pattern`), so "me.com" is not "me com";
- the principal's own words (`store_terms`): the words of the onboarding answers to `identity.name`,
  `identity.role` and `identity.orgs-detail` (from the nodes and from the ledger, so an answer a node
  withholds counts too), the name answer's name words written as one ("quillvarden"), and the labels of every
  email domain but a public mail service's. They hold an item, a value item too, since an answer also has
  generic words ("team"), and the built-in house soul is not checked against them. A name that is a plain
  word (from the heading or the `identity.name` answer) is held when it is capitalised mid-sentence. These
  terms and the heading name match next to '-', '_' or a digit too (`own_pattern`), as in a handle or a repo
  name. The initial plus surname (from the heading or the name answer) fails only as a handle
  (`handle_pattern`: after '@' or a host's '/', or joined to a digit), since it is often a word ("Pat Rice"
  gives "price");
- email addresses, and URLs other than a short allow list of public docs;
- capitalised words that are not plain words: held for method items, failed for value items.

Value check, value items only (the stricter domain check, rulings 3 and 4): no named faith or religious
term, no prescribed practice, no party or ideology by name, no nation, nationality, ethnic group, place or
territory, no stance on a named contested question, no first person (personal history), no family role, no
health or life event. Contested questions may ship only as reasoning style, so the generic words
"religious" and "political" are allowed; the names and the prescriptions are not.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from ..store import LEAKS

ALLOW_HOSTS = ("docs.python.org", "peps.python.org", "developer.mozilla.org")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL_RE = re.compile(r"\b(?:https?://|www\.)([\w.-]+)", re.I)
HOST_RE = re.compile(r"https?://([\w.-]+)", re.I)
CAP_RE = re.compile(r"\b[A-Z][\w'-]*")
HEADING_RE = re.compile(r"^#\s*PRINCIPAL\s*:\s*(.+?)\s*$", re.M | re.I)
SAID_RE = re.compile(r"^\*\*Said \([^)]*\):\*\* (.*)$", re.M)
ASKED_RE = re.compile(r"^\*\*Q:\*\* (.*)$", re.M)
TOKEN_RE = re.compile(r"\w[\w.'\u2019-]*\w|\w")
DOMAIN_RE = re.compile(r"(?:[\w.+-]+@)?[\w-]+(?:\.[\w-]+)+")
IDENTITY_QS = ("identity.name", "identity.role", "identity.orgs-detail")
# Labels of public mail services: an address there names no organisation, so the label is no term.
MAIL_LABELS = frozenset("gmail googlemail outlook hotmail live msn yahoo ymail icloud me mac aol proton protonmail "
                        "pm gmx web mail fastmail hey zoho yandex tutanota tuta".split())


def _words(*groups: str) -> str:
    return r"\b(?:" + "|".join(w for g in groups for w in g.split()) + r")\b"


# Value-domain categories. Each is one case-insensitive regex; `_` in a word stands for a space.
CATEGORIES: tuple[tuple[str, re.Pattern], ...] = tuple((label, re.compile(rx.replace("_", r"\s+"), re.I)) for label, rx in (
    ("religion", _words(
        "islam islamic muslims? christians? christianity catholics? catholicism protestants? evangelicals?",
        "eastern_orthodox greek_orthodox jews? jewish judaism hindus? hinduism buddhists? buddhism sikhs? sikhism",
        "jains? jainism atheists? atheism agnostics? gods? allah jesus christ muhammad prophets? quran koran qur'an",
        "bible biblical torah talmud gospels? scriptures? church(?:es)? mosques? temples? synagogues? imams? rabbis?",
        "priests? pastors? pope vatican mecca sharia fatwa sins? sinful heaven hell salvation divine worship")
     + r"|(?<!good )(?<!bad )\bfaith\b"),
    ("prescribed practice", _words(
        "pray prayers? praying halal haram kosher ramadan sabbath hijab veils? fasting tithes? tithing",
        "pilgrimages? hajj zakat circumcis\\w* pork modest_dress dietary_laws?")),
    ("party or ideology", _words(
        "democrats? democratic_party republicans? gop maga labour tory tories conservative_party liberal_democrats?",
        "socialis[mt]s? communis[mt]s? capitalis[mt]s? marxis[mt]s? leninis[mt]s? maois[mt]s? fascis[mt]s? nazis?",
        "libertarians? libertarianism anarchis[mt]s? populis[mt]s? nationalis[mt]s? zionis[mt]s? feminis[mt]s?",
        "liberalism conservatism progressivism left-wing right-wing leftists? rightists? far-left far-right woke",
        "political_part(?:y|ies) elections? parliament congress")),
    ("nation or place", _words(
        "borders? territor(?:y|ies|ial) homelands? sovereignty nationalit(?:y|ies) annex\\w*",
        "america americans? usa united_states britain british uk england scotland scottish wales",
        "welsh ireland irish france french germany germans? italy italians? spain spanish portugal portuguese",
        "russia russians? ukraine ukrainians? poland china chinese japan japanese korea koreans? india",
        "indians? pakistan pakistanis? bangladesh iran iranians? iraq iraqis? syria syrians? turkey turkish egypt",
        "egyptians? saudi arabia arabs? arabic persians? kurds? kurdish israel israelis? palestine palestinians?",
        "gaza west_bank lebanon jordan afghanistan afghans? kashmir taiwan tibet crimea mexico mexicans? canada",
        "canadians? brazil brazilians? argentina nigeria nigerians? kenya ethiopia south_africa australia",
        "australians? europe europeans? asia asians? africa africans? latin_america middle_east gulf_states",
        "london paris berlin moscow beijing washington new_york jerusalem dubai istanbul karachi lahore delhi",
        "mumbai dhaka tehran riyadh cairo")),
    ("ethnic group", _words("ethnic ethnicity racial caste tribes? tribal")),
    ("contested question", _words(
        "abortion pro-life pro-choice gun_control second_amendment death_penalty capital_punishment euthanasia",
        "same-sex gay_marriage homosexual\\w* transgender lgbt\\w* immigration immigrants? migrants? refugees?",
        "deport\\w* brexit")),
    ("family", r"\b(?:my|our)\s+(?:father|mother|dad|mom|mum|parents?|wife|husband|partner|sons?|daughters?|kids?|"
               r"children|brothers?|sisters?|siblings?|grand\w+|uncles?|aunts?|cousins?|family|in-laws)\b"),
    ("personal history", r"\b(?:me|my|mine|myself)\b|(?-i:\bI\b)|\bgrowing up\b|\bchildhood\b|\bback when\b|"
                         r"\byears at\b|\bwe used to\b|\bour (?:firm|company|team|family|town|village|country)\b"),
    ("health or life event", _words(
        "diagnos\\w* illness(?:es)? diseases? cancer depress\\w* adhd autis\\w* bipolar therap(?:y|ist) medications?",
        "rehab addict\\w* divorc\\w* widow\\w* pregnan\\w* miscarr\\w* funerals? bereave\\w* died passed_away",
        "death_of married marriage wedding")),
))


@dataclass(frozen=True)
class Hit:
    label: str
    match: str
    hold: bool = False      # True: keep the item out and list it for review; False: fail the export

    def __str__(self) -> str:
        return f"{self.label}: {self.match}" + (" (held)" if self.hold else "")


# --- plain words --------------------------------------------------------------

@lru_cache(maxsize=1)
def _shipped_plain() -> frozenset[str]:
    p = Path(__file__).with_name("plain-words.txt")
    words = set()
    for ln in p.read_text(encoding="utf-8").splitlines():
        if ln.strip() and not ln.lstrip().startswith("#"):
            words.update(w.lower() for w in ln.split())
    return frozenset(words)


def plain_words(extra: Iterable[str] = ()) -> frozenset[str]:
    """The plain-word list: the shipped `plain-words.txt` plus the store's `policies/house-allow.txt`."""
    return _shipped_plain() | {w.strip().lower() for w in extra if w.strip()}


def capitalised_unknown(text: str, plain: frozenset[str]) -> list[str]:
    """Capitalised words that are likely names. At a sentence start a word passes when it is a plain word;
    anywhere else a capitalised word passes only when it is on the list too (as `JSON` or `Markdown` are)."""
    out: list[str] = []
    for m in CAP_RE.finditer(text):
        w = m.group(0).rstrip("'-")
        low = w.lower()
        if low.endswith("'s"):
            low = low[:-2]
        if low in plain:
            continue
        if w not in out:
            out.append(w)
    return out


# --- store deny list ----------------------------------------------------------

DENY_DOMAINS = {"person", "relationship"}
DENY_TAGS = {"person", "client", "project", "private"}


def _node_is_identity(n: Mapping) -> bool:
    tags = {str(t).lower() for t in (n.get("tags") or [])}
    return (str(n.get("domain") or "") in DENY_DOMAINS or str(n.get("type") or "") == "entity"
            or bool(tags & DENY_TAGS) or str(n.get("id") or "").split("/", 1)[0] in DENY_DOMAINS | {"client", "project"})


def _tokens(text: str) -> list[str]:
    """The words of `text`. A possessive or contraction loses its ending ("Ann's" is Ann, "I'm" is I), and
    a word ending in n't is dropped whole, so no piece of "don't" ("don") becomes a term."""
    return [re.sub(r"['\u2019](?:s|re|ve|ll|d|m)$", "", w, flags=re.I)
            for w in TOKEN_RE.findall(text) if not re.search(r"n['\u2019]t$", w, re.I)]


def _answer_words(qid: str, answer: str, asked: Iterable[str]) -> list[tuple[str, str]]:
    """(question id, word) for the words of one answer that hold a letter and are not words of `asked`."""
    skip = {w.lower() for q in asked for w in _tokens(q)}
    return [(qid, w) for w in _tokens(answer) if w.lower() not in skip and re.search(r"[^\W\d_]", w)]


def _identity_answers(n: Mapping) -> list[tuple[str, str, list[str]]]:
    """(question id, answer, questions) for the onboarding answers to `IDENTITY_QS` in one node.
    `onboard.render_nodes` writes one `**Said (date):** "..."` line per answer, in the order of the node's
    `sources` notes; when the two do not line up, every quoted answer in the node counts, with no question id.
    A withheld answer is not quoted here; `_ledger_answers` has it."""
    notes = [str(s.get("note") or "") for s in n.get("sources") or [] if isinstance(s, Mapping)]
    qids = [note.rsplit(" ", 1)[-1] if note.startswith("onboarding answer to ") else "" for note in notes]
    if not set(qids) & set(IDENTITY_QS):
        return []
    body = str(n.get("_body") or "")
    said = SAID_RE.findall(body)
    pick = [(q, a) for q, a in zip(qids, said) if q in IDENTITY_QS] if len(said) == len(qids) else \
        [("", a) for a in said]
    asked = ASKED_RE.findall(body)
    return [(q, a, asked) for q, a in pick if a.startswith('"')]


def _name_runs(answer: str, skip: Iterable[str], plain: frozenset[str]) -> list[list[str]]:
    """Runs of two or more name words next to each other in an `identity.name` answer ("Quill Varden; call me
    quill" has one). A name word holds a letter, is capitalised or not a plain word, and is no word of the
    question; only a space, or an initial's '.', comes between two words of a run."""
    skip = {w.lower() for q in skip for w in _tokens(q)}
    runs: list[list[str]] = [[]]
    prev, end = "", 0
    for m in TOKEN_RE.finditer(answer):
        w = (_tokens(m.group(0)) or [""])[0]
        name = (bool(re.search(r"[^\W\d_]", w)) and w.lower() not in skip
                and (w[:1].isupper() or not _plain(w, plain)))
        gap = answer[end:m.start()]
        if not (name and (gap.isspace() or len(prev) == 1 and re.fullmatch(r"\.\s*", gap))):
            runs.append([])
        if name:
            runs[-1].append(w)
        prev, end = w, m.end()
    return [r for r in runs if len(r) > 1]


def _ledger_answers(store) -> list[tuple[str, str, str]]:
    """(question id, question, answer) for every onboarding answer in the store's ledger, read without
    writing. A node withholds an answer that looks like a contact detail or a secret (`store.LEAKS`), so the
    ledger is the only place its words live."""
    path = getattr(store, "ledger_path", None)
    if path is None or not Path(path).is_file():
        return []
    from ..ledger import Ledger
    with Ledger(path) as led:
        bodies = [ev.get("body") or {} for ev in led.iter(kind="answer")]
    return [(str(b.get("question_id") or ""), str(b.get("question") or ""), str(b.get("answer") or ""))
            for b in bodies if isinstance(b, Mapping) and b.get("source") == "onboard"]


def _clean(terms: Iterable[str], plain: frozenset[str]) -> list[str]:
    out: dict[str, str] = {}
    for t in terms:
        t = " ".join(str(t).split())
        if len(t) >= 3 and t.lower() not in plain:
            out.setdefault(t.lower(), t)
    return list(out.values())


def store_deny_terms(store, plain: frozenset[str] | None = None) -> list[str]:
    """Names the source store itself says are people, clients, projects or places in its world: the
    titles, ids and aliases of every person, relationship, entity, client and project node, the title words
    of those nodes that are not plain words, the principal's name and short name from the `PRINCIPAL.md`
    heading, and every email address, email domain and URL host in the store's nodes and top-level files.
    `corpus/private` is not read."""
    return store_terms(store, plain)[0]


class StoreTerms(NamedTuple):
    """`fail` is `store_deny_terms`. `hold` is the non-plain words of the principal's onboarding identity
    answers (`IDENTITY_QS`, from the nodes and the ledger), each two words of a run of name words in the
    `identity.name` answer written as one ("Quill Varden" gives "quillvarden"), and the labels of every email
    domain but a public mail service's (`MAIL_LABELS`); a hit holds the item, a value item too. `cased` is the
    plain words of the principal's name (the heading and the `identity.name` answer) as written capitalised:
    they count only when capitalised away from a sentence start. A plain word of the role or organisation answers
    ("Python") is no name and is not a term. `own` is the part of `fail` that is the principal's own: the
    `PRINCIPAL.md` heading name, short name and their words, and any answer word that `fail` already has.
    An address, domain or host in `fail` or `hold` matches with `domain_pattern`; other terms of `own`, `hold`
    and `cased` with `own_pattern`, and the rest of `fail` with `gate.name_pattern`. `handle` is the initial
    plus surname of the heading name and of each such pair in the name answer (Ann Lee gives "alee"): often a
    plain word, so it fails only in a handle (`handle_pattern`)."""
    fail: list[str]
    hold: list[str]
    cased: list[str]
    own: list[str]
    handle: list[str]


def _plain(word: str, plain: frozenset[str]) -> bool:
    return all(p in plain or len(p) < 3 for p in re.split(r"[-.]", word.lower()))


def store_terms(store, plain: frozenset[str] | None = None) -> StoreTerms:
    """The principal's own terms beside `store_deny_terms`: see `StoreTerms`."""
    plain = plain if plain is not None else plain_words()
    terms: list[str] = []
    own: list[str] = []
    handle: list[str] = []
    said: list[tuple[str, str]] = []
    texts: list[str] = []
    answers: list[tuple[str, str, list[str]]] = []
    for qid, question, answer in _ledger_answers(store):
        texts.append(answer)                    # an address in a withheld answer still counts
        answers += [(qid, answer, [question])] if qid in IDENTITY_QS else []
    for nid, n in sorted(store.nodes.items()):
        texts.append(" ".join(str(v) for k, v in n.items() if k != "_path"))
        answers += _identity_answers(n)
        if not _node_is_identity(n):
            continue
        title = str(n.get("title") or "").strip()
        terms += [title, nid.rsplit("/", 1)[-1]]
        aliases = n.get("aliases") or []
        terms += [str(a) for a in (aliases if isinstance(aliases, list) else [aliases])]
        if str(n.get("domain")) == "person" or n.get("type") == "entity":
            terms += [w for w in re.findall(r"[A-Za-z][\w'-]+", title) if w.lower() not in plain]
    for rel in ("PRINCIPAL.md", "INDEX.md"):
        p = store.root / rel
        if p.is_file():
            texts.append(p.read_text(encoding="utf-8", errors="replace"))
            m = HEADING_RE.search(texts[-1]) if rel == "PRINCIPAL.md" else None
            if m:       # a "<name>" or "<short name>" placeholder still in the template's heading is not a name
                name, _, short = re.sub(r"<[^>]*>", "", m.group(1)).partition("(")
                names = [x for x in (name.strip(), short.strip(" )\"'\u201c\u201d")) if x]
                words = [w for x in names for w in _tokens(x)]
                full = _tokens(name)
                own += names + words
                handle += [(full[0][0] + full[-1]).lower()] if len(full) > 1 else []     # "alee"
                said += [("identity.name", w) for w in words]
    joined: list[str] = []
    for qid, answer, asked in answers:
        said += _answer_words(qid, answer, asked)
        for run in _name_runs(answer, asked, plain) if qid == "identity.name" else []:
            run = ["".join(re.findall(r"[^\W\d_]", w)).lower() for w in run]
            pairs = [(a, b) for i, a in enumerate(run) for b in run[i + 1:]]
            joined += [a + b for a, b in pairs if len(b) > 1]      # "Quill Varden" gives "quillvarden"
            handle += [a[0] + b for a, b in pairs]                  # and "qvarden"
    tl = store.root / "timeline"
    if tl.is_dir():
        texts += [p.read_text(encoding="utf-8", errors="replace") for p in sorted(tl.rglob("*.md"))]
    blob = "\n".join(texts)
    emails = EMAIL_RE.findall(blob)
    domains = [e.rsplit("@", 1)[1].lower().rstrip(".") for e in emails]
    terms += emails + domains
    terms += [h.lower().rstrip(".") for h in HOST_RE.findall(blob)]
    hold = [w for _, w in said if not _plain(w, plain)] + joined   # a plain compound ("full-time") is no name
    hold += [label for d in domains for label in d.split(".")[:-1] if label not in MAIL_LABELS]
    cased = [w for q, w in said if q == "identity.name" and w[:1].isupper() and w.lower() in plain]
    fail = _clean(terms + own, plain)
    low = {t.lower() for t in fail}
    hold = _clean(hold, plain)
    own = _clean(own + [t for t in hold if t.lower() in low], plain)    # an answer word a node also names
    return StoreTerms(fail, [t for t in hold if t.lower() not in low],
                      list(dict.fromkeys(w for w in cased if len(w) >= 3)), own,
                      [t for t in _clean(handle, plain) if t.lower() not in low])


def own_pattern(term: str) -> str:
    """Regex for one of the principal's own terms. Where `gate.name_pattern` matches a whole token only, here
    only a letter ends the match, so the term joined with '-', '_' or digits (a handle such as "ann-bot" or
    "ann42", a repo such as "acme_tools") still counts."""
    from ..gate import name_pattern
    t = term.strip()
    if re.fullmatch(r"[A-Za-z]+", t) or len(t) < 4:
        return r"(?<![^\W\d_])" + re.escape(t) + r"(?![^\W\d_])"
    return name_pattern(t)


def domain_pattern(term: str) -> str:
    """Regex for an email address, domain or host: as written, with a subdomain ("mail.acme.io" has
    "acme.io") but not a longer name ("acme.io.example" or "myacme.io"). `gate.name_pattern` would join its
    labels with any separator, and "me.com" would then match "Show me commands"."""
    return r"(?<![\w-])" + re.escape(term.strip()) + r"(?!\.?[\w-])"


def handle_pattern(term: str) -> str:
    """Regex for a term that counts only as a handle: after '@' or after a host and '/' ("@alee",
    "github.com/alee"), or joined to a digit ("alee42"). Elsewhere it is a word ("Pat Rice" gives "price")."""
    t = re.escape(term.strip())
    return r"(?:@|\.[a-z]{2,}/|\d)" + t + r"(?![^\W\d_])|(?<![^\W\d_])" + t + r"\d"


# --- the checker --------------------------------------------------------------

class Checker:
    """One privacy check, built once per export. `deny` is `store_deny_terms`; `scrub` is the gate's
    normalised scrub (`{rules, allow}`); `plain` is the plain-word list; `hold`, `cased`, `own` and `handle`
    are the fields of `StoreTerms`."""

    def __init__(self, deny: Sequence[str] = (), scrub: Mapping | None = None,
                 plain: frozenset[str] | None = None, allow_hosts: Sequence[str] = ALLOW_HOSTS,
                 hold: Sequence[str] = (), cased: Sequence[str] = (), own: Sequence[str] = (),
                 handle: Sequence[str] = ()):
        from ..gate import name_pattern
        self.plain = plain if plain is not None else plain_words()
        self.allow_hosts = tuple(h.lower() for h in allow_hosts)
        mine = {t.lower() for t in own}

        def rx(t: str, wide: bool) -> re.Pattern[str]:     # an address, domain or host matches as written
            return re.compile(domain_pattern(t) if DOMAIN_RE.fullmatch(t.strip()) else
                              own_pattern(t) if wide else name_pattern(t), re.I)
        self.deny = [(t, rx(t, t.lower() in mine)) for t in deny]
        self.deny += [(t, re.compile(handle_pattern(t), re.I)) for t in handle]
        self.hold = [(t, rx(t, True)) for t in hold]
        self.cased = [(t, re.compile(own_pattern(t))) for t in cased]
        self.scrub_rules = [(r["label"], re.compile(r["pattern"], re.I)) for r in (scrub or {}).get("rules") or []]
        self.scrub_allow = set((scrub or {}).get("allow") or [])

    @classmethod
    def for_store(cls, store, env: Mapping[str, str] | None = None) -> "Checker":
        from ..gate import internal_names, normalize_scrub
        allow_file = store.root / "policies" / "house-allow.txt"
        extra = allow_file.read_text(encoding="utf-8").split() if allow_file.is_file() else []
        plain = plain_words(w for w in extra if not w.startswith("#"))
        scrub = normalize_scrub(None, internal_names(store.root, env))
        t = store_terms(store, plain)
        return cls(t.fail, scrub, plain, hold=t.hold, cased=t.cased, own=t.own, handle=t.handle)

    def general(self, text: str, *, value: bool = False) -> list[Hit]:
        hits: list[Hit] = []

        def add(label: str, match: str, hold: bool = False) -> None:
            h = Hit(label, match, hold)
            if h not in hits:
                hits.append(h)

        for p in LEAKS:
            for m in p.finditer(text):
                add("leak", m.group(0))
        for label, rx in self.scrub_rules:
            for m in rx.finditer(text):
                if m.group(0).lower() not in self.scrub_allow:
                    add(label, m.group(0))
        for term, rx in self.deny:
            if rx.search(text):
                add("store name", term)
        for term, rx in self.hold:          # even a value item only holds: an answer has generic words too
            if rx.search(text):
                add("store word", term, hold=True)
        for term, rx in self.cased:
            if any(text[:m.start()].rstrip()[-1:] not in ("", *".!?:;\"'") for m in rx.finditer(text)):
                add("store word", term, hold=True)
        for m in EMAIL_RE.finditer(text):
            add("email", m.group(0))
        for m in URL_RE.finditer(text):
            host = m.group(1).lower().rstrip(".")
            if host not in self.allow_hosts:
                add("url", m.group(0))
        for w in capitalised_unknown(text, self.plain):
            add("capitalised word", w, hold=not value)
        return hits

    @staticmethod
    def value_domain(text: str) -> list[Hit]:
        hits: list[Hit] = []
        for label, rx in CATEGORIES:
            for m in rx.finditer(text):
                h = Hit(label, m.group(0))
                if h not in hits:
                    hits.append(h)
        return hits

    def check(self, text: str, *, value: bool = False) -> list[Hit]:
        """All hits for one line of shippable text. Value items get the value-domain check too, and a
        capitalised unknown word fails them instead of holding."""
        return self.general(text, value=value) + (self.value_domain(text) if value else [])


def verdict(hits: Sequence[Hit]) -> str:
    """'pass', 'hold' or 'fail' for a list of hits."""
    if any(not h.hold for h in hits):
        return "fail"
    return "hold" if hits else "pass"
