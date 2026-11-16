"""목표·페르소나 preset (계약 5.1, 6.6, 17.9).

`config/presets.json`이 원본이다. `verification`과 `referencePathConfig`는 Backend 전용이라
`public_view()`에서 빼고 내보낸다. 모델 요청에도 넣지 않는다.

모든 목표는 `startPath`를 갖고 그 화면에서만 시작한다 (요청에서 생략하면 그 값을 쓰고, 다르면 422).
허용 경로(`referencePathConfig.allowedPaths`)가 시작 화면 기준이라, 다른 화면에서 시작하면
정상 탐색도 경로 이탈·추가 단계로 잘못 판정되기 때문이다. 다른 시작 화면이 필요하면 C3처럼 목표를 따로 둔다.

`testOnly: true` 목표(예: C3용 `refund-from-mypage-test`)는 개발 테스트 전용이다.
`POST /api/runs`로는 실행할 수 있지만 `GET /api/presets` 기본 응답에서는 뺀다 (E 실행·시연에서 고르지 않게).
"""
import json
import os

from uxm.goal import success_condition  # noqa: F401  (루프와 같은 변환. 이전 경로 호환)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESETS_PATH = os.path.join(ROOT, "config", "presets.json")


class Presets:
    def __init__(self, path: str = PRESETS_PATH):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.goals = {g["goalId"]: g for g in data["goals"]}
        for g in self.goals.values():
            _check_start_path(g)
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
