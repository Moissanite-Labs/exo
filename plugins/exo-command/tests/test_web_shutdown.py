"""Web shutdown drains real loopback handlers before its caller stops the loop."""
import asyncio
from types import SimpleNamespace

from exo_command.server import WebServer


def test_close_unsubscribes_event_stream():
    async def check():
        subscriptions = set()

        def subscribe():
            queue = asyncio.Queue()
            subscriptions.add(queue)
            return queue

        centre = SimpleNamespace(state=SimpleNamespace(seq=0), subscribe=subscribe,
                                 unsubscribe=subscriptions.remove)
        web = WebServer(centre, token="test-token")
        port = await web.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(("GET /api/stream?token=test-token HTTP/1.1\r\n"
                          f"Host: 127.0.0.1:{port}\r\n\r\n").encode())
            await writer.drain()
            await reader.readuntil(b"data: {\"seq\": 0}\n\n")
            assert subscriptions
            clients = tuple(web._conns)
            await web.close()
            assert not subscriptions
            assert not web._conns
            assert all(task.done() for task in clients)
            assert await reader.read() == b""
        finally:
            await web.close()
            writer.close()
            await writer.wait_closed()
    asyncio.run(asyncio.wait_for(check(), 5))


def test_close_drains_idle_connection():
    async def check():
        web = WebServer(None)
        port = await web.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            # Accept callbacks and their coroutine tasks can run on separate turns.
            while not web._conns:
                await asyncio.sleep(0)
            clients = tuple(web._conns)
            await web.close()
            assert not web._conns
            assert all(task.done() for task in clients)
            assert await reader.read() == b""
            await web.close()
        finally:
            await web.close()
            writer.close()
            await writer.wait_closed()
    asyncio.run(asyncio.wait_for(check(), 5))


def test_concurrent_close_waits_for_cleanup_and_survives_cancelled_waiter():
    async def check():
        web = WebServer(None)
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def request(reader, writer):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await release.wait()

        web._request = request
        port = await web.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await entered.wait()
            clients = tuple(web._conns)
            first = asyncio.create_task(web.close())
            await cleaning.wait()
            assert not first.done()
            assert all(not task.done() for task in clients)
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            second = asyncio.create_task(web.close())
            # Give the second closer a turn to prove it waits on the same drain.
            await asyncio.sleep(0)
            assert not second.done()
            assert all(not task.done() for task in clients)
            release.set()
            await second
            assert not web._conns
            assert all(task.done() for task in clients)
            assert await reader.read() == b""
            await web.close()
        finally:
            release.set()
            await web.close()
            writer.close()
            await writer.wait_closed()
    asyncio.run(asyncio.wait_for(check(), 5))


def test_handler_can_close_without_cancelling_or_joining_itself():
    async def check():
        web = WebServer(None)
        finished = asyncio.Event()

        async def request(reader, writer):
            await web.close()
            assert not asyncio.current_task().cancelling()
            finished.set()

        web._request = request
        port = await web.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await finished.wait()
            assert await reader.read() == b""
            assert not web._conns
        finally:
            await web.close()
            writer.close()
            await writer.wait_closed()
    asyncio.run(asyncio.wait_for(check(), 5))


def test_external_close_joins_handler_initiated_shutdown():
    async def check():
        web = WebServer(None)
        closed, release = asyncio.Event(), asyncio.Event()

        async def request(reader, writer):
            await web.close()
            closed.set()
            await release.wait()

        web._request = request
        port = await web.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await closed.wait()
            external = asyncio.create_task(web.close())
            await asyncio.sleep(0)
            assert not external.done()
            try:
                await web.start()
            except RuntimeError as exc:
                assert "shutdown" in str(exc)
            else:
                raise AssertionError("start replaced an unfinished shutdown")
            release.set()
            await external
            assert not web._conns
            assert await reader.read() == b""
        finally:
            release.set()
            await web.close()
            writer.close()
            await writer.wait_closed()
    asyncio.run(asyncio.wait_for(check(), 5))


def test_handler_cleanup_can_reenter_close_during_external_drain():
    async def check():
        web = WebServer(None)
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def request(reader, writer):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                await web.close()
                cleaned.set()

        web._request = request
        port = await web.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await entered.wait()
            await web.close()
            assert cleaned.is_set()
            assert not web._conns
            assert await reader.read() == b""
        finally:
            writer.close()
            await writer.wait_closed()
    asyncio.run(asyncio.wait_for(check(), 5))


def test_accept_registers_handler_before_its_first_turn():
    async def check():
        web = WebServer(None)
        requested = False
        loop = asyncio.get_running_loop()
        previous_factory = loop.get_task_factory()

        def shutdown_first(loop, coro, **kwargs):
            if coro.cr_code.co_name == "_client":
                # Queue a real shutdown drain before the accepted coroutine's
                # first turn. Acceptance must register it synchronously.
                web._close_task = asyncio.Task(web._close(None), loop=loop)
            return asyncio.Task(coro, loop=loop, **kwargs)

        async def request(reader, writer):
            nonlocal requested
            requested = True

        web._request = request
        port = await web.start()
        loop.set_task_factory(shutdown_first)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
        finally:
            loop.set_task_factory(previous_factory)
        try:
            await web.close()
            assert not requested
            assert not web._conns
            assert await reader.read() == b""
        finally:
            await web.close()
            writer.close()
            await writer.wait_closed()
    asyncio.run(asyncio.wait_for(check(), 5))
