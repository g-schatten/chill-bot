"""Tiny HTTP server for hosts that need an open port (Render) and for UptimeRobot to ping.

  GET /         -> always 200 "alive"           (use for the host's own port check)
  GET /healthz  -> 200 when Discord AND Lavalink are connected, otherwise 503 + JSON details
                   (point UptimeRobot here: it then also emails you when the bot is really down)

Only started when a PORT (or HEALTH_PORT) environment variable is set, i.e. never in the
normal docker-compose setup.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

import wavelink
from aiohttp import web

if TYPE_CHECKING:
    from main import MusicBot

log = logging.getLogger(__name__)


def build_app(status: Callable[[], dict]) -> web.Application:
    async def alive(_: web.Request) -> web.Response:
        return web.Response(text="alive")

    async def healthz(_: web.Request) -> web.Response:
        info = status()
        ok = bool(info.get("discord")) and bool(info.get("lavalink"))
        return web.json_response(info, status=200 if ok else 503)

    app = web.Application()
    app.router.add_get("/", alive)
    app.router.add_get("/healthz", healthz)
    return app


def bot_status(bot: MusicBot) -> dict:
    try:
        lavalink_ok = wavelink.Pool.get_node().status is wavelink.NodeStatus.CONNECTED
    except wavelink.InvalidNodeException:
        lavalink_ok = False
    return {
        "discord": bot.is_ready(),
        "lavalink": lavalink_ok,
        "guilds": len(bot.guilds),
    }


async def start_health_server(bot: MusicBot, port: int) -> web.AppRunner:
    runner = web.AppRunner(build_app(lambda: bot_status(bot)), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Health endpoint listening on port %d (/ and /healthz)", port)
    return runner
