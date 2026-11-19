#!/usr/bin/env bash
# 서버 코드 갱신. 수동 배포 workflow(.github/workflows/deploy.yml)가 SSH로 부르고, 서버에서 직접 실행해도 된다.
#
#   ./deploy/update.sh                        # develop
#   ./deploy/update.sh main                   # 다른 브랜치
#   ./deploy/update.sh develop --sha <커밋>   # 이 커밋일 때만 배포 (workflow가 테스트한 커밋)
#   ./deploy/update.sh develop --force        # 진행 중인 Run이 있어도 (그 Run은 끊긴다)
#
# 순서
#   1. 쓰기 잠금 (실제 요청으로 503 확인) — 새 Run이 API로 들어오지 못한다
#   2. 진행 중인 Run 확인
#   3. 받을 커밋 확인 (--sha와 같은지, fast-forward인지) → Run 다시 확인 → 그 커밋의 패키지 먼저 설치
#   4. 코드 전환 → 진행 중인 Run 한 번 더 확인 → 재시작
#   5. 실제로 Run을 돌릴 수 있는지 (API READY · 쇼핑몰 5개 · 바깥 HTTPS)
#   6. 잠금 해제 (원래 잠겨 있었으면 그대로). 해제하지 못하면 실패로 끝난다
# 재시작 전에 멈추면 코드를 원래 커밋으로 되돌린다. 재시작 뒤 확인이 실패하면 되돌리지 않고(모델 서버 문제일 수 있음)
# 되돌리는 명령을 알려 준다.
# API 밖에서 도는 스크립트 Run(자동 개선 검증 등)은 잠금으로 막을 수 없으니, 끝난 뒤에 배포한다.
set -euo pipefail

# 실행 중에 코드 전환으로 이 파일이 바뀌어도 안전하도록, 전체를 함수로 읽은 뒤 실행한다.
main() {
  local ref="develop" sha="" force="" app_dir run_user
  if [ "$#" -gt 0 ] && [[ "$1" != -* ]]; then ref="$1"; shift; fi
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --sha) [ "$#" -ge 2 ] || { echo "--sha 뒤에 커밋이 필요합니다." >&2; exit 1; }; sha="$2"; shift 2 ;;
      --force) force=1; shift ;;
      *) echo "알 수 없는 옵션: $1" >&2; exit 1 ;;
    esac
  done
  if ! [[ "$ref" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || [[ "$ref" == *..* ]]; then
    echo "브랜치 이름이 올바르지 않습니다: $ref" >&2
    exit 1
  fi
  if [ -n "$sha" ] && ! [[ "$sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "--sha는 40자리 커밋 해시여야 합니다: $sha" >&2
    exit 1
  fi

  app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  run_user="$(id -un)"
  cd "$app_dir"
  # shellcheck source=deploy/lib.sh
  source deploy/lib.sh

  # 동시에 두 배포가 돌지 않게 (서버에서 직접 실행 + workflow)
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$app_dir/.git/uxm-deploy.lock"
    flock -n 9 || { echo "다른 배포가 진행 중입니다." >&2; exit 1; }
  fi
  if ! caddy_has_lock; then
    echo "이 서버의 Caddy 설정에 쓰기 잠금이 없습니다 (이전 setup.sh로 설치). ./deploy/setup.sh를 한 번 다시 실행해 주세요." >&2
    exit 1
  fi

  # ---- 1. 쓰기 잠금
  DEPLOY_LOCKED=""; DEPLOY_BEFORE=""; DEPLOY_SWITCHED=""; DEPLOY_RESTARTED=""; DEPLOY_REF="$ref"
  DEPLOY_ORIG_BRANCH="$(git symbolic-ref -q --short HEAD || true)"
  trap on_exit EXIT
  case "$(lock_state)" in
    off)
      DEPLOY_LOCKED=1
      set_lock on || { echo "쓰기 잠금을 켜지 못해 멈춥니다." >&2; exit 1; }
      echo "쓰기 잠금 on (배포 중 새 Run 차단)"
      sleep 5 ;;   # 잠금 직전에 들어와 실행 전 확인 중인 요청이 Run을 만들 시간
    on)
      echo "쓰기 잠금이 이미 켜져 있어 그대로 둡니다." ;;
    *)
      echo "쓰기 잠금 상태를 실제 요청으로 확인할 수 없어 멈춥니다. Caddy 상태를 먼저 확인해 주세요 (sudo systemctl status caddy)." >&2
      exit 1 ;;
  esac

  # ---- 2. 진행 중인 Run
  if [ -z "$force" ]; then
    check_idle || exit 1
  else
    echo "[주의] --force: 진행 중인 Run이 있어도 재시작합니다."
  fi

  # ---- 3. 받을 커밋 확인 (여기까지는 서버를 바꾸지 않는다)
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "서버 저장소에 커밋되지 않은 수정이 있어 멈춥니다: $(git status --short --untracked-files=no | head -5 | tr '\n' ' ')" >&2
    exit 1
  fi
  local target local_tip reqs
  DEPLOY_BEFORE="$(git rev-parse HEAD)"
  git fetch --quiet origin "+refs/heads/$ref:refs/remotes/origin/$ref" \
    || { echo "origin에서 $ref를 받지 못했습니다." >&2; exit 1; }
  target="$(git rev-parse --verify -q "refs/remotes/origin/$ref^{commit}")" \
    || { echo "origin/$ref를 찾지 못했습니다." >&2; exit 1; }
  if [ -n "$sha" ] && [ "$target" != "$sha" ]; then
    echo "테스트한 커밋(${sha:0:7})과 지금 $ref(${target:0:7})가 다릅니다. 그 사이 새 커밋이 올라왔으니 다시 실행해 주세요." >&2
    exit 1
  fi
  local_tip="$(git rev-parse --verify -q "refs/heads/$ref" || true)"
  if [ -n "$local_tip" ] && ! git merge-base --is-ancestor "$local_tip" "$target"; then
    echo "서버의 $ref가 origin/$ref보다 앞서거나 갈라져 있습니다 (fast-forward 불가)." >&2
    exit 1
  fi
  # 패키지를 바꾸기 직전에도 확인한다 (돌고 있는 Run의 환경을 바꾸지 않게)
  if [ -z "$force" ]; then
    check_idle || exit 1
  fi
  reqs="$(mktemp)"
  git show "$target:deploy/requirements-server.txt" > "$reqs"
  .venv/bin/pip install -q -r "$reqs" || { rm -f "$reqs"; echo "패키지 설치에 실패했습니다." >&2; exit 1; }
  rm -f "$reqs"

  # ---- 4. 코드 전환 → 한 번 더 확인 → 재시작
  DEPLOY_SWITCHED=1
  git checkout --quiet -B "$ref" "$target" --
  echo "코드: ${DEPLOY_BEFORE:0:7} → ${target:0:7} ($ref)"
  if [ -z "$force" ]; then
    check_idle || exit 1   # 패키지 설치 중에 시작된 Run (잠금 전에 들어와 늦게 만들어진 것 등)
  fi
  for unit in uxm-shops uxm-api; do   # 서비스 파일이 바뀌었을 수 있으므로 다시 쓴다 (setup.sh와 같은 방식)
    sed -e "s#__APP_DIR__#$app_dir#g" -e "s#__USER__#$run_user#g" "deploy/$unit.service" \
      | sudo tee "/etc/systemd/system/$unit.service" >/dev/null
  done
  sudo systemctl daemon-reload
  DEPLOY_RESTARTED=1
  sudo systemctl restart uxm-shops uxm-api

  # ---- 5. 실제로 Run을 돌릴 수 있는지
  echo "확인 중..."
  if ! check_ready 60 "$(site_domain)"; then
    echo "배포는 됐지만 Run을 돌릴 수 없는 상태입니다. 위 항목을 확인해 주세요." >&2
    echo "코드 문제라면 되돌리기: ./deploy/update.sh 대신 git checkout -B $ref ${DEPLOY_BEFORE:0:12} && sudo systemctl restart uxm-shops uxm-api" >&2
    exit 1
  fi

  # ---- 6. 잠금 해제
  if [ -n "$DEPLOY_LOCKED" ]; then
    set_lock off || { echo "배포는 됐지만 쓰기 잠금을 풀지 못했습니다: ./deploy/write_lock.sh off" >&2; DEPLOY_LOCKED=""; exit 1; }
    DEPLOY_LOCKED=""
    echo "쓰기 잠금 해제"
  fi
  echo "배포 완료."
}

# 중간에 멈췄을 때: 재시작 전이면 코드를 되돌리고, 내가 건 잠금은 푼다.
on_exit() {
  local rc=$?
  if [ "$rc" != 0 ] && [ -n "${DEPLOY_SWITCHED:-}" ] && [ -z "${DEPLOY_RESTARTED:-}" ] && [ -n "${DEPLOY_BEFORE:-}" ]; then
    if [ "${DEPLOY_ORIG_BRANCH:-}" = "$DEPLOY_REF" ]; then
      git checkout --quiet -B "$DEPLOY_REF" "$DEPLOY_BEFORE" --
    elif [ -n "${DEPLOY_ORIG_BRANCH:-}" ]; then
      git checkout --quiet "$DEPLOY_ORIG_BRANCH" --
    else
      git checkout --quiet --detach "$DEPLOY_BEFORE" --
    fi && echo "코드를 원래 커밋(${DEPLOY_BEFORE:0:7})으로 되돌렸습니다." >&2
  fi
  if [ -n "${DEPLOY_LOCKED:-}" ]; then
    if set_lock off >/dev/null; then echo "쓰기 잠금 해제" >&2
    else echo "[주의] 쓰기 잠금을 풀지 못했습니다: ./deploy/write_lock.sh off" >&2; rc=1; fi
  fi
  exit "$rc"
}

main "$@"; exit $?
