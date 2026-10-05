"""All music commands. Each command works as both a slash command and a prefix command."""

from __future__ import annotations

import asyncio
import logging
import math
from typing import TYPE_CHECKING, Literal, Optional, cast

import discord
import wavelink
from discord import app_commands
from discord.ext import commands

import queue_ops
from utils import (
    ParsedQuery, QueryKind, fmt_duration, parse_query, parse_timestamp, progress_bar, truncate,
)

if TYPE_CHECKING:
    from main import MusicBot

log = logging.getLogger(__name__)

EMBED_COLOR = discord.Color.blurple()
MAX_PLAYLIST_TRACKS = 200
ALONE_TIMEOUT_SECONDS = 180
QUEUE_PAGE_SIZE = 10
MAX_VOLUME = 150

LOOP_LABELS = {
    wavelink.QueueMode.normal: "Off",
    wavelink.QueueMode.loop: "Track 🔂",
    wavelink.QueueMode.loop_all: "Queue 🔁",
}

Ctx = commands.Context


# --------------------------------------------------------------------------- helpers
def _requester_id(track: wavelink.Playable) -> int | None:
    return getattr(track.extras, "requester", None)


def _track_link(track: wavelink.Playable, limit: int = 60) -> str:
    title = truncate(track.title, limit).replace("[", "(").replace("]", ")")
    return f"[{title}]({track.uri})" if track.uri else title


def _length(track: wavelink.Playable) -> str:
    return "LIVE" if track.is_stream else fmt_duration(track.length)


def _track_embed(heading: str, track: wavelink.Playable, extra: str | None = None) -> discord.Embed:
    embed = discord.Embed(title=heading, description=f"{_track_link(track, 80)}\nby **{track.author}**", color=EMBED_COLOR)
    embed.add_field(name="Length", value=_length(track))
    if extra:
        embed.add_field(name="Position", value=extra)
    requester = _requester_id(track)
    if requester:
        embed.add_field(name="Requested by", value=f"<@{requester}>")
    if track.artwork:
        embed.set_thumbnail(url=track.artwork)
    return embed


def _same_track(a: wavelink.Playable | None, b: wavelink.Playable | None) -> bool:
    return a is not None and b is not None and a.encoded == b.encoded


# --------------------------------------------------------------------------- views
class WrongSongView(discord.ui.View):
    """'Wrong song?' button shown after a text search: lets the requester pick another result."""

    def __init__(self, cog: Music, player: wavelink.Player, track: wavelink.Playable,
                 alternatives: list[wavelink.Playable], requester_id: int) -> None:
        super().__init__(timeout=90)
        self.cog, self.player, self.track = cog, player, track
        self.alternatives, self.requester_id = alternatives, requester_id
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Wrong song?", style=discord.ButtonStyle.secondary, emoji="🔁")
    async def wrong(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Only the person who requested it can swap it.", ephemeral=True)
            return
        picker = PickView(self.cog, self.player, self.alternatives, self.requester_id, replace=self.track, parent=self)
        await interaction.response.send_message("Pick the right one:", view=picker, ephemeral=True)


class PickView(discord.ui.View):
    """A dropdown of tracks. Used both for /search and for swapping a wrong match."""

    def __init__(self, cog: Music, player: wavelink.Player, options: list[wavelink.Playable],
                 requester_id: int, *, replace: wavelink.Playable | None = None,
                 parent: WrongSongView | None = None) -> None:
        super().__init__(timeout=60)
        self.cog, self.player, self.options = cog, player, options
        self.requester_id, self.replace, self.parent = requester_id, replace, parent

        select = discord.ui.Select(
            placeholder="Choose a track...",
            options=[
                discord.SelectOption(
                    label=truncate(t.title, 95),
                    description=truncate(f"{t.author} • {_length(t)}", 95),
                    value=str(i),
                )
                for i, t in enumerate(options[:25])
            ],
        )
        select.callback = self._picked  # type: ignore[method-assign]
        self.add_item(select)

    async def _picked(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("This menu isn't yours.", ephemeral=True)
            return
        select = cast(discord.ui.Select, self.children[0])
        chosen = self.options[int(select.values[0])]
        self.stop()

        if self.replace is None:  # /search: add the chosen track
            started, pos = await self.cog.add_track(self.player, chosen, self.requester_id)
            heading = "Playing now" if started else "Added to queue"
            await interaction.response.edit_message(content=None, embed=_track_embed(heading, chosen, None if started else f"#{pos}"), view=None)
            return

        if await self.cog.replace_track(self.player, self.replace, chosen, self.requester_id):
            if self.parent:
                self.parent.stop()
                if self.parent.message:
                    try:
                        await self.parent.message.edit(view=None)
                    except discord.HTTPException:
                        pass
            await interaction.response.edit_message(content=f"Swapped to **{truncate(chosen.title, 80)}**.", view=None)
        else:
            await interaction.response.edit_message(content="That track is no longer in the queue.", view=None)


class QueuePages(discord.ui.View):
    def __init__(self, player: wavelink.Player, page: int = 1) -> None:
        super().__init__(timeout=120)
        self.player = player
        self.page = max(1, page)
        self.message: discord.Message | None = None

    @property
    def pages(self) -> int:
        return max(1, math.ceil(len(self.player.queue) / QUEUE_PAGE_SIZE))

    def build(self) -> discord.Embed:
        self.page = min(self.page, self.pages)
        queue = self.player.queue
        embed = discord.Embed(title="Queue", color=EMBED_COLOR)

        current = self.player.current
        if current:
            embed.add_field(name="Now playing", value=f"{_track_link(current)} — `{_length(current)}`", inline=False)

        start = (self.page - 1) * QUEUE_PAGE_SIZE
        chunk = list(queue)[start:start + QUEUE_PAGE_SIZE]
        if chunk:
            lines = [f"`{start + i}.` {_track_link(t)} — `{_length(t)}`" for i, t in enumerate(chunk, 1)]
            embed.add_field(name="Up next", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Up next", value="Nothing queued. Add songs with the play command.", inline=False)

        total_ms = sum(t.length for t in queue if not t.is_stream)
        footer = f"Page {self.page}/{self.pages} • {len(queue)} queued"
        if len(queue):
            footer += f" • {fmt_duration(total_ms)} total"
        footer += f" • Loop: {LOOP_LABELS[queue.mode]}"
        embed.set_footer(text=footer)
        return embed

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(1, self.page - 1)
        await interaction.response.edit_message(embed=self.build(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = min(self.pages, self.page + 1)
        await interaction.response.edit_message(embed=self.build(), view=self)

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


# ----------------------------------------------------------------------------- cog
class Music(commands.Cog):
    def __init__(self, bot: MusicBot) -> None:
        self.bot = bot
        self._alone_tasks: dict[int, asyncio.Task[None]] = {}

    async def cog_unload(self) -> None:
        for task in self._alone_tasks.values():
            task.cancel()

    async def cog_check(self, ctx: Ctx) -> bool:  # type: ignore[override]
        return ctx.guild is not None

    # ------------------------------------------------------------ shared logic
    async def _join_for(self, ctx: Ctx) -> wavelink.Player | None:
        """Return a connected player in the author's voice channel, joining if needed."""
        voice = ctx.author.voice if isinstance(ctx.author, discord.Member) else None
        if not voice or not voice.channel:
            await ctx.send("Join a voice channel first.", ephemeral=True)
            return None

        player = cast("wavelink.Player | None", ctx.voice_client)
        if player is not None:
            if player.channel != voice.channel:
                await ctx.send(f"I'm already playing in {player.channel.mention}. Join that channel first.", ephemeral=True)
                return None
            player.home = ctx.channel  # type: ignore[attr-defined]
            return player

        perms = voice.channel.permissions_for(ctx.guild.me)
        if not (perms.connect and perms.speak):
            await ctx.send(f"I need **Connect** and **Speak** permission in {voice.channel.mention}.", ephemeral=True)
            return None

        try:
            player = await voice.channel.connect(cls=wavelink.Player, self_deaf=True, timeout=20)
        except (discord.ClientException, wavelink.WavelinkException, asyncio.TimeoutError) as exc:
            log.warning("Could not join voice channel: %s", exc)
            await ctx.send("I couldn't join that voice channel. Is Lavalink running? (try again in a moment)", ephemeral=True)
            return None

        player.autoplay = wavelink.AutoPlayMode.partial  # play the queue, no recommendations
        player.home = ctx.channel  # type: ignore[attr-defined]
        await player.set_volume(self.bot.cfg.default_volume)
        return player

    async def _control(self, ctx: Ctx, *, need_track: bool = False) -> wavelink.Player | None:
        """Return the player for control commands, checking the caller is in the same channel."""
        player = cast("wavelink.Player | None", ctx.voice_client)
        if player is None:
            await ctx.send("I'm not in a voice channel.", ephemeral=True)
            return None
        voice = ctx.author.voice if isinstance(ctx.author, discord.Member) else None
        if not voice or voice.channel != player.channel:
            await ctx.send(f"Join {player.channel.mention} to control the music.", ephemeral=True)
            return None
        if need_track and player.current is None:
            await ctx.send("Nothing is playing right now.", ephemeral=True)
            return None
        return player

    async def _search(self, parsed: ParsedQuery) -> wavelink.Search:
        if parsed.kind is not QueryKind.TEXT:
            return await wavelink.Playable.search(parsed.value)
        # Plain text: YouTube Music first (favours official audio), then normal YouTube.
        try:
            results = await wavelink.Playable.search(parsed.value, source=wavelink.TrackSource.YouTubeMusic)
            if results:
                return results
        except wavelink.LavalinkLoadException as exc:
            log.info("YouTube Music search failed (%s), falling back to YouTube", exc.error)
        return await wavelink.Playable.search(parsed.value, source=wavelink.TrackSource.YouTube)

    async def _start_if_idle(self, player: wavelink.Player, start_ms: int = 0) -> wavelink.Playable | None:
        if player.current is not None:
            return None
        try:
            track = player.queue.get()
        except wavelink.QueueEmpty:
            return None
        player.skip_announce = track.encoded  # type: ignore[attr-defined]
        await player.play(track, start=start_ms, paused=False)
        return track

    async def add_track(self, player: wavelink.Player, track: wavelink.Playable, requester_id: int, *,
                        front: bool = False, start_ms: int = 0) -> tuple[bool, int]:
        """Queue a track. Returns (started_playing_now, 1-based queue position)."""
        track.extras = {"requester": requester_id}
        if front:
            player.queue.put_at(0, track)
        else:
            await player.queue.put_wait(track)
        started = await self._start_if_idle(player, start_ms)
        if started is not None and started is track:
            return True, 0
        try:
            pos = player.queue.index(track) + 1
        except ValueError:
            pos = len(player.queue)
        return False, pos

    async def replace_track(self, player: wavelink.Player, old: wavelink.Playable,
                            new: wavelink.Playable, requester_id: int) -> bool:
        new.extras = {"requester": requester_id}
        for i, t in enumerate(player.queue):
            if t is old:
                player.queue.delete(i)
                player.queue.put_at(i, new)
                return True
        if _same_track(player.current, old):
            player.skip_announce = new.encoded  # type: ignore[attr-defined]
            await player.play(new, paused=False)
            player.queue.loaded = new
            return True
        return False

    async def _enqueue(self, ctx: Ctx, query: str, *, front: bool) -> None:
        await ctx.defer()
        parsed = parse_query(query)
        if parsed.kind is QueryKind.TEXT and not parsed.value:
            await ctx.send("Tell me what to play: a song name, or a YouTube/Spotify link.", ephemeral=True)
            return
        if parsed.kind is QueryKind.UNSUPPORTED:
            await ctx.send(parsed.error or "I can't play that.", ephemeral=True)
            return
        if parsed.kind is QueryKind.SPOTIFY and not self.bot.cfg.spotify_enabled:
            await ctx.send("Spotify links aren't switched on yet. Set `SPOTIFY_ENABLED=true` (see the README), or just type the song name.", ephemeral=True)
            return

        player = await self._join_for(ctx)
        if player is None:
            return

        try:
            results = await self._search(parsed)
        except wavelink.LavalinkLoadException as exc:
            log.warning("Lavalink load failed for %r: %s (%s)", parsed.value, exc.error, getattr(exc, "cause", ""))
            await ctx.send(f"Couldn't load that: {truncate(str(exc.error), 200)}", ephemeral=True)
            await self._leave_if_unused(player)
            return
        except wavelink.WavelinkException as exc:
            log.warning("Search failed for %r: %s", parsed.value, exc)
            await ctx.send("Search failed. Lavalink may be restarting; try again in a moment.", ephemeral=True)
            return

        if not results:
            await ctx.send(f"No results for **{truncate(parsed.value, 100)}**.", ephemeral=True)
            await self._leave_if_unused(player)
            return

        if isinstance(results, wavelink.Playlist):
            await self._enqueue_playlist(ctx, player, results, front=front)
            return

        track = results[0]
        # A timestamp in a link (?t=90) only applies if the song starts right away.
        started, pos = await self.add_track(player, track, ctx.author.id, front=front,
                                            start_ms=parsed.start_ms if player.current is None else 0)
        heading = "Playing now" if started else ("Playing next" if front else "Added to queue")
        embed = _track_embed(heading, track, None if started else f"#{pos}")

        view = None
        if parsed.kind is QueryKind.TEXT and len(results) > 1:
            view = WrongSongView(self, player, track, list(results[1:6]), ctx.author.id)
        msg = await ctx.send(embed=embed, view=view) if view else await ctx.send(embed=embed)
        if view:
            view.message = msg

    async def _enqueue_playlist(self, ctx: Ctx, player: wavelink.Player, playlist: wavelink.Playlist, *, front: bool) -> None:
        tracks = list(playlist.tracks)[:MAX_PLAYLIST_TRACKS]
        truncated = len(playlist.tracks) > len(tracks)
        for t in tracks:
            t.extras = {"requester": ctx.author.id}
        if front:
            for i, t in enumerate(tracks):
                player.queue.put_at(i, t)
        else:
            await player.queue.put_wait(tracks)
        await self._start_if_idle(player)

        note = f" (first {MAX_PLAYLIST_TRACKS} only)" if truncated else ""
        embed = discord.Embed(
            title="Playlist added",
            description=f"**{truncate(playlist.name, 100)}**\n{len(tracks)} tracks{note}",
            color=EMBED_COLOR,
        )
        await ctx.send(embed=embed)

    async def _leave_if_unused(self, player: wavelink.Player) -> None:
        """If a failed request left the bot sitting alone and idle in voice, leave."""
        if player.current is None and player.queue.is_empty:
            await player.disconnect()

    async def _notify(self, player: wavelink.Player, message: str) -> None:
        home = getattr(player, "home", None)
        if home is None:
            return
        try:
            await home.send(message)
        except discord.HTTPException:
            pass

    # ---------------------------------------------------------------- commands
    @commands.hybrid_command(name="play", aliases=["p"], description="Play a song from a YouTube/Spotify link or a search")
    @app_commands.describe(query="A YouTube/Spotify link, or a song name (artist optional)")
    async def play(self, ctx: Ctx, *, query: str) -> None:
        """Play a song. Links play exactly that version; plain text is searched."""
        await self._enqueue(ctx, query, front=False)

    @commands.hybrid_command(name="playnext", aliases=["pn"], description="Add a song to the front of the queue")
    @app_commands.describe(query="A YouTube/Spotify link, or a song name")
    async def playnext(self, ctx: Ctx, *, query: str) -> None:
        await self._enqueue(ctx, query, front=True)

    @commands.hybrid_command(name="search", aliases=["find"], description="Show the top results for a search and pick one")
    @app_commands.describe(query="What to search for")
    async def search(self, ctx: Ctx, *, query: str) -> None:
        await ctx.defer()
        parsed = parse_query(query)
        if parsed.kind is not QueryKind.TEXT:
            await self._enqueue(ctx, query, front=False)
            return
        player = await self._join_for(ctx)
        if player is None:
            return
        try:
            results = await self._search(parsed)
        except wavelink.WavelinkException as exc:
            log.warning("Search failed for %r: %s", parsed.value, exc)
            await ctx.send("Search failed. Try again in a moment.", ephemeral=True)
            return
        if not results or isinstance(results, wavelink.Playlist):
            await ctx.send(f"No results for **{truncate(parsed.value, 100)}**.", ephemeral=True)
            await self._leave_if_unused(player)
            return
        top = list(results[:5])
        lines = [f"`{i}.` {_track_link(t, 70)} — {t.author} • `{_length(t)}`" for i, t in enumerate(top, 1)]
        embed = discord.Embed(title=f"Results for “{truncate(parsed.value, 60)}”", description="\n".join(lines), color=EMBED_COLOR)
        await ctx.send(embed=embed, view=PickView(self, player, top, ctx.author.id))

    @commands.hybrid_command(name="join", description="Make me join your voice channel")
    async def join(self, ctx: Ctx) -> None:
        player = await self._join_for(ctx)
        if player:
            await ctx.send(f"Joined {player.channel.mention}.")

    @commands.hybrid_command(name="pause", description="Pause the music")
    async def pause(self, ctx: Ctx) -> None:
        if player := await self._control(ctx, need_track=True):
            if player.paused:
                await ctx.send("Already paused. Use the resume command.", ephemeral=True)
                return
            await player.pause(True)
            await ctx.send("⏸️ Paused.")

    @commands.hybrid_command(name="resume", aliases=["unpause"], description="Resume the music")
    async def resume(self, ctx: Ctx) -> None:
        if player := await self._control(ctx, need_track=True):
            if not player.paused:
                await ctx.send("Not paused.", ephemeral=True)
                return
            await player.pause(False)
            await ctx.send("▶️ Resumed.")

    @commands.hybrid_command(name="skip", aliases=["s", "next"], description="Skip the current song")
    async def skip(self, ctx: Ctx) -> None:
        if player := await self._control(ctx, need_track=True):
            skipped = await player.skip(force=True)
            await ctx.send(f"⏭️ Skipped **{truncate(skipped.title, 80)}**." if skipped else "⏭️ Skipped.")

    @commands.hybrid_command(name="skipto", description="Skip ahead to a position in the queue")
    @app_commands.describe(position="Queue position to jump to (see the queue command)")
    async def skipto(self, ctx: Ctx, position: int) -> None:
        if player := await self._control(ctx):
            if not 1 <= position <= len(player.queue):
                await ctx.send(f"Pick a position between 1 and {len(player.queue)}.", ephemeral=True)
                return
            target = player.queue[position - 1]
            queue_ops.skip_to(player.queue, position)
            if player.current is not None:
                await player.skip(force=True)
            else:
                await self._start_if_idle(player)
            await ctx.send(f"⏭️ Jumped to **{truncate(target.title, 80)}**.")

    @commands.hybrid_command(name="back", aliases=["previous", "prev"], description="Go back to the previous song")
    async def back(self, ctx: Ctx) -> None:
        if player := await self._control(ctx):
            previous = queue_ops.pop_previous(player.queue.history, player.current)
            if previous is None:
                await ctx.send("There's no previous song.", ephemeral=True)
                return
            if player.current is not None:
                player.queue.put_at(0, player.current)  # come back to it afterwards
            player.skip_announce = previous.encoded  # type: ignore[attr-defined]
            await player.play(previous, paused=False)
            player.queue.loaded = previous
            await ctx.send(embed=_track_embed("Playing previous", previous))

    @commands.hybrid_command(name="queue", aliases=["q"], description="Show the queue")
    @app_commands.describe(page="Page number")
    async def queue(self, ctx: Ctx, page: int = 1) -> None:
        player = cast("wavelink.Player | None", ctx.voice_client)
        if player is None or (player.current is None and player.queue.is_empty):
            await ctx.send("The queue is empty.", ephemeral=True)
            return
        view = QueuePages(player, page)
        view.message = await ctx.send(embed=view.build(), view=view)

    @commands.hybrid_command(name="nowplaying", aliases=["np", "current"], description="Show the current song")
    async def nowplaying(self, ctx: Ctx) -> None:
        player = cast("wavelink.Player | None", ctx.voice_client)
        if player is None or player.current is None:
            await ctx.send("Nothing is playing right now.", ephemeral=True)
            return
        track = player.current
        embed = _track_embed("Now playing", track)
        if not track.is_stream:
            bar = progress_bar(player.position, track.length)
            embed.add_field(name="Progress", value=f"{bar}\n`{fmt_duration(player.position)} / {fmt_duration(track.length)}`", inline=False)
        embed.add_field(name="Loop", value=LOOP_LABELS[player.queue.mode])
        embed.add_field(name="Volume", value=f"{player.volume}%")
        if player.paused:
            embed.set_footer(text="⏸️ Paused")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="loop", aliases=["repeat"], description="Loop the current track, the whole queue, or turn looping off")
    @app_commands.describe(mode="off, track, or queue (leave empty to cycle)")
    async def loop(self, ctx: Ctx, mode: Optional[Literal["off", "track", "queue"]] = None) -> None:
        if player := await self._control(ctx):
            order = [wavelink.QueueMode.normal, wavelink.QueueMode.loop, wavelink.QueueMode.loop_all]
            if mode is None:
                new = order[(order.index(player.queue.mode) + 1) % len(order)]
            else:
                new = {"off": order[0], "track": order[1], "queue": order[2]}[mode]
            player.queue.mode = new
            if new is wavelink.QueueMode.loop and player.current is not None:
                player.queue.loaded = player.current  # make sure *this* track is the one that repeats
            await ctx.send(f"Loop: **{LOOP_LABELS[new]}**")

    @commands.hybrid_command(name="remove", aliases=["rm"], description="Remove a song from the queue")
    @app_commands.describe(position="Queue position (see the queue command)")
    async def remove(self, ctx: Ctx, position: int) -> None:
        if player := await self._control(ctx):
            if not 1 <= position <= len(player.queue):
                await ctx.send(f"Pick a position between 1 and {len(player.queue)}.", ephemeral=True)
                return
            track = queue_ops.remove_at(player.queue, position)
            await ctx.send(f"🗑️ Removed **{truncate(track.title, 80)}**.")

    @commands.hybrid_command(name="move", aliases=["mv"], description="Move a song to another place in the queue")
    @app_commands.describe(source="Current position", destination="New position")
    async def move(self, ctx: Ctx, source: int, destination: int) -> None:
        if player := await self._control(ctx):
            n = len(player.queue)
            if not (1 <= source <= n and 1 <= destination <= n):
                await ctx.send(f"Positions must be between 1 and {n}.", ephemeral=True)
                return
            track = queue_ops.move(player.queue, source, destination)
            await ctx.send(f"↕️ Moved **{truncate(track.title, 80)}** to #{destination}.")

    @commands.hybrid_command(name="shuffle", description="Shuffle the queue")
    async def shuffle(self, ctx: Ctx) -> None:
        if player := await self._control(ctx):
            if len(player.queue) < 2:
                await ctx.send("Not enough songs in the queue to shuffle.", ephemeral=True)
                return
            player.queue.shuffle()
            await ctx.send("🔀 Queue shuffled.")

    @commands.hybrid_command(name="clear", description="Clear the queue (keeps the current song playing)")
    async def clear(self, ctx: Ctx) -> None:
        if player := await self._control(ctx):
            count = len(player.queue)
            player.queue.clear()
            await ctx.send(f"🧹 Cleared {count} songs.")

    @commands.hybrid_command(name="seek", description="Jump to a time in the current song")
    @app_commands.describe(time="e.g. 1:30 or 90 (seconds)")
    async def seek(self, ctx: Ctx, time: str) -> None:
        if player := await self._control(ctx, need_track=True):
            track = player.current
            ms = parse_timestamp(time)
            if ms is None:
                await ctx.send("Use a time like `1:30` or `90`.", ephemeral=True)
                return
            if not track.is_seekable or track.is_stream:
                await ctx.send("This track can't be seeked.", ephemeral=True)
                return
            if ms >= track.length:
                await ctx.send(f"That's past the end (`{fmt_duration(track.length)}`).", ephemeral=True)
                return
            await player.seek(ms)
            await ctx.send(f"⏩ Jumped to `{fmt_duration(ms)}`.")

    @commands.hybrid_command(name="volume", aliases=["vol"], description="Show or set the volume")
    @app_commands.describe(level=f"0 to {MAX_VOLUME}")
    async def volume(self, ctx: Ctx, level: Optional[int] = None) -> None:
        if player := await self._control(ctx):
            if level is None:
                await ctx.send(f"🔊 Volume is **{player.volume}%**.")
                return
            level = max(0, min(MAX_VOLUME, level))
            await player.set_volume(level)
            await ctx.send(f"🔊 Volume set to **{level}%**.")

    @commands.hybrid_command(name="stop", description="Stop playing and clear the queue")
    async def stop(self, ctx: Ctx) -> None:
        if player := await self._control(ctx):
            player.queue.clear()
            if player.queue.history is not None:
                player.queue.history.clear()
            player.queue.mode = wavelink.QueueMode.normal
            await player.pause(False)
            await player.skip(force=True)
            await ctx.send("⏹️ Stopped and cleared the queue.")

    @commands.hybrid_command(name="leave", aliases=["disconnect", "dc", "bye"], description="Disconnect from voice")
    async def leave(self, ctx: Ctx) -> None:
        if player := await self._control(ctx):
            await player.disconnect()
            await ctx.send("👋 Disconnected.")

    @commands.hybrid_command(name="help", description="Show all commands")
    async def help(self, ctx: Ctx) -> None:
        p = ctx.clean_prefix
        embed = discord.Embed(title="Music commands", color=EMBED_COLOR,
                              description="Every command also works as a slash command.")
        embed.add_field(name="Playing", inline=False, value=(
            f"`{p}play <link or name>` (`{p}p`) – YouTube/Spotify link, or search\n"
            f"`{p}playnext <link or name>` – put it first in the queue\n"
            f"`{p}search <name>` – pick from the top 5 results"))
        embed.add_field(name="Controls", inline=False, value=(
            f"`{p}pause` `{p}resume` `{p}skip` `{p}skipto <n>` `{p}back`\n"
            f"`{p}seek <1:30>` `{p}volume <0-{MAX_VOLUME}>` `{p}stop` `{p}leave`"))
        embed.add_field(name="Queue", inline=False, value=(
            f"`{p}queue` (`{p}q`) `{p}nowplaying` (`{p}np`)\n"
            f"`{p}loop [off|track|queue]` `{p}shuffle` `{p}clear`\n"
            f"`{p}remove <n>` `{p}move <from> <to>`"))
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="debug", description="(Owner) Show Lavalink status")
    @commands.is_owner()
    async def debug(self, ctx: Ctx) -> None:
        await ctx.defer(ephemeral=True)
        try:
            node = wavelink.Pool.get_node()
        except wavelink.InvalidNodeException:
            await ctx.send("No Lavalink node is connected.", ephemeral=True)
            return
        lines = [f"Node status: **{node.status.name}**", f"Session: `{node.session_id}`", f"Active players: {len(node.players)}"]
        try:
            info = await node.fetch_info()
            lines.append(f"Lavalink: `{info.get('version', {}).get('semver', '?')}`")
            plugins = ", ".join(f"{p['name']} {p['version']}" for p in info.get("plugins", [])) or "none"
            lines.append(f"Plugins: {plugins}")
            stats = await node.fetch_stats()
            lines.append(f"Playing: {stats.get('playingPlayers', '?')} • Uptime: {fmt_duration(stats.get('uptime', 0))}")
        except Exception as exc:  # noqa: BLE001 - diagnostics only
            lines.append(f"Could not fetch details: {exc}")
        await ctx.send("\n".join(lines), ephemeral=True)

    # ------------------------------------------------------------------ events
    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        log.info("Lavalink node %r ready (session resumed: %s)", payload.node.identifier, payload.resumed)

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload) -> None:
        player = payload.player
        if player is None:
            return
        track = payload.track
        if getattr(player, "skip_announce", None) == track.encoded:
            player.skip_announce = None  # type: ignore[attr-defined]
            player.last_announced = track.encoded  # type: ignore[attr-defined]
            return
        if player.queue.mode is wavelink.QueueMode.loop and getattr(player, "last_announced", None) == track.encoded:
            return  # don't spam "now playing" on every repeat
        player.last_announced = track.encoded  # type: ignore[attr-defined]
        home = getattr(player, "home", None)
        if home is not None:
            try:
                await home.send(embed=_track_embed("Now playing", track))
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_wavelink_track_exception(self, payload: wavelink.TrackExceptionEventPayload) -> None:
        log.warning("Track exception for %r: %s", payload.track.title, payload.exception)
        if payload.player:
            message = (payload.exception or {}).get("message", "unknown error")
            await self._notify(payload.player, f"⚠️ Couldn't play **{truncate(payload.track.title, 80)}** ({truncate(str(message), 120)}). Skipping.")

    @commands.Cog.listener()
    async def on_wavelink_track_stuck(self, payload: wavelink.TrackStuckEventPayload) -> None:
        log.warning("Track stuck: %r", payload.track.title)
        if payload.player:
            await self._notify(payload.player, f"⚠️ **{truncate(payload.track.title, 80)}** got stuck. Skipping.")
            await payload.player.skip(force=True)

    @commands.Cog.listener()
    async def on_wavelink_inactive_player(self, player: wavelink.Player) -> None:
        await self._notify(player, "👋 Leaving the voice channel because nothing is playing (or nobody is listening).")
        await player.disconnect()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        guild = member.guild
        player = cast("wavelink.Player | None", guild.voice_client)
        if player is None or player.channel is None:
            self._cancel_alone_task(guild.id)
            return
        humans = [m for m in player.channel.members if not m.bot]
        if humans:
            self._cancel_alone_task(guild.id)
        elif guild.id not in self._alone_tasks:
            self._alone_tasks[guild.id] = asyncio.create_task(self._leave_if_alone(guild.id))

    def _cancel_alone_task(self, guild_id: int) -> None:
        task = self._alone_tasks.pop(guild_id, None)
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def _leave_if_alone(self, guild_id: int) -> None:
        try:
            await asyncio.sleep(ALONE_TIMEOUT_SECONDS)
            guild = self.bot.get_guild(guild_id)
            player = cast("wavelink.Player | None", guild.voice_client) if guild else None
            if player is None or player.channel is None:
                return
            if any(not m.bot for m in player.channel.members):
                return
            await self._notify(player, "👋 Everyone left, so I'm leaving too.")
            await player.disconnect()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Error while leaving an empty voice channel")
        finally:
            self._alone_tasks.pop(guild_id, None)


async def setup(bot: MusicBot) -> None:
    await bot.add_cog(Music(bot))
