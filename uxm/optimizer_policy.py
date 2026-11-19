"""Optimizer 평가·선정·검증 정책. 입력 기록은 수정하지 않는다."""
import math
from decimal import Decimal

from uxm.metrics import normalized_entropy, top2_margin, validate_probabilities

SELECTION_VERSION = "selection-v1"
VERIFICATION_VERSION = "verification-v1"
_TIE = Decimal("0.01")
_MIN_GAIN = Decimal("0.05")
_MAX_PATH_LOSS = Decimal("0.02")


def _probability(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and 0 <= value <= 1)


def compute_evaluation_metrics(evaluation: dict) -> dict:
    """전체 클릭 분포의 엔트로피(합 1로 보정)와 원본 확률 Top-2 차이.

    허용되는 합계 오차는 엔진과 동일하다. 원본 확률·다른 엔진 필드는 바꾸지 않는다.
    분포가 없거나 후보가 하나면 계산할 수 없는 지표는 None이다.
    """
    probs = evaluation.get("probabilities")
    if probs is None or probs == {}:
        return {"normalizedEntropy": None, "top2Margin": None}
    if not isinstance(probs, dict) or any(not isinstance(k, str) or not k for k in probs):
        raise ValueError("확률 분포의 후보 키가 올바르지 않습니다.")
    if any(not _probability(p) for p in probs.values()):
        raise ValueError("확률은 0과 1 사이의 유한한 숫자여야 합니다.")
    validate_probabilities(probs, list(probs))
    values = list(probs.values())
    total = math.fsum(values)
    return {"normalizedEntropy": normalized_entropy([p / total for p in values]),
            "top2Margin": top2_margin(values)}


def _as_decimal(value):
    return Decimal(str(value))


def _rank(candidates):
    """남은 최고 점수와 1%p 이내를 한 그룹으로 묶어 결정적으로 정렬한다."""
    remaining = list(candidates)
    ordered = []
    while remaining:
        highest = max(_as_decimal(c["targetOptionProbability"]) for c in remaining)
        group = [c for c in remaining if highest - _as_decimal(c["targetOptionProbability"]) <= _TIE]
        group.sort(key=lambda c: (c["semanticClarityRank"], len(c["label"]), c["candidateOrder"], c["candidateId"]))
        ordered.extend(group)
        ids = {id(c) for c in group}
        remaining = [c for c in remaining if id(c) not in ids]
    return ordered


def select_candidate(evaluations: list[dict]) -> dict:
    """동결된 C0 기준으로 적격 후보를 분류하고 추천·검증 대상을 선택한다."""
    baselines = [e for e in evaluations if e.get("role") == "BASELINE"]
    if len(baselines) != 1 or baselines[0].get("evaluationStatus") != "COMPLETED":
        raise ValueError("완료된 Baseline 평가 하나가 필요합니다.")
    baseline = baselines[0]
    base_target, base_path = baseline.get("targetOptionProbability"), baseline.get("goalPathProbability")
    eligible, exclusions = [], []
    for candidate in (e for e in evaluations if e.get("role") == "CANDIDATE"):
        reasons = []
        target, path = candidate.get("targetOptionProbability"), candidate.get("goalPathProbability")
        if candidate.get("evaluationStatus") != "COMPLETED":
            reasons.append("EVALUATION_NOT_COMPLETED")
            reasons.extend(candidate.get("invalidReasons") or [])
            error = candidate.get("error")
            if isinstance(error, dict) and error.get("code"):
                reasons.append(error["code"])
        else:
            if not _probability(base_target) or not _probability(base_path):
                reasons.append("BASELINE_METRIC_UNAVAILABLE")
            if not _probability(target) or not _probability(path):
                reasons.append("CANDIDATE_METRIC_UNAVAILABLE")
            rank, order, label, cid = (candidate.get("semanticClarityRank"), candidate.get("candidateOrder"),
                                       candidate.get("label"), candidate.get("candidateId"))
            if (type(rank) is not int or rank < 0 or type(order) is not int or order < 0
                    or not isinstance(label, str) or not label or not isinstance(cid, str) or not cid):
                reasons.append("CANDIDATE_METADATA_UNAVAILABLE")
            if _probability(base_path) and _probability(path) and _as_decimal(base_path) - _as_decimal(path) > _MAX_PATH_LOSS:
                reasons.append("GOAL_PATH_DECREASE_EXCEEDED")
        if reasons:
            exclusions.append({"candidateId": candidate.get("candidateId"), "reasonCodes": list(dict.fromkeys(reasons))})
        else:
            eligible.append(candidate)

    ranked = _rank(eligible)
    recommended = [c for c in ranked if _as_decimal(c["targetOptionProbability"]) - _as_decimal(base_target) >= _MIN_GAIN]
    verification = ranked[0] if ranked else None
    recommendation = recommended[0] if recommended else None
    top_score = max((c["targetOptionProbability"] for c in eligible), default=None)
    reason = ("NO_VALID_CANDIDATE" if verification is None else
              "TIE_BREAK" if verification["targetOptionProbability"] < top_score else "TOP_TARGET_PROBABILITY")
    return {"selectionStatus": "SELECTED" if verification else "NOT_SELECTABLE",
            "recommendationStatus": "RECOMMENDED" if recommendation else "NO_RECOMMENDATION",
            "recommendedCandidateId": recommendation["candidateId"] if recommendation else None,
            "verificationCandidateId": verification["candidateId"] if verification else None,
            "rankedCandidates": [c["candidateId"] for c in ranked],
            "selectionReasonCode": reason,
            "excludedCandidates": exclusions, "selectionPolicyVersion": SELECTION_VERSION}


def judge_verification(baseline_run: dict, candidate_run: dict) -> dict:
    """두 유효 Run의 목표 도달 및 비용을 비교한다."""
    def outcome(run):
        if not isinstance(run, dict) or run.get("validity") != "VALID":
            return None
        summary = run.get("summary")
        if not isinstance(summary, dict):
            return None
        reached, cost = summary.get("goalReached"), summary.get("navigationCost")
        if type(reached) is not bool or type(cost) is not int or cost < 0:
            return None
        return reached, cost

    b, c = outcome(baseline_run), outcome(candidate_run)
    delta = None
    if b is None or c is None:
        decision, reason = "INCONCLUSIVE", "INVALID_OR_MISSING_RUN_DATA"
    elif not b[0] and c[0]:
        decision, reason = "IMPROVEMENT_OBSERVED", "GOAL_REACHED_GAINED"
    elif b[0] and not c[0]:
        decision, reason = "HOLD", "GOAL_REACHED_LOST"
    elif not b[0] and not c[0]:
        decision, reason = "NEEDS_MORE_EVIDENCE", "BOTH_GOALS_NOT_REACHED"
    else:
        delta = c[1] - b[1]
        decision = "IMPROVEMENT_OBSERVED" if delta < 0 else "HOLD" if delta > 0 else "NEEDS_MORE_EVIDENCE"
        reason = "NAVIGATION_COST"
    return {"finalDecision": decision, "reasonCode": reason, "navigationCostDelta": delta,
            "verificationPolicyVersion": VERIFICATION_VERSION}
