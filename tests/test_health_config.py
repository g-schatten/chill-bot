import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

import health
from config import ConfigError, load_config


def _get(status, path):
    async def go():
        async with TestClient(TestServer(health.build_app(lambda: status))) as client:
            resp = await client.get(path)
            return resp.status, await resp.text()
    return asyncio.run(go())


def test_root_is_always_200_even_when_not_ready():
    code, body = _get({"discord": False, "lavalink": False}, "/")
    assert code == 200 and body == "alive"


def test_healthz_200_only_when_discord_and_lavalink_are_up():
    assert _get({"discord": True, "lavalink": True, "guilds": 1}, "/healthz")[0] == 200
    assert _get({"discord": True, "lavalink": False}, "/healthz")[0] == 503
    assert _get({"discord": False, "lavalink": True}, "/healthz")[0] == 503


def test_healthz_reports_details():
    code, body = _get({"discord": True, "lavalink": False, "guilds": 2}, "/healthz")
    assert code == 503 and '"lavalink": false' in body and '"guilds": 2' in body


@pytest.fixture
def env(monkeypatch):
    for k in ("PORT", "HEALTH_PORT", "LAVALINK_CONNECT_ATTEMPTS", "OWNER_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "tok")
    monkeypatch.setenv("GUILD_IDS", "1, 2")
    monkeypatch.setenv("LAVALINK_PASSWORD", "pw")
    monkeypatch.setattr("config.load_dotenv", lambda *a, **k: None)
    return monkeypatch


def test_config_defaults_have_no_health_server(env):
    cfg = load_config()
    assert cfg.health_port is None and cfg.lavalink_attempts == 30
    assert cfg.guild_ids == (1, 2)


def test_config_render_style_port_and_attempts(env):
    env.setenv("PORT", "10000")
    env.setenv("LAVALINK_CONNECT_ATTEMPTS", "60")
    cfg = load_config()
    assert cfg.health_port == 10000 and cfg.lavalink_attempts == 60


@pytest.mark.parametrize("name,value", [("PORT", "abc"), ("LAVALINK_CONNECT_ATTEMPTS", "0"), ("OWNER_ID", "me")])
def test_config_rejects_bad_numbers(env, name, value):
    env.setenv(name, value)
    with pytest.raises(ConfigError):
        load_config()


def test_config_requires_guild_ids(env):
    env.setenv("GUILD_IDS", "")
    with pytest.raises(ConfigError):
        load_config()
