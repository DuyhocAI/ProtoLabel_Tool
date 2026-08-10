#!/usr/bin/env bash
set -Eeuo pipefail

# ProtoLabel one-command launcher.
# Usage: ./scripts/run_all.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
FRONTEND_HOST="${PROTOLABEL_HOST:-0.0.0.0}"
BACKEND_HOST="${PROTOLABEL_BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8100}"
FRONTEND_PORT="${FRONTEND_PORT:-8101}"
LOG_DIR="${PROTOLABEL_LOG_DIR:-$ROOT_DIR/logs}"
mkdir -p "$LOG_DIR"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  source "$ROOT_DIR/.env"
  set +a
fi

# 0.0.0.0 chỉ dùng để bind server. Người dùng phải mở bằng IP LAN thật.
LAN_IPS=""
if command -v hostname >/dev/null 2>&1; then
  LAN_IPS="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | grep -v '^127\.' | paste -sd' ' - || true)"
fi

if [[ ! -f "$BACKEND_DIR/app/main.py" ]]; then
  echo "[ProtoLabel] Không tìm thấy backend: $BACKEND_DIR/app/main.py" >&2; exit 1
fi
if [[ ! -f "$FRONTEND_DIR/package.json" ]]; then
  echo "[ProtoLabel] Không tìm thấy frontend: $FRONTEND_DIR/package.json" >&2; exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "[ProtoLabel] Chưa có npm trong PATH." >&2; exit 1
fi

# Ưu tiên môi trường conda sgdetr vì ultralytics/Torch thường nằm ở đó.
if command -v conda >/dev/null 2>&1 && conda env list | awk '{print $1}' | grep -qx sgdetr; then
  PYTHON_CMD=(conda run --no-capture-output -n sgdetr python)
else
  PYTHON_CMD=(python)
  echo "[ProtoLabel] Cảnh báo: không thấy conda env sgdetr; dùng python hiện tại." >&2
fi

if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  echo "[ProtoLabel] Cài frontend dependencies…"
  (cd "$FRONTEND_DIR" && npm install)
fi

echo "[ProtoLabel] Frontend bind: $FRONTEND_HOST"
echo "[ProtoLabel] Backend : http://127.0.0.1:$BACKEND_PORT (API nội bộ)"
echo "[ProtoLabel] Logs    : $LOG_DIR"
if [[ -n "$LAN_IPS" ]]; then
  for ip in $LAN_IPS; do
    echo "[ProtoLabel] Mở trên LAN: http://$ip:$FRONTEND_PORT"
  done
else
  echo "[ProtoLabel] Không tự tìm thấy IP LAN; chạy: hostname -I"
fi

(
  cd "$BACKEND_DIR"
  exec "${PYTHON_CMD[@]}" -m uvicorn app.main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT"
) >"$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

(
  cd "$FRONTEND_DIR"
  exec npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT"
) >"$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

cleanup() {
  trap - INT TERM EXIT
  echo
  echo "[ProtoLabel] Đang dừng services…"
  kill "$FRONTEND_PID" "$BACKEND_PID" 2>/dev/null || true
  wait "$FRONTEND_PID" "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

sleep 1
if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
  echo "[ProtoLabel] Backend không khởi động được. Xem $LOG_DIR/backend.log" >&2; exit 1
fi
if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
  echo "[ProtoLabel] Frontend không khởi động được. Xem $LOG_DIR/frontend.log" >&2; exit 1
fi

echo "[ProtoLabel] Đang chạy. Nhấn Ctrl+C để dừng cả backend và frontend."
wait "$BACKEND_PID" "$FRONTEND_PID"
