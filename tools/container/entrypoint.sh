#!/bin/bash
# Container start: close the network, then serve the throwaway instance
# (dev.py: sample data, its own AUTORUNDOWN_HOME, Ask answered by the fake
# API in tools/fake_anthropic.py, nothing reaches Anthropic) and keep the
# box alive for `box.sh exec`.
set -euo pipefail

rm -f /tmp/ready             # a restarted box is ready only when it says so again
sudo /usr/local/bin/init-firewall.sh

cd /work
# Two published ports, two views of the same app (autorundown/auth.py: a
# request is admin only when it comes from loopback and names a loopback host):
#   18757 -> forward.py -> 127.0.0.1:8757: arrives from loopback = admin
#   8757 directly (dev.py --lan binds 0.0.0.0): arrives from Docker's
#        gateway, not loopback = a phone on the shop network
# --lan also tries to announce autorundown.local; with no outbound network
# that goes nowhere.
python dev.py --no-browser --lan --fake-api > /tmp/dev.log 2>&1 &
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
