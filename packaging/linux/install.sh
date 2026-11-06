#!/usr/bin/env bash
# Set up AutoRundown on a headless Linux server (Ubuntu), from this checkout:
# a venv with the app's and the extractor's packages, and a systemd user
# service that starts at boot. Run it as the user who will own the app (and
# the Mercedes VM, if any) -- not as root. Re-running it is safe.
#
#   bash packaging/linux/install.sh            # install / update and start
#   bash packaging/linux/install.sh --check    # only say what is missing
#
# It installs no system packages itself; it says which `sudo apt install`
# to run when something is missing.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
USER="${USER:-$(id -un)}"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

if [ "$(id -u)" = 0 ]; then
    echo "Run this as the user who will own AutoRundown, not as root." >&2
    exit 1
fi

missing=()
command -v python3 > /dev/null || missing+=(python3)
python3 -c "import venv, ensurepip" 2> /dev/null || missing+=(python3-venv)
echo "AutoRundown in $REPO"
if [ ${#missing[@]} -gt 0 ]; then
    echo "Needed first:  sudo apt install ${missing[*]}" >&2
    exit 1
fi

# optional pieces: said, never required
if command -v VBoxManage > /dev/null; then
    echo "  VirtualBox: $(VBoxManage --version 2> /dev/null | head -1)"
else
    echo "  VirtualBox: not installed -- only needed for the Mercedes VM import"
    echo "              (sudo apt install virtualbox)"
fi
nashorn=""
for j in /usr/lib/jvm/java-8-*/jre/bin/java /usr/lib/jvm/java-11-*/bin/java; do
    [ -x "$j" ] && { nashorn="$j"; break; }
done
if [ -n "$nashorn" ]; then
    echo "  Java for the VM import: $nashorn"
else
    echo "  Java for the VM import: none with Nashorn -- only needed for the Mercedes VM import"
    echo "              (sudo apt install openjdk-8-jre-headless)"
fi
[ $CHECK_ONLY = 1 ] && exit 0

# start at boot, before anyone signs in (a server nobody logs in to)
linger_hint=""
if [ "$(loginctl show-user "$USER" -p Linger --value 2> /dev/null)" != "yes" ]; then
    if ! loginctl enable-linger "$USER" 2> /dev/null; then
        linger_hint="sudo loginctl enable-linger $USER"
        echo "To start it at boot without signing in, run:  $linger_hint"
    fi
fi

# `systemctl --user` needs this user's systemd session: an SSH login has
# one; `su` / `sudo -u` leave XDG_RUNTIME_DIR unset even when it exists
if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -d "/run/user/$(id -u)" ]; then
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi
if ! systemctl --user show-environment > /dev/null 2>&1; then
    echo "No systemd session for $USER, so the service can't be set up." >&2
    echo "Sign in as $USER over SSH (not su / sudo -u) and run this again," >&2
    echo "or first run:  ${linger_hint:-sudo loginctl enable-linger $USER}" >&2
    exit 1
fi

if [ ! -x "$REPO/.venv/bin/python" ]; then
    echo "Creating $REPO/.venv"
    python3 -m venv "$REPO/.venv"
fi
"$REPO/.venv/bin/pip" install --quiet --upgrade pip
"$REPO/.venv/bin/pip" install --quiet -r "$REPO/requirements.txt" -r "$REPO/requirements-extractor.txt"

mkdir -p "$UNIT_DIR"
sed "s#@REPO@#$REPO#g" "$REPO/packaging/linux/autorundown.service" > "$UNIT_DIR/autorundown.service"
systemctl --user daemon-reload
systemctl --user enable autorundown.service > /dev/null
systemctl --user restart autorundown.service

sleep 2
systemctl --user --no-pager --lines=0 status autorundown.service || true
cat << EOF

AutoRundown runs on port 8756.
  Shop setup (admin) from your own computer, over SSH:
      ssh -L 8756:localhost:8756 $USER@$(hostname)
      then open http://localhost:8756
  Phones on the shop network: http://$(hostname -I 2> /dev/null | awk '{print $1}'):8756
      (allow it through the firewall if one is on: sudo ufw allow 8756/tcp)
  Log:     journalctl --user -u autorundown -f
  Update:  git pull, then run this script again
EOF
