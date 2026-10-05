"""Pure helper functions: turning what the user typed into a lookup, and formatting."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto
from urllib.parse import parse_qs, urlparse


class QueryKind(Enum):
    TEXT = auto()              # "blinding lights the weeknd"
    YOUTUBE_VIDEO = auto()     # one specific video (played exactly as linked)
    YOUTUBE_PLAYLIST = auto()  # a whole playlist
    SPOTIFY = auto()           # open.spotify.com track / album / playlist / artist
    URL = auto()               # any other link (SoundCloud, Bandcamp, ...)
    UNSUPPORTED = auto()


@dataclass(frozen=True)
class ParsedQuery:
    kind: QueryKind
    value: str
    start_ms: int = 0
    error: str | None = None


_YT_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be",
}
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{10,}$")
_SPOTIFY_ID = re.compile(r"^[A-Za-z0-9]{10,}$")
_SPOTIFY_KINDS = {"track", "album", "playlist", "artist"}
_SPOTIFY_URI = re.compile(r"^spotify:(track|album|playlist|artist):([A-Za-z0-9]+)$")
_SHORT_LINK_HOSTS = {"spotify.link", "spoti.fi"}


def parse_query(raw: str) -> ParsedQuery:
    """Decide what kind of thing the user typed and normalise it.

    YouTube links are reduced to ``watch?v=ID`` so that exactly that video is loaded
    (a ``&list=`` in a video link is ignored on purpose).
    """
    text = (raw or "").strip()
    if text.startswith("<") and text.endswith(">"):  # <link> = Discord's "no embed" form
        text = text[1:-1].strip()
    if not text:
        return ParsedQuery(QueryKind.TEXT, "")

    m = _SPOTIFY_URI.match(text)
    if m:
        return ParsedQuery(QueryKind.SPOTIFY, f"https://open.spotify.com/{m.group(1)}/{m.group(2)}")

    first = text.split()[0]
    if re.match(r"^https?://\S+$", first, re.IGNORECASE):
        if first.startswith("<") and first.endswith(">"):
            first = first[1:-1]
        url = urlparse(first)
        host = (url.hostname or "").lower()
        if host in _YT_HOSTS:
            return _parse_youtube(url, first)
        if host == "open.spotify.com":
            return _parse_spotify(url, first)
        if host in _SHORT_LINK_HOSTS:
            return ParsedQuery(
                QueryKind.UNSUPPORTED, first,
                error="Spotify short links (spotify.link) aren't supported. "
                      "Open it and paste the full open.spotify.com link instead.",
            )
        return ParsedQuery(QueryKind.URL, first)

    return ParsedQuery(QueryKind.TEXT, text)


def _parse_yt_time(value: str | None) -> int:
    """'90', '90s', '1m30s', '1h2m3s' -> milliseconds (0 if it can't be read)."""
    if not value:
        return 0
    value = value.strip().lower()
    if value.isdigit():
        return int(value) * 1000
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", value)
    if not m or not any(m.groups()):
        return 0
    h, mi, s = (int(g) if g else 0 for g in m.groups())
    return (h * 3600 + mi * 60 + s) * 1000


def _parse_youtube(url, original: str) -> ParsedQuery:
    host = (url.hostname or "").lower()
    path = url.path or ""
    qs = parse_qs(url.query)

    video_id: str | None = None
    if host.endswith("youtu.be"):
        video_id = path.lstrip("/").split("/")[0] or None
    elif path == "/watch":
        video_id = (qs.get("v") or [None])[0]
    else:
        m = re.match(r"^/(?:shorts|live|embed|v)/([^/?]+)", path)
        if m:
            video_id = m.group(1)

    start_ms = _parse_yt_time((qs.get("t") or qs.get("start") or [""])[0])

    if video_id and _VIDEO_ID.match(video_id):
        return ParsedQuery(
            QueryKind.YOUTUBE_VIDEO, f"https://www.youtube.com/watch?v={video_id}", start_ms
        )

    list_id = (qs.get("list") or [None])[0]
    if list_id and _PLAYLIST_ID.match(list_id) and path in ("/playlist", "/watch"):
        return ParsedQuery(QueryKind.YOUTUBE_PLAYLIST, f"https://www.youtube.com/playlist?list={list_id}")

    return ParsedQuery(
        QueryKind.UNSUPPORTED, original,
        error="That YouTube link doesn't point to a video or playlist I can play.",
    )


def _parse_spotify(url, original: str) -> ParsedQuery:
    parts = [p for p in (url.path or "").split("/") if p]
    if parts and parts[0].startswith("intl-"):  # /intl-de/track/ID
        parts = parts[1:]

    for i in range(len(parts) - 1):
        kind, ident = parts[i], parts[i + 1]
        if kind in _SPOTIFY_KINDS and _SPOTIFY_ID.match(ident):
            return ParsedQuery(QueryKind.SPOTIFY, f"https://open.spotify.com/{kind}/{ident}")

    if parts and parts[0] in {"episode", "show"}:
        return ParsedQuery(QueryKind.UNSUPPORTED, original, error="Spotify podcasts aren't supported, only music.")
    return ParsedQuery(QueryKind.UNSUPPORTED, original, error="I couldn't understand that Spotify link.")


def parse_timestamp(text: str) -> int | None:
    """'90', '1:30', '1:02:03' -> milliseconds, or None if invalid."""
    text = (text or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3 or not all(p.isdigit() for p in parts):
        return None
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + int(p)
    return seconds * 1000


def fmt_duration(ms: int) -> str:
    seconds = max(0, int(ms // 1000))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def progress_bar(position: int, length: int, width: int = 15) -> str:
    if length <= 0:
        return "▬" * width
    idx = min(width - 1, int(position / length * width))
    return "▬" * idx + "🔘" + "▬" * (width - idx - 1)


def truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
