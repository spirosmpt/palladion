#!/usr/bin/env bash
# Palladion on a boxd machine. Run inside the machine, from the unpacked repo:  bash deploy/boxd.sh
# Steps: Python env, data (OSM, Copernicus, weather), production build of the desk, then a start script.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)

echo "== 1/4 Python"
command -v python3 >/dev/null || { sudo apt-get update && sudo apt-get install -y python3 python3-venv; }
python3 -m venv backend/.venv 2>/dev/null || { sudo apt-get install -y python3-venv && python3 -m venv backend/.venv; }
backend/.venv/bin/pip install -q --upgrade pip
backend/.venv/bin/pip install -q -e "backend[pipeline]"

echo "== 2/4 Data (1-2 minutes)"
if [ ! -f data/processed/roads.geojson ]; then
  (cd backend && .venv/bin/python -m palladion.datasets.fetch)
fi

echo "== 3/4 Desk build"
command -v npm >/dev/null || { curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs; }
(cd frontend && npm ci --silent && npm run build)

echo "== 4/4 Start script"
cat > "$ROOT/start.sh" <<'EOS'
#!/usr/bin/env bash
# Starts the desk on port 8000 (point the machine's HTTPS proxy there). Keeps running after you leave the shell.
cd "$(dirname "$0")/backend"
pkill -f "uvicorn palladion.api.app" 2>/dev/null || true
nohup .venv/bin/uvicorn palladion.api.app:app --host 0.0.0.0 --port 8000 --proxy-headers \
  --forwarded-allow-ips='*' > ../uvicorn.log 2>&1 &
sleep 4
curl -s -o /dev/null -w "desk: %{http_code} (401 = locked, correct)\n" http://127.0.0.1:8000/
curl -s -o /dev/null -w "live page: %{http_code} (200 = correct)\n" http://127.0.0.1:8000/send
EOS
chmod +x "$ROOT/start.sh"
echo "Done. Next: put PALLADION_ROOM_KEY and the Vertex settings in backend/.env, sign in to Google, then ./start.sh"
