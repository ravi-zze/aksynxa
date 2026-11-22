#!/usr/bin/env bash
# AKSynxa WhatsApp Base — ek command me setup + start
# Usage: ./run.sh            (demo dental brand ke saath)
#        BRAND_CONFIG=brands/aksynxa.yaml ./run.sh
set -e
cd "$(dirname "$0")"

export BRAND_CONFIG="${BRAND_CONFIG:-brands/demo-dental.yaml}"
PORT="${PORT:-8000}"

if [ ! -d .venv ]; then
  echo "Virtual environment bana rahe hain…"
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt

echo ""
echo "=================================================="
echo "  AKSynxa WhatsApp Base chal raha hai (MULTI-BRAND)"
echo "  Default brand: $BRAND_CONFIG (baaki brands bhi saath live hain)"
echo "  Landing page : http://localhost:$PORT/"
echo "  Admin setup  : http://localhost:$PORT/admin   (password: ADMIN_PASSWORD env, default admin123)"
echo "  Dashboard    : http://localhost:$PORT/dashboard"
echo "  Demo chat    : http://localhost:$PORT/demo"
echo "=================================================="
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
