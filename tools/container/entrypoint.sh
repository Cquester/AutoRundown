#!/bin/bash
# Container start: close the network, then serve the throwaway instance
# (dev.py: sample data, its own AUTORUNDOWN_HOME, no API key) and keep the
# box alive for `box.sh exec`.
set -euo pipefail

sudo /usr/local/bin/init-firewall.sh

cd /work
# dev.py binds 127.0.0.1:8757. The app makes a request admin only when it
# comes from loopback and names a loopback host (autorundown/auth.py), so
# the published port goes through forward.py: Docker delivers the PC's
# browser to 0.0.0.0:18757, forward.py hands it to 127.0.0.1:8757.
python dev.py --no-browser > /tmp/dev.log 2>&1 &
python tools/sandbox/forward.py --listen 0.0.0.0:18757 --to 127.0.0.1:8757 > /tmp/forward.log 2>&1 &

for _ in $(seq 120); do
    curl -sf http://127.0.0.1:8757/api/me > /dev/null && break
    sleep 0.5
done
if curl -sf http://127.0.0.1:8757/api/me > /dev/null; then
    echo "ready" > /tmp/ready
    echo "AutoRundown test box ready"
else
    echo "dev.py did not start:"; tail -30 /tmp/dev.log
fi
exec sleep infinity
