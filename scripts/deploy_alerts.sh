#!/usr/bin/env bash
# Deploy the Valtgeist alert fleet to a remote host (e.g. the Hetzner "shredstream" box)
# and run it 24/7 under systemd. Uses YOUR local ssh key — nothing is embedded here.
#
#   Usage:  scripts/deploy_alerts.sh root@167.233.216.113
#
# Idempotent: re-run it any time to push code changes (it syncs + restarts the service).
set -euo pipefail

TARGET="${1:?usage: deploy_alerts.sh user@host   (e.g. root@167.233.216.113)}"
REMOTE_DIR=/opt/valtgeist
REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo "→ [1/4] syncing code to $TARGET:$REMOTE_DIR (podrunner + research + controllers only)"
ssh "$TARGET" "mkdir -p $REMOTE_DIR"
rsync -az --delete \
  --exclude '__pycache__' --exclude '*.pyc' \
  "$REPO/podrunner" "$REPO/research" "$REPO/controllers" \
  "$TARGET:$REMOTE_DIR/"

echo "→ [2/4] provisioning python venv + websockets on the box"
ssh "$TARGET" bash -s <<'REMOTE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
command -v python3 >/dev/null || { apt-get update && apt-get install -y python3; }
dpkg -s python3-venv >/dev/null 2>&1 || { apt-get update && apt-get install -y python3-venv python3-pip; }
[ -d /opt/valtgeist/.venv ] || python3 -m venv /opt/valtgeist/.venv
/opt/valtgeist/.venv/bin/pip install --quiet --upgrade pip
/opt/valtgeist/.venv/bin/pip install --quiet websockets pillow
mkdir -p /etc/valtgeist
if [ ! -f /etc/valtgeist/alerts.env ]; then
  cat > /etc/valtgeist/alerts.env <<'ENV'
# --- FILL THESE IN, then: systemctl restart valtgeist-alerts ---
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
# --- alert engine ---
ALERTS=1
WS_FEED=1
DRY_RUN=1
FLEET_SIZE=6
POLL_SECONDS=3
RISK=aggressive
# demo-tuned thresholds (raise to 0.70 / 0.80 for a high-signal production channel)
ALERT_CASCADE_N=0.55
ALERT_VPIN=0.70
ENV
  chmod 600 /etc/valtgeist/alerts.env
  echo "   wrote /etc/valtgeist/alerts.env — EDIT IT to add TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID"
else
  echo "   /etc/valtgeist/alerts.env already exists — left untouched"
fi
REMOTE

echo "→ [3/4] installing systemd unit"
scp "$REPO/scripts/valtgeist-alerts.service" "$TARGET:/etc/systemd/system/valtgeist-alerts.service"
ssh "$TARGET" "systemctl daemon-reload && systemctl enable valtgeist-alerts >/dev/null 2>&1 || true"

echo "→ [4/4] done."
cat <<NEXT

Next steps (run these against the box):

  1) add your Telegram creds:
       ssh $TARGET 'nano /etc/valtgeist/alerts.env'

  2) see the FIRST post immediately (delivery smoke-test):
       ssh $TARGET 'set -a; . /etc/valtgeist/alerts.env; \
         /opt/valtgeist/.venv/bin/python3 /opt/valtgeist/podrunner/alerts.py --send-test'

  3) start it 24/7 and watch real alerts stream:
       ssh $TARGET 'systemctl start valtgeist-alerts && journalctl -u valtgeist-alerts -f'

NEXT
