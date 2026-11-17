"""T-LEAK: 모델 요청 payload에 실험 정보가 섞였는지 검사 (기획안 5.4, 테스트 B3, 계약 12장).

두 가지를 본다.
1. 문자열 검사: 요청 전체를 JSON 문자열로 만들어 `FORBIDDEN` 글자가 있으면 실패.
2. 성공 조건 key·구조 검사 (B3 ③): 목표 판정 전용 key(`FORBIDDEN_KEYS`)나 목표 판정 구조
   (`type: PATH_AND_VISIBLE_TEXT`, 또는 `path`와 `text`를 같이 가진 객체)가 어디에든 있으면 실패.
   목표 문장에 "환불"이 들어가므로 글자가 아니라 key·구조 기준으로 검사한다.
"""
import json

FORBIDDEN = [
    "variant", "before", "after", "testMode", "testmode", "nochange", "deadend",
    "experimentKey", "data-test-goal", "refund-entry", "semanticKey", "menu_", "goalId",
]

# 목표 판정에만 쓰는 key (계약 12장). 모델 요청 어디에도 있으면 안 된다.
FORBIDDEN_KEYS = {"verification", "successCondition", "goalVerification", "pathMatched", "textVisible"}
GOAL_CHECK_TYPE = "PATH_AND_VISIBLE_TEXT"


def _structure_hits(obj, where: str = "$") -> list[str]:
    """금지 key·목표 판정 구조가 있는 위치 목록 (예: `$.state.verification`)."""
    hits = []
    if isinstance(obj, dict):
        hits += [f"{where}.{k}" for k in obj if k in FORBIDDEN_KEYS]
        if obj.get("type") == GOAL_CHECK_TYPE or {"path", "text"} <= obj.keys():
            hits.append(f"{where} (목표 판정 구조)")
        for k, v in obj.items():
            hits += _structure_hits(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += _structure_hits(v, f"{where}[{i}]")
    return hits


def assert_no_leak(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    words = [w for w in FORBIDDEN if w in text]
    if words:
        raise RuntimeError(f"T-LEAK 실패: 모델 입력에 금지 문자열 {words}")
    keys = _structure_hits(payload)
    if keys:
        raise RuntimeError(f"T-LEAK 실패: 모델 입력에 목표 판정 정보 {keys}")
