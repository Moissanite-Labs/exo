"""Shared fixtures: a real CoreEngine with a scripted fake provider, a template store, and a learner."""
import json

import pytest

from exo.core.engine import CoreEngine
from exo.core.models.fake import FakeProvider
from exo.learn.learner import Learner
from exo.protocol import schema as s

MODELS = {"roles": {"main": ["fake:x"], "aux": ["fake:x"], "eval": ["fake:x"]}, "fake": {"reply": "ok"}}


@pytest.fixture
def make_engine(tmp_path, template_store):
    """`make_engine(script=[...], store=True)` -> engine whose fake provider answers from `script`."""
    def build(script=None, *, store=True, repeat=False):
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        if store:
            (home / "config.json").write_text(json.dumps({"learn": {"store": str(template_store.root)}}))
        eng = CoreEngine(home, MODELS, cwd=str(tmp_path), env={})
        eng.fake = FakeProvider(script or [], repeat=repeat)
        eng.router.instances["fake"] = eng.fake
        return eng
    return build


@pytest.fixture
def engine(make_engine):
    return make_engine()


@pytest.fixture
def learner(engine):
    lr = Learner(engine, env={})
    engine._learn = lr
    yield lr
    lr.close()


def new_session(engine, *msgs, title="chat", kind="", parent=None):
    """Create a session and fill it with (role, text) messages; returns (session id, message ids). ``kind`` makes
    it a thread of the engine's agent (``private``: one that is not remembered, D-028; ``subagent`` with
    ``parent``: a delegate child)."""
    from exo.core.types import ContentPart, Message
    info = engine.store.create_session(title=title, model="fake:x", cwd=".", parent=parent,
                                       **({"agent": engine.agent, "kind": kind} if kind else {}))
    ids = []
    for role, text in msgs:
        m = engine.store.append_message(info.id, Message(role=role, content=[ContentPart.of_text(text)]), "t1")
        ids.append(m.id)
    return info.id, ids


def feedback(session, verdict, text="", message_id=None):
    return s.FeedbackSend(session=session, verdict=verdict, message_id=message_id, text=text)
