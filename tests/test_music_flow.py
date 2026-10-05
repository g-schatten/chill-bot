"""Behaviour tests for the music cog using a fake player (no Discord / Lavalink needed)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import wavelink

from cogs import music as music_mod
from cogs.music import Music, WrongSongView
from config import Config
from test_queue_ops import make_track


class FakePlayer:
    def __init__(self):
        self.queue = wavelink.Queue()
        self.queue.mode = wavelink.QueueMode.normal
        self.current = None
        self.paused = False
        self.volume = 60
        self.position = 0
        self.played = []
        self.channel = SimpleNamespace(mention="#music")
        self.disconnect = AsyncMock()
        self.skip = AsyncMock(side_effect=self._skip)
        self.pause = AsyncMock()

    async def play(self, track, **kwargs):
        self.played.append((track, kwargs))
        self.current = track
        self.queue.history.put(track) if kwargs.get("add_history", True) else None

    async def _skip(self, force=True):
        old, self.current = self.current, None
        return old


class FakeCtx:
    def __init__(self, author_id=42):
        self.author = SimpleNamespace(id=author_id)
        self.sent = []
        self.defer = AsyncMock()
        self.send = AsyncMock(side_effect=self._send)

    async def _send(self, *args, **kwargs):
        self.sent.append((args, kwargs))
        return MagicMock()

    @property
    def last_embed(self):
        return self.sent[-1][1].get("embed")

    @property
    def last_text(self):
        return self.sent[-1][0][0] if self.sent[-1][0] else None


def make_cog(player, spotify=True):
    cfg = Config(token="x", prefix="!", guild_ids=(1,), owner_id=None, lavalink_uri="u",
                 lavalink_password="p", default_volume=60, inactive_timeout=600, spotify_enabled=spotify)
    cog = Music(SimpleNamespace(cfg=cfg))
    cog._join_for = AsyncMock(return_value=player)
    cog._control = AsyncMock(return_value=player)
    return cog


@pytest.fixture
def search(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(music_mod.wavelink.Playable, "search", mock)
    return mock


def run(coro):
    return asyncio.run(coro)


def test_text_query_plays_first_result_and_offers_wrong_song_button(search):
    results = [make_track(i) for i in range(1, 8)]
    search.return_value = results
    p, ctx = FakePlayer(), FakeCtx()
    cog = make_cog(p)

    run(cog._enqueue(ctx, "blinding lights the weeknd", front=False))

    # searched YouTube Music first
    assert search.call_args.kwargs["source"] is wavelink.TrackSource.YouTubeMusic
    assert p.played[0][0] is results[0]
    assert len(p.queue) == 0
    assert ctx.last_embed.title == "Playing now"
    view = ctx.sent[-1][1]["view"]
    assert isinstance(view, WrongSongView) and len(view.alternatives) == 5
    assert dict(results[0].extras)["requester"] == 42


def test_text_query_falls_back_to_youtube_when_music_search_is_empty(search):
    search.side_effect = [[], [make_track(1)]]
    p, ctx = FakePlayer(), FakeCtx()
    run(make_cog(p)._enqueue(ctx, "obscure song", front=False))
    sources = [c.kwargs.get("source") for c in search.call_args_list]
    assert sources == [wavelink.TrackSource.YouTubeMusic, wavelink.TrackSource.YouTube]
    assert p.played


def test_second_song_is_queued_not_played(search):
    p, ctx = FakePlayer(), FakeCtx()
    cog = make_cog(p)
    search.return_value = [make_track(1)]
    run(cog._enqueue(ctx, "one", front=False))
    search.return_value = [make_track(2)]
    run(cog._enqueue(ctx, "two", front=False))
    assert len(p.played) == 1
    assert [t.title for t in p.queue] == ["Song 2"]
    assert ctx.last_embed.title == "Added to queue"


def test_playnext_goes_to_front(search):
    p, ctx = FakePlayer(), FakeCtx()
    cog = make_cog(p)
    search.return_value = [make_track(1)]
    run(cog._enqueue(ctx, "one", front=False))
    for n in (2, 3):
        search.return_value = [make_track(n)]
        run(cog._enqueue(ctx, str(n) + " song", front=False))
    search.return_value = [make_track(9)]
    run(cog._enqueue(ctx, "urgent", front=True))
    assert [t.title for t in p.queue] == ["Song 9", "Song 2", "Song 3"]
    assert ctx.last_embed.title == "Playing next"


def test_youtube_link_loads_exactly_that_video_with_no_search_and_no_swap_button(search):
    search.return_value = [make_track(1), make_track(2)]
    p, ctx = FakePlayer(), FakeCtx()
    run(make_cog(p)._enqueue(ctx, "https://youtu.be/dQw4w9WgXcQ?si=x&t=45", front=False))
    assert search.call_args.args[0] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert "source" not in search.call_args.kwargs          # direct link, not a text search
    assert p.played[0][1]["start"] == 45_000                # timestamp honoured on first track
    assert ctx.sent[-1][1].get("view") is None              # no "wrong song?" for exact links


def test_video_link_with_list_param_ignores_the_playlist(search):
    search.return_value = [make_track(1)]
    run(make_cog(FakePlayer())._enqueue(FakeCtx(), "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabcdefghijk123", front=False))
    assert search.call_args.args[0] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def make_playlist(n):
    payloads = [
        {
            "encoded": f"enc{i}",
            "info": {"identifier": f"id{i}", "isSeekable": True, "author": "Artist", "length": 180_000,
                     "isStream": False, "position": 0, "title": f"Song {i}", "uri": f"https://example.com/{i}",
                     "sourceName": "youtube"},
            "pluginInfo": {}, "userData": {},
        }
        for i in range(1, n + 1)
    ]
    data = {"info": {"name": "My Mix", "selectedTrack": -1}, "pluginInfo": {}, "tracks": payloads}
    return wavelink.Playlist(data)


def test_playlist_is_queued_capped_and_first_track_starts(search):
    search.return_value = make_playlist(250)
    p, ctx = FakePlayer(), FakeCtx()
    run(make_cog(p)._enqueue(ctx, "https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf", front=False))
    assert len(p.played) == 1
    assert len(p.queue) == music_mod.MAX_PLAYLIST_TRACKS - 1
    assert "first 200 only" in ctx.last_embed.description


def test_spotify_link_when_disabled_explains_and_does_not_search(search):
    p, ctx = FakePlayer(), FakeCtx()
    run(make_cog(p, spotify=False)._enqueue(ctx, "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT", front=False))
    search.assert_not_called()
    assert "SPOTIFY_ENABLED" in ctx.last_text


def test_spotify_link_when_enabled_passes_clean_url(search):
    search.return_value = [make_track(1)]
    run(make_cog(FakePlayer())._enqueue(FakeCtx(), "https://open.spotify.com/intl-de/track/4cOdK2wGLETKBW3PvgPWqT?si=zz", front=False))
    assert search.call_args.args[0] == "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"


def test_no_results_message_and_leaves_if_idle(search):
    search.return_value = []
    p, ctx = FakePlayer(), FakeCtx()
    run(make_cog(p)._enqueue(ctx, "zzzzzzzz", front=False))
    assert "No results" in ctx.last_text
    p.disconnect.assert_awaited()


def test_lavalink_load_error_is_reported_nicely(search):
    exc = wavelink.LavalinkLoadException.__new__(wavelink.LavalinkLoadException)
    exc.error, exc.severity, exc.cause = "Video unavailable", "common", "cause"
    search.side_effect = exc
    p, ctx = FakePlayer(), FakeCtx()
    run(make_cog(p)._enqueue(ctx, "https://youtu.be/dQw4w9WgXcQ", front=False))
    assert "Video unavailable" in ctx.last_text


def test_replace_track_in_queue_and_when_current():
    p = FakePlayer()
    cog = make_cog(p)
    a, b, wrong, new = make_track(1), make_track(2), make_track(3), make_track(4)
    for t in (a, b, wrong):
        p.queue.put(t)
    assert run(cog.replace_track(p, wrong, new, 7)) is True
    assert [t.title for t in p.queue] == ["Song 1", "Song 2", "Song 4"]

    p.current = make_track(5)
    replacement = make_track(6)
    assert run(cog.replace_track(p, make_track(5), replacement, 7)) is True
    assert p.played[-1][0] is replacement

    assert run(cog.replace_track(p, make_track(99), make_track(100), 7)) is False


def test_back_returns_to_previous_and_requeues_current():
    p = FakePlayer()
    cog = make_cog(p)
    one, two = make_track(1), make_track(2)
    p.queue.history.put(one)
    p.queue.history.put(two)
    p.current = two
    ctx = FakeCtx()
    run(cog.back.callback(cog, ctx))
    assert p.played[-1][0].title == "Song 1"
    assert [t.title for t in p.queue] == ["Song 2"]


def test_back_with_no_history_says_so():
    p, ctx = FakePlayer(), FakeCtx()
    cog = make_cog(p)
    p.queue.history.put(make_track(1))
    p.current = make_track(1)
    run(cog.back.callback(cog, ctx))
    assert "no previous" in ctx.last_text.lower()
    assert not p.played


def test_loop_cycles_and_sets_modes():
    p, ctx = FakePlayer(), FakeCtx()
    cog = make_cog(p)
    p.current = make_track(1)
    run(cog.loop.callback(cog, ctx, None)); assert p.queue.mode is wavelink.QueueMode.loop
    assert p.queue.loaded is p.current
    run(cog.loop.callback(cog, ctx, None)); assert p.queue.mode is wavelink.QueueMode.loop_all
    run(cog.loop.callback(cog, ctx, None)); assert p.queue.mode is wavelink.QueueMode.normal
    run(cog.loop.callback(cog, ctx, "queue")); assert p.queue.mode is wavelink.QueueMode.loop_all
    run(cog.loop.callback(cog, ctx, "off")); assert p.queue.mode is wavelink.QueueMode.normal


def test_remove_move_shuffle_clear_commands():
    p, ctx = FakePlayer(), FakeCtx()
    cog = make_cog(p)
    for i in range(1, 6):
        p.queue.put(make_track(i))
    run(cog.remove.callback(cog, ctx, 2))
    assert [t.title for t in p.queue] == ["Song 1", "Song 3", "Song 4", "Song 5"]
    run(cog.move.callback(cog, ctx, 4, 1))
    assert p.queue[0].title == "Song 5"
    run(cog.remove.callback(cog, ctx, 99))
    assert "between 1 and 4" in ctx.last_text
    run(cog.shuffle.callback(cog, ctx))
    assert len(p.queue) == 4
    run(cog.clear.callback(cog, ctx))
    assert len(p.queue) == 0


def test_skipto_drops_earlier_tracks_and_skips_current():
    p, ctx = FakePlayer(), FakeCtx()
    cog = make_cog(p)
    p.current = make_track(0)
    for i in range(1, 5):
        p.queue.put(make_track(i))
    run(cog.skipto.callback(cog, ctx, 3))
    assert [t.title for t in p.queue] == ["Song 3", "Song 4"]
    p.skip.assert_awaited()


def test_stop_clears_everything():
    p, ctx = FakePlayer(), FakeCtx()
    cog = make_cog(p)
    p.current = make_track(1)
    p.queue.put(make_track(2))
    p.queue.mode = wavelink.QueueMode.loop_all
    run(cog.stop.callback(cog, ctx))
    assert len(p.queue) == 0 and p.queue.mode is wavelink.QueueMode.normal
    p.pause.assert_awaited_with(False)
    p.skip.assert_awaited()
