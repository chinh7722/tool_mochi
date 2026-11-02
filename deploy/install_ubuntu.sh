#!/usr/bin/env bash
set -euo pipefail
# Install from a reviewed checkout/archive, using a Python >= 3.11 virtualenv.
if [ "$(id -u)" -ne 0 ]; then
    echo 'Run: sudo bash deploy/install_ubuntu.sh /path/to/python3.12' >&2
    exit 1
fi
python_bin="${1:-python3.12}"
"$python_bin" -c 'import sys; assert sys.version_info >= (3, 11), "Python >= 3.11 required"'
app_dir="$(cd -- "$(dirname -- "$0")/.." && pwd)"
if [ "$app_dir" != /opt/hoshino/app ]; then
    echo 'Place application source at /opt/hoshino/app first.' >&2
    exit 1
fi
id hoshino >/dev/null 2>&1 || useradd --system --home-dir /var/lib/hoshino --create-home --shell /usr/sbin/nologin hoshino
install -d -o hoshino -g hoshino -m 700 /var/lib/hoshino
install -d -o root -g hoshino -m 750 /etc/hoshino
"$python_bin" -m venv /opt/hoshino/venv
/opt/hoshino/venv/bin/python -m pip install -r /opt/hoshino/app/requirements.txt
# Linux browser packages and system libraries from the Playwright installer.
/opt/hoshino/venv/bin/python -m playwright install-deps chromium
apt-get install -y libegl1 libopengl0 libxkbcommon0 libdbus-1-3
runuser -u hoshino -- env PLAYWRIGHT_BROWSERS_PATH=/var/lib/hoshino/browsers /opt/hoshino/venv/bin/python -m playwright install chromium
if [ ! -f /etc/hoshino/runtime.env ]; then
    /opt/hoshino/venv/bin/python -c 'import secrets; print("HOSHINO_API_TOKEN="+secrets.token_urlsafe(48))' > /etc/hoshino/runtime.env
fi
chown root:hoshino /etc/hoshino/runtime.env
chmod 640 /etc/hoshino/runtime.env
chown -R root:root /opt/hoshino/app /opt/hoshino/venv
install -m 644 /opt/hoshino/app/deploy/hoshino.service /etc/systemd/system/hoshino.service
systemctl daemon-reload
# Do not start before the operator has run checks and reviewed existing data.
echo 'Installed. Verify, then run: sudo systemctl enable --now hoshino'
