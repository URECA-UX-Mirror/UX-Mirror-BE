"""목표·페르소나 preset (계약 5.1, 6.6, 17.9).

`config/presets.json`이 원본이다. `verification`과 `referencePathConfig`는 Backend 전용이라
`public_view()`에서 빼고 내보낸다. 모델 요청에도 넣지 않는다.

모든 목표는 `startPath`를 갖고 그 화면에서만 시작한다 (요청에서 생략하면 그 값을 쓰고, 다르면 422).
허용 경로(`referencePathConfig.allowedPaths`)가 시작 화면 기준이라, 다른 화면에서 시작하면
정상 탐색도 경로 이탈·추가 단계로 잘못 판정되기 때문이다. 다른 시작 화면이 필요하면 C3처럼 목표를 따로 둔다.

목표 판정 조건(`verification`)도 로드할 때 검사한다. 잘못된 값이 실행 중에야 드러나 Run이 무효가 되지 않게
서버 시작(`Presets()`)에서 바로 실패시킨다.

`testOnly: true` 목표(예: C3용 `refund-from-mypage-test`)는 개발 테스트 전용이다.
`POST /api/runs`로는 실행할 수 있지만 `GET /api/presets` 기본 응답에서는 뺀다 (E 실행·시연에서 고르지 않게).
"""
import json
import os

from uxm.goal import SCOPES, success_condition  # success_condition: 루프와 같은 변환 (이전 import 경로 호환)

GOAL_CHECK_TYPE = "PATH_AND_VISIBLE_TEXT"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESETS_PATH = os.path.join(ROOT, "config", "presets.json")


class Presets:
    def __init__(self, path: str = PRESETS_PATH):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.goals = {g["goalId"]: g for g in data["goals"]}
        for g in self.goals.values():
            _check_start_path(g)
            _check_verification(g)
        self.personas = {p["personaId"]: p for p in data["personas"]}

    def goal(self, goal_id: str) -> dict | None:
        return self.goals.get(goal_id)

    def persona(self, persona_id: str) -> dict | None:
        return self.personas.get(persona_id)

    def public_view(self, include_test: bool = False) -> dict:
        return {
            "goals": [{"goalId": g["goalId"], "goal": g["goal"]} for g in self.goals.values()
                      if include_test or not g.get("testOnly")],
            "personas": [{"personaId": p["personaId"], "persona": p["persona"]} for p in self.personas.values()],
        }


def _check_start_path(goal: dict) -> None:
    """시작 화면과 허용 경로의 첫 화면이 같아야 한다."""
    start = goal.get("startPath")
    if not start or not start.startswith("/shop/"):
        raise ValueError(f"goalId={goal['goalId']}: startPath(/shop/...)가 필요합니다.")
    page = start.rstrip("/").removeprefix("/shop/")
    paths = goal["referencePathConfig"]["allowedPaths"]
    if not paths or any(not p or p[0] != page for p in paths):
        raise ValueError(f"goalId={goal['goalId']}: 허용 경로는 모두 시작 화면 {page}에서 시작해야 합니다.")


def _check_verification(goal: dict, path_check=None) -> None:
    """목표 판정 조건이 계약 2.3 형식인지. 루프에 넘기는 변환(`success_condition`)도 여기서 한 번 돌려 본다.

    path_check: 시나리오 실행(#74)에서 도착 화면을 그 사이트 화면 목록으로 확인하는 함수. 없으면 MORO MARKET `/shop/...`.
    """
    gid = goal.get("goalId") or "(직접 입력)"
    v = goal.get("verification")
    if not isinstance(v, dict):
        raise ValueError(f"goalId={gid}: verification이 필요합니다.")
    if v.get("type") != GOAL_CHECK_TYPE:
        raise ValueError(f"goalId={gid}: verification.type은 {GOAL_CHECK_TYPE}이어야 합니다.")
    if path_check is not None:
        path_check(v.get("path"))
    elif not isinstance(v.get("path"), str) or not v["path"].startswith("/shop/"):
        raise ValueError(f"goalId={gid}: verification.path는 /shop/...이어야 합니다.")
    if not isinstance(v.get("text"), str) or not v["text"].strip():
        raise ValueError(f"goalId={gid}: verification.text가 비어 있습니다.")
    if "exact" in v and not isinstance(v["exact"], bool):
        raise ValueError(f"goalId={gid}: verification.exact는 true/false여야 합니다.")
    if "scope" in v and v["scope"] not in SCOPES:
        raise ValueError(f"goalId={gid}: verification.scope는 {'/'.join(SCOPES)} 중 하나여야 합니다.")
    # 허용 경로는 도착 화면에서 끝나야 extraSteps·경로 이탈이 맞게 계산된다
    goal_page = v["path"].rstrip("/").removeprefix("/shop/")
    ref = goal.get("referencePathConfig")   # 직접 입력한 목표는 허용 경로가 없다 (None)
    if ref and any(p[-1] != goal_page for p in ref["allowedPaths"]):
        raise ValueError(f"goalId={gid}: 허용 경로는 모두 도착 화면 {goal_page}에서 끝나야 합니다.")
    success_condition(goal)


def normalize_verification(v: dict) -> dict:
    """Run에 저장할 성공 조건. 공백 정리 + 생략된 exact·scope를 기본값으로 채운다 (분석·비교는 저장값을 그대로 비교)."""
    cleaned = {k: (val.strip() if isinstance(val, str) else val) for k, val in v.items()
               if k in ("path", "text", "exact", "scope") and val is not None}
    return {"type": GOAL_CHECK_TYPE, **success_condition({"verification": cleaned})}
