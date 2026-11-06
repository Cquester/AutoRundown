#!/bin/bash
# packaging/linux/install.sh on a stand-in Ubuntu server, end to end -- FREE,
# a few minutes (most of it pip). Needs network (apt for the image, pip in
# the container), unlike a test box.
#
#   wsl -d Ubuntu -- bash tools/container/server_test.sh [--keep] [--ubuntu 24.04]
#
# The server (tools/container/server/Dockerfile) runs systemd and sshd; the
# test signs in over SSH as `shop` (no sudo) and, like a person would:
#   1. runs install.sh from a copy of this checkout (uncommitted edits too)
#   2. checks the service runs and answers, and that `--check` passes
#   3. reaches Shop setup through `ssh -L` (admin) -- and is refused it from
#      the network (a phone)
#   4. turns on lingering the way install.sh tells the person to (root,
#      standing in for sudo) and restarts the "server": the app is back with
#      nobody signed in
#   5. runs install.sh again (an update): still one service, still answering
# Nothing of the PC is in the container; no API key; no Mercedes data.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UBUNTU=26.04
KEEP=0
while [ $# -gt 0 ]; do
    case "$1" in
        --keep) KEEP=1 ;;
        --ubuntu) UBUNTU="$2"; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done
IMAGE="autorundown-server-test:$UBUNTU"
NAME="ar-server-test-$$"
WORK="$(mktemp -d)"
FAILS=0

ok()   { echo "  ok   $*"; }
fail() { echo "  FAIL $*"; FAILS=$((FAILS + 1)); }
step() { echo; echo "-- $* --"; }
cleanup() {
    if [ $KEEP = 1 ]; then
        echo "kept: docker exec -it $NAME bash   (ssh -i $WORK/key -p ${SSH_PORT:-?} shop@localhost)"
    else
        docker rm -f "$NAME" > /dev/null 2>&1 || true
        rm -rf "$WORK"
    fi
}
trap cleanup EXIT

step "the server image (Ubuntu $UBUNTU, systemd, sshd)"
docker build -q --build-arg UBUNTU="$UBUNTU" -t "$IMAGE" "$ROOT/tools/container/server" > /dev/null
ok "built $IMAGE"

# systemd as PID 1 in a container: its own cgroup namespace, /run on tmpfs
docker run -d --name "$NAME" --privileged --cgroupns=private \
    --tmpfs /run --tmpfs /run/lock -p 127.0.0.1::22 -p 127.0.0.1::8756 "$IMAGE" > /dev/null
connect() {   # the published ports change when the container restarts
    SSH_PORT="$(docker port "$NAME" 22/tcp | head -1 | sed 's/.*://')"
    APP_PORT="$(docker port "$NAME" 8756/tcp | head -1 | sed 's/.*://')"
    SSH=(ssh -i "$WORK/key" -p "$SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
         -o LogLevel=ERROR -o BatchMode=yes shop@127.0.0.1)
    for _ in $(seq 60); do "${SSH[@]}" true 2> /dev/null && return 0; sleep 1; done
    "${SSH[@]}" true
}
ssh-keygen -q -t ed25519 -N "" -f "$WORK/key"
docker cp "$WORK/key.pub" "$NAME:/home/shop/.ssh/authorized_keys"
docker exec "$NAME" bash -c "chown shop:shop /home/shop/.ssh/authorized_keys && chmod 600 /home/shop/.ssh/authorized_keys"
docker exec "$NAME" systemctl is-system-running --wait > /dev/null 2>&1 || true

step "1. install.sh, as the README says"
# the checkout as a fresh clone + uncommitted edits would have it
tar -C "$ROOT" -c --exclude-vcs --exclude=.git --exclude=./.dev --exclude=./.sandbox \
    --exclude=./sample_corpus --exclude=./sample_lemon --exclude=./sample_epc --exclude=./sample_charm \
    --exclude=./dist --exclude=./build --exclude='*.db' --exclude='*.db-*' --exclude=__pycache__ \
    --exclude=node_modules --exclude=./tools/out --exclude=./.claude/worktrees --exclude=./.venv . \
    | docker exec -i -u shop -w /home/shop "$NAME" bash -c "mkdir -p AutoRundown && tar -C AutoRundown -x"
# first as `sudo -u shop` would run it, before shop has ever signed in: no
# systemd session of shop's
if docker exec -u shop -w /home/shop "$NAME" bash AutoRundown/packaging/linux/install.sh \
        > "$WORK/nosession.log" 2>&1; then
    fail "with no systemd session it went on anyway"
else
    grep -q "Sign in as shop over SSH" "$WORK/nosession.log" \
        && ok "with no systemd session (sudo -u / su) it stops and says to sign in over SSH" \
        || { fail "with no session:"; cat "$WORK/nosession.log"; }
    docker exec "$NAME" test -e /home/shop/AutoRundown/.venv \
        && fail "... after making the venv (should stop before any work)" \
        || ok "... before any work (no venv made)"
fi
connect || { fail "no SSH into the server"; exit 1; }
ok "signed in over SSH as shop (no sudo)"
if "${SSH[@]}" "bash AutoRundown/packaging/linux/install.sh" > "$WORK/install1.log" 2>&1; then
    ok "install.sh finished"
else
    fail "install.sh failed:"; tail -30 "$WORK/install1.log"
fi
grep -q "sudo loginctl enable-linger shop" "$WORK/install1.log" \
    && ok "no sudo, so it says how to start at boot: sudo loginctl enable-linger shop" \
    || fail "it did not say how to turn on lingering: $(grep -i linger "$WORK/install1.log")"
grep -q "ssh -L 8756:localhost:8756" "$WORK/install1.log" && ok "it says how to reach Shop setup (ssh -L)" \
    || fail "no ssh -L line in its output"

step "2. the service"
answer() {   # $1 = how many seconds to wait for the app's first page
    for _ in $(seq "$1"); do
        "${SSH[@]}" "curl -fsS -o /dev/null http://localhost:8756/" 2> /dev/null && return 0
        sleep 1
    done
    return 1
}
"${SSH[@]}" "systemctl --user is-active autorundown" > /dev/null 2>&1 \
    && ok "autorundown.service is active (systemd user unit)" \
    || { fail "the service is not active:"; "${SSH[@]}" "systemctl --user status autorundown --no-pager; journalctl --user -u autorundown --no-pager | tail -30" || true; }
answer 60 && ok "the app answers on port 8756" || fail "nothing answers on 8756"
"${SSH[@]}" "journalctl --user -u autorundown --no-pager" 2> /dev/null | grep -qi "8756" \
    && ok "its log is in the journal (journalctl --user -u autorundown)" \
    || fail "nothing about 8756 in the journal"
"${SSH[@]}" "bash AutoRundown/packaging/linux/install.sh --check" > "$WORK/check.log" 2>&1 \
    && ok "install.sh --check passes (VirtualBox, Java named as optional)" \
    || { fail "--check failed:"; cat "$WORK/check.log"; }

step "3. Shop setup through an SSH tunnel; refused from the network"
TUNNEL=$((20000 + RANDOM % 20000))
"${SSH[@]}" -f -N -L "$TUNNEL:localhost:8756" -o ExitOnForwardFailure=yes || fail "no ssh -L tunnel"
sleep 1
code="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$TUNNEL/api/settings" || true)"
[ "$code" = 200 ] && ok "through ssh -L: /api/settings 200 (admin)" || fail "through ssh -L: /api/settings $code"
pkill -f "$TUNNEL:localhost:8756" 2> /dev/null || true
# from the network: Docker's published port arrives from the bridge, not loopback
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$APP_PORT/api/settings" || true)"
[ "$code" = 403 ] || [ "$code" = 401 ] && ok "from the network (a phone): /api/settings $code" \
    || fail "from the network: /api/settings $code (want 401/403)"
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$APP_PORT/" || true)"
[ "$code" = 200 ] && ok "... while the page itself loads for a phone" || fail "a phone gets / $code"

step "4. start at boot, nobody signed in"
docker exec "$NAME" loginctl enable-linger shop      # what the person runs with sudo
docker restart "$NAME" > /dev/null
docker exec "$NAME" systemctl is-system-running --wait > /dev/null 2>&1 || true
up=0
for _ in $(seq 90); do
    docker exec "$NAME" curl -fsS -o /dev/null http://localhost:8756/ 2> /dev/null && { up=1; break; }
    sleep 1
done
[ $up = 1 ] && ok "after a restart the app answers before anyone signs in (lingering)" \
    || fail "after a restart nothing answers on 8756"
who="$(docker exec "$NAME" bash -c "ps -o user= -C python3 -C python | sort -u" 2> /dev/null || true)"
[ "$who" = shop ] && ok "... running as shop, not root" || fail "running as: $who"

step "5. install.sh again (an update)"
connect || fail "no SSH after the restart"
if "${SSH[@]}" "bash AutoRundown/packaging/linux/install.sh" > "$WORK/install2.log" 2>&1; then
    ok "a second run finishes"
else
    fail "the second run failed:"; tail -30 "$WORK/install2.log"
fi
grep -q "enable-linger" "$WORK/install2.log" && fail "it asks for lingering again" \
    || ok "... and no longer asks for lingering"
answer 60 && ok "... the app answers again" || fail "nothing answers after the update"
n="$("${SSH[@]}" "systemctl --user list-units --all --no-legend 'autorundown*' | wc -l" || true)"
[ "$n" = 1 ] && ok "... one autorundown service" || fail "$n autorundown units"

echo
if [ $FAILS = 0 ]; then echo "all passed (Ubuntu $UBUNTU)"; else echo "$FAILS FAILED (Ubuntu $UBUNTU)"; fi
[ $FAILS = 0 ]
