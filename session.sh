#!/bin/sh
# Bucle del launcher: menú -> juego -> menú ...
DIR="$(dirname "$(readlink -f "$0")")"
RT="${XDG_RUNTIME_DIR:-/tmp}"
ACT="$RT/zlauncher.action"
LOGDIR="$HOME/.local/state/zlauncher"
mkdir -p "$LOGDIR"

exec 9>"$RT/zlauncher.lock"
flock -n 9 || exit 0   # ya hay uno abierto

while true; do
    rm -f "$ACT"
    python3 "$DIR/launcher.py" --menu "$ACT" 2>>"$LOGDIR/launcher.log"
    a="$(cat "$ACT" 2>/dev/null)"
    case "$a" in
        play:*)
            echo "=== $(date '+%F %T') $a" >>"$LOGDIR/game.log"
            python3 "$DIR/launcher.py" --run "${a#play:}" >>"$LOGDIR/game.log" 2>&1 9>&-
            ;;
        reboot)   systemctl reboot;   exit 0 ;;
        poweroff) systemctl poweroff; exit 0 ;;
        *)        exit 0 ;;   # "desktop" o error: volver al escritorio
    esac
done
