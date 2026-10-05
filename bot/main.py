"""Entry point for the private music bot."""

from __future__ import annotations

import asyncio
import logging
import sys

import discord
import wavelink
from discord.ext import commands

from aiohttp import web

from config import Config, ConfigError, load_config
from health import start_health_server

log = logging.getLogger("musicbot")

class MusicBot(commands.Bot):
    def __init__(self, cfg: Config) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # needed for "!play ..." style commands
        super().__init__(
            command_prefix=commands.when_mentioned_or(cfg.prefix),
            intents=intents,
            help_command=None,  # replaced by our own /help
            owner_id=cfg.owner_id,
            allowed_mentions=discord.AllowedMentions.none(),
            activity=discord.Activity(type=discord.ActivityType.listening, name=f"{cfg.prefix}play"),
        )
        self.cfg = cfg
        self.allowed_guilds: frozenset[int] = frozenset(cfg.guild_ids)
        self.add_check(self._only_allowed_guilds)
        self._health_runner: web.AppRunner | None = None

    # ------------------------------------------------------------------ setup
    async def setup_hook(self) -> None:
        # Open the health port first: hosts like Render require a listening port within minutes.
        if self.cfg.health_port:
            self._health_runner = await start_health_server(self, self.cfg.health_port)
        await self.load_extension("cogs.music")
        await self._connect_lavalink()

        # Slash commands are registered per server (instant), never globally.
        for gid in self.allowed_guilds:
            guild = discord.Object(id=gid)
            self.tree.copy_global_to(guild=guild)
            try:
                synced = await self.tree.sync(guild=guild)
                log.info("Synced %d slash commands to guild %s", len(synced), gid)
            except discord.HTTPException as exc:
                log.error(
                    "Could not sync slash commands to guild %s (%s). Is the bot invited to that "
                    "server with the 'applications.commands' scope?", gid, exc,
                )

    async def _connect_lavalink(self) -> None:
        """Wait for Lavalink to come up (it takes a while on first start: plugin downloads)."""
        attempts = self.cfg.lavalink_attempts
        for attempt in range(1, attempts + 1):
            node = wavelink.Node(
                uri=self.cfg.lavalink_uri,
                password=self.cfg.lavalink_password,
                inactive_player_timeout=self.cfg.inactive_timeout,
            )
            # Pool.connect() logs failures and returns the pool's nodes instead of raising.
            nodes = await wavelink.Pool.connect(nodes=[node], client=self, cache_capacity=100)
            if nodes:
                log.info("Connected to Lavalink at %s", self.cfg.lavalink_uri)
                return
            try:
                await node.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
            delay = min(2 * attempt, 15)
            log.warning(
                "Lavalink not ready yet (attempt %d/%d). Retrying in %ds...",
                attempt, attempts, delay,
            )
            await asyncio.sleep(delay)
        raise RuntimeError(
            "Could not connect to Lavalink. Check `docker compose logs lavalink` and that "
            "LAVALINK_PASSWORD matches in .env."
        )

    async def close(self) -> None:
        if self._health_runner is not None:
            await self._health_runner.cleanup()
            self._health_runner = None
        await super().close()

    # ------------------------------------------------------------ allowlist
    async def _only_allowed_guilds(self, ctx: commands.Context) -> bool:
        return ctx.guild is not None and ctx.guild.id in self.allowed_guilds

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "?")
        for guild in list(self.guilds):
            if guild.id not in self.allowed_guilds:
                log.warning("Leaving non-allowed guild %s (%s)", guild.name, guild.id)
                await guild.leave()

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if guild.id not in self.allowed_guilds:
            log.warning("Invited to non-allowed guild %s (%s). Leaving.", guild.name, guild.id)
            await guild.leave()

    # -------------------------------------------------------------- errors
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.CheckFailure):
            if ctx.guild is None:
                await self._reply(ctx, "I only work inside the server, not in DMs.")
            return  # silently ignore other servers / owner-only commands
        if isinstance(error, commands.MissingRequiredArgument):
            usage = f"{ctx.clean_prefix}{ctx.command.qualified_name} {ctx.command.signature}".strip()
            await self._reply(ctx, f"Missing `{error.param.name}`. Usage: `{usage}`")
            return
        if isinstance(error, (commands.BadArgument, commands.BadLiteralArgument, commands.BadUnionArgument)):
            await self._reply(ctx, f"That doesn't look right: {error}")
            return

        original = getattr(error, "original", error)
        log.error("Unhandled error in command %s", ctx.command, exc_info=original)
        await self._reply(ctx, "Something went wrong. Check the bot logs.")

    @staticmethod
    async def _reply(ctx: commands.Context, message: str) -> None:
        try:
            await ctx.send(message, ephemeral=True)
        except discord.HTTPException:
            pass


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.client").setLevel(logging.INFO)


def main() -> None:
    setup_logging()
    try:
        cfg = load_config()
    except ConfigError as exc:
        log.error("Configuration problem: %s", exc)
        sys.exit(1)

    bot = MusicBot(cfg)
    bot.run(cfg.token, log_handler=None)


if __name__ == "__main__":
    main()
