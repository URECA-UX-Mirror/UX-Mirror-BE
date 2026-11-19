#!/usr/bin/env bash
# UX MIRROR 백엔드 서버 설치 (Ubuntu 24.04, EC2. caddy를 Ubuntu 기본 저장소에서 받으므로 24.04 이상). 저장소를 받은 사용자(예: ubuntu)로 실행한다. 다시 실행해도 된다.
#
#   ./deploy/setup.sh                 # 도메인 없이: <공인 IP>.sslip.io 로 HTTPS
#   ./deploy/setup.sh api.example.com # 도메인이 이 서버를 가리키면 그 이름으로
#   ./deploy/setup.sh "" --force      # 다시 실행할 때 진행 중인 Run이 있어도 재시작 (그 Run은 끊긴다)
#
# 처음 실행하면 .env를 만들고 멈춘다 → .env를 채운 뒤 다시 실행.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="$(id -un)"
DOMAIN="${1:-}"
cd "$APP_DIR"

if [ "$RUN_USER" = "root" ]; then
  echo "root가 아닌 사용자(예: ubuntu)로 실행해 주세요. 필요한 곳에서만 sudo를 씁니다." >&2
  exit 1
fi

# ---- 1. .env (처음이면 예시를 복사하고 멈춤)
if [ ! -f .env ]; then
  cp deploy/env.server.example .env
  chmod 600 .env
  echo ".env를 만들었습니다. ZORO_BASE_URL·ZORO_ENDPOINT_ID·UXM_CORS_ORIGINS를 채운 뒤 다시 실행해 주세요."
  exit 0
fi
env_value() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- | tr -d '\r'; }
missing=()
for key in ZORO_BACKEND ZORO_BASE_URL ZORO_ENDPOINT_ID UXM_CORS_ORIGINS; do
  [ -n "$(env_value "$key")" ] || missing+=("$key")
done
if [ "${#missing[@]}" -gt 0 ]; then
  echo ".env에 값이 비어 있습니다: ${missing[*]}" >&2
  exit 1
fi
if [ "$(env_value ZORO_BACKEND)" != "http" ]; then
  echo "서버에서는 ZORO_BACKEND=http (GPU 서버)만 씁니다." >&2
  exit 1
fi

# shellcheck source=deploy/lib.sh
source deploy/lib.sh

# ---- 2. HTTPS 주소
if [ -z "$DOMAIN" ]; then
  token="$(curl -sf -m 3 -X PUT http://169.254.169.254/latest/api/token \
             -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' || true)"
  ip="$(curl -sf -m 3 -H "X-aws-ec2-metadata-token: $token" \
          http://169.254.169.254/latest/meta-data/public-ipv4 || true)"
  if [ -z "$ip" ]; then
    echo "공인 IP를 찾지 못했습니다. 도메인을 인자로 주세요: ./deploy/setup.sh <도메인>" >&2
    exit 1
  fi
  DOMAIN="${ip//./-}.sslip.io"
fi
echo "HTTPS 주소: https://$DOMAIN"

# ---- 3. 패키지
sudo apt-get update -y
if ! apt-cache show caddy >/dev/null 2>&1; then
  echo "이 Ubuntu 저장소에 caddy가 없습니다. Ubuntu 24.04 이상 이미지를 써 주세요." >&2
  exit 1
fi
sudo apt-get install -y python3-venv python3-pip git curl caddy

# ---- 4. 파이썬 패키지 + Chromium
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r deploy/requirements-server.txt
sudo .venv/bin/python -m playwright install-deps chromium
.venv/bin/python -m playwright install chromium

# ---- 5. 서비스 등록 (쇼핑몰 → API). 다시 실행할 때 진행 중인 Run이 있으면 재시작하지 않는다
if systemctl is-active --quiet uxm-api 2>/dev/null && [ "${2:-}" != "--force" ] && ! check_idle; then
  exit 1
fi
for unit in uxm-shops uxm-api; do
  sed -e "s#__APP_DIR__#$APP_DIR#g" -e "s#__USER__#$RUN_USER#g" "deploy/$unit.service" \
    | sudo tee "/etc/systemd/system/$unit.service" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable uxm-shops uxm-api >/dev/null
sudo systemctl restart uxm-shops uxm-api

# ---- 6. Caddy (HTTPS → 127.0.0.1:8000). 쓰기 잠금 파일은 처음엔 꺼진 상태로
if [ ! -f "$LOCK_FILE" ]; then
  printf '%s\n' "$LOCK_OFF" | sudo tee "$LOCK_FILE" >/dev/null
fi
sed -e "s#__DOMAIN__#$DOMAIN#g" deploy/Caddyfile | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo systemctl enable caddy >/dev/null
sudo systemctl restart caddy

# ---- 7. 확인: 실제로 Run을 돌릴 수 있는지 (API READY · 쇼핑몰 5개 · 바깥 HTTPS)
echo "확인 중... (처음엔 인증서 발급에 몇 초 걸립니다)"
if ! check_ready 60 "$DOMAIN"; then
  echo "설치는 끝났지만 Run을 돌릴 수 없는 상태입니다. 위 항목을 고친 뒤 ./deploy/setup.sh를 다시 실행해 주세요." >&2
  exit 1
fi
echo
echo "완료: https://$DOMAIN"
echo "Vercel 환경변수 VITE_API_BASE_URL=https://$DOMAIN, VITE_USE_MOCK=false 로 바꾸고 재배포하세요."
