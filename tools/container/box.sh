#!/bin/bash
# AutoRundown test boxes: throwaway Linux containers (Docker in WSL) that
# hold a COPY of this checkout and run it -- the app serving sample data,
# plus everything its tests need. The box never sees this PC's files, the
# real index, settings or spend log, has no API key, and no outbound network.
# Nothing in it needs a sign-in.
#
# Agents and people work on a box from OUTSIDE: through its web address,
# like a browser (http://localhost:<port>, admin), and by running commands
# inside it with `exec`.
#
# From Windows (PowerShell / Git Bash), prefix with `wsl -d Ubuntu -- bash`:
#   wsl -d Ubuntu -- bash tools/container/box.sh build
#   ...                       box.sh up qa [--port 18800]   # http://localhost:18800 (admin)
#   ...                       box.sh check qa               # every CI check, inside (~1 min)
#   ...                       box.sh exec qa python test_records.py
#   ...                       box.sh fetch qa /tmp/ui-shots # copy files out
#   ...                       box.sh list | down qa | down --all
#
# `up` copies the working tree as it is now (uncommitted changes included,
# no .git, no indexes or generated samples) -- a box tests a snapshot, so
# re-run `up` after changing code. Several boxes can run at once, one port
# each; everything in a box goes with `down`.
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

cmd_up() {
    local name="${1:-}"; shift || true
    name_ok "$name"
    local port=18800
    while [ $# -gt 0 ]; do
        case "$1" in
            --port) port="$2"; shift 2 ;;
            *) die "unknown option $1" ;;
        esac
    done
    docker image inspect "$IMAGE" > /dev/null 2>&1 || die "no image yet: run 'box.sh build'"
    docker container inspect "ar-$name" > /dev/null 2>&1 && die "ar-$name exists: 'box.sh down $name' first"
    # --restart: WSL stops its VM (and Docker) soon after the last Windows
    # command ends; when it starts again the box comes back by itself
    docker create --name "ar-$name" --label autorundown-box=1 \
        --restart unless-stopped \
        --cap-add NET_ADMIN --cap-add NET_RAW \
        -p "127.0.0.1:$port:18757" \
        "$IMAGE" > /dev/null
    snapshot | docker cp -a - "ar-$name:/work"
    docker start "ar-$name" > /dev/null
    for _ in $(seq 240); do
        docker exec "ar-$name" test -f /tmp/ready 2>/dev/null && break
        docker container inspect -f '{{.State.Running}}' "ar-$name" | grep -q true \
            || { docker logs "ar-$name"; die "ar-$name stopped"; }
        sleep 0.5
    done
    docker exec "ar-$name" test -f /tmp/ready || { docker logs "ar-$name"; die "ar-$name not ready"; }
    docker logs "ar-$name" 2>&1 | grep -E "firewall|ready" || true
    echo "ar-$name: http://localhost:$port (admin)"
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
    docker ps -a --filter label=autorundown-box=1 \
        --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
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
