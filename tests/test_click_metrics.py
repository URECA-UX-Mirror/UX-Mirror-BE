"""클릭 전용 지표 경계 및 에이전트 저장 결과 (모델·브라우저 불필요)."""
import copy
import math
from types import SimpleNamespace

import pytest

from uxm.agent import _Loop
from uxm.metrics import PROBABILITY_EPSILON, decision_metrics


def test_d3_back_probability_and_click_distribution_are_separate():
    probs = {"a": 0.45, "b": 0.30, "back": 0.25}
    original = copy.deepcopy(probs)
    result = decision_metrics(probs, "back")
    assert result["goBackProbability"] == 0.25
    assert math.isclose(result["top2Margin"], 0.15)
    assert math.isclose(result["clickOnlyTop2Margin"], 0.2)
    assert result["normalizedEntropy"] != result["clickOnlyNormalizedEntropy"]
    assert result["metricWarnings"] == [] and probs == original


@pytest.mark.parametrize("mass", [0.0, PROBABILITY_EPSILON / 2, PROBABILITY_EPSILON])
def test_d3a_two_clicks_at_or_below_epsilon_warn(mass):
    result = decision_metrics({"a": mass / 2, "b": mass / 2, "back": 1.0}, "back")
    assert result["clickOnlyNormalizedEntropy"] is None
    assert result["clickOnlyTop2Margin"] is None
    assert len(result["metricWarnings"]) == 1
    assert result["metricWarnings"][0]["code"] == "NO_CLICK_PROBABILITY_MASS"
    assert result["metricWarnings"][0]["message"]


@pytest.mark.parametrize("probs, back", [({}, None), ({"back": 1.0}, "back"),
                                          ({"a": 0.0, "back": 1.0}, "back"),
                                          ({"a": 1.0}, None)])
def test_d3b_fewer_than_two_clicks_never_warn(probs, back):
    result = decision_metrics(probs, back)
    assert result["clickOnlyNormalizedEntropy"] is None
    assert result["clickOnlyTop2Margin"] is None
    assert result["metricWarnings"] == []


def test_above_epsilon_uses_direct_click_mass_with_allowed_sum_error():
    mass = 2 * PROBABILITY_EPSILON
    result = decision_metrics({"a": mass / 4, "b": mass * 3 / 4, "back": 0.99}, "back")
    assert math.isclose(result["clickOnlyTop2Margin"], 0.5)
    assert result["clickOnlyNormalizedEntropy"] is not None
    assert result["metricWarnings"] == []


def test_no_back_uses_click_probabilities_and_null_back():
    result = decision_metrics({"a": 0.4, "b": 0.6})
    assert result["goBackProbability"] is None
    assert math.isclose(result["clickOnlyNormalizedEntropy"], result["normalizedEntropy"])
    assert result["metricWarnings"] == []


def test_agent_decision_keeps_metrics_warning():
    loop = SimpleNamespace(stack=[{"pageKey": "home"}], allowed=[["home", "orders"]],
                           env=SimpleNamespace(page_key=lambda path: path.rsplit("/", 1)[-1]),
                           origin="MOCK", s={"goalJudgmentMode": "DOM_ONLY"})
    candidates = [{"optionId": "a", "href": "/shop/orders"}, {"optionId": "b", "href": "/shop/support"}]
    decision = _Loop._decision(loop, "back", True, {"a": 0.0, "b": 0.0, "back": 1.0},
                               "back", candidates, 5)
    assert decision["selectedActionType"] == "GO_BACK"
    assert decision["clickOnlyNormalizedEntropy"] is None
    assert decision["metricWarnings"][0]["code"] == "NO_CLICK_PROBABILITY_MASS"
