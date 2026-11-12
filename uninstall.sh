#!/bin/sh
# Remove Game Station Launcher.  ./uninstall.sh --purge also deletes the game list and covers.
set -eu
UUID="zlauncher-helper@game-station"

pkill -f "zlauncher/session.sh" 2>/dev/null || true
pkill -f "zlauncher/launcher.py" 2>/dev/null || true
rm -rf "$HOME/.local/share/zlauncher" "$HOME/.local/share/gnome-shell/extensions/$UUID"
rm -f "$HOME/.config/autostart/zlauncher.desktop" "$HOME/.local/share/applications/zlauncher.desktop"

if command -v gsettings >/dev/null 2>&1; then
    python3 - "$UUID" <<'PY'
import ast, subprocess, sys
uuid = sys.argv[1]
out = subprocess.run(["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
                     capture_output=True, text=True).stdout.strip()
cur = [] if out.startswith("@as") or not out else ast.literal_eval(out)
if uuid in cur:
    cur.remove(uuid)
    subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", str(cur)], check=True)
PY
fi

if [ "${1:-}" = "--purge" ]; then
    rm -rf "$HOME/.config/zlauncher" "$HOME/.local/state/zlauncher"
fi
echo "Uninstalled."
