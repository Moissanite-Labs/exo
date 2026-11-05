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


@pytest.mark.parametrize("recall", [False, True], ids=["protocol-history", "agent-log-recall"])
def test_shared_bm25_statistics_can_reorder_owned_hits(tmp_path, recall):
    """Characterize the known ranking residual; an owner-isolated ranking fix should replace this expectation."""
    async def run():
        eng = _engine(tmp_path, "worker")
        try:
            own = eng.store.create_session(agent="worker")
            foreign = eng.store.create_session(agent="other")
            first = eng.store.append_message(own.id, Message.user("beaconzebra beaconzebra beaconzebra controlterm"))
            second = eng.store.append_message(own.id, Message.user("beaconzebra controlterm controlterm controlterm"))
            # Give both query terms positive IDF before the foreign insert; these rows never match the query.
            for _ in range(3):
                eng.store.append_message(own.id, Message.user("neutral background filler"))

            async def hits():
                if recall:
                    return eng.store.search("beaconzebra controlterm", agent="worker", owner="worker")
                response = await Dispatcher(eng).run(s.Command.of(s.Search(query="beaconzebra controlterm")))
                assert response.ok, response.error
                return response.result["results"]

            before = await hits()
            assert [hit["message_id"] for hit in before] == [first.id, second.id]
            eng.store.append_message(foreign.id, Message.user("beaconzebra synthetic background"))
            after = await hits()
            assert [hit["message_id"] for hit in after] == [second.id, first.id]
            assert all(hit["session"] == own.id for hit in before + after)
        finally:
            await eng.close()
    asyncio.run(run())
