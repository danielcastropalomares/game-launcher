#!/bin/sh
# Install Game Station Launcher for the current user.
#   ./install.sh                 install / update
#   ./install.sh --deps          also install the apt dependencies (needs sudo)
#   ./install.sh --no-autostart  do not start the launcher at login
set -eu

SRC="$(cd "$(dirname "$0")" && pwd)"
UUID="zlauncher-helper@game-station"
APP="$HOME/.local/share/zlauncher"
CONF="$HOME/.config/zlauncher"
EXT="$HOME/.local/share/gnome-shell/extensions/$UUID"
AUTOSTART=1

for arg in "$@"; do
    case "$arg" in
        --deps) sudo apt-get install -y python3-pygame libglib2.0-bin util-linux fonts-ubuntu ;;
        --no-autostart) AUTOSTART=0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

if ! python3 -c "import pygame" >/dev/null 2>&1; then
    echo "pygame is not installed. Run: ./install.sh --deps  (or: sudo apt install python3-pygame)" >&2
    exit 1
fi

install -d "$APP" "$CONF/covers" "$EXT" "$HOME/.local/share/applications" "$HOME/.config/autostart"
install -m 755 "$SRC/launcher.py" "$SRC/session.sh" "$APP/"
install -m 644 "$SRC/gnome-extension/$UUID/metadata.json" "$SRC/gnome-extension/$UUID/extension.js" "$EXT/"

if [ ! -f "$CONF/games.json" ]; then
    printf '{\n  "games": [],\n  "browse_root": "~"\n}\n' > "$CONF/games.json"
fi

DESKTOP="[Desktop Entry]
Type=Application
Name=Game Station
Comment=Fullscreen game launcher
Exec=$APP/session.sh
Icon=input-gaming
Terminal=false
Categories=Game;
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=2"
printf '%s\n' "$DESKTOP" > "$HOME/.local/share/applications/zlauncher.desktop"
if [ "$AUTOSTART" = 1 ]; then
    printf '%s\n' "$DESKTOP" > "$HOME/.config/autostart/zlauncher.desktop"
else
    rm -f "$HOME/.config/autostart/zlauncher.desktop"
fi

# Enable the GNOME Shell helper. A newly copied extension is only discovered
# after logging in again, so add it to the enabled list directly.
if command -v gsettings >/dev/null 2>&1; then
    python3 - "$UUID" <<'PY'
import ast, subprocess, sys
uuid = sys.argv[1]
out = subprocess.run(["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
                     capture_output=True, text=True).stdout.strip()
cur = [] if out.startswith("@as") or not out else ast.literal_eval(out)
if uuid not in cur:
    cur.append(uuid)
    subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", str(cur)], check=True)
PY
fi

echo "Installed."
echo "Log out and back in (or reboot) so GNOME loads the helper extension."
[ "$AUTOSTART" = 1 ] && echo "The launcher will start automatically at login."
exit 0
