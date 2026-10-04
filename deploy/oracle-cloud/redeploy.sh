#!/usr/bin/env bash
# Pulls the latest code, applies any pending database migrations, and
# restarts the service. Run as: sudo bash redeploy.sh - this is also what
# the GitHub Actions deploy workflow runs over SSH on every push to master.
set -euo pipefail
APP_DIR="/opt/distributor-app"

cd "$APP_DIR"
sudo -u distributor git pull
sudo -u distributor "$APP_DIR/venv/bin/pip" install -r requirements.txt
# DATA_DIR (and SECRET_KEY) normally reach the app via systemd's
# EnvironmentFile=.env - a bare `python run_migrations.py` here wouldn't see
# them and would silently run against the wrong (default, git-checkout-
# local) database path instead of the real one on /opt/distributor-app-data.
# Source .env the same way systemd effectively does before running it.
# Safe to run on every deploy: it's a no-op ("Nothing to do.") whenever
# there's no new migrate_*.py script since the last deploy.
sudo -u distributor bash -c 'set -a; source .env; set +a; venv/bin/python run_migrations.py'
systemctl restart distributor
systemctl status distributor --no-pager -l | head -20
