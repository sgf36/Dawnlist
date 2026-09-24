#!/usr/bin/env bash
set -euo pipefail

# PySide6 needs these system libraries even in headless/offscreen mode.
sudo apt-get update -qq
sudo apt-get install -y -qq --no-install-recommends \
    libegl1 libgl1 libglib2.0-0 libfontconfig1 \
    libxkbcommon0 libdbus-1-3 libxcb-xinerama0 libxcb-cursor0 \
    libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 >/dev/null

pip install --require-hashes -r requirements.lock.txt -q
pip install --require-hashes -r requirements-dev.lock.txt -q

# Worker dependencies (Node.js)
if [ -f server/package-lock.json ]; then
    (cd server && npm ci --ignore-scripts -q)
fi

echo "Setup complete. Run: pytest tests/ -x -q"
