"""모델 없이 도는 테스트: 지표 계산, 누출 검사, 요청 형식(trida-stack 파서 통과 여부)."""
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from uxm.backends import VENDOR  # noqa: E402
from uxm.leak import assert_no_leak  # noqa: E402
from uxm.metrics import decision_metrics, normalized_entropy, validate_probabilities  # noqa: E402
from uxm.scenarios import SCENARIOS, build_request  # noqa: E402

sys.path.insert(0, VENDOR)


def test_entropy_bounds():
    assert normalized_entropy([1.0, 0.0, 0.0]) == 0.0
    assert math.isclose(normalized_entropy([1 / 3] * 3), 1.0)
    assert normalized_entropy([1.0]) is None


def test_click_only_renormalises_without_back():
    m = decision_metrics({"o1": 0.4, "o2": 0.4, "o3": 0.2}, back_option_id="o3")
    assert m["goBackProbability"] == 0.2
    assert math.isclose(m["clickOnlyNormalizedEntropy"], 1.0)
    assert math.isclose(m["clickOnlyTop2Margin"], 0.0)


def test_single_click_candidate_is_na():   # A7: support 화면, 클릭 후보 1개 + GO_BACK
    m = decision_metrics({"o1": 0.3, "o2": 0.7}, back_option_id="o2")
    assert m["clickOnlyNormalizedEntropy"] is None and m["normalizedEntropy"] is not None


def test_validate_rejects_bad():
    with pytest.raises(ValueError):
        validate_probabilities({"o1": 0.5}, ["o1", "o2"])
    with pytest.raises(ValueError):
        validate_probabilities({"o1": 0.9, "o2": 0.9}, ["o1", "o2"])


def test_leak_detects_variant():
    with pytest.raises(RuntimeError):
        assert_no_leak({"state": {"currentPath": "/shop/home?variant=after"}})


@pytest.mark.parametrize("sid", list(SCENARIOS))
def test_scenarios_build_clean_and_parse(sid):
    from decide_request import parse
    req, meta = build_request(sid)
    assert_no_leak(req)
    parse(req)   # 공식 파서가 받아들이는 형식인지
    if SCENARIOS[sid]["kind"] == "choice":
        assert set(meta["optionMap"]) == set(req["questions"]["next_click"]["criteria"])


def test_probability_by_destination_sums_duplicates():   # 마이페이지처럼 같은 목적지 링크가 여러 개일 때
    from uxm.metrics import probability_by_destination
    probs = {"o1": 0.1, "o2": 0.3, "o3": 0.2, "o4": 0.4}
    dest = {"o1": "/shop/home", "o2": "/shop/orders", "o3": "/shop/orders", "o4": "/shop/refund"}
    r = probability_by_destination(probs, dest)
    assert math.isclose(r["/shop/orders"], 0.5) and list(r)[0] == "/shop/orders"
    assert math.isclose(sum(r.values()), 1.0)


def test_goal_dom_only_ignores_model():
    from uxm.goal import judge_goal, should_call_model
    assert not should_call_model("DOM_ONLY")
    r = judge_goal("DOM_ONLY", verified=True)
    assert r["goalReached"] is True and r["modelGoalReached"] is None and r["goalJudgmentMismatch"] is False
    with pytest.raises(ValueError):
        judge_goal("DOM_ONLY", verified=False, model_yes_prob=0.7)


def test_goal_model_yes_never_overrides_dom():   # A10 같은 오탐이 성공으로 처리되지 않아야 한다
    from uxm.goal import judge_goal
    r = judge_goal("MODEL_AND_DOM", verified=False, model_yes_prob=0.70)
    assert r["goalReached"] is False and r["modelGoalReached"] is True and r["goalJudgmentMismatch"] is True
    r = judge_goal("MODEL_AND_DOM", verified=True, model_yes_prob=0.2)
    assert r["goalReached"] is True and r["goalJudgmentMismatch"] is True


def test_default_goal_mode_is_dom_only():
    from uxm import config
    assert config.load()["goal_judgment_mode"] == "DOM_ONLY"


class _FakeLocator:
    def __init__(self, visible):
        self._v = visible

    def count(self):
        return len(self._v)

    def nth(self, i):
        v = self._v[i]
        return type("L", (), {"is_visible": lambda self: v})()


class _FakePage:
    def __init__(self, url, visible):
        self.url, self._visible = url, visible

    def get_by_text(self, text):
        return _FakeLocator(self._visible)


def test_goal_needs_both_path_and_visible_text():   # 마커 없이 주소 + 보이는 글자
    from uxm.goal import verify_goal
    assert verify_goal(_FakePage("http://127.0.0.1:8001/shop/refund", [True]))[0] is True
    assert verify_goal(_FakePage("http://127.0.0.1:8001/shop/refund?x=1", [False, True]))[0] is True
    # mypage에도 '환불 / 반품 신청' 링크가 있음 → 주소가 다르면 도착 아님
    assert verify_goal(_FakePage("http://127.0.0.1:8001/shop/mypage", [True]))[0] is False
    # 주소만 맞고 글자가 안 보이면 도착 아님 (URL만으로 성공 처리 금지)
    assert verify_goal(_FakePage("http://127.0.0.1:8001/shop/refund", [False]))[0] is False
    assert verify_goal(_FakePage("http://127.0.0.1:8001/shop/refund", []))[0] is False


def test_before_after_differ_only_in_label():   # B3의 하드코딩 버전
    a2, _ = build_request("A2")
    a3, _ = build_request("A3")
    c2, c3 = a2["questions"]["next_click"]["criteria"], a3["questions"]["next_click"]["criteria"]
    assert a2["state"] == a3["state"] and list(c2) == list(c3)
    assert [k for k in c2 if c2[k] != c3[k]] == ["option_01"]
