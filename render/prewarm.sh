#!/usr/bin/env bash
# Build-time only: boot Lavalink once so it downloads its plugins into /opt/Lavalink/plugins,
# then stop it. The plugins stay in the image.
cd /opt/Lavalink || exit 0
export LAVALINK_PASSWORD=build CIPHER_PASSWORD=build SERVER_ADDRESS=127.0.0.1 SERVER_PORT=2333
export CIPHER_URL=http://127.0.0.1:8001

java -Xmx400m -jar Lavalink.jar > /tmp/prewarm.log 2>&1 &
PID=$!

for _ in $(seq 1 90); do
  if curl -fs -m 2 -H "Authorization: build" http://127.0.0.1:2333/version >/dev/null 2>&1; then
    echo "prewarm: Lavalink came up"; break
  fi
  kill -0 "$PID" 2>/dev/null || { echo "prewarm: Lavalink exited early"; break; }
  sleep 2
done

kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null
echo "prewarm: plugins in image:"; ls -1 /opt/Lavalink/plugins 2>/dev/null || echo "  (none)"
tail -n 15 /tmp/prewarm.log
exit 0
