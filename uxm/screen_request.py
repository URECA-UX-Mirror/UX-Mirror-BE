"""실제 화면에서 뽑은 후보 → Zoro-1 요청 (B3, B5). 형식은 A 시나리오(build_request)와 같다."""
import json
from urllib.parse import urlsplit

from uxm import prompts as P


def build_screen_request(extracted: dict, history: list | None = None, failed: list | None = None,
                         go_back: bool = False) -> tuple[dict, dict]:
    """(모델 요청, 백엔드 전용 메타). semanticKey·href·bbox는 메타에만 둔다."""
    state = {
        "pageTitle": extracted["title"],
        "currentPath": urlsplit(extracted["url"]).path,   # 쿼리(testMode 등)는 버린다
        "goal": P.GOAL,
        "persona": P.PERSONA,
        "recentActions": [P.describe_history(h) for h in (history or [])],
        "previousFailedActions": [P.describe_history(h) for h in (failed or [])],
    }
    criteria, meta = {}, {"promptVersion": P.PROMPT_VERSION, "optionMap": {}, "destination": {},
                          "bbox": {}, "backOptionId": None}
    for c in extracted["candidates"]:
        oid = c["optionId"]
        criteria[oid] = P.describe_candidate(c["region"], c["label"])   # context는 기록만 (v3 폐기)
        meta["optionMap"][oid] = c["semanticKey"]
        meta["destination"][oid] = c["href"]
        meta["bbox"][oid] = c["bbox"]
    if go_back:
        oid = f"option_{len(criteria) + 1:02d}"
        criteria[oid] = P.GO_BACK_LABEL
        meta["optionMap"][oid] = meta["destination"][oid] = "GO_BACK"
        meta["backOptionId"] = oid
    q = {"next_click": {"type": "choice", "instructions": P.NEXT_CLICK_INSTRUCTIONS, "criteria": criteria}}
    return {"state": state, "questions": q}, meta


def payload_diff(a: dict, b: dict) -> list[tuple[str, str, str]]:
    """두 요청의 문자열 차이 (경로, a값, b값). B3: 라벨 하나만 달라야 한다."""
    diffs = []

    def walk(x, y, path):
        if isinstance(x, dict) and isinstance(y, dict):
            for k in sorted(set(x) | set(y)):
                walk(x.get(k), y.get(k), f"{path}.{k}")
        elif isinstance(x, list) and isinstance(y, list) and len(x) == len(y):
            for i, (u, v) in enumerate(zip(x, y)):
                walk(u, v, f"{path}[{i}]")
        elif json.dumps(x, ensure_ascii=False) != json.dumps(y, ensure_ascii=False):
            diffs.append((path, x, y))

    walk(a, b, "$")
    return diffs
