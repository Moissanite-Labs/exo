"""Search authorization uses session ownership, not display labels or log positions."""
import asyncio

import pytest

from exo.core.types import Message
from exo.protocol import schema as s
from exo.server.app import Dispatcher
from test_session_seat_binding import _engine


@pytest.mark.parametrize("seat", [None, "worker"])
def test_search_owner_boundary(tmp_path, seat):
    async def run():
        eng = _engine(tmp_path, seat)
        try:
            foreign = eng.store.create_session(agent="other", seat="worker")
            eng.store.append_message(foreign.id, Message.user("needle foreign"))
            own = eng.store.create_session(agent="worker")
            child = eng.store.create_session(agent="worker", parent=own.id, kind="subagent")
            private = eng.store.create_session(agent="worker", kind="private")
            for info in (own, child, private):
                eng.store.append_message(info.id, Message.user("needle owned"))
            response = await Dispatcher(eng).run(s.Command.of(s.Search(query="needle", limit=20)))
            assert response.ok
            expected = {own.id, child.id, private.id}
            if seat is None:
                expected.add(foreign.id)
            assert {hit["session"] for hit in response.result["results"]} == expected
            # Authorization must run before LIMIT, not discard hits afterwards.
            response = await Dispatcher(eng).run(s.Command.of(s.Search(query="needle", limit=1)))
            assert len(response.result["results"]) == 1
            if seat is not None:
                assert response.result["results"][0]["session"] != foreign.id
            assert eng.store.search("needle", session=foreign.id, owner="worker") == []
            # Recall's existing private-thread exclusion remains independent.
            assert private.id not in {h["session"] for h in eng.store.search("needle", agent="worker")}
            assert eng.store.search("needle", agent="other", owner="worker") == []
        finally:
            await eng.close()
    asyncio.run(run())
