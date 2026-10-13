#!/bin/bash
# No outbound network for the test box: the app's own replies and loopback
# only. Package sites, GitHub, the LAN and this PC's other services are all
# refused, so nothing tested here can fetch or send anything -- the app has
# no API key either, so Ask can't spend. Inbound (the published port) is
# untouched. Runs as root at container start (entrypoint.sh, via sudo).
set -euo pipefail

iptables -F OUTPUT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT   # replies to inbound
iptables -P OUTPUT DROP
ip6tables -P OUTPUT DROP 2>/dev/null || true

# prove it: the outside world must be unreachable
if curl -s --max-time 5 https://example.com > /dev/null; then
    echo "firewall: example.com is still reachable" >&2; exit 1
fi
echo "firewall: no outbound network"
