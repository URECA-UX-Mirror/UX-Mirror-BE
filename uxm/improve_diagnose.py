"""자동 개선 진단: 막히는 화면과 고칠 버튼 (#106, 「자동 개선 에이전트 설계 v2.2」 §4.1, 역할 배분 1b).

도달 확률 엔진(`uxm/reach.py`)의 결과와 화면별 후보 확률로 "사용자가 어디서 새는지"를 찾고,
1단계에서 이름을 바꿔 볼 버튼을 고른다. 모델 호출·브라우저는 쓰지 않는 순수 함수다.

- 정답 버튼: 사이트 지도에서 그 화면보다 도착 화면까지 거리가 줄어드는 화면으로 가는 링크.
- 막힘 점수(화면 X) = P(도착 전에 X에서 선택함) × (1 − X의 정답 버튼 확률 합).
  앞쪽 항은 엔진의 `pageVisitProbability`(#100). 확률은 엔진과 같이 화면마다 합 1로 정규화한다.
- 고칠 버튼: 막힘 점수가 가장 큰 화면에서, 정답이 아닌 버튼 중 확률 1등(오답 1등)과 **같은 영역**의 정답 버튼 중
  확률이 가장 낮은 것. 같은 영역에 정답 버튼이 없으면 정답 버튼 전체에서 가장 낮은 것.
- 1단계는 최종안 사이트 생성기(`relabel_nav`·`parity_ok`)가 바꿀 수 있는 버튼(`EDITABLE_KEYS`)만 고친다.
  위 규칙으로 고른 버튼이 바꿀 수 없는 버튼이면, 그 화면에서 바꿀 수 있는 버튼이 **정답 버튼일 때 그 버튼**을 고친다
  (원래 고른 버튼은 `freeTargetButton`으로 남김). 바꿀 수 있는 정답 버튼이 없으면 `NOT_EDITABLE_IN_STAGE1`로 멈춘다.
- 엔진 결과가 `COMPLETE`가 아니면(지도와 실제 화면이 다르거나 확률이 빠진 화면) 진단하지 않는다.
"""
from uxm.label_edit import EDITABLE_KEY
from uxm.sitemap import distances

EDITABLE_KEYS = (EDITABLE_KEY,)   # 1단계: 상단 메뉴 "이용정보" 자리 버튼 하나 (#107 `uxm.label_edit`과 같은 값)

NOT_COMPLETE = "ESTIMATE_NOT_COMPLETE"
NO_BLOCKAGE = "NO_BLOCKAGE"
NO_CORRECT_BUTTON = "NO_CORRECT_BUTTON"
NOT_EDITABLE = "NOT_EDITABLE_IN_STAGE1"


def region_of(semantic_key: str) -> str:
    """semanticKey(`영역>목적지#순번`)의 영역."""
    return semantic_key.split(">", 1)[0]


def correct_keys(site_map: dict, page_key: str, goal: str, dist: dict | None = None) -> set[str]:
    """그 화면의 정답 버튼 semanticKey (도착 화면까지 거리가 줄어드는 화면으로 가는 링크).

    dist: 미리 계산한 `sitemap.distances` 결과 (여러 화면에 쓸 때 한 번만 계산하려고). 없으면 여기서 계산.
    """
    dist = distances(site_map["pages"], goal) if dist is None else dist
    here = dist.get(page_key)
    if here is None:
        return set()
    return {e["semanticKey"] for e in site_map["pages"][page_key].get("edges", [])
            if dist.get(e["to"]) is not None and dist[e["to"]] < here}


def _normalized(probs: dict[str, float]) -> dict[str, float]:
    total = sum(p for p in probs.values() if p > 0)
    return {k: (p / total if total > 0 and p > 0 else 0.0) for k, p in probs.items()}


def _button(key: str, prob: float, labels: dict[str, str] | None) -> dict:
    return {"semanticKey": key, "label": (labels or {}).get(key), "region": region_of(key), "probability": round(prob, 6)}


def diagnose(site_map: dict, page_probs: dict[str, dict[str, float]], start: str, goal: str, est: dict, *,
             labels: dict[str, dict[str, str]] | None = None, editable_keys=EDITABLE_KEYS) -> dict:
    """막히는 화면과 고칠 버튼.

    page_probs: {pageKey: {semanticKey: 확률}} (`reach.collect_page_probabilities` 결과).
    est: 같은 확률로 만든 `reach.estimate` 결과 (`status`, `pageVisitProbability` 사용).
    labels: {pageKey: {semanticKey: 버튼 글자}} — 있으면 결과 버튼에 글자를 붙인다 (없으면 label=None).
    반환: {bottleneckPage, blockScore, blockScores, correctProbability, targetButton, rivalButton, reason}.
    고칠 버튼을 정할 수 없으면 targetButton=None과 reason. 1단계에서 못 바꾸는 버튼이면 targetButton은 채우고
    reason=NOT_EDITABLE_IN_STAGE1 (루프는 reason이 있으면 멈춘다).
    """
    result = {"start": start, "goal": goal, "bottleneckPage": None, "blockScore": None, "blockScores": {},
              "correctProbability": None, "targetButton": None, "rivalButton": None, "reason": None}
    if est.get("status") != "COMPLETE":
        result["reason"] = NOT_COMPLETE
        return result
    visit = est.get("pageVisitProbability") or {}
    dist = distances(site_map["pages"], goal)   # 화면마다 쓰므로 한 번만
    pages = {}                                  # {pageKey: (정규화 확률, 정답 키, 정답 확률 합)}
    for page_key in visit:
        if page_key in page_probs:
            norm = _normalized(page_probs[page_key])
            right = correct_keys(site_map, page_key, goal, dist)
            pages[page_key] = (norm, right, sum(p for k, p in norm.items() if k in right))
    scores = {k: visit[k] * (1 - correct) for k, (_, _, correct) in pages.items()}
    result["blockScores"] = {k: round(v, 6) for k, v in sorted(scores.items())}
    if not scores or max(scores.values()) <= 0:
        result["reason"] = NO_BLOCKAGE
        return result
    # 막힘 점수 최대 화면. 같으면 들어올 확률이 큰 화면, 그다음 이름 순 (결과가 하나로 정해지게)
    page_key = min(scores, key=lambda k: (-scores[k], -visit[k], k))
    norm, right, correct = pages[page_key]
    result.update(bottleneckPage=page_key, blockScore=round(scores[page_key], 6), correctProbability=round(correct, 6))
    page_labels = (labels or {}).get(page_key)
    wrong = {k: p for k, p in norm.items() if k not in right}
    if not wrong:
        result["reason"] = NO_BLOCKAGE
        return result
    rival = min(wrong, key=lambda k: (-wrong[k], k))
    result["rivalButton"] = _button(rival, wrong[rival], page_labels)

    candidates = {k: p for k, p in norm.items() if k in right}
    if not candidates:
        result["reason"] = NO_CORRECT_BUTTON
        return result
    same_region = {k: p for k, p in candidates.items() if region_of(k) == region_of(rival)}
    pool = same_region or candidates
    target = min(pool, key=lambda k: (pool[k], k))
    if target in editable_keys:
        result["targetButton"] = _button(target, pool[target], page_labels)
        return result
    # 규칙대로 고른 버튼을 1단계에서 못 바꿀 때: 그 화면에서 바꿀 수 있는 버튼이 정답 버튼이면 그 버튼을 고친다.
    # (이름을 한 번 바꿔 채택하면 다음 회차에는 다른 정답 버튼이 가장 약해지기 쉬워서, 이 규칙이 없으면 1회차 뒤 바로 멈춘다)
    # 원래 고른 버튼은 freeTargetButton으로 리포트에 남긴다.
    result["freeTargetButton"] = _button(target, pool[target], page_labels)
    editable = {k: p for k, p in candidates.items() if k in editable_keys}
    if editable:
        target = min(editable, key=lambda k: (editable[k], k))
        result["targetButton"] = _button(target, editable[target], page_labels)
    else:
        result["targetButton"] = result["freeTargetButton"]
        result["reason"] = NOT_EDITABLE
    return result


def describe(diagnosis: dict, goal_name: str, destination: str | None = None) -> str:
    """제안 프롬프트에 넣을 진단 요약 (자연어). 점수·식별자·경로 이름은 넣지 않는다 (누출 검사 원칙).

    destination: 고칠 버튼을 누르면 가는 화면 설명 (기준 고정 파일 `edit.targetPageDescription`).
    모델이 메뉴가 어디로 가는지 모르면 화면에 보이는 다른 메뉴 이름만 따라 쓴다 (11/19 연습 실행).
    """
    target, rival = diagnosis.get("targetButton"), diagnosis.get("rivalButton")
    if not target or not target.get("label"):
        raise ValueError("고칠 버튼 글자가 없는 진단은 설명할 수 없습니다.")
    text = f"{goal_name} 사용자가 '{target['label']}' 메뉴를 잘 고르지 않는다."
    if rival and rival.get("label"):
        text = f"{goal_name} 사용자가 '{target['label']}' 메뉴를 잘 고르지 않고 '{rival['label']}' 쪽으로 간다."
    if destination:
        text = f"'{target['label']}' 메뉴를 누르면 {destination}으로 간다. " + text
    return text
