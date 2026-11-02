#!/usr/bin/env bash
# Run as the account that will own the service, inside ~/hoshino-tool.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r vps/requirements.txt
mkdir -p "$HOME/.config/hoshino" "$HOME/.local/share/hoshino"
chmod 700 "$HOME/.config/hoshino" "$HOME/.local/share/hoshino"
.venv/bin/python - <<'PY'
from pathlib import Path
import os, secrets
path = Path.home() / '.config/hoshino/server.env'
try:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    pass
else:
    with os.fdopen(descriptor, 'w') as stream:
        stream.write('HOSHINO_ADMIN_TOKEN=' + secrets.token_urlsafe(32) + '\n')
        stream.write('HOSHINO_DATA_DIR=' + str(Path.home() / '.local/share/hoshino') + '\n')
        stream.write('HOSHINO_PORT=18900\n')
PY
.venv/bin/python -I vps/test_service.py
.venv/bin/python -I clone/test_miraimind.py
printf 'Source and dependencies are ready. Install deploy/hoshino.service with systemd.\n'
