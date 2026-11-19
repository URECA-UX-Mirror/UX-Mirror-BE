"""Optimizer 평가·선정·검증 정책. 입력 기록은 수정하지 않는다."""
import math
from decimal import Decimal

from uxm.metrics import normalized_entropy, top2_margin, validate_probabilities
from uxm.status import STATUS_BY_REASON

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
    error = _evaluation_probability_error(evaluation, check_target=False)
    if error:
        return {"normalizedEntropy": None, "top2Margin": None, "policyMetricError": error}
    values = list(probs.values())
    total = math.fsum(values)
    return {"normalizedEntropy": normalized_entropy([p / total for p in values]),
            "top2Margin": top2_margin(values)}


def _evaluation_probability_error(evaluation, *, check_target=True):
    probs = evaluation.get("probabilities")
    if not isinstance(probs, dict) or not probs or any(not isinstance(k, str) or not k for k in probs):
        return "INVALID_PROBABILITY_DISTRIBUTION"
    if any(not _probability(p) for p in probs.values()):
        return "INVALID_PROBABILITY_DISTRIBUTION"
    try:
        validate_probabilities(probs, list(probs))
    except (TypeError, ValueError):
        return "INVALID_PROBABILITY_DISTRIBUTION"
    if check_target:
        key, target = evaluation.get("semanticKey"), evaluation.get("targetOptionProbability")
        if not isinstance(key, str) or key not in probs or not _probability(target):
            return "TARGET_PROBABILITY_UNAVAILABLE"
        if not math.isclose(probs[key], target, rel_tol=0, abs_tol=1e-12):
            return "TARGET_PROBABILITY_MISMATCH"
    return None


def _whole_number(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value >= 0 and value == int(value))


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
    baseline = baselines[0] if len(baselines) == 1 and baselines[0].get("evaluationStatus") == "COMPLETED" else {}
    base_target, base_path = baseline.get("targetOptionProbability"), baseline.get("goalPathProbability")
    base_error = (_evaluation_probability_error(baseline) or baseline.get("policyMetricError")) if baseline else "BASELINE_NOT_COMPLETED"
    base_basis = baseline.get("goalPathBasis")
    ids = [e.get("candidateId") for e in evaluations if isinstance(e, dict) and isinstance(e.get("candidateId"), str)]
    duplicate_ids = {cid for cid in ids if ids.count(cid) > 1}
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
            if not _probability(base_target) or not _probability(base_path) or base_error or not base_basis:
                reasons.append("BASELINE_METRIC_UNAVAILABLE")
            if not _probability(target) or not _probability(path):
                reasons.append("CANDIDATE_METRIC_UNAVAILABLE")
            probability_error = _evaluation_probability_error(candidate)
            if probability_error:
                reasons.append(probability_error)
            if candidate.get("policyMetricError"):
                reasons.append(candidate["policyMetricError"])
            if not candidate.get("goalPathBasis") or candidate.get("goalPathBasis") != base_basis:
                reasons.append("GOAL_PATH_BASIS_MISMATCH")
            rank, order, label, cid = (candidate.get("semanticClarityRank"), candidate.get("candidateOrder"),
                                       candidate.get("label"), candidate.get("candidateId"))
            if (not _whole_number(rank) or not _whole_number(order)
                    or not isinstance(label, str) or not label or not isinstance(cid, str) or not cid):
                reasons.append("CANDIDATE_METADATA_UNAVAILABLE")
            if _probability(base_path) and _probability(path) and _as_decimal(base_path) - _as_decimal(path) > _MAX_PATH_LOSS:
                reasons.append("GOAL_PATH_DECREASE_EXCEEDED")
        if isinstance(candidate.get("candidateId"), str) and candidate["candidateId"] in duplicate_ids:
            reasons.append("DUPLICATE_CANDIDATE_ID")
        if baseline.get("candidateId") in duplicate_ids:
            reasons.append("DUPLICATE_BASELINE_ID")
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
        status, reason = run.get("status"), run.get("terminationReason")
        if not reason or STATUS_BY_REASON.get(reason) != status:
            return None
        summary = run.get("summary")
        if not isinstance(summary, dict):
            return None
        reached, cost = summary.get("goalReached"), summary.get("navigationCost")
        if type(reached) is not bool or not _whole_number(cost) or reached != (reason == "GOAL_VERIFIED"):
            return None
        return reached, cost

    b, c = outcome(baseline_run), outcome(candidate_run)
    required = ("modelVersion", "modelEndpointId", "decisionMode", "promptVersion", "goal", "persona",
                "goalJudgmentMode", "temperature", "limits", "startPath", "viewport", "siteVersion",
                "thresholdConfig", "analysisVersion", "referencePathConfig", "verification", "resultOrigin")
    context_fields = ("initialBrowserStateId", "layoutVersion", "candidatePolicyVersion")
    conditions_match = (all(k in baseline_run and k in candidate_run and baseline_run[k] == candidate_run[k]
                            for k in required) if b is not None and c is not None else False)
    if conditions_match:
        nonempty = ("modelVersion", "modelEndpointId", "decisionMode", "promptVersion", "goal", "persona",
                    "goalJudgmentMode", "startPath", "siteVersion", "analysisVersion", "resultOrigin")
        conditions_match = (all(isinstance(baseline_run[k], str) and baseline_run[k].strip() for k in nonempty)
                            and all(isinstance(baseline_run[k], dict) and baseline_run[k]
                                    for k in ("limits", "viewport", "thresholdConfig", "verification")))
    if conditions_match:
        bc, cc = baseline_run.get("comparisonContext"), candidate_run.get("comparisonContext")
        conditions_match = (isinstance(bc, dict) and isinstance(cc, dict)
                            and all(k in bc and k in cc and bc[k] == cc[k] and bc[k] is not None
                                    for k in context_fields)
                            and bc.get("leakCheckPassed") is True and cc.get("leakCheckPassed") is True)
    delta = None
    if b is None or c is None:
        decision, reason = "INCONCLUSIVE", "INVALID_OR_MISSING_RUN_DATA"
    elif not conditions_match:
        decision, reason = "INCONCLUSIVE", "RUN_CONDITIONS_MISMATCH_OR_MISSING"
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
