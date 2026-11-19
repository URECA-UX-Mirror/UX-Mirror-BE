import copy
import pytest

from uxm.optimizer_policy import compute_evaluation_metrics, judge_verification, select_candidate


def evaluation(cid="C0", target=0.4, path=0.6, **changes):
    item = {"candidateId": cid, "role": "BASELINE" if cid == "C0" else "CANDIDATE",
            "candidateOrder": int(cid[1:]), "label": "메뉴" + cid, "semanticClarityRank": int(cid[1:]),
            "evaluationStatus": "COMPLETED", "candidateValidity": "VALID", "invalidReasons": [],
            "semanticKey": "target", "probabilities": {"target": target, "other": 1 - target} if target is not None else None,
            "targetOptionProbability": target, "goalPathProbability": path, "goalPathBasis": "ALLOWED_PATHS"}
    item.update(changes)
    return item


@pytest.mark.parametrize("probs,entropy,margin", [
    ({"a": 0.5, "b": 0.5}, 1.0, 0.0),
    ({"a": 1.0, "b": 0.0}, 0.0, 1.0),
    ({"a": 0.5, "b": 0.51}, 0.9999292857750865, 0.01),
    ({"a": 1.0}, None, None),
])
def test_metrics_and_input_preserved(probs, entropy, margin):
    ev = {"probabilities": probs, "targetOptionProbability": 0.5, "extra": {"a": [1]}}
    old = copy.deepcopy(ev)
    result = compute_evaluation_metrics(ev)
    assert ev == old and set(result) == {"normalizedEntropy", "top2Margin"}
    if entropy is None:
        assert result["normalizedEntropy"] is None
    else:
        assert result["normalizedEntropy"] == pytest.approx(entropy)
    if margin is None:
        assert result["top2Margin"] is None
    else:
        assert result["top2Margin"] == pytest.approx(margin)


@pytest.mark.parametrize("probs", [{"a": -0.1, "b": 1.1}, {"a": float("nan")}, {"a": True},
                                   {"a": 0.5}, {"a": 0.5, "b": 0.521}, {"": 1.0}])
def test_invalid_probability_distribution(probs):
    result = compute_evaluation_metrics({"probabilities": probs})
    assert result == {"normalizedEntropy": None, "top2Margin": None,
                      "policyMetricError": "INVALID_PROBABILITY_DISTRIBUTION"}


def test_missing_distribution_is_not_a_valid_zero():
    assert compute_evaluation_metrics({"probabilities": {}})["policyMetricError"] == "INVALID_PROBABILITY_DISTRIBUTION"


@pytest.mark.parametrize("target,recommended", [(0.449999, False), (0.45, True), (0.451, True)])
def test_recommendation_threshold(target, recommended):
    candidates = [evaluation(), evaluation("C1", target=target)]
    old = copy.deepcopy(candidates)
    result = select_candidate(candidates)
    assert candidates == old
    assert result["verificationCandidateId"] == "C1"
    assert result["recommendedCandidateId"] == ("C1" if recommended else None)


@pytest.mark.parametrize("path,selected", [(0.580001, True), (0.58, True), (0.579999, False)])
def test_goal_path_loss_boundary(path, selected):
    result = select_candidate([evaluation(), evaluation("C1", path=path)])
    assert (result["verificationCandidateId"] == "C1") is selected
    if not selected:
        assert result["excludedCandidates"][0]["reasonCodes"] == ["GOAL_PATH_DECREASE_EXCEEDED"]


def test_tie_break_and_separate_recommendation():
    result = select_candidate([evaluation(),
        evaluation("C1", target=0.45, semanticClarityRank=2),
        evaluation("C2", target=0.44, semanticClarityRank=1)])
    assert result["rankedCandidates"] == ["C2", "C1"]
    assert result["verificationCandidateId"] == "C2"
    assert result["recommendedCandidateId"] == "C1"
    assert result["recommendationStatus"] == "RECOMMENDED"
    assert result["selectionReasonCode"] == "TIE_BREAK"


def test_tie_boundary_length_and_order():
    result = select_candidate([evaluation(),
        evaluation("C1", target=0.50, semanticClarityRank=1, label="긴 라벨"),
        evaluation("C2", target=0.49, semanticClarityRank=1, label="짧음"),
        evaluation("C3", target=0.489999, semanticClarityRank=0, label="더짧음")])
    assert result["rankedCandidates"] == ["C2", "C1", "C3"]
    tied = select_candidate([evaluation(), evaluation("C1", label="가", semanticClarityRank=1),
                             evaluation("C2", label="나", semanticClarityRank=1)])
    assert tied["rankedCandidates"] == ["C1", "C2"]


def test_incomplete_or_missing_candidates_excluded():
    items = [evaluation(), evaluation("C1", evaluationStatus="FAILED", candidateValidity="VALID",
                                      error={"code": "MODEL_TIMEOUT"}),
             evaluation("C2", evaluationStatus="FAILED", candidateValidity=None, error={"code": "BROWSER_ERROR"}),
             evaluation("C3", evaluationStatus="SKIPPED", candidateValidity="INVALID_CANDIDATE",
                        invalidReasons=["SITE_INSTANCE_MISSING"]),
             evaluation("C4", target=None)]
    result = select_candidate(items)
    assert result["selectionStatus"] == "NOT_SELECTABLE" and result["verificationCandidateId"] is None
    assert result["recommendationStatus"] == "NO_RECOMMENDATION"
    assert [x["candidateId"] for x in result["excludedCandidates"]] == ["C1", "C2", "C3", "C4"]
    assert "MODEL_TIMEOUT" in result["excludedCandidates"][0]["reasonCodes"]
    assert "SITE_INSTANCE_MISSING" in result["excludedCandidates"][2]["reasonCodes"]


def run(reached=True, cost=3, **changes):
    result = {"validity": "VALID", "status": "SUCCESS" if reached else "MAX_STEPS",
              "terminationReason": "GOAL_VERIFIED" if reached else "MAX_STEPS_REACHED",
              "modelVersion": "model", "modelEndpointId": "endpoint", "decisionMode": "DIRECT",
              "promptVersion": "v2", "goal": "goal", "persona": "persona", "goalJudgmentMode": "DOM_ONLY",
              "temperature": None, "limits": {"maxSteps": 8}, "startPath": "/shop/home", "viewport": {"width": 1440},
              "siteVersion": "shop-v1", "thresholdConfig": {"version": "v1"}, "analysisVersion": "v1",
              "referencePathConfig": None, "verification": {"path": "/shop/orders"}, "resultOrigin": "LIVE",
              "comparisonContext": {"initialBrowserStateId": "clean", "layoutVersion": "v1",
                                    "candidatePolicyVersion": "v1", "leakCheckPassed": True},
              "summary": {"goalReached": reached, "navigationCost": cost}}
    result.update(changes)
    return result


@pytest.mark.parametrize("b,c,expected", [
    (run(False), run(True), "IMPROVEMENT_OBSERVED"), (run(True), run(False), "HOLD"),
    (run(False), run(False), "NEEDS_MORE_EVIDENCE"), (run(True, 3), run(True, 2), "IMPROVEMENT_OBSERVED"),
    (run(True, 3), run(True, 4), "HOLD"), (run(True, 3), run(True, 3), "NEEDS_MORE_EVIDENCE"),
    (run(validity="INVALID"), run(), "INCONCLUSIVE"), (run(summary=None), run(), "INCONCLUSIVE"),
    (run(summary={"goalReached": True}), run(), "INCONCLUSIVE"),
    (run(False, status="ERROR", terminationReason="OUT_OF_SCOPE_NAVIGATION"), run(True), "IMPROVEMENT_OBSERVED"),
    (run(False, status="BLOCKED", terminationReason="NO_VALID_CANDIDATES"),
     run(False, status="TIMEOUT", terminationReason="RUN_TIMEOUT"), "NEEDS_MORE_EVIDENCE"),
    (run(False, status="MAX_STEPS"), run(True), "IMPROVEMENT_OBSERVED"),
])
def test_judgment(b, c, expected):
    old = copy.deepcopy((b, c))
    result = judge_verification(b, c)
    assert result["finalDecision"] == expected
    assert result == judge_verification(b, c) and (b, c) == old


@pytest.mark.parametrize("bad", [None, True, -1, float("nan"), float("inf")])
def test_invalid_cost_is_inconclusive(bad):
    assert judge_verification(run(cost=bad), run())["finalDecision"] == "INCONCLUSIVE"


@pytest.mark.parametrize("field,value", [("modelVersion", "other"), ("goal", "other"),
                                        ("verification", {"path": "/shop/refund"}), ("limits", {"maxSteps": 2})])
def test_mismatched_conditions_are_inconclusive(field, value):
    assert judge_verification(run(), run(**{field: value}))["finalDecision"] == "INCONCLUSIVE"


def test_invalid_status_reason_and_missing_conditions_are_inconclusive():
    assert judge_verification(run(status="SUCCESS", terminationReason="MODEL_ERROR"), run())["finalDecision"] == "INCONCLUSIVE"
    candidate = run()
    del candidate["modelVersion"]
    assert judge_verification(run(), candidate)["finalDecision"] == "INCONCLUSIVE"
    assert judge_verification(run(cost=2.0), run(cost=3.0))["finalDecision"] == "HOLD"


def test_basis_target_and_duplicate_id_checks():
    base = evaluation()
    mismatch_basis = evaluation("C1", goalPathBasis="SITE_MAP")
    mismatch_target = evaluation("C2", targetOptionProbability=0.9)
    duplicate = evaluation("C3")
    result = select_candidate([base, mismatch_basis, mismatch_target, duplicate, copy.deepcopy(duplicate)])
    assert result["verificationCandidateId"] is None
    reasons = {item["candidateId"]: item["reasonCodes"] for item in result["excludedCandidates"]}
    assert "GOAL_PATH_BASIS_MISMATCH" in reasons["C1"]
    assert "TARGET_PROBABILITY_MISMATCH" in reasons["C2"]
    assert "DUPLICATE_CANDIDATE_ID" in reasons["C3"]


def test_integer_float_rank_and_invalid_baseline_are_safe():
    assert select_candidate([evaluation(), evaluation("C1", semanticClarityRank=1.0)])["verificationCandidateId"] == "C1"
    result = select_candidate([evaluation(evaluationStatus="FAILED"), evaluation("C1")])
    assert result["verificationCandidateId"] is None


def test_one_invalid_distribution_does_not_discard_other_candidate():
    invalid = evaluation("C1", probabilities={"target": 0.9, "other": 0.9})
    invalid.update(compute_evaluation_metrics(invalid))
    result = select_candidate([evaluation(), invalid, evaluation("C2", target=0.5)])
    assert result["verificationCandidateId"] == "C2"
    assert result["excludedCandidates"] == [{"candidateId": "C1", "reasonCodes": ["INVALID_PROBABILITY_DISTRIBUTION"]}]
