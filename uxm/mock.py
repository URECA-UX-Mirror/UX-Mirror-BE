"""가짜 모델 MockBackend (2번, 이슈 #7). 테스트 시나리오 C0-MOCK, C4~C11용.

진짜 백엔드(uxm/backends.py)와 같은 `decide(request, temperature)` 모양과 같은 응답 형식을 돌려준다.
루프는 진짜인지 가짜인지 구분하지 않는다. 결과는 resultOrigin=MOCK으로 기록한다.

대본(answers)은 라벨이 아니라 semanticKey로 적는다 (Before/After에서 라벨이 달라도 같은 대본을 쓰게).
모델 요청에는 semanticKey가 없으므로(누출 금지), 루프가 백엔드 전용 메타(optionMap)를 따로 넘겨준다.
`wants_meta = True`인 백엔드에만 넘긴다.

대본 항목:
  "상단 메뉴>/shop/orders#1"  그 semanticKey의 후보를 고른다
  "GO_BACK"                   뒤로가기 선택지를 고른다
  {"choice": ..., "probabilities": {...}}   응답을 그대로 돌려준다 (잘못된 응답 테스트용)
"""
import time

SELECTED_PROBABILITY = 0.9


class MockScriptError(RuntimeError):
    """대본이 끝났거나, 대본의 후보가 지금 화면에 없음."""


class MockBackend:
    name = "mock"
    wants_meta = True

    def __init__(self, answers, delay: float = 0.0):
        self.answers = list(answers)
        self.delay = delay
        self.calls = 0

    def info(self) -> dict:
        return {"backend": self.name, "answers": len(self.answers), "delay": self.delay}

    def decide(self, request: dict, temperature: float | None = None, meta: dict | None = None) -> dict:
        if self.delay:
            time.sleep(self.delay)
        if self.calls >= len(self.answers):
            raise MockScriptError(f"대본이 끝남 ({len(self.answers)}개)")
        answer = self.answers[self.calls]
        self.calls += 1

        if isinstance(answer, dict):
            return {"answers": {"next_click": {"type": "choice", "confidence": None, **answer}},
                    "timing_ms": self.delay * 1000}

        option_ids = list(request["questions"]["next_click"]["criteria"])
        if answer == "GO_BACK":
            choice = (meta or {}).get("backOptionId")
        else:
            choice = next((oid for oid, key in (meta or {}).get("optionMap", {}).items() if key == answer), None)
        if choice is None:
            raise MockScriptError(f"{self.calls}번째 대본 {answer!r}이 지금 선택지에 없음")

        rest = [o for o in option_ids if o != choice]
        probs = {o: (1.0 - SELECTED_PROBABILITY) / len(rest) for o in rest} if rest else {}
        probs[choice] = SELECTED_PROBABILITY if rest else 1.0
        return {"answers": {"next_click": {"type": "choice", "choice": choice, "confidence": None,
                                           "probabilities": {o: probs[o] for o in option_ids}}},
                "timing_ms": self.delay * 1000}
