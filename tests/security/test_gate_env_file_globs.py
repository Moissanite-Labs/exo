"""#310: a `.env.<suffix>` read asks in every spelling: the glob, and an expansion that ends the word.

`.env.*` matched no glob sample (the samples held only `.env`), a glob in a folder the samples do not
live in matched nothing by its head, and `.env.$(hostname)` was read as no glob at all. The rows below
ask; an expansion in the middle of a word, and a glob whose name is no store name, keep their class.
Every check classifies; nothing is run."""
import pytest

from exo import gate

POL = gate.DEFAULT_POLICIES


def outcome(command):
    c = gate.classify({"tool": "bash", "args": {"command": command}})
    return c.category, gate.decide(c, POL).outcome


@pytest.mark.parametrize("line", [
    "cat ~/proj/.env.local",                    # control: the literal name
    "cat ~/proj/.env.$(hostname)",
    "cat ~/proj/.env.${STAGE}",
    "cat ~/proj/.env.`hostname`",
    "cat ~/proj/.env.${STAGE:-local}",
    "cat .env.$STAGE",
    "cat .env.*",
    "cat proj/.env.*",                          # from HOME: a folder the samples do not live in
    "cat proj/.env*",
    "cat proj/server.*",                        # a store name at any depth, as the literal is
    "cat proj/credentials.*",
])
def test_an_env_suffix_read_asks(line):
    assert outcome(line) == ("credentials", "ask")


@pytest.mark.parametrize("line", [
    "cat build-$(date +%s).log",                # an expansion in the middle of a word
    "cat notes-$(date +%F).md",
    "cat report-$USER",                         # a trailing expansion that names no store
    "cat src/config.*",                         # config.json is a store name only under .docker/
    "cat proj/hosts.*",                         # hosts.yml only under .config/gh/
    "cat proj/*",
    "cat proj/.e*",                             # too little literal text to judge
    "cat docs/*.md",
])
def test_ordinary_words_keep_their_class(line):
    assert outcome(line) == ("read", "allow")
