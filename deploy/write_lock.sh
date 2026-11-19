#!/usr/bin/env bash
# 쓰기 잠금: 켜면 Caddy에서 POST(Run·Optimization 생성·검증 시작)만 503으로 막고 조회·비교·재생은 그대로 둔다.
# 발표·실제 검증 시간에 외부에서 Run이 끼어들지 않게 할 때, 배포(update.sh) 중에 자동으로 쓴다.
#
#   ./deploy/write_lock.sh on | off | status
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=deploy/lib.sh
source deploy/lib.sh

case "${1:-status}" in
  on)  set_lock on  && echo "쓰기 잠금: on (POST는 503)" ;;
  off) set_lock off && echo "쓰기 잠금: off" ;;
  status)
    state="$(lock_state)"
    echo "쓰기 잠금: $state (파일: $(lock_file_state))"
    [ "$state" != unknown ] || { echo "실제 요청으로 확인하지 못했습니다. Caddy 상태를 확인해 주세요." >&2; exit 1; } ;;
  *) echo "사용법: $0 on|off|status" >&2; exit 1 ;;
esac
