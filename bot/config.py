"""Settings, read from environment variables (and from a .env file when running locally)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Raised when a required setting is missing or invalid."""


@dataclass(frozen=True)
class Config:
    token: str
    prefix: str
    guild_ids: tuple[int, ...]
    owner_id: int | None
    lavalink_uri: str
    lavalink_password: str
    default_volume: int
    inactive_timeout: int
    spotify_enabled: bool
    health_port: int | None = None      # set by PORT on hosts like Render
    lavalink_attempts: int = 30         # how long to wait for Lavalink to boot


def _bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _int_list(value: str | None) -> tuple[int, ...]:
    out: list[int] = []
    for part in (value or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise ConfigError(f"GUILD_IDS must be numbers separated by commas, got {part!r}")
        out.append(int(part))
    return tuple(out)


def load_config() -> Config:
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise ConfigError("DISCORD_TOKEN is empty. Put your bot token in the .env file.")

    guild_ids = _int_list(os.getenv("GUILD_IDS"))
    if not guild_ids:
        raise ConfigError(
            "GUILD_IDS is empty. This bot is private: list your server ID(s) in .env "
            "(it leaves every server that is not listed)."
        )

    owner_raw = os.getenv("OWNER_ID", "").strip()
    if owner_raw and not owner_raw.isdigit():
        raise ConfigError("OWNER_ID must be a number (your Discord user ID).")

    password = os.getenv("LAVALINK_PASSWORD", "").strip()
    if not password:
        raise ConfigError("LAVALINK_PASSWORD is empty. Set it in .env.")

    try:
        volume = int(os.getenv("DEFAULT_VOLUME", "60"))
    except ValueError:
        raise ConfigError("DEFAULT_VOLUME must be a number between 0 and 150.") from None

    port_raw = (os.getenv("HEALTH_PORT") or os.getenv("PORT") or "").strip()
    if port_raw and not port_raw.isdigit():
        raise ConfigError("PORT / HEALTH_PORT must be a number.")
    attempts_raw = os.getenv("LAVALINK_CONNECT_ATTEMPTS", "30").strip()
    if not attempts_raw.isdigit() or int(attempts_raw) < 1:
        raise ConfigError("LAVALINK_CONNECT_ATTEMPTS must be a positive number.")

    return Config(
        token=token,
        prefix=os.getenv("BOT_PREFIX", "!").strip() or "!",
        guild_ids=guild_ids,
        owner_id=int(owner_raw) if owner_raw else None,
        lavalink_uri=os.getenv("LAVALINK_URI", "http://localhost:2333").strip(),
        lavalink_password=password,
        default_volume=max(0, min(150, volume)),
        inactive_timeout=600,
        spotify_enabled=_bool(os.getenv("SPOTIFY_ENABLED")),
        health_port=int(port_raw) if port_raw else None,
        lavalink_attempts=int(attempts_raw),
    )
