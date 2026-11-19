"""자동 개선 제안과 다목표 선택 정책. 모델 호출과 결과 저장은 호출자가 담당한다.

baseline.mainScore는 현재 기준의 주 목표 점수이고, baseline.guardScores는
동결된 지킬 목표 기준이다. 최초 v1 대비 정책에서는 최초 v1 점수를 넘긴다.
rules: minMainGain, maxGuardLoss, tieTolerance (모두 필수, 확률 단위).
"""
import math
import re
import unicodedata
from decimal import Decimal

from uxm.candidates import DANGEROUS_WORDS
from uxm.leak import assert_no_leak

# 일반 후보의 위험 동작도 막는다. 이 목록은 기존 탐색 규칙을 변경하지 않는다.
_DANGEROUS = tuple(dict.fromkeys((*DANGEROUS_WORDS, "결제", "구매하기", "바로구매", "주문하기", "장바구니 담기", "비우기")))
# 명확한 미완성 예시만 차단한다. 임의 한국어 단어의 완결성은 판별할 수 없다.
_TRUNCATED = re.compile(r"(?:[·/\-]\s*환|[·/\-])$")


def _string(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def build_prompt(diagnosis_text, button, siblings, persona, history):
    """자연어만 받는다. 식별자·점수·성공 조건은 호출자가 전달하지 않는다."""
    diagnosis = _string(diagnosis_text, "diagnosis_text")
    # 진단 결과의 targetButton 객체를 그대로 받을 수 있지만 내부 키는 모델에 보내지 않는다.
    current = _string(button.get("label") if isinstance(button, dict) else button, "button.label")
    user = _string(persona, "persona")
    if not isinstance(siblings, (list, tuple)) or not all(isinstance(x, str) for x in siblings):
        raise ValueError("siblings must be a list of names")
    if not isinstance(history, (list, tuple)) or not all(isinstance(x, str) for x in history):
        raise ValueError("history must be a list of natural-language summaries")
    forbidden_names = list(dict.fromkeys([current, *siblings]))
    prompt = ("사용자가 목적을 찾기 쉬운 메뉴 이름을 제안하세요.\n"
              "쓰지 말 이름 목록과 이전 시도에 나온 이름을 제외하고 새 이름만 제안하세요.\n"
              "설명 없이 한 줄에 이름 하나씩, 최대 6개를 출력하세요.\n"
              f"쓰지 말 이름: {', '.join(forbidden_names)}\n"
              f"현재 메뉴: {current}\n같은 영역의 다른 메뉴: {', '.join(siblings)}\n"
              f"사용자 유형: {user}\n상황: {diagnosis}\n"
              f"이전 시도: {'; '.join(history) if history else '없음'}")
    assert_no_leak({"prompt": prompt})
    return prompt


def _key(value):
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def filter_labels(raw_text, context):
    """줄바꿈 또는 쉼표로 구분한 후보를 검사하고 거절 사유를 보존한다.

    잘린 단어 검사는 명확한 기호 끝과 '·환' 예시에 한정한 휴리스틱이다.
    줄 앞의 일반적인 목록 기호를 제거한다. 설명·코드 블록은 후보로 취급하지 않는다.
    """
    if not isinstance(raw_text, str) or not isinstance(context, dict):
        raise ValueError("raw_text and context must be a string and a dict")
    limit = context.get("maxLength")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("maxLength must be a positive integer")
    current = context.get("currentLabel")
    siblings, tried = context.get("siblingLabels"), context.get("triedLabels")
    if not isinstance(current, str) or not all(isinstance(x, list) and all(isinstance(v, str) for v in x) for x in (siblings, tried)):
        raise ValueError("context requires currentLabel, siblingLabels and triedLabels")
    existing = {_key(x) for x in [current, *siblings, *tried]}
    seen, result, accepted = set(), [], 0
    for raw in (part for line in raw_text.splitlines() for part in unicodedata.normalize("NFKC", line).split(",")):
        label = unicodedata.normalize("NFKC", raw).strip()
        label = re.sub(r"^(?:\d+[.)]|[-*•])\s*", "", label, count=1)
        label = " ".join(label.split())
        key = _key(label)
        reason = None
        if not label:
            reason = "EMPTY_LABEL"
        elif len(label) > limit:
            reason = "LABEL_TOO_LONG"
        elif key in seen:
            reason = "DUPLICATE_LABEL"
        elif key in existing:
            reason = "EXISTING_LABEL"
        elif any(_key(word) in key for word in _DANGEROUS):
            reason = "DANGEROUS_LABEL"
        elif _TRUNCATED.search(label):
            reason = "TRUNCATED_LABEL"
        elif not re.fullmatch(r"[가-힣A-Za-z0-9]+(?:[ ·/\-][가-힣A-Za-z0-9]+)*", label) or not re.search(r"[가-힣A-Za-z]", label):
            reason = "INVALID_FORMAT"
        elif accepted >= 6:
            reason = "CANDIDATE_LIMIT_EXCEEDED"
        if key:
            seen.add(key)
        if reason is None:
            accepted += 1
        result.append({"label": label, "accepted": reason is None, "rejectedReason": reason})
    return result


def _probability(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and 0 <= value <= 1)


def _decimal(value):
    return Decimal(str(value))


def choose(baseline, candidates, rules):
    """현재 주 점수와 동결된 지킬 목표 점수를 기준으로 적격 후보를 선택한다.

    잘못된 기준·규칙·중복 ID는 ValueError. 개별 실패는 rejected에 기록한다.
    ranking은 적격 후보만 포함하며, 남은 최고점 기준으로 동률군을 반복 구성한다.
    """
    if not isinstance(baseline, dict) or not isinstance(candidates, list) or not isinstance(rules, dict):
        raise ValueError("baseline, candidates and rules have invalid types")
    main, guards = baseline.get("mainScore"), baseline.get("guardScores")
    if not _probability(main) or not isinstance(guards, dict) or not guards or any(not isinstance(k, str) or not k or not _probability(v) for k, v in guards.items()):
        raise ValueError("baseline scores must contain a valid main score and guard scores")
    required_rules = ("minMainGain", "maxGuardLoss", "tieTolerance")
    if any(k not in rules for k in required_rules):
        raise ValueError("rules must include minMainGain, maxGuardLoss and tieTolerance")
    params = {k: rules[k] for k in required_rules}
    if any(not _probability(v) for v in params.values()):
        raise ValueError("rules must contain probability thresholds between 0 and 1")
    ids = [c.get("candidateId") if isinstance(c, dict) else None for c in candidates]
    if any(not isinstance(cid, str) or not cid for cid in ids) or len(ids) != len(set(ids)):
        raise ValueError("candidateId must be non-empty and unique")
    eligible, rejected = [], []
    for c in candidates:
        cid, score, scores = c["candidateId"], c.get("mainScore"), c.get("guardScores")
        label, source, order = c.get("label"), c.get("source"), c.get("order")
        if c.get("evaluationStatus") != "COMPLETED":
            reason = "EVALUATION_FAILED"
        elif not _probability(score) or not isinstance(scores, dict) or set(scores) != set(guards) or any(not _probability(v) for v in scores.values()):
            reason = "SCORE_MISSING"
        elif not isinstance(label, str) or not label.strip() or source not in ("HUMAN", "AI") or isinstance(order, bool) or not isinstance(order, int) or order < 0:
            reason = "INVALID_CANDIDATE"
        elif any(_decimal(guards[g]) - _decimal(scores[g]) > _decimal(params["maxGuardLoss"]) for g in guards):
            reason = "GUARD_LOSS_EXCEEDED"
        elif _decimal(score) - _decimal(main) < _decimal(params["minMainGain"]):
            reason = "MAIN_GAIN_INSUFFICIENT"
        else:
            reason = None
        if reason:
            item = {"candidateId": cid, "reason": reason}
            if reason == "GUARD_LOSS_EXCEEDED":
                item["guardGoals"] = [g for g in guards if _decimal(guards[g]) - _decimal(scores[g]) > _decimal(params["maxGuardLoss"])]
            rejected.append(item)
        else:
            eligible.append(c)
    ranking = []
    while eligible:
        highest = max(_decimal(c["mainScore"]) for c in eligible)
        group = [c for c in eligible if highest - _decimal(c["mainScore"]) <= _decimal(params["tieTolerance"])]
        group.sort(key=lambda c: (-sum((_decimal(v) for v in c["guardScores"].values()), Decimal(0)) / len(guards),
                                  len(c["label"]), c["source"] != "HUMAN", c["order"], c["candidateId"]))
        ranking.extend(c["candidateId"] for c in group)
        selected = {c["candidateId"] for c in group}
        eligible = [c for c in eligible if c["candidateId"] not in selected]
    return {"adopted": ranking[0] if ranking else None, "ranking": ranking, "rejected": rejected}
