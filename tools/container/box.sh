#!/bin/bash
# AutoRundown test boxes: throwaway Linux containers (Docker in WSL) that
# hold a COPY of this checkout, serve it with sample data, and run tests and
# Claude Code agents inside -- they never see this PC's files, the real
# index, settings or spend log, and their outbound network is Anthropic only.
#
# From Windows (PowerShell / Git Bash), prefix with `wsl -d Ubuntu -- bash`:
#   wsl -d Ubuntu -- bash tools/container/box.sh build
#   ...                       box.sh up qa [--port 18800]   # http://localhost:18800 (admin)
#   ...                       box.sh exec qa python test_records.py
#   ...                       box.sh claude qa "use the qa-engineer agent ..."
#   ...                       box.sh fetch qa .dev/qa       # copy results out
#   ...                       box.sh list | down qa | down --all
#   ...                       box.sh login                  # once: sign Claude Code in
#
# `up` copies the working tree as it is now (uncommitted changes included,
# no .git, no indexes or generated samples) -- a box tests a snapshot, so
# re-run `up` after changing code. Several boxes can run at once, one port
# each. The Claude Code sign-in is the one thing kept between boxes (Docker
# volume ar-claude-auth); everything else goes with `down`.
set -euo pipefail

IMAGE=autorundown-test
AUTH_VOLUME=ar-claude-auth
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
    docker create --name "ar-$name" --label autorundown-box=1 \
        --cap-add NET_ADMIN --cap-add NET_RAW \
        -p "127.0.0.1:$port:18757" \
        -v "$AUTH_VOLUME:/home/dev/.claude" \
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
    docker exec -w /work "ar-$name" "$@"
}

cmd_claude() {
    # A headless Claude Code run inside the box. Permission prompts are off:
    # that is what the box is for -- it holds only a copy of the repo and can
    # reach nothing but Anthropic.
    local name="${1:-}" prompt="${2:-}"
    name_ok "$name"
    [ -n "$prompt" ] || die "usage: box.sh claude NAME \"prompt\""
    docker exec -w /work "ar-$name" claude -p "$prompt" --dangerously-skip-permissions
}

cmd_fetch() {
    local name="${1:-}" path="${2:-}"
    name_ok "$name"
    [ -n "$path" ] || die "usage: box.sh fetch NAME PATH-in-repo"
    local dest="$ROOT/.dev/box/$name"
    mkdir -p "$dest"
    docker cp "ar-$name:/work/$path" "$dest/"
    echo "copied to .dev/box/$name/$(basename "$path")"
}

cmd_login() {
    docker run -it --rm --cap-add NET_ADMIN --cap-add NET_RAW \
        -v "$AUTH_VOLUME:/home/dev/.claude" --entrypoint bash "$IMAGE" \
        -c "sudo /usr/local/bin/init-firewall.sh && claude"
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
    claude) shift; cmd_claude "$@" ;;
    fetch) shift; cmd_fetch "$@" ;;
    login) shift; cmd_login ;;
    list) cmd_list ;;
    down) shift; cmd_down "$@" ;;
    *) sed -n '2,24p' "$0"; exit 2 ;;
esac
