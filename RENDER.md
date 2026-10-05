# Hosting on Render (free) + UptimeRobot

> **Read this first.** Render's free tier is built for small websites, not for a Java audio server.
> This guide gives you the best setup I could design for it, but treat it as a **one-hour experiment**,
> not a sure thing. The "Is it worth it?" section below has the numbers and the pass/fail test.

## 1. Is it worth it? (numbers)

| | Render free instance | What this bot needs |
|---|---|---|
| RAM | 512 MB | Lavalink alone used **~200 MB** in my test (idle, heap capped at 192 MB, *no plugins*, nothing playing). The Python bot adds ~50 MB. The cipher server, the two plugins and real playback add more |
| CPU | **0.1 CPU** | Lavalink needed **8.8 CPU-seconds just to boot** (measured), i.e. about **90 seconds of wall time at 0.1 CPU**, before plugins. Live audio encoding on a tenth of a core is the big unknown |
| Idle behaviour | Sleeps after 15 min with no *inbound* HTTP traffic | The bot's connection to Discord is outbound, so it doesn't count. That's what UptimeRobot fixes |
| Hours | 750 free instance hours per month for the **whole workspace** | One always-on service uses 744 h in a 31-day month. A second always-on service would run you out around mid-month, which is why everything runs in **one container** |
| Disk | None (files vanish on restart) | The queue lives in memory anyway. Plugins are baked into the image so they aren't re-downloaded on every restart |
| Bandwidth | 5 GB/month | Audio sent to Discord counts. My estimate: roughly 90–170 hours of listening per month (64–128 kbps) |

Two things I could **not** test from here:

1. **Playback on 0.1 CPU.** It may stutter or fall behind.
2. **YouTube from Render's IP addresses.** One open-source Discord music bot hosted on Render documents that *YouTube currently rejects audio requests from its Render instance*, and notes that even a paid plan doesn't change that. Cloud IPs are the ones YouTube blocks most.

**Pass/fail test for the experiment** (do all three before investing more time):

- [ ] `/healthz` shows `"discord": true, "lavalink": true` after the first deploy
- [ ] `/play` with a YouTube link plays for 5+ minutes with no stuttering
- [ ] The service is still up the next morning (no restart loop, no spin-down)

If any of these fail, jump to **section 6 (Plan B)**. No hard feelings: the free tier genuinely isn't sized for this.

*If you decide to pay:* Render's Starter plan is $7/month (0.5 CPU, 512 MB) and Standard is $25/month (1 CPU, 2 GB). A ~$5/month VPS elsewhere typically has 4 GB RAM, and the YouTube IP problem exists on any cloud host.

## 2. How the setup works

```
UptimeRobot ──every 5 min──▶ https://<your-service>.onrender.com/healthz
                                   │   (inbound traffic = Render keeps the service awake)
                                   ▼
        ONE Render container (Dockerfile.render, started by render/start.sh)
        ├─ yt-cipher   (127.0.0.1:8001)   YouTube helper
        ├─ Lavalink    (127.0.0.1:2333)   audio, never exposed to the internet
        └─ bot (Python)                   talks to Discord + exposes / and /healthz on $PORT
```

- `GET /` always returns 200 (Render's own port check).
- `GET /healthz` returns **200 only when Discord and Lavalink are both connected**, otherwise **503** with details. Point UptimeRobot at this one, so it also **emails you when the bot is really broken**.
- If any of the three processes dies, the container exits and Render restarts it.

## 3. Setup

### Step 1: Put the project on GitHub
Render deploys from a Git repository.

```bash
cd musicbot
git init
git add .
git commit -m "music bot"
# create an EMPTY *private* repo on github.com, then:
git remote add origin https://github.com/<you>/musicbot.git
git branch -M main
git push -u origin main
```
`.env` is in `.gitignore`, so your token is **not** uploaded. Double-check with `git status` that no `.env` file is listed.

### Step 2: Create the Discord bot
Follow README.md section 1 (token, Message Content intent, invite link, server ID).

### Step 3: Create the Render service
1. Sign up at <https://render.com> and connect your GitHub account.
2. **New + → Web Service** → pick your repo. *(Or **New + → Blueprint**, which reads `render.yaml` and asks you for the secrets.)*
3. Settings (labels may differ slightly from what Render shows today):

| Setting | Value |
|---|---|
| Language / Runtime | **Docker** |
| Dockerfile Path | `./Dockerfile.render` |
| Docker build context | `.` (repo root) |
| Region | **Singapore** (closest to you; can't be changed later) |
| Instance Type | **Free** |
| Health Check Path | leave **empty** (a failing check during the slow first boot would roll the deploy back) |

4. Environment variables:

| Key | Value |
|---|---|
| `DISCORD_TOKEN` | your bot token |
| `GUILD_IDS` | your server ID |
| `OWNER_ID` | your user ID (optional, enables `/debug`) |
| `LAVALINK_PASSWORD` | any long random string (only used inside the container) |
| `CIPHER_PASSWORD` | another long random string |
| `LAVALINK_HEAP_MB` | `200` (if Render reports out-of-memory, try `160`) |
| `LAVALINK_CONNECT_ATTEMPTS` | `60` (Lavalink boots slowly on 0.1 CPU; the bot waits for it) |
| `SPOTIFY_ENABLED` | `false` for now (see README "Spotify links" to switch it on later) |

5. **Create Web Service.** The first build takes several minutes: it builds the cipher, downloads Lavalink and pre-downloads its plugins.

### Step 4: Watch the first boot
In the service's **Logs** tab you should eventually see, in roughly this order:
```
[start] yt-cipher...
[start] Lavalink (heap 200 MB)...
[start] bot...
Health endpoint listening on port 10000 (/ and /healthz)
Lavalink not ready yet (attempt 1/60). Retrying in 2s...      <- normal, can repeat many times
Connected to Lavalink at http://127.0.0.1:2333
Synced 22 slash commands to guild ...
```
Then open `https://<your-service>.onrender.com/healthz`. You want:
```json
{"discord": true, "lavalink": true, "guilds": 1}
```

### Step 5: Add the UptimeRobot monitor
1. Create a free account at <https://uptimerobot.com>.
2. **Add New Monitor**:
   - Monitor type: **HTTP(s)**
   - Friendly name: `music bot`
   - URL: `https://<your-service>.onrender.com/healthz`
   - Monitoring interval: **5 minutes** (the free plan's setting)
   - Alert contact: your email
3. **Create monitor.** UptimeRobot now hits the endpoint every 5 minutes, which is well inside Render's 15-minute window, so the service never sleeps.

Notes:
- UptimeRobot's free plan is for **personal, non-commercial use**, which fits a private bot for friends.
- A 503 response (Lavalink or Discord down) makes UptimeRobot mark it "down" and email you. That's intended. It still counts as traffic, so the service stays awake.
- If UptimeRobot itself has an outage for 15+ minutes, Render will put the service to sleep. The next successful ping wakes it, but it takes minutes to boot at 0.1 CPU and the queue is lost.

### Step 6: Test (the pass/fail checklist from section 1)
Join a voice channel and run `/play https://www.youtube.com/watch?v=...` with a normal song, then listen for 5 minutes. Then try `/play some song name`.

## 4. What to expect

- **Every restart wipes the queue and drops the voice connection.** Render restarts services on deploys and now and then for maintenance. After a restart the bot needs a few minutes to be ready again.
- **After a deploy the bot is silent for minutes**: Lavalink's boot at 0.1 CPU is slow. The slash commands answer "couldn't join" until `/healthz` is green.
- **Updating YouTube support** (README "Updating"): edit `lavalink/application.yml`, `git push`, Render rebuilds automatically.

## 5. Troubleshooting

| Symptom | What to try |
|---|---|
| Build fails in the cipher stage | The yt-cipher project may have changed. Copy the error from the build log and send it to me. It's the likeliest build-time failure since I couldn't run Docker here |
| Logs show `Out of memory` / the instance restarts in a loop | Set `LAVALINK_HEAP_MB=160`. If it persists, the free tier is too small: see Plan B |
| Music stutters or lags | That's the 0.1 CPU limit. There's no setting that fixes it on the free plan |
| `All clients failed` / `Sign in to confirm you're not a bot` | YouTube blocks Render's IP range. Try the YouTube sign-in with a **throwaway** Google account (README, "YouTube sign-in") by setting `YT_OAUTH=true`. If that doesn't help, see Plan B |
| Service sleeps anyway | Check the UptimeRobot monitor is **running**, the URL starts with `https://`, and the interval is 5 minutes. Check the monitor's log for failures |
| `/healthz` says `lavalink: false` for a long time | `LAVALINK_CONNECT_ATTEMPTS` may be too low, or Lavalink crashed. Look in the Logs tab for the `[start] a process exited` line and the lines just above it |
| Free hours run out | 750 h is enough for **one** always-on service in a 31-day month. Don't create a second free service in the same workspace |

## 6. Plan B (if the experiment fails)

1. **A spare PC / old laptop / Raspberry Pi at home** with the normal `docker compose` setup (README). Free, no CPU limit, and a home IP is rarely blocked by YouTube. Recommended.
2. **Oracle Cloud Always Free** (2 Arm cores, 12 GB RAM): the same `docker compose` setup, but it's a datacenter IP and the capacity is hard to get.
3. **A ~$5/month VPS** with the same `docker compose` setup.

You can delete the Render service at any time. Nothing else in the project depends on it.
