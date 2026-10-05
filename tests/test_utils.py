import pytest

from utils import (
    QueryKind, fmt_duration, parse_query, parse_timestamp, progress_bar, truncate,
)

VID = "dQw4w9WgXcQ"
WATCH = f"https://www.youtube.com/watch?v={VID}"


@pytest.mark.parametrize("raw", [
    f"https://www.youtube.com/watch?v={VID}",
    f"https://youtube.com/watch?v={VID}",
    f"https://m.youtube.com/watch?v={VID}",
    f"https://music.youtube.com/watch?v={VID}",
    f"https://youtu.be/{VID}",
    f"https://youtu.be/{VID}?si=abc123",
    f"https://www.youtube.com/shorts/{VID}",
    f"https://www.youtube.com/live/{VID}",
    f"https://www.youtube.com/embed/{VID}",
    f"<https://youtu.be/{VID}>",
    f"  https://youtu.be/{VID}  ",
    f"https://youtu.be/{VID} please",
])
def test_youtube_video_variants_normalise_to_exact_video(raw):
    p = parse_query(raw)
    assert p.kind is QueryKind.YOUTUBE_VIDEO
    assert p.value == WATCH


def test_video_link_with_playlist_plays_only_that_video():
    p = parse_query(f"https://www.youtube.com/watch?v={VID}&list=PLabcdefghijk123&index=4")
    assert p.kind is QueryKind.YOUTUBE_VIDEO
    assert p.value == WATCH


@pytest.mark.parametrize("raw,ms", [
    (f"https://youtu.be/{VID}?t=90", 90_000),
    (f"https://youtu.be/{VID}?t=1m30s", 90_000),
    (f"https://www.youtube.com/watch?v={VID}&t=1h2m3s", 3_723_000),
    (f"https://www.youtube.com/watch?v={VID}&start=45", 45_000),
    (f"https://youtu.be/{VID}?t=garbage", 0),
    (f"https://youtu.be/{VID}", 0),
])
def test_timestamps(raw, ms):
    assert parse_query(raw).start_ms == ms


def test_pure_playlist_link():
    p = parse_query("https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf")
    assert p.kind is QueryKind.YOUTUBE_PLAYLIST
    assert p.value == "https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"


@pytest.mark.parametrize("raw", [
    "https://www.youtube.com/@somechannel",
    "https://www.youtube.com/",
    "https://youtu.be/short",
    "https://www.youtube.com/watch?v=tooshort",
])
def test_bad_youtube_links_are_unsupported(raw):
    p = parse_query(raw)
    assert p.kind is QueryKind.UNSUPPORTED and p.error


@pytest.mark.parametrize("raw,expected", [
    ("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT?si=abcdef", "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"),
    ("https://open.spotify.com/intl-de/track/4cOdK2wGLETKBW3PvgPWqT", "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"),
    ("https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3", "https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3"),
    ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=x", "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"),
    ("spotify:track:4cOdK2wGLETKBW3PvgPWqT", "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"),
])
def test_spotify_links(raw, expected):
    p = parse_query(raw)
    assert p.kind is QueryKind.SPOTIFY and p.value == expected


def test_spotify_podcast_and_short_links_rejected():
    assert parse_query("https://open.spotify.com/episode/4cOdK2wGLETKBW3PvgPWqT").kind is QueryKind.UNSUPPORTED
    assert parse_query("https://spotify.link/abc123").kind is QueryKind.UNSUPPORTED


def test_other_links_and_text():
    assert parse_query("https://soundcloud.com/artist/track").kind is QueryKind.URL
    p = parse_query("blinding lights the weeknd")
    assert p.kind is QueryKind.TEXT and p.value == "blinding lights the weeknd"
    assert parse_query("   ").value == ""


@pytest.mark.parametrize("text,ms", [
    ("90", 90_000), ("1:30", 90_000), ("1:02:03", 3_723_000), ("0:05", 5_000),
])
def test_parse_timestamp_ok(text, ms):
    assert parse_timestamp(text) == ms


@pytest.mark.parametrize("text", ["", "abc", "1:2:3:4", "-5", "1:xx"])
def test_parse_timestamp_bad(text):
    assert parse_timestamp(text) is None


def test_formatting():
    assert fmt_duration(0) == "0:00"
    assert fmt_duration(65_000) == "1:05"
    assert fmt_duration(3_725_000) == "1:02:05"
    assert len(progress_bar(0, 100)) == 15
    assert "🔘" in progress_bar(50, 100)
    assert progress_bar(100, 100).endswith("🔘")
    assert progress_bar(5, 0) == "▬" * 15
    assert truncate("abcdef", 4) == "abc…"
    assert truncate("abc", 4) == "abc"
