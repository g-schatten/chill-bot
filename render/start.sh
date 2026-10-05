#!/usr/bin/env bash
# Runs yt-cipher + Lavalink + the bot inside ONE container (Render's free plan allows one service).
# If any of the three dies, everything is stopped and the script exits non-zero, so Render
# restarts the whole service.
set -u

: "${DISCORD_TOKEN:?Set DISCORD_TOKEN}" "${GUILD_IDS:?Set GUILD_IDS}"
: "${LAVALINK_PASSWORD:?Set LAVALINK_PASSWORD}" "${CIPHER_PASSWORD:?Set CIPHER_PASSWORD}"

# Render injects PORT (for the web server). The cipher also reads PORT, so give it its own.
LAVALINK_HEAP_MB="${LAVALINK_HEAP_MB:-200}"

echo "[start] yt-cipher..."
( cd /opt/cipher && exec env PORT=8001 HOST=127.0.0.1 API_TOKEN="$CIPHER_PASSWORD" \
    OVERRIDE_PLAYER_VARIANT=IAS MAX_THREADS=1 PREPROCESSED_CACHE_SIZE=10 ./server ) &
CIPHER_PID=$!

echo "[start] Lavalink (heap ${LAVALINK_HEAP_MB} MB)..."
( cd /opt/Lavalink && exec env SERVER_ADDRESS=127.0.0.1 SERVER_PORT=2333 \
    CIPHER_URL=http://127.0.0.1:8001 \
    java -Xms64m -Xmx"${LAVALINK_HEAP_MB}"m -Xss512k -XX:+UseSerialGC \
         -XX:MaxMetaspaceSize=128m -XX:ReservedCodeCacheSize=48m -XX:TieredStopAtLevel=1 \
         -XX:+ExitOnOutOfMemoryError -jar Lavalink.jar ) &
LAVALINK_PID=$!

echo "[start] bot..."
( cd /app && exec env LAVALINK_URI=http://127.0.0.1:2333 \
    LAVALINK_CONNECT_ATTEMPTS="${LAVALINK_CONNECT_ATTEMPTS:-60}" \
    /app/venv/bin/python main.py ) &
BOT_PID=$!

stop_all() {
  kill "$BOT_PID" "$LAVALINK_PID" "$CIPHER_PID" 2>/dev/null
  wait 2>/dev/null
}
trap 'echo "[start] got stop signal"; stop_all; exit 0' TERM INT

wait -n
echo "[start] a process exited (status $?). Stopping the rest so the host restarts the service."
stop_all
exit 1
