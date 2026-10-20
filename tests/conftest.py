import contextlib

import anyio.from_thread
import pytest
from starlette.testclient import TestClient


@pytest.fixture(autouse=True)
def _testclient_single_event_loop(monkeypatch):
    """Run every TestClient request/websocket in a test on ONE shared event loop.

    Outside `with TestClient(app):` (which we avoid: it runs the lifespan's background
    loops), starlette gives each websocket_connect() its own portal thread + event loop.
    Multiplayer room tests open two sockets, so the server's broadcast to the other socket
    crosses event loops — not thread-safe — and that client's receive_json() can miss its
    wake-up and block forever. That was the intermittent full-suite hang. uvicorn runs one
    loop, so sharing one here also matches production.
    """
    stack = contextlib.ExitStack()
    shared = {}

    @contextlib.contextmanager
    def portal_factory(self):
        if self.portal is not None:
            yield self.portal
            return
        if "portal" not in shared:
            shared["portal"] = stack.enter_context(
                anyio.from_thread.start_blocking_portal(**self.async_backend))
        yield shared["portal"]

    monkeypatch.setattr(TestClient, "_portal_factory", portal_factory)
    with stack:
        yield
