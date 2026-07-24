#!/usr/bin/env bash
# Restart the alert fleet if its heartbeat has gone stale — i.e. the process is alive but hung
# (a stuck await, a wedged socket) so Restart=always never fires. Run every few minutes by the
# valtgeist-healthcheck.timer. A missing heartbeat file is treated as "still starting" (no action);
# a genuine crash is already handled by the service's Restart=always.
set -euo pipefail

HB="${HEARTBEAT_FILE:-/run/valtgeist-alerts.heartbeat}"
MAX_AGE="${HEALTHCHECK_MAX_AGE:-600}"   # seconds without a heartbeat before we restart
UNIT=valtgeist-alerts

systemctl is-active --quiet "$UNIT" || exit 0   # not running: systemd's Restart= owns that case

if [ ! -f "$HB" ]; then
    # No heartbeat file at all. Treat as "still starting" only within the grace window —
    # if the service has been active for longer than MAX_AGE and never wrote its heartbeat,
    # something is genuinely wrong (hung startup, bad HEARTBEAT_FILE path) and silently
    # exiting 0 forever would disable the exact protection this timer exists to provide.
    started="$(systemctl show -p ActiveEnterTimestamp --value "$UNIT")"
    if [ -n "$started" ]; then
        up=$(( $(date +%s) - $(date -d "$started" +%s 2>/dev/null || date +%s) ))
        if [ "$up" -gt "$MAX_AGE" ]; then
            echo "valtgeist-healthcheck: no heartbeat after ${up}s of uptime — restarting $UNIT"
            systemctl restart "$UNIT"
        fi
    fi
    exit 0
fi

age=$(( $(date +%s) - $(stat -c %Y "$HB") ))
if [ "$age" -gt "$MAX_AGE" ]; then
    echo "valtgeist-healthcheck: heartbeat stale (${age}s > ${MAX_AGE}s) — restarting $UNIT"
    systemctl restart "$UNIT"
fi
