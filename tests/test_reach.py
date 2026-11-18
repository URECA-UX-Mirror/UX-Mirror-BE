"""사이트 지도 기반 목표 도달 확률 엔진 (uxm/reach.py, 19번)."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from uxm import reach  # noqa: E402

# A(시작) --a1--> B, A --a2--> G(도착), B --b1--> G, B --b2--> A. A의 a3은 지도 밖(이탈)
MAP = {"siteMapVersion": "t", "siteMapHash": "h", "pages": {
    "A": {"path": "/a", "edges": [{"semanticKey": "a1", "to": "B"}, {"semanticKey": "a2", "to": "G"}]},
    "B": {"path": "/b", "edges": [{"semanticKey": "b1", "to": "G"}, {"semanticKey": "b2", "to": "A"}]},
    "G": {"path": "/g", "edges": []}}}
PROBS = {"A": {"a1": 0.5, "a2": 0.3, "a3": 0.2}, "B": {"b1": 0.6, "b2": 0.4}}


def test_matches_hand_calculation():
    # 1번째: G 0.3, B 0.5, 이탈 0.2 / 2번째: B→G 0.3, B→A 0.2 / 3번째: A→G 0.06, A→B 0.1, 이탈 0.04
    est = reach.estimate(MAP, PROBS, "A", "G", 3)
    assert est["reachByStep"] == [0.3, 0.3, 0.06]
    assert est["reachProbability"] == 0.66
    assert est["lostProbability"] == 0.24
    assert est["unfinishedProbability"] == 0.1          # 3번 안에 못 닿고 B에 남은 확률
    assert est["expectedStepsGivenReach"] == round((0.3 * 1 + 0.3 * 2 + 0.06 * 3) / 0.66, 6)
    assert est["status"] == "COMPLETE" and est["missingPages"] == []
    assert est["mostLikelyPath"] == {"path": ["A", "G"], "probability": 0.3}   # A→B→G도 0.3이면 짧은 쪽
    assert (est["shortestSteps"], est["reachWithinShortestProbability"]) == (1, 0.3)   # 지도상 최단 A→G 1번
    assert (est["engineVersion"], est["basis"], est["siteMapHash"]) == ("reach-v1", "SITE_MAP", "h")


def test_probability_mass_is_conserved():
    for k in range(1, 12):
        est = reach.estimate(MAP, PROBS, "A", "G", k)
        assert est["reachProbability"] + est["lostProbability"] + est["unfinishedProbability"] == pytest.approx(1.0)


def test_long_horizon_converges_to_absorption_probability():
    # 무한 단계 도달 확률 r_A = 0.3 + 0.5 r_B, r_B = 0.6 + 0.4 r_A → r_A = 0.6 / 0.8 = 0.75
    assert reach.estimate(MAP, PROBS, "A", "G", 60)["reachProbability"] == pytest.approx(0.75, abs=1e-6)


def test_missing_page_probabilities_is_incomplete_lower_bound():
    est = reach.estimate(MAP, {"A": PROBS["A"]}, "A", "G", 3)
    assert est["status"] == "INCOMPLETE" and est["missingPages"] == ["B"]
    assert est["reachProbability"] == 0.3            # B를 거치는 경로는 세지 않음 (하한)
    assert est["unfinishedProbability"] == 0.5


def test_deterministic_and_start_equals_goal():
    assert reach.estimate(MAP, PROBS, "A", "G", 5) == reach.estimate(MAP, PROBS, "A", "G", 5)
    same = reach.estimate(MAP, PROBS, "G", "G", 5)
    assert same["reachProbability"] == 1.0 and same["expectedStepsGivenReach"] == 0.0


def test_invalid_inputs():
    with pytest.raises(ValueError):
        reach.estimate(MAP, PROBS, "X", "G", 3)
    with pytest.raises(ValueError):
        reach.estimate(MAP, PROBS, "A", "G", 0)


def test_compare_requires_same_conditions():
    a = reach.estimate(MAP, PROBS, "A", "G", 3)
    b = reach.estimate(MAP, {**PROBS, "A": {"a1": 0.2, "a2": 0.7, "a3": 0.1}}, "A", "G", 3)
    cmp = reach.compare(a, b)
    assert cmp["comparable"] and cmp["deltaReachProbability"] > 0 and cmp["bothComplete"]
    assert reach.compare(a, reach.estimate(MAP, PROBS, "A", "G", 4)) == {"comparable": False, "mismatched": ["maxSteps"]}


# ---------------------------------------------------------------- 실제 쇼핑몰·지도 파일

class KeywordBackend:
    """'환불' 2, '주문' 1, 나머지 0.2 비율로 확률을 주는 가짜 모델. 호출 요청을 기록한다."""

    def __init__(self):
        self.requests = []

    def decide(self, request, temperature=None):
        self.requests.append(request)
        crit = request["questions"]["next_click"]["criteria"]
        score = {o: (2.0 if "환불" in t else 1.0 if "주문" in t else 0.2) for o, t in crit.items()}
        total = sum(score.values())
        return {"answers": {"next_click": {"type": "choice", "probabilities": {o: s / total for o, s in score.items()}}}}


def test_real_shops_with_real_site_map(shops):
    pytest.importorskip("playwright")
    from uxm.browser import close_browser, open_browser
    from uxm.sitemap import load_site_map
    site_map = load_site_map(os.path.join(ROOT, "config", "sitemaps", "sitemap-shop-v1.json"))
    out = {}
    for variant, url in shops.items():
        backend = KeywordBackend()
        session = open_browser(url + "/shop/home")
        try:
            probs, records = reach.collect_page_probabilities(
                session["page"], url, site_map, backend, goal="환불 신청 화면 찾기", persona="처음 쓰는 사용자",
                skip={"refund"})
        finally:
            close_browser(session)
        assert set(probs) == set(site_map["pages"]) - {"refund"}       # 도착 화면 빼고 화면당 1번
        assert len(backend.requests) == len(probs)
        assert all(r["state"]["goal"] == "환불 신청 화면 찾기" for r in backend.requests)
        # 후보 semanticKey가 지도 간선과 같은 규칙이라 지도 밖 확률은 일부(같은 화면 앵커 등)만
        out[variant] = reach.estimate(site_map, probs, "home", "refund", 8)
        assert out[variant]["status"] == "COMPLETE"
    cmp = reach.compare(out["before"], out["after"])
    assert cmp["comparable"] and cmp["bothComplete"]
    # After 상단 메뉴 "주문·환불"이 키워드에 걸려 orders(→refund)로 가는 확률이 커진다
    assert cmp["deltaReachProbability"] > 0
    # 이동 8번이면 둘 다 거의 1이라, 최단 2번 안에 닿을 확률과 기대 단계로 차이가 보인다
    assert out["before"]["shortestSteps"] == out["after"]["shortestSteps"] == 2
    assert cmp["deltaReachWithinShortestProbability"] > 0.05 and cmp["deltaExpectedStepsGivenReach"] < 0
    assert out["after"]["mostLikelyPath"]["path"] == ["home", "orders", "refund"]
