#!/usr/bin/env bash
# One-time setup for a fresh Oracle Cloud (or any Ubuntu 22.04/24.04) VM.
# Review this before running - it installs packages and creates a system
# user/service. Run as: sudo bash provision.sh
#
# Fill these in first:
REPO_URL="https://github.com/hariharagangabusiness/distributor_app.git"
BRANCH="master"
APP_DIR="/opt/distributor-app"
DATA_DIR="/opt/distributor-app-data"   # kept OUTSIDE the git checkout so
                                        # `git pull` never touches the live
                                        # database. uploads/ stays INSIDE the
                                        # checkout - db.py supports DATA_DIR,
                                        # but app.py's UPLOAD_ROOT is a fixed
                                        # path next to app.py, not overridable
                                        # via env var. That's fine: uploads/
                                        # is gitignored, so `git pull` never
                                        # touches it either.
DOMAIN=""   # e.g. distributor.yourcompany.com - leave blank to skip nginx/certbot setup

set -euo pipefail

echo "== Installing Python, nginx, git =="
apt-get update
apt-get install -y python3 python3-venv python3-pip nginx git

echo "== Adding swap space =="
# Same reasoning as the Node app's provisioning script - a safety net on a
# small-RAM Always Free shape.
if [ "$(swapon --show | wc -l)" -eq 0 ] && [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# Oracle's Ubuntu marketplace image ships iptables rules that allow only
# SSH (22) in by default, separate from the VCN Security List you configure
# in the OCI console - both have to allow 80/443 or the app stays
# unreachable even after the console-side rule is added. Uncomment once
# you've confirmed this is actually needed (check with: sudo iptables -L):
# iptables -I INPUT 1 -p tcp --dport 80 -j ACCEPT
# iptables -I INPUT 1 -p tcp --dport 443 -j ACCEPT
# netfilter-persistent save

echo "== Creating app user and data directory =="
id -u distributor &>/dev/null || useradd --system --create-home --shell /usr/sbin/nologin distributor
mkdir -p "$DATA_DIR"
chown -R distributor:distributor "$DATA_DIR"

echo "== Cloning the app =="
if [ ! -d "$APP_DIR/.git" ]; then
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

echo "== Creating the Python virtual environment and installing dependencies =="
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

echo "== Writing .env (SECRET_KEY + DATA_DIR - loaded by systemd's EnvironmentFile) =="
if [ ! -f "$APP_DIR/.env" ]; then
  cat > "$APP_DIR/.env" <<EOF
SECRET_KEY=$(venv/bin/python -c "import secrets; print(secrets.token_hex(32))")
DATA_DIR=$DATA_DIR
EOF
fi
chown distributor:distributor "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"

echo "== Creating the first Admin login (skipped if one already exists) =="
# migrate_auth.py only needs DATA_DIR (to find the right database) plus the
# two ADMIN_* vars for its non-interactive path - it never touches
# SECRET_KEY, so there's no need to source the whole .env file here.
ADMIN_PASSWORD_GENERATED=$(venv/bin/python -c "import secrets; print(secrets.token_urlsafe(12))")
sudo -u distributor env \
  DATA_DIR="$DATA_DIR" \
  ADMIN_USERNAME=admin \
  ADMIN_PASSWORD="$ADMIN_PASSWORD_GENERATED" \
  "$APP_DIR/venv/bin/python" "$APP_DIR/migrate_auth.py"

echo "== Fixing ownership =="
chown -R distributor:distributor "$APP_DIR"

echo "== Installing the systemd service =="
cp "$APP_DIR/deploy/oracle-cloud/distributor.service" /etc/systemd/system/distributor.service
sed -i "s|/opt/distributor-app|$APP_DIR|g" /etc/systemd/system/distributor.service
systemctl daemon-reload
systemctl enable --now distributor

if [ -n "$DOMAIN" ]; then
  echo "== Configuring nginx + HTTPS for $DOMAIN =="
  cp "$APP_DIR/deploy/oracle-cloud/nginx-distributor.conf" /etc/nginx/sites-available/distributor
  sed -i "s/YOUR_DOMAIN/$DOMAIN/g" /etc/nginx/sites-available/distributor
  ln -sf /etc/nginx/sites-available/distributor /etc/nginx/sites-enabled/distributor
  rm -f /etc/nginx/sites-enabled/default
  nginx -t && systemctl reload nginx
  apt-get install -y certbot python3-certbot-nginx
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m admin@"$DOMAIN" || \
    echo "certbot failed or needs interactive input - run manually: certbot --nginx -d $DOMAIN"
else
  echo "DOMAIN not set - skipping nginx/HTTPS. The app is reachable on port 5000 for now."
fi

echo ""
echo "== Done. Check status with: systemctl status distributor =="
echo "== First login: username 'admin', password: $ADMIN_PASSWORD_GENERATED =="
echo "== Change that password immediately after logging in (top-right menu > Change Password). =="
