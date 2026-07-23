#!/usr/bin/env bash
# Restart the alert fleet if its heartbeat has gone stale — i.e. the process is alive but hung
# (a stuck await, a wedged socket) so Restart=always never fires. Run every few minutes by the
# valtgeist-healthcheck.timer. A missing heartbeat file is treated as "still starting" (no action);
# a genuine crash is already handled by the service's Restart=always.
set -euo pipefail

HB="${HEARTBEAT_FILE:-/run/valtgeist-alerts.heartbeat}"
MAX_AGE="${HEALTHCHECK_MAX_AGE:-600}"   # seconds without a heartbeat before we restart
UNIT=valtgeist-alerts

[ -f "$HB" ] || exit 0
age=$(( $(date +%s) - $(stat -c %Y "$HB") ))
if [ "$age" -gt "$MAX_AGE" ]; then
    echo "valtgeist-healthcheck: heartbeat stale (${age}s > ${MAX_AGE}s) — restarting $UNIT"
    systemctl restart "$UNIT"
fi
