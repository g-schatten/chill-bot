# Private Discord Music Bot

A music bot for your own server, in the style of Jockie Music.

- **YouTube link** → plays exactly that video (a `&list=` in the link is ignored, a `?t=90` timestamp is honoured).
- **Spotify link** (track / album / playlist) → finds the matching audio on YouTube.
- **Song name** (with or without artist) → searches YouTube Music first (official audio), then YouTube. A **"Wrong song?"** button lets you swap in another result.
- Queue, loop (track / queue), pause, resume, skip, skip-to, back, seek, volume, shuffle, remove, move, clear, now-playing.
- Every command works as a slash command (`/play`) **and** with a prefix (`!play`).
- Private by design: it only works in the servers listed in `GUILD_IDS` and leaves any other server.

```
You ──▶ Discord ──▶ bot (Python, discord.py + wavelink) ──▶ Lavalink (audio) ──▶ Discord voice
                                                              ├─ youtube-source plugin ◀─ cipher (yt-cipher)
                                                              └─ LavaSrc plugin (Spotify → YouTube matching)
```

## 1. Create the Discord bot (≈10 minutes)

1. Go to <https://discord.com/developers/applications> → **New Application**.
2. **Bot** tab → **Reset Token** → copy it (this is `DISCORD_TOKEN`). Treat it like a password.
3. Still on the **Bot** tab:
   - turn **Public Bot** OFF (so nobody else can invite it),
   - turn **Message Content Intent** ON (needed for `!play` style commands).
4. Copy your **Application ID** from the *General Information* tab and open this link (replace `YOUR_APP_ID`):

   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot%20applications.commands&permissions=3230720
   ```

   Pick your server and authorise. (The permissions are: View Channels, Send Messages, Embed Links, Read Message History, Connect, Speak.)
5. In Discord: **Settings → Advanced → Developer Mode** ON. Right-click your server icon → **Copy Server ID** (this is `GUILD_IDS`). Right-click yourself → **Copy User ID** (optional `OWNER_ID`).

## 2. Configure

```bash
cp .env.example .env
```

Edit `.env` and fill in at least `DISCORD_TOKEN`, `GUILD_IDS`, `LAVALINK_PASSWORD` and `CIPHER_PASSWORD` (make the last two long random strings; they only secure the containers talking to each other).

## 3. Start it

You need [Docker](https://docs.docker.com/get-docker/) (Docker Desktop on Windows/Mac).

```bash
docker compose up -d --build
docker compose logs -f bot lavalink
```

The **first start takes a few minutes**: Lavalink downloads its plugins and the cipher server is built. The bot retries until Lavalink is ready. You're good when the bot log says `Connected to Lavalink` and `Synced 22 slash commands`.

Then join a voice channel and try `/play never gonna give you up`.

### Developing on your PC (optional)
Run only the helpers in Docker and the bot directly with Python:

```bash
docker compose up -d lavalink cipher
cd bot
pip install -r requirements.txt
python main.py          # reads ../.env, talks to Lavalink on localhost:2333
```

## Commands

| Command | What it does |
|---|---|
| `play <link or name>` (`p`) | Add to the end of the queue; starts playing if idle |
| `playnext <link or name>` (`pn`) | Add to the *front* of the queue |
| `search <name>` | Show the top 5 results and pick one |
| `pause` / `resume` | Pause / resume |
| `skip` (`s`) · `skipto <n>` · `back` | Move through the queue |
| `queue [page]` (`q`) · `nowplaying` (`np`) | Show the queue / the current song with a progress bar |
| `loop [off\|track\|queue]` | Loop the song or the whole queue (no argument = cycle) |
| `remove <n>` · `move <from> <to>` · `shuffle` · `clear` | Edit the queue |
| `seek <1:30>` · `volume [0-150]` | Jump in the song / set volume |
| `stop` · `leave` | Stop and clear everything / disconnect |
| `join` · `help` | Join your channel / list commands |
| `debug` | *(owner only)* Lavalink status, version and plugins |

Only people in the bot's voice channel can control it. It leaves after ~3 minutes alone, or ~10 minutes with nothing playing.

## Spotify links

The bot never plays audio *from* Spotify (nobody can). It reads the track info and plays the best matching YouTube result.

Spotify changed its rules in 2026: the account that creates an API app now needs an active **Premium** subscription.

- **Option A (official):** someone with Premium creates an app at <https://developer.spotify.com/dashboard>, then in `.env`:
  ```
  SPOTIFY_ENABLED=true
  SPOTIFY_CLIENT_ID=...
  SPOTIFY_CLIENT_SECRET=...
  ```
- **Option B (unofficial, nobody has Premium):** set `SPOTIFY_ENABLED=true` and `SPOTIFY_PREFER_ANON=true`. This uses the token Spotify's own web player uses. It goes against Spotify's terms and can break without warning. If Lavalink logs a Spotify error at startup, set both back to `false`.

Apply changes with `docker compose up -d`. Playlists are capped at 200 tracks, albums at 100.

## Hosting on Render (free) + UptimeRobot

See **[RENDER.md](RENDER.md)**. It includes a single-container build (`Dockerfile.render`), a health endpoint for UptimeRobot, and an honest assessment: Render's free tier (512 MB, 0.1 CPU) is very tight for Lavalink, so treat it as an experiment with a clear pass/fail test.

## Updating (do this when playback breaks)

YouTube changes things constantly, so every month or two something will break. The 5-minute routine:

1. Check <https://github.com/lavalink-devs/youtube-source/releases>. If there is a release **newer than 1.18.2**, edit `lavalink/application.yml`:
   ```yaml
   - dependency: "dev.lavalink.youtube:youtube-plugin:NEW_VERSION"
     snapshot: false
   ```
   (The file currently pins a snapshot build because 1.18.2 stopped working when YouTube forced SABR-only responses in September 2026.)
2. Pull fresh images and rebuild:
   ```bash
   docker compose pull
   docker compose build --pull --no-cache cipher bot
   docker compose up -d
   ```

## Troubleshooting

| Symptom | What to try |
|---|---|
| Slash commands don't show up | Re-invite with the link above (needs the `applications.commands` scope), check `GUILD_IDS`, restart the bot, reload Discord (Ctrl+R) |
| `Lavalink not ready yet` forever | `docker compose logs lavalink`. Usually a plugin download still running, or `LAVALINK_PASSWORD` differs between files |
| `Couldn't load that: ...` / `All clients failed` | YouTube broke something. Do the **Updating** routine above and read `docker compose logs lavalink` |
| `Sign in to confirm you're not a bot` | YouTube flagged your server's IP (common on cloud servers). Best fix: run the stack on a home machine. Otherwise enable sign-in below |
| Bot joins but there's no sound | `docker compose pull && docker compose up -d` (Discord's encrypted voice needs a current Lavalink), then run `/debug` |
| Wrong song from a text search | Press **Wrong song?** under the message, or use `/search`, or paste the exact link |

### YouTube sign-in (only if you hit the bot error)
1. Create a **throwaway Google account** (never your main one: it can get flagged).
2. Set `YT_OAUTH=true` in `.env`, run `docker compose up -d`.
3. `docker compose logs -f lavalink` shows a code. Open <https://google.com/device>, enter it, sign in with the throwaway account.
4. The log then prints a refresh token. Put it in `lavalink/application.yml` (uncomment `refreshToken:`) so you don't need to repeat step 3.

## Security notes

- `.env` holds your bot token. Never share it or commit it. If it leaks: Developer Portal → Bot → Reset Token.
- Lavalink's port is bound to `127.0.0.1` only, so it isn't reachable from the internet. Don't change that.
- Streaming YouTube audio like this is against YouTube's terms of service. A private bot for a few friends is very low profile, but it is a gray area.

## Tests

```bash
pip install -r bot/requirements.txt pytest
pytest
```

## Layout

```
docker-compose.yml        lavalink + cipher + bot
lavalink/application.yml  Lavalink + plugin settings
bot/main.py               startup, Lavalink connection, server allowlist, error handling
bot/cogs/music.py         all commands and events
bot/utils.py              link parsing (YouTube/Spotify), formatting
bot/queue_ops.py          remove / move / skip-to / back helpers
bot/health.py             / and /healthz endpoint (only used on Render)
Dockerfile.render         all-in-one image for Render (cipher + Lavalink + bot)
render/start.sh           starts and supervises the three processes
render.yaml               optional Render Blueprint
RENDER.md                 Render + UptimeRobot guide
tests/                    unit + behaviour tests
```
