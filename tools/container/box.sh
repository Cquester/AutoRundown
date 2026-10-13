#!/bin/bash
# AutoRundown test boxes: throwaway Linux containers (Docker in WSL) that
# hold a COPY of this checkout and run it -- the app serving sample data,
# plus everything its tests need. The box never sees this PC's files, the
# real index, settings or spend log, has no API key, and no outbound network.
# Nothing in it needs a sign-in.
#
# Agents and people work on a box from OUTSIDE, through two web addresses
# and `exec`:
#   admin  http://localhost:<port>    -- arrives from loopback: "this PC",
#                                        Shop setup, imports, admin pages
#   phone  http://localhost:<port+1>  -- arrives from the network: what a
#                                        mechanic's phone gets (no Shop setup,
#                                        sign-in codes, admin pages refused)
#
# From Windows (PowerShell / Git Bash), prefix with `wsl -d Ubuntu -- bash`:
#   wsl -d Ubuntu -- bash tools/container/box.sh build
#   ...                       box.sh up [NAME] [--port N]   # prints its name + addresses
#   ...                       box.sh check NAME             # every CI check, inside (~35 s)
#   ...                       box.sh exec NAME python test_records.py
#   ...                       box.sh fetch NAME /tmp/ui-shots   # copy files out
#   ...                       box.sh list | down NAME | down --all
#
# Several chats can test at once: each `up` takes the next free pair of
# ports from 18800 (checked in Docker, WSL and Windows; two `up`s at the
# same moment can't take the same pair) and, without a NAME, names the box
# after its port (box-18800). Use the name `up` printed for everything else.
# Its last line is machine-readable:  BOX name=box-18800 admin=... phone=...
#
# `up` copies the working tree as it is now (uncommitted changes included,
# no .git, no indexes or generated samples) -- a box tests a snapshot, so
# re-run `up` after changing code. Everything in a box goes with `down`.
set -euo pipefail

IMAGE=autorundown-test
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

die() { echo "box: $*" >&2; exit 1; }
name_ok() { [[ "$1" =~ ^[a-z0-9][a-z0-9-]{0,30}$ ]] || die "name must be a-z, 0-9, '-': $1"; }

snapshot() {
    # the files a fresh clone + uncommitted edits would have, minus anything
    # generated or private; owned by the box's user (uid 1000)
    tar -C "$ROOT" -c --owner=1000 --group=1000 \
        --exclude-vcs --exclude=.git \
        --exclude=./.dev --exclude=./.sandbox --exclude=./sample_corpus \
        --exclude=./sample_lemon --exclude=./dist --exclude=./build \
        --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
        --exclude=__pycache__ --exclude=node_modules --exclude=./tools/out \
        --exclude=./.claude/worktrees --exclude='*.log' \
        .
}

cmd_build() {
    docker build -t "$IMAGE" -f "$ROOT/tools/container/Dockerfile" "$ROOT"
}

FIRST_PORT=18800
WIN_LISTENING=""

port_taken() {
    # in use by a box (running or not), by anything in WSL, or on Windows --
    # WSL forwards localhost:<port> to Windows, where something else may own it
    local p="$1"
    docker ps -a --filter label=autorundown-box=1 --format '{{.Label "ar.ports"}}' \
        | tr ',' '\n' | grep -qx "$p" && return 0
    ss -ltnH "sport = :$p" 2> /dev/null | grep -q . && return 0
    [ -z "$WIN_LISTENING" ] && WIN_LISTENING="$(cmd.exe /c netstat -ano -p tcp 2> /dev/null \
        | tr -d '\r' | awk '$4 == "LISTENING" {print $2}' | sed 's/.*://' | sort -u | tr '\n' ' ')"
    [[ " $WIN_LISTENING " == *" $p "* ]]
}

cmd_up() {
    local name="" port="" fixed_port=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --port) port="$2"; fixed_port=1; shift 2 ;;
            -*) die "unknown option $1" ;;
            *) [ -z "$name" ] || die "one name only"; name="$1"; shift ;;
        esac
    done
    [ -n "$name" ] && name_ok "$name"
    docker image inspect "$IMAGE" > /dev/null 2>&1 || die "no image yet: run 'box.sh build'"
    [ -n "$name" ] && docker container inspect "ar-$name" > /dev/null 2>&1 \
        && die "ar-$name exists: 'box.sh down $name' first, or pick another name"
    port="${port:-$FIRST_PORT}"

    # take the first free pair; `docker create` with the name is the lock
    # two chats starting at once can't both win, and `docker start` fails if
    # a port turned out to be taken -- either way, move on to the next pair
    local box tries=0
    while :; do
        tries=$((tries + 1)); [ $tries -le 50 ] || die "no free port pair from $FIRST_PORT"
        if [ -z "$fixed_port" ] && { port_taken "$port" || port_taken $((port + 1)); }; then
            port=$((port + 2)); continue
        fi
        box="ar-${name:-box-$port}"
        # --restart: WSL stops its VM (and Docker) soon after the last Windows
        # command ends; when it starts again the box comes back by itself
        if ! docker create --name "$box" --label autorundown-box=1 \
                --label "ar.ports=$port,$((port + 1))" \
                --restart unless-stopped \
                --cap-add NET_ADMIN --cap-add NET_RAW \
                -p "127.0.0.1:$port:18757" -p "127.0.0.1:$((port + 1)):8757" \
                "$IMAGE" > /dev/null 2>&1; then
            [ -n "$name" ] && die "could not create $box"
            port=$((port + 2)); continue       # another chat took this pair's name
        fi
        snapshot | docker cp -a - "$box:/work"
        if docker start "$box" > /dev/null 2>&1; then break; fi
        docker rm -f "$box" > /dev/null
        [ -n "$fixed_port" ] && die "port $port or $((port + 1)) is in use"
        port=$((port + 2))
    done
    name="${box#ar-}"
    for _ in $(seq 240); do
        docker exec "ar-$name" test -f /tmp/ready 2>/dev/null && break
        docker container inspect -f '{{.State.Running}}' "ar-$name" | grep -q true \
            || { docker logs "ar-$name"; die "ar-$name stopped"; }
        sleep 0.5
    done
    docker exec "ar-$name" test -f /tmp/ready || { docker logs "ar-$name"; die "ar-$name not ready"; }
    docker logs "ar-$name" 2>&1 | grep -E "firewall|ready" || true
    echo "box $name:"
    echo "  admin  http://localhost:$port"
    echo "  phone  http://localhost:$((port + 1))"
    echo "BOX name=$name admin=http://localhost:$port phone=http://localhost:$((port + 1))"
}

cmd_exec() {
    local name="${1:-}"; shift || true
    name_ok "$name"
    docker exec -i -w /work "ar-$name" "$@"
}

cmd_check() {
    local name="${1:-}"
    name_ok "$name"
    docker exec -w /work "ar-$name" bash tools/container/check.sh
}

cmd_fetch() {
    # a path inside the box (relative to /work, or absolute) -> .dev/box/<name>/
    local name="${1:-}" path="${2:-}"
    name_ok "$name"
    [ -n "$path" ] || die "usage: box.sh fetch NAME PATH"
    [[ "$path" = /* ]] || path="/work/$path"
    local dest="$ROOT/.dev/box/$name"
    mkdir -p "$dest"
    docker cp "ar-$name:$path" "$dest/"
    echo "copied to .dev/box/$name/$(basename "$path")"
}

cmd_list() {
    # name, state, admin and phone addresses
    docker ps -a --filter label=autorundown-box=1 --format '{{.Names}}|{{.Status}}|{{.Label "ar.ports"}}' \
        | awk -F'|' 'BEGIN {printf "%-16s %-28s %-24s %s\n", "NAME", "STATUS", "ADMIN", "PHONE"}
                     {split($3, p, ","); sub(/^ar-/, "", $1)
                      printf "%-16s %-28s %-24s %s\n", $1, $2, "http://localhost:" p[1], "http://localhost:" p[2]}'
}

cmd_down() {
    if [ "${1:-}" = "--all" ]; then
        docker ps -aq --filter label=autorundown-box=1 | xargs -r docker rm -f > /dev/null
        echo "all boxes removed"
    else
        name_ok "${1:-}"
        docker rm -f "ar-$1" > /dev/null && echo "ar-$1 removed"
    fi
}

case "${1:-}" in
    build) shift; cmd_build "$@" ;;
    up) shift; cmd_up "$@" ;;
    exec) shift; cmd_exec "$@" ;;
    check) shift; cmd_check "$@" ;;
    fetch) shift; cmd_fetch "$@" ;;
    list) cmd_list ;;
    down) shift; cmd_down "$@" ;;
    *) sed -n '2,/^set -e/{/^set -e/!p}' "$0"; exit 2 ;;
esac
