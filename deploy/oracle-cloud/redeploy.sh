#!/usr/bin/env bash
# Pulls the latest code and restarts the service. Run as: sudo bash redeploy.sh
set -euo pipefail
APP_DIR="/opt/distributor-app"

cd "$APP_DIR"
sudo -u distributor git pull
sudo -u distributor "$APP_DIR/venv/bin/pip" install -r requirements.txt
systemctl restart distributor
systemctl status distributor --no-pager -l | head -20
