"""목표·페르소나 preset (계약 5.1, 6.6, 17.9).

`config/presets.json`이 원본이다. `verification`과 `referencePathConfig`는 Backend 전용이라
`public_view()`에서 빼고 내보낸다. 모델 요청에도 넣지 않는다.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESETS_PATH = os.path.join(ROOT, "config", "presets.json")


class Presets:
    def __init__(self, path: str = PRESETS_PATH):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.goals = {g["goalId"]: g for g in data["goals"]}
        self.personas = {p["personaId"]: p for p in data["personas"]}

    def goal(self, goal_id: str) -> dict | None:
        return self.goals.get(goal_id)

    def persona(self, persona_id: str) -> dict | None:
        return self.personas.get(persona_id)

    def public_view(self) -> dict:
        return {
            "goals": [{"goalId": g["goalId"], "goal": g["goal"]} for g in self.goals.values()],
            "personas": [{"personaId": p["personaId"], "persona": p["persona"]} for p in self.personas.values()],
        }


def success_condition(goal: dict) -> dict:
    """`uxm/goal.py`의 `verify_goal`이 받는 형식 `{"path", "text"}`로 바꾼다 (계약 2.3)."""
    v = goal["verification"]
    return {"path": v["path"], "text": v["text"]}
