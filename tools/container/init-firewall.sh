#!/bin/bash
# Outbound network for the test box: Anthropic only (Claude Code's API and
# sign-in), plus DNS. Everything else -- package sites, GitHub, the LAN,
# this PC's other services -- is refused, so an agent in here can't fetch
# or send anything else. Inbound (the published port) is untouched.
# Runs as root at container start (entrypoint.sh, via sudo).
set -euo pipefail

ALLOWED_DOMAINS=(
    api.anthropic.com
    statsig.anthropic.com
    claude.ai
    console.anthropic.com
    platform.claude.com
    sentry.io
)

iptables -F OUTPUT
iptables -P OUTPUT ACCEPT          # resolve first, then close
ips=()
for d in "${ALLOWED_DOMAINS[@]}"; do
    for ip in $(dig +short A "$d" | grep -E '^[0-9.]+$'); do ips+=("$ip"); done
done
[ ${#ips[@]} -gt 0 ] || { echo "firewall: could not resolve any allowed domain" >&2; exit 1; }

iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT   # replies to inbound
iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT
for ip in $(printf '%s\n' "${ips[@]}" | sort -u); do
    iptables -A OUTPUT -d "$ip" -p tcp --dport 443 -j ACCEPT
done
iptables -P OUTPUT DROP
ip6tables -P OUTPUT DROP 2>/dev/null || true

# prove it: something outside the list must fail
if curl -s --max-time 5 https://example.com > /dev/null; then
    echo "firewall: example.com is still reachable" >&2; exit 1
fi
echo "firewall: outbound limited to ${#ALLOWED_DOMAINS[@]} Anthropic domains ($(printf '%s\n' "${ips[@]}" | sort -u | wc -l) addresses)"
