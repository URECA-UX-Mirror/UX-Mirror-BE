"""T-LEAK: 모델 요청 payload에 실험 정보가 섞였는지 검사 (기획안 5.4, 테스트 B3)."""
import json

FORBIDDEN = [
    "variant", "before", "after", "testMode", "testmode", "nochange", "deadend",
    "experimentKey", "data-test-goal", "refund-entry", "semanticKey", "menu_", "goalId",
]


def assert_no_leak(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    hits = [w for w in FORBIDDEN if w in text]
    if hits:
        raise RuntimeError(f"T-LEAK 실패: 모델 입력에 금지 문자열 {hits}")
