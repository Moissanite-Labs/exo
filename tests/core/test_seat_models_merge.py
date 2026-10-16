"""A seat's [models] tables merge over config.json's entry by entry, never replace the whole table (hermetic: env={})."""

import asyncio
import json
import tomllib

import pytest

from exo.config import Config
from exo.core import seats
from exo.core.engine import CoreEngine


@pytest.fixture
def engine(tmp_path):
    made = []

    def build(models, seat_models, name="scout"):
        seat = seats.parse({"seat": {"name": name}, "models": seat_models})
        eng = CoreEngine(tmp_path / f"home-{len(made)}", models, env={}, cwd=str(tmp_path), seat=seat)
        made.append(eng)
        return eng

    yield build
    for eng in made:
        asyncio.run(eng.close())


def test_seat_role_adds_to_config_roles(engine):
    eng = engine({"roles": {"main": ["fake:a"], "aux": ["fake:b"]}}, {"roles": {"deep": ["fake:c"]}})
    roles = eng.router.roles
    assert roles["main"] == ["fake:a"] and roles["aux"] == ["fake:b"] and roles["deep"] == ["fake:c"]
    assert [r.ref for r in eng.router.chain("aux")] == ["fake:b", "fake:a"]
    assert eng._automatic_main is False  # config.json named main: the engine keeps it


def test_seat_role_wins_on_conflict_and_leaves_the_rest(engine):
    eng = engine({"roles": {"main": ["fake:a"], "aux": ["fake:b"]}}, {"roles": {"main": ["fake:z"]}})
    assert eng.router.roles["main"] == ["fake:z"] and eng.router.roles["aux"] == ["fake:b"]


def test_seat_can_blank_one_role(engine):
    eng = engine({"roles": {"main": ["fake:a"], "decision": ["fake:d"]}}, {"roles": {"decision": []}})
    assert eng.router.roles["decision"] == [] and eng.router.roles["main"] == ["fake:a"]


def test_seat_price_adds_to_config_prices(engine):
    eng = engine({"roles": {"main": ["fake:a"]}, "models": {"fake:a": {"input": 1, "output": 2}}},
                 {"models": {"fake:c": {"input": 3}, "fake:a": {"input": 5, "output": 6}}})
    assert eng.router.models["fake:c"]["input"] == 3
    assert eng.router.models["fake:a"]["input"] == 5 and eng.router.models["fake:a"]["output"] == 6
    other = engine({"roles": {"main": ["fake:a"]}, "models": {"fake:a": {"input": 1, "output": 2}}},
                   {"models": {"fake:c": {"input": 3}}})
    assert other.router.models["fake:a"] == {"input": 1, "output": 2}


def test_seat_provider_adds_to_config_providers_and_replaces_one_entry_whole(engine):
    p1 = {"transport": "openai", "base_url": "http://127.0.0.1:1/v1", "key_env": "P1_KEY"}
    p2 = {"transport": "openai", "base_url": "http://127.0.0.1:2/v1"}
    seat_p1 = {"transport": "openai", "base_url": "http://127.0.0.1:3/v1"}
    eng = engine({"roles": {"main": ["p1:m"]}, "providers": {"p1": p1, "p2": p2}}, {"providers": {"p1": seat_p1}})
    assert eng.router.providers["p2"] == p2
    assert eng.router.providers["p1"] == seat_p1  # no key_env carried over to the seat's base_url


def test_seat_fake_table_merges_key_by_key(engine):
    rules = [{"match": "hi", "steps": ["yo"]}]
    eng = engine({"roles": {"main": ["fake:a"]}, "fake": {"rules": rules, "reply": "A"}}, {"fake": {"reply": "B"}})
    fake = eng.router.instances["fake"]
    assert fake.reply == "B" and fake.rules == rules


def test_seat_merge_leaves_the_callers_config_untouched(engine):
    models = {"roles": {"main": ["fake:a"]}, "models": {"fake:a": {"input": 1}}}
    eng = engine(models, {"roles": {"deep": ["fake:c"]}, "models": {"fake:a": {"input": 9}}})
    assert models == {"roles": {"main": ["fake:a"]}, "models": {"fake:a": {"input": 1}}}
    assert eng.router.roles["main"] == ["fake:a"]


def test_local_only_config_stays_local_under_the_seats_md_example(engine, tmp_path):
    home = tmp_path / "cfg"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"models": {"local": {
        "base_url": "http://127.0.0.1:8080/v1", "model": "qwen"}}}))
    seat_toml = tomllib.loads('[models]\ntier = "fast"\nroles = { fast = ["local:qwen"] }\n')
    eng = engine(Config.load(home).models(), seat_toml["models"])
    assert eng.router.roles["main"] == ["local:qwen"]
    assert [r.ref for r in eng.router.chain("aux")] == ["local:qwen"]
    assert [r.ref for r in eng.router.chain("decision")] == ["local:qwen"]
