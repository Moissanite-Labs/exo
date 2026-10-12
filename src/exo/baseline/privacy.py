"""The house taste privacy check (D-044, docs/TASTE-BASELINE.md section 1, "The export", step 3).

Every line that would ship is checked here. A hit either **fails** (the export writes nothing) or **holds**
(the item stays out of the pack and is listed for review). Nothing here can pass an item that a rule fails.

General check, every item and the soul:

- the store's leak patterns (`exo.store.LEAKS`);
- the gate's internal-name sweep (`exo.gate.internal_names` and `normalize_scrub`): seat, registry and agent
  names, home paths, model ids, internal ids, `policies/internal-names.txt`;
- the deny list built from the source store (`store_deny_terms`): titles, ids and aliases of person,
  relationship, entity, client and project nodes, and every email address and URL host in the store;
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

from ..store import LEAKS

ALLOW_HOSTS = ("docs.python.org", "peps.python.org", "developer.mozilla.org")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL_RE = re.compile(r"\b(?:https?://|www\.)([\w.-]+)", re.I)
HOST_RE = re.compile(r"https?://([\w.-]+)", re.I)
CAP_RE = re.compile(r"\b[A-Z][\w'-]*")


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


def store_deny_terms(store, plain: frozenset[str] | None = None) -> list[str]:
    """Names the source store itself says are people, clients, projects or places in its world: the
    titles, ids and aliases of every person, relationship, entity, client and project node, the title words
    of those nodes that are not plain words, and every email address and URL host in the store's nodes and
    top-level files. `corpus/private` is not read."""
    plain = plain if plain is not None else plain_words()
    terms: list[str] = []
    texts: list[str] = []
    for nid, n in sorted(store.nodes.items()):
        texts.append(" ".join(str(v) for k, v in n.items() if k != "_path"))
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
    tl = store.root / "timeline"
    if tl.is_dir():
        texts += [p.read_text(encoding="utf-8", errors="replace") for p in sorted(tl.rglob("*.md"))]
    blob = "\n".join(texts)
    terms += EMAIL_RE.findall(blob)
    terms += [h.lower().rstrip(".") for h in HOST_RE.findall(blob)]
    seen: dict[str, None] = {}
    for t in terms:
        t = " ".join(str(t).split())
        if len(t) >= 3 and t.lower() not in plain:
            seen.setdefault(t, None)
    return list(seen)


# --- the checker --------------------------------------------------------------

class Checker:
    """One privacy check, built once per export. `deny` is `store_deny_terms`; `scrub` is the gate's
    normalised scrub (`{rules, allow}`); `plain` is the plain-word list."""

    def __init__(self, deny: Sequence[str] = (), scrub: Mapping | None = None,
                 plain: frozenset[str] | None = None, allow_hosts: Sequence[str] = ALLOW_HOSTS):
        from ..gate import name_pattern
        self.plain = plain if plain is not None else plain_words()
        self.allow_hosts = tuple(h.lower() for h in allow_hosts)
        self.deny = [(t, re.compile(name_pattern(t), re.I)) for t in deny]
        self.scrub_rules = [(r["label"], re.compile(r["pattern"], re.I)) for r in (scrub or {}).get("rules") or []]
        self.scrub_allow = set((scrub or {}).get("allow") or [])

    @classmethod
    def for_store(cls, store, env: Mapping[str, str] | None = None) -> "Checker":
        from ..gate import internal_names, normalize_scrub
        allow_file = store.root / "policies" / "house-allow.txt"
        extra = allow_file.read_text(encoding="utf-8").split() if allow_file.is_file() else []
        plain = plain_words(w for w in extra if not w.startswith("#"))
        scrub = normalize_scrub(None, internal_names(store.root, env))
        return cls(store_deny_terms(store, plain), scrub, plain)

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
