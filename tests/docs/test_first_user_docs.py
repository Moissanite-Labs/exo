"""What a first user reads before and right after installing must match a fresh install (docs/DOCS-MAP.md).

Each check reads the docs as text, and where a doc quotes behaviour (the automatic Claude Code route, a provider's
roles, the installed commands) it asks the code or the packaging for the value, so the docs cannot go stale quietly.
"""
import re
import tomllib
from pathlib import Path

from exo import providers_setup
from exo.core.models.router import Router, load_catalog

ROOT = Path(__file__).resolve().parents[2]
USER_DOCS = ("README.md", "docs/USER-GUIDE.md", "docs/INSTALL.md", "docs/PROVIDERS.md", "docs/ONBOARDING.md",
             "docs/UI.md", "docs/ui/WEB.md")
GLOSSARY = "docs/USER-GUIDE.md#words-exo-uses"
TERMS = ("principal", "store", "ledger", "seat", "thread", "finding", "wave")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _prose(text: str) -> str:
    """Markdown without fenced blocks and inline code, so a quoted command or path is not read as a link."""
    return re.sub(r"`[^`\n]*`", "", re.sub(r"^```.*?^```", "", text, flags=re.M | re.S))


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert m, f"no '## {heading}' section"
    return m.group(1)


def _anchors(text: str) -> set[str]:
    """GitHub's heading anchors: lower case, punctuation dropped, spaces to hyphens."""
    return {re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-") for h in re.findall(r"^#+ (.+)$", text, re.M)}


def _automatic_main_route() -> str:
    """The route the core picks when only the claude CLI can serve main (providers_setup.main_status)."""
    table = [{"provider": "claude-cli", "ready": True, "roles": [], "transport": "claude-cli"}]
    return providers_setup.main_status(Router.from_config({}, env={}), ROOT, automatic=True, table=table)["route"]


def test_screenshots_and_links_in_user_docs_resolve():
    bad = []
    for rel in USER_DOCS:
        doc = ROOT / rel
        text = _read(rel)
        for path in re.findall(r"(?<![\w./-])((?:[\w.-]+/)+[\w.-]+\.(?:png|svg|gif))", text):
            if not (ROOT / path).exists():
                bad.append(f"{rel}: image {path}")
        for target in re.findall(r"\]\(([^)\s]+)\)", _prose(text)):
            if "://" in target or target.startswith("mailto:"):
                continue
            path, _, anchor = target.partition("#")
            dest = (doc.parent / path) if path else doc
            if not dest.exists():
                bad.append(f"{rel}: link {target}")
            elif anchor and dest.suffix == ".md" and anchor not in _anchors(dest.read_text(encoding="utf-8")):
                bad.append(f"{rel}: anchor {target}")
    assert not bad, bad


def test_readme_sections_named_by_other_docs_exist():
    headings = set(re.findall(r"^## (.+)$", _read("README.md"), re.M))
    named = [(rel, h) for rel in USER_DOCS for h in re.findall(r"README[^.\n]*?\(`## ([^`]+)`", _read(rel))]
    assert named, "no doc names a README section any more: drop this test"
    assert [(rel, h) for rel, h in named if h not in headings] == []


def test_commands_a_user_is_told_to_type_are_installed():
    scripts = set(tomllib.loads(_read("pyproject.toml"))["project"]["scripts"])
    plugins = {p.name for p in (ROOT / "plugins").iterdir()}
    skills = {p.name for p in (ROOT / "skills").iterdir() if p.is_dir()}  # `exo-intro` names a skill, not a command
    for rel in ("README.md", "docs/USER-GUIDE.md", "docs/INSTALL.md", "skills/exo-intro/SKILL.md"):
        named = set(re.findall(r"`(exo-[a-z]+)\b[^`]*`", _read(rel)))
        assert named - scripts - plugins - skills == set(), f"{rel} names a command the installer does not put on PATH"


def test_web_links_carry_the_page_token():
    for rel in ("README.md", "docs/USER-GUIDE.md", "site/app/index.html"):
        text = _read(rel)
        assert "127.0.0.1:7342" in text, rel
        bare = [m.start() for m in re.finditer(r"127\.0\.0\.1:7342(?!/#token=)", text)]
        assert bare == [], f"{rel} sends the user to the bare address, which has no page token"


def test_quick_start_says_the_repository_is_public_and_an_early_preview():
    readme = _section(_read("README.md"), "Quick start")
    m = re.search(r'id="install".*?</section>', _read("site/cli/index.html"), re.S)
    assert m, "site/cli/index.html has no #install section"
    for where, text in (("README.md Quick start", readme), ("site/cli #install", m.group(0))):
        assert "public" in text and "early preview" in text, where
        assert "private preview" not in text and "on request" not in text, f"{where}: the repository is public"
        assert text.index("early preview") < text.index("git clone"), f"{where}: say it before the clone"
        assert "git clone https://github.com/Moissanite-Labs/exo " in text, f"{where}: clone the moved repository"


def test_readme_opens_with_what_exo_is_today():
    first = " ".join(_read("README.md").split("\n# Exo\n", 1)[1].strip().split("\n\n", 1)[0].split())
    for words in ("coding agent", "terminal", "browser", "memory", "exact text", "cost", "Claude Code login",
                  "API key"):
        assert words in first, f"README's first paragraph does not say {words!r}"
    # docs/GATE.md binds the exact text for outward acts only. Other "always" answers are scoped to the target
    assert "approval to post, send or pay to the exact text" in first, "say which approvals bind the exact text"


def test_claude_code_route_is_documented_as_the_core_picks_it():
    route = _automatic_main_route()
    assert route.startswith("claude-cli:")
    providers = _section(_read("docs/PROVIDERS.md"), "Use your Claude Code login")
    first_run = _section(_read("docs/USER-GUIDE.md"), "1. Install and first run")
    readme = _section(_read("README.md"), "Quick start")
    for where, text in (("PROVIDERS.md", providers), ("USER-GUIDE.md §1", first_run), ("README.md", readme)):
        assert f"`{route}`" in text, f"{where} must name the automatic route {route}"
        assert "`unknown`" in text, f"{where} must say why cost reads unknown on that route"
        assert "PATH" in text, f"{where} must say the route needs `claude` on PATH"
    assert "Use your Claude Code login" in first_run and "exo providers default" in first_run


def test_first_run_without_a_key_is_described_as_the_terminal_client_does():
    """A keyless first run gets a note and the providers panel, or the model picker when one provider is set up and
    no main model is chosen (tests/clients/tui/test_providers.py runs it). The note is conditional: a keyless
    default (local server or CLI) gets start/login advice instead of "add a key". The docs say so, and how to
    reopen the panel."""
    from exo_tui.providers import setup_advice

    app = _read("clients/tui/exo_tui/app.py")
    m = re.search(r"\n    async def first_run_providers\(.*?(?=\n    (?:async )?def )", app, re.S)
    assert m, "first_run_providers moved: point this test at the first-run check"
    check = m.group(0)
    assert "setup_advice(" in check, "the first-run note no longer follows the default's setup advice"
    assert "self.open_providers()" in check and "self.pick_provider_default(" in check, (
        "the first-run check no longer opens the panel or the model picker: change the docs with it")
    # A keyless default must NOT be told to add a key: the note names what that default needs instead.
    keyless_server = setup_advice([{"provider": "ollama", "default": True, "ready": False,
                                     "needs": "server", "base_url": "http://127.0.0.1:11434/v1"}])
    assert "add a key" not in keyless_server, "a stopped local server must not ask for a key"
    assert "ollama" in keyless_server and "start it" in keyless_server
    keyless_cli = setup_advice([{"provider": "claude-cli", "default": True, "ready": False,
                                 "needs": "cli", "status": "not installed"}])
    assert "add a key" not in keyless_cli, "a missing CLI must not ask for a key"
    assert "log in" in keyless_cli
    # The key-needed fallback still tells the user to add a key.
    assert setup_advice([]) == "No model provider is set up yet. Type /providers to add a key."
    assert "Type /providers to add a key." in setup_advice(
        [{"provider": "anthropic", "default": True, "ready": False}])
    for where, text in (("README.md Quick start", _section(_read("README.md"), "Quick start")),
                        ("docs/INSTALL.md", _read("docs/INSTALL.md")),
                        ("USER-GUIDE.md §1", _section(_read("docs/USER-GUIDE.md"), "1. Install and first run"))):
        flat = " ".join(text.split())
        assert "opens the providers panel" in flat or "the providers panel opens" in flat, (
            f"{where} must say a keyless first run opens the providers panel")
        assert "the model picker opens instead" in flat, f"{where} must say when the model picker opens instead"
        assert "Type `/providers` to open the panel again" in flat, f"{where} must say how to reopen the panel"
        assert "tells you to type `/providers`" not in flat, f"{where} still says the first run only shows a note"


def test_user_guide_offers_no_role_limited_provider_as_the_main_model():
    first_run = _section(_read("docs/USER-GUIDE.md"), "1. Install and first run")
    limited = {name: spec["roles"] for name, spec in load_catalog()["providers"].items()
               if spec.get("roles") and "main" not in spec["roles"]}
    assert "muse-cli" in limited, "the catalog no longer limits muse-cli: revisit USER-GUIDE §1"
    for paragraph in first_run.split("\n\n"):
        for name, roles in limited.items():
            if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", paragraph):
                assert any(re.search(rf"\b{role}\b", paragraph) for role in roles), (
                    f"USER-GUIDE §1 names {name} without saying it serves only {roles}")


def test_glossary_comes_before_its_words():
    guide = _read("docs/USER-GUIDE.md")
    glossary = _section(guide, "Words Exo uses")
    start = guide.index("## Words Exo uses")
    for term in TERMS:
        assert re.search(rf"^\| {term} \|", glossary, re.M), f"the glossary does not define {term!r}"
        first = re.search(rf"\b{term}s?\b", guide, re.I)
        assert first and first.start() > start, f"USER-GUIDE uses {term!r} before the glossary"
    readme = _read("README.md")
    link = readme.find(f"]({GLOSSARY})")
    assert link >= 0, "README does not link the glossary"
    for term in TERMS:
        first = re.search(rf"\b{term}s?\b", readme[readme.index("\n# Exo\n"):], re.I)
        assert first is None or readme.index("\n# Exo\n") + first.start() > link, (
            f"README uses {term!r} before it links the glossary")
