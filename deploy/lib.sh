# deploy/setup.sh·update.sh·write_lock.sh가 같이 쓰는 함수. 직접 실행하지 않고 source 한다.
# 함수 정의만 있어서 source 하는 순간 다 읽힌다 (뒤에 git pull로 파일이 바뀌어도 영향 없음).

API_URL="${UXM_API_URL:-http://127.0.0.1:8000}"
SHOP_PORTS="${UXM_SHOP_PORTS:-8001 8002 8003 8004 8005}"
CADDYFILE="${UXM_CADDYFILE:-/etc/caddy/Caddyfile}"
LOCK_FILE="${UXM_CADDY_LOCK:-/etc/caddy/uxm-write-lock.caddy}"
CADDY_RELOAD="${UXM_CADDY_RELOAD:-sudo systemctl reload caddy}"
LOCK_IMPORT="import /etc/caddy/uxm-write-lock.caddy"

# 쓰기 잠금 내용 (Caddy가 import 하는 파일). 잠그면 POST(Run·Optimization 생성·검증 시작)만 503, 조회는 그대로.
LOCK_ON='# deploy/write_lock.sh on — POST(실행·Optimization 생성·검증)만 503, 조회는 그대로
@uxm_write method POST
header @uxm_write Access-Control-Allow-Origin {http.request.header.Origin}
header @uxm_write Vary Origin
header @uxm_write Content-Type application/json
respond @uxm_write `{"error":{"code":"SERVER_MAINTENANCE","message":"서버 점검 중이라 지금은 실행을 시작할 수 없습니다. 잠시 뒤 다시 시도해 주세요.","retryable":true,"details":null}}` 503'
LOCK_OFF='# deploy/write_lock.sh off — 잠금 없음'

_write_root_file() {   # 내용 → 파일 (쓸 수 없으면 sudo)
  local path="$1" content="$2"
  if [ -w "$path" ] || { [ ! -e "$path" ] && [ -w "$(dirname "$path")" ]; }; then
    printf '%s\n' "$content" > "$path"
  else
    printf '%s\n' "$content" | sudo tee "$path" >/dev/null
  fi
}

site_domain() {   # Caddyfile 첫 사이트 주소 (UXM_SITE_DOMAIN이 정해져 있으면 그 값, 빈 값이면 바깥 확인 생략)
  if [ -n "${UXM_SITE_DOMAIN+x}" ]; then echo "$UXM_SITE_DOMAIN"; return 0; fi
  [ -f "$CADDYFILE" ] || return 0
  { grep -E '^[^#[:space:]][^[:space:]]* \{' "$CADDYFILE" || true; } | head -n1 | cut -d' ' -f1
}

caddy_has_lock() {   # 지금 Caddy 설정이 잠금 파일을 읽는지 (이전 setup.sh로 설치한 서버는 아님)
  [ -f "$CADDYFILE" ] && grep -qF "$LOCK_IMPORT" "$CADDYFILE"
}

# Caddy로 실제 POST를 보내 잠금 상태를 본다 (파일 내용이 아니라 실제 동작).
# POST /api/health는 API에 없는 메서드라 아무것도 만들지 않는다.
#   on      503 — 잠금 중에는 Caddy가 API로 넘기지 않고 직접 답한다 (API가 꺼져 있어도 확인됨)
#   off     405(API가 답함) / 502·504(API로 넘겼지만 API가 꺼짐) — 요청이 API까지 갔다 = 잠금 없음
#   unknown 그 밖 (Caddy가 응답하지 않음 등) — 잠금 상태를 알 수 없다
lock_probe() {
  local domain code url
  domain="$(site_domain)"
  if [ -n "${UXM_LOCK_PROBE_URL:-}" ]; then
    code="$(curl -s -o /dev/null -m 5 -w '%{http_code}' -X POST "$UXM_LOCK_PROBE_URL" || true)"
  elif [ -n "$domain" ]; then
    url="https://$domain/api/health"
    code="$(curl -s -o /dev/null -m 5 -w '%{http_code}' -X POST --resolve "$domain:443:127.0.0.1" "$url" || true)"
  fi
  case "${code:-}" in
    503) echo on ;;
    405|502|504) echo off ;;
    *) echo unknown ;;
  esac
}

lock_state() {   # 실제 동작 기준: on / off / unknown (파일 내용으로 대신하지 않는다)
  lock_probe
}

lock_file_state() {   # 잠금 파일에 적힌 값 (표시용. 실제 적용 여부는 lock_state)
  if [ -f "$LOCK_FILE" ] && grep -q '^respond @uxm_write' "$LOCK_FILE"; then echo on; else echo off; fi
}

# on|off → 파일 쓰기 → Caddy 다시 읽기 → 실제 요청으로 그렇게 된 것을 확인했을 때만 0.
# 확인하지 못하면(unknown) 실패로 본다.
set_lock() {
  local want="$1" i got=""
  if [ "$want" = on ]; then _write_root_file "$LOCK_FILE" "$LOCK_ON"; else _write_root_file "$LOCK_FILE" "$LOCK_OFF"; fi
  for i in 1 2 3; do   # 다시 읽기 → 실제 응답 확인. 안 맞으면 한 번 더 (파일 반영이 늦는 경우)
    $CADDY_RELOAD || { echo "Caddy를 다시 읽지 못했습니다 (sudo journalctl -u caddy -n 30)" >&2; return 1; }
    sleep 1
    got="$(lock_probe)"
    [ "$got" = "$want" ] && return 0
  done
  if [ "$got" = unknown ]; then
    echo "쓰기 잠금이 $want인지 실제 요청으로 확인하지 못했습니다. Caddy 상태를 먼저 확인해 주세요 (sudo systemctl status caddy)." >&2
  else
    echo "쓰기 잠금을 $want(으)로 바꿨지만 실제 응답은 $got 입니다." >&2
  fi
  return 1
}

# 진행 중인 실행이 있으면 이유를 출력 (쓰기 잠금을 건 뒤에 부른다).
#  - 결과 폴더의 Run 잠금(api/run_lock.py, #122): API Run·Optimization·서버에서 직접 돌리는 검증 스크립트 Run 모두
#    (잡은 프로세스가 죽으면 저절로 풀리므로 남아서 막지 않는다)
#  - /api/health의 activeRun·activeOptimization (같은 정보, API 쪽에서 한 번 더)
#  - API가 켜져 있는데 health가 응답하지 않으면 판단할 수 없으므로 실행 중으로 본다
busy_reason() {
  local health="" held=""
  if [ -x .venv/bin/python ]; then
    held="$(.venv/bin/python -m api.run_lock status 2>/dev/null)" && held=""
    [ -n "$held" ] && { echo "Run 잠금: $held"; return 0; }
  fi
  health="$(curl -sf -m 15 "$API_URL/api/health" || true)"
  if [ -z "$health" ]; then
    if systemctl is-active --quiet uxm-api 2>/dev/null; then
      echo "API는 켜져 있는데 /api/health가 응답하지 않아 진행 중인 Run을 확인할 수 없음"
    fi
    return 0
  fi
  printf '%s' "$health" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    print("/api/health 응답을 읽을 수 없음"); sys.exit()
if d.get("activeRun"):
    print("Run " + d["activeRun"])
elif d.get("activeOptimization"):
    print("Optimization " + str(d["activeOptimization"].get("optimizationId")))'
}

check_idle() {   # 진행 중이면 이유를 출력하고 1
  local why
  why="$(busy_reason)"
  if [ -n "$why" ]; then
    echo "진행 중인 실행이 있어 멈춥니다: $why (끝난 뒤 다시, 또는 --force)" >&2
    return 1
  fi
}

# 실제로 Run을 돌릴 수 있는 상태인지. 안 되는 항목을 모두 출력하고 하나라도 있으면 1.
#   API status READY(모델·브라우저·루프) + 쇼핑몰 5개 + (도메인이 있으면) 바깥 HTTPS
check_ready() {
  local wait_s="${1:-60}" domain="${2:-}" health="" detail="" problems=() i code port shops_ok
  for i in $(seq 1 "$wait_s"); do
    health="$(curl -sf -m 3 "$API_URL/api/health" || true)"
    shops_ok=1
    for port in $SHOP_PORTS; do
      code="$(curl -s -o /dev/null -m 3 -w '%{http_code}' "http://127.0.0.1:$port/shop/home" || true)"
      [ "$code" = 200 ] || shops_ok=0
    done
    if [ "$shops_ok" = 1 ] && printf '%s' "$health" | grep -q '"status":"READY"'; then break; fi
    sleep 1
  done
  if [ -z "$health" ]; then
    problems+=("API 응답 없음 (sudo journalctl -u uxm-api -n 50)")
  else
    detail="$(printf '%s' "$health" | python3 -c '
import json, sys
d = json.load(sys.stdin)
bad = [k for k in ("model", "browser", "agent") if not (d.get(k) or {}).get("ready")]
print(d.get("status"), " ".join(bad))' || echo "UNKNOWN 응답 형식")"
    if [ "${detail%% *}" != "READY" ]; then
      problems+=("API status=${detail%% *}, 준비 안 됨: ${detail#* } (model이면 GPU 주소·서버 확인)")
    fi
  fi
  for port in $SHOP_PORTS; do
    code="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:$port/shop/home" || true)"
    [ "$code" = 200 ] || problems+=("쇼핑몰 $port 응답 $code (sudo journalctl -u uxm-shops -n 50)")
  done
  if [ -n "$domain" ]; then
    code=""
    for i in $(seq 1 30); do   # 처음 설치 때는 인증서 발급에 몇 초 걸린다
      code="$(curl -s -o /dev/null -m 10 -w '%{http_code}' "https://$domain/api/health" || true)"
      [ "$code" = 200 ] && break
      sleep 2
    done
    [ "$code" = 200 ] || problems+=("바깥 HTTPS https://$domain/api/health 응답 $code (보안 그룹 80·443, sudo journalctl -u caddy -n 50)")
  fi
  if [ "${#problems[@]}" -gt 0 ]; then
    printf '  ✗ %s\n' "${problems[@]}" >&2
    return 1
  fi
  echo "  ✓ API READY · 쇼핑몰 ${SHOP_PORTS// /·} 응답${domain:+ · https://$domain}"
}
