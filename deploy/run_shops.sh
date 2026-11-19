#!/usr/bin/env bash
# 쇼핑몰(v1 8001·v2 8002·후보 C1 8003·C3 8004·C4 8005·C2 8006)을 이 서버 안(127.0.0.1)에서만 띄운다. 하나라도 죽으면 전부 내리고 끝나서 systemd가 다시 띄운다.
# 포트는 config/site_instances.json·.env의 SHOP_*_URL과 같아야 한다.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="$PWD/.venv/bin/python"
SHOPS="moro-before:8001 moro-after:8002 moro-c1:8003 moro-c3:8004 moro-c4:8005 moro-c2:8006"

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT

for spec in $SHOPS; do
  dir="${spec%%:*}"
  port="${spec##*:}"
  (cd "shop/$dir" && exec "$PY" server.py --host 127.0.0.1 --port "$port") &
  pids+=("$!")
done

# 하나라도 끝나면 나간다 (bash 버전과 상관없이 동작하도록 wait -n 대신 확인 반복)
while :; do
  for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "쇼핑몰 서버 하나가 종료되어 전체를 다시 시작합니다." >&2
      exit 1
    fi
  done
  sleep 2
done
