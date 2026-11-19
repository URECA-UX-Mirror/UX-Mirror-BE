#!/usr/bin/env bash
# 서버 코드 갱신. 수동 배포 workflow(.github/workflows/deploy.yml)가 SSH로 부르고, 서버에서 직접 실행해도 된다.
#
#   ./deploy/update.sh               # develop
#   ./deploy/update.sh main          # 다른 브랜치
#   ./deploy/update.sh develop --force   # 진행 중인 Run이 있어도 (그 Run은 끊긴다)
#
# 재시작하면 진행 중인 Run이 끊기므로, Run·Optimization이 돌고 있으면 아무것도 바꾸지 않고 멈춘다.
# 처음 설치는 deploy/setup.sh. 이 스크립트는 그 뒤 코드 갱신만 한다.
set -euo pipefail

# 실행 중에 git pull로 이 파일이 바뀌어도 안전하도록, 전체를 함수로 읽은 뒤 실행한다.
main() {
  local ref="${1:-develop}" force="${2:-}"
  local app_dir run_user
  app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  run_user="$(id -un)"
  cd "$app_dir"

  if ! [[ "$ref" =~ ^[A-Za-z0-9._/-]+$ ]]; then
    echo "브랜치 이름이 올바르지 않습니다: $ref" >&2
    exit 1
  fi
  if [ -n "$force" ] && [ "$force" != "--force" ]; then
    echo "알 수 없는 옵션: $force" >&2
    exit 1
  fi

  if [ "$force" != "--force" ]; then
    check_idle
  else
    echo "[주의] --force: 진행 중인 Run이 있어도 재시작합니다."
  fi

  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "서버 저장소에 커밋되지 않은 수정이 있어 멈춥니다: $(git status --short | head -5 | tr '\n' ' ')" >&2
    exit 1
  fi
  local before
  before="$(git rev-parse --short HEAD)"
  git fetch --quiet origin "$ref"
  git checkout --quiet "$ref"
  git merge --quiet --ff-only "origin/$ref"
  echo "코드: $before → $(git rev-parse --short HEAD) ($ref)"

  .venv/bin/pip install -q -r deploy/requirements-server.txt

  # 서비스 파일이 바뀌었을 수 있으므로 다시 쓴다 (setup.sh와 같은 방식)
  for unit in uxm-shops uxm-api; do
    sed -e "s#__APP_DIR__#$app_dir#g" -e "s#__USER__#$run_user#g" "deploy/$unit.service" \
      | sudo tee "/etc/systemd/system/$unit.service" >/dev/null
  done
  sudo systemctl daemon-reload
  sudo systemctl restart uxm-shops uxm-api

  local health=""
  for _ in $(seq 1 40); do
    health="$(curl -sf -m 3 http://127.0.0.1:8000/api/health || true)"
    [ -n "$health" ] && break
    sleep 1
  done
  if [ -z "$health" ]; then
    echo "API가 다시 뜨지 않았습니다: sudo journalctl -u uxm-api -n 50" >&2
    exit 1
  fi
  printf '%s' "$health" | .venv/bin/python -c '
import json, sys
d = json.load(sys.stdin)
print("health:", d["status"], "| model", d["model"]["ready"], "| browser", d["browser"]["ready"], "| agent", d["agent"]["ready"])'
}

# 진행 중인 Run이 있으면 멈춘다.
#  - API 안의 Run·Optimization: /api/health의 activeRun·activeOptimization
#  - API 밖(스크립트, 예: 자동 개선 검증 Run)의 Run: 결과 폴더에서 최근 30분 안에 바뀐 READY·RUNNING run.json
check_idle() {
  local health busy
  health="$(curl -sf -m 10 http://127.0.0.1:8000/api/health || true)"
  if [ -n "$health" ]; then
    busy="$(printf '%s' "$health" | .venv/bin/python -c '
import json, sys
d = json.load(sys.stdin)
if d.get("activeRun"):
    print("Run " + d["activeRun"])
elif d.get("activeOptimization"):
    print("Optimization " + str(d["activeOptimization"].get("optimizationId")))')"
    if [ -n "$busy" ]; then
      echo "진행 중인 실행이 있어 배포하지 않습니다: $busy (끝난 뒤 다시, 또는 --force)" >&2
      exit 1
    fi
  fi
  busy="$(.venv/bin/python - <<'PY'
import glob, json, os, time
from dotenv import dotenv_values
results = os.getenv("UXM_RESULTS_DIR") or dotenv_values(".env").get("UXM_RESULTS_DIR") or "results"
now, found = time.time(), []
for path in glob.glob(os.path.join(results, "runs", "*", "run.json")):
    if now - os.path.getmtime(path) > 1800:
        continue
    try:
        with open(path, encoding="utf-8") as f:
            run = json.load(f)
    except (OSError, ValueError):
        continue
    if run.get("status") in ("READY", "RUNNING"):
        found.append(run.get("runId") or os.path.basename(os.path.dirname(path)))
print(" ".join(sorted(found)))
PY
)"
  if [ -n "$busy" ]; then
    echo "결과 폴더에 진행 중인 Run이 있어 배포하지 않습니다: $busy (끝난 뒤 다시, 또는 --force)" >&2
    exit 1
  fi
}

main "$@"; exit $?
