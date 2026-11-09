import logging

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.bot.tracing import TracedAiohttpSession, method_name, summarize

TOKEN = "123456:SECRET-token-value"


def test_method_name_drops_the_token():
    assert method_name(f"/bot{TOKEN}/editMessageMedia") == "editMessageMedia"


def test_summary_lists_phases_relative_to_start():
    marks = [("start", 10.0), ("reused", 10.001), ("sent", 10.002), ("total", 12.5)]

    assert summarize("sendPhoto", marks) == "telegram_call method=sendPhoto reused=1 sent=2 total=2500"


def test_summary_names_the_error():
    marks = [("start", 0.0), ("total", 5.0)]

    line = summarize("sendPhoto", marks, TimeoutError())

    assert line.endswith("total=5000 error=TimeoutError")


async def _traced_requests(caplog, *methods: str) -> list[str]:
    async def ok(request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "result": True})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", ok)
    server = TestServer(app)
    await server.start_server()
    session = TracedAiohttpSession()
    try:
        client = await session.create_session()
        with caplog.at_level(logging.INFO, logger="app.bot.tracing"):
            for method in methods:
                async with client.post(server.make_url(f"/bot{TOKEN}/{method}")) as response:
                    await response.read()
    finally:
        await session.close()
        await server.close()
    return [record.getMessage() for record in caplog.records]


@pytest.mark.asyncio
async def test_a_real_request_is_logged_by_phase_without_the_token(caplog):
    lines = await _traced_requests(caplog, "editMessageMedia")

    assert len(lines) == 1
    assert lines[0].startswith("telegram_call method=editMessageMedia ")
    assert "connected=" in lines[0] and "total=" in lines[0]
    assert "SECRET" not in lines[0]


@pytest.mark.asyncio
async def test_the_long_poll_is_not_logged(caplog):
    assert await _traced_requests(caplog, "getUpdates") == []
