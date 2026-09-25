#!/usr/bin/env bash
# Polls for new commits on master and deploys them automatically - wired
# into root's crontab on the production VM (140.245.198.157) via:
#   */5 * * * * /opt/distributor-app/deploy/oracle-cloud/auto-pull.sh >> /var/log/distributor-deploy.log 2>&1
# Only touches anything (pip install, restart) when there's actually a new
# commit, so most runs are a no-op. Runs git/pip as the distributor user
# (which owns the checkout) via `sudo -u distributor`, and restarts directly
# as root - no new sudoers rules needed since this script itself already
# runs as root under cron.
set -euo pipefail
APP_DIR="/opt/distributor-app"

cd "$APP_DIR"
BEFORE=$(sudo -u distributor git rev-parse HEAD)
# --ff-only: if the checkout ever diverges from origin/master for any reason
# (should never happen in this workflow - all changes go through git), fail
# loudly here rather than silently creating a merge commit.
sudo -u distributor git pull --ff-only -q
AFTER=$(sudo -u distributor git rev-parse HEAD)

if [ "$BEFORE" != "$AFTER" ]; then
  echo "$(date -Iseconds) deploying $BEFORE -> $AFTER"
  sudo -u distributor "$APP_DIR/venv/bin/pip" install -r requirements.txt
  systemctl restart distributor
  echo "$(date -Iseconds) restarted distributor"
fi
