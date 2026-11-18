"""API 가짜 모델 모드 (`ZORO_BACKEND=mock`).

GPU 서버·로컬 모델 없이 실행 API를 끝까지 돌려 보기 위한 모델이다 (프론트 실연동, 팀 통합 확인).
결과는 항상 `resultOrigin=MOCK`으로 기록하고 발표·E 실행 수치로 쓰지 않는다.

- 대본이 필요한 `uxm.mock.MockBackend`(테스트용)와 달리, 요청만 보고 결정적으로 답한다 (같은 요청 → 같은 확률).
- 후보 설명과 목표 문장이 글자 두 개 단위로 얼마나 겹치는지로 점수를 매긴다. 실제 Zoro-1 판단을 흉내 내는 것이
  목적이 아니라, 형식이 맞는 응답과 그럴듯한 탐색이 나오게 하는 것이 목적이다.
- 뒤로가기 선택지는 점수를 낮게 줘서 정상 이동이 먼저 나오게 한다.
"""
import math

MOCK_MODEL_VERSION = "mock-heuristic-v1"
MOCK_ENDPOINT_ID = "mock"
GO_BACK_PREFIX = "[브라우저]"


def _bigrams(text: str) -> set[str]:
    s = "".join(ch for ch in text if not ch.isspace())
    return {s[i:i + 2] for i in range(len(s) - 1)}


class HeuristicMockBackend:
    name = "mock"

    def info(self) -> dict:
        return {"backend": self.name, "modelVersion": MOCK_MODEL_VERSION}

    def decide(self, request: dict, temperature: float | None = None) -> dict:
        state = request.get("state") or {}
        goal = _bigrams(f"{state.get('goal', '')} {state.get('persona', '')}")
        criteria = request["questions"]["next_click"]["criteria"]
        scores = {}
        for oid, text in criteria.items():
            label = text.split("]", 1)[-1]
            overlap = len(goal & _bigrams(label))
            scores[oid] = -1.0 if text.startswith(GO_BACK_PREFIX) else float(overlap)
        top = max(scores.values())
        weights = {oid: math.exp(v - top) for oid, v in scores.items()}
        total = sum(weights.values())
        probs = {oid: w / total for oid, w in weights.items()}
        choice = max(criteria, key=lambda oid: (probs[oid], -list(criteria).index(oid)))
        return {"answers": {"next_click": {"type": "choice", "choice": choice, "confidence": None,
                                           "probabilities": probs}},
                "timing_ms": 0}
