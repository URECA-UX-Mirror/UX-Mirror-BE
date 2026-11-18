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
    assert est["status"] == "COMPLETE" and est["missingPages"] == [] and est["nullReasons"] == {}
    assert est["mostLikelyPath"] == {"path": ["A", "G"], "probability": 0.3}   # A→B→G도 0.3이면 짧은 쪽
    assert (est["shortestSteps"], est["reachWithinShortestProbability"]) == (1, 0.3)   # 지도상 최단 A→G 1번
    assert (est["engineVersion"], est["basis"], est["siteMapHash"]) == ("reach-v1", "SITE_MAP", "h")


def test_probability_mass_is_conserved():
    for k in range(1, 12):
        est = reach.estimate(MAP, PROBS, "A", "G", k)
        total = est["reachProbability"] + est["lostProbability"] + est["unfinishedProbability"]
        assert total == pytest.approx(1.0, abs=2e-6)   # 각 값을 소수 6자리로 반올림한 합 (최대 1.5e-6)


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


# A에서 도착 화면 G로 가는 후보가 둘 (리뷰 #62-1: 허용 오차 안의 합계)
TWO_WAY = {"siteMapVersion": "t", "siteMapHash": "h2", "pages": {
    "A": {"path": "/a", "edges": [{"semanticKey": "x", "to": "G"}, {"semanticKey": "y", "to": "G"}]},
    "G": {"path": "/g", "edges": []}}}


@pytest.mark.parametrize("p", [0.505, 0.495])   # 합계 1.01, 0.99: validate_probabilities(±0.02)는 통과한다
def test_probability_sum_within_tolerance_is_normalized(p):
    est = reach.estimate(TWO_WAY, {"A": {"x": p, "y": p}}, "A", "G", 3)
    assert est["reachProbability"] == 1.0 and est["lostProbability"] == 0.0   # 1.01도, 이탈 0.01도 아님
    assert est["unfinishedProbability"] == 0.0 and est["reachByStep"] == [1.0, 0.0, 0.0]
    assert est["mostLikelyPath"]["probability"] <= 1.0


def test_normalized_mass_is_conserved_and_bounded():
    probs = {"A": {"a1": 0.51, "a2": 0.31, "a3": 0.19}, "B": {"b1": 0.6, "b2": 0.41}}   # 합 1.01, 1.01
    for k in range(1, 12):
        est = reach.estimate(MAP, probs, "A", "G", k)
        parts = (est["reachProbability"], est["lostProbability"], est["unfinishedProbability"])
        # 세 값을 각각 소수 6자리로 반올림하므로 합은 최대 1.5e-6까지 어긋난다
        assert sum(parts) == pytest.approx(1.0, abs=2e-6) and all(0.0 <= x <= 1.0 for x in parts)
        assert all(0.0 <= x <= 1.0 for x in est["reachByStep"])


def test_zero_probability_move_does_not_make_incomplete():
    """확률 0인 이동으로만 닿는 화면은 확률이 없어도 INCOMPLETE가 아니다 (리뷰 #62-2)."""
    site_map = {"siteMapVersion": "t", "siteMapHash": "h3", "pages": {
        "A": {"path": "/a", "edges": [{"semanticKey": "g", "to": "G"}, {"semanticKey": "b", "to": "B"}]},
        "B": {"path": "/b", "edges": [{"semanticKey": "g", "to": "G"}]},
        "G": {"path": "/g", "edges": []}}}
    est = reach.estimate(site_map, {"A": {"g": 1.0, "b": 0.0}}, "A", "G", 2)
    assert est["status"] == "COMPLETE" and est["missingPages"] == []
    assert est["reachProbability"] == 1.0 and est["unfinishedProbability"] == 0.0
    assert est["mostLikelyPath"] == {"path": ["A", "G"], "probability": 1.0}


def test_map_mismatch_is_not_a_normal_result():
    mismatch = {"A": {"added": [{"semanticKey": "a9", "to": "B"}], "removed": []}}
    est = reach.estimate(MAP, PROBS, "A", "G", 3, map_mismatches=mismatch)
    assert est["status"] == "MAP_MISMATCH" and est["mapMismatches"] == mismatch
    ok = reach.estimate(MAP, PROBS, "A", "G", 3)
    assert ok["mapMismatches"] == {}
    assert reach.compare(ok, est) == {"comparable": False, "mismatched": ["siteStructure"]}


def test_compare_of_incomplete_estimates_is_flagged():
    a = reach.estimate(MAP, {"A": PROBS["A"]}, "A", "G", 3)
    b = reach.estimate(MAP, PROBS, "A", "G", 3)
    cmp = reach.compare(a, b)
    assert cmp["comparable"] and not cmp["bothComplete"] and "개선 판단 근거로 쓰지 않는다" in cmp["note"]
    assert "note" not in reach.compare(b, b)


def _site_map():
    from uxm.sitemap import load_site_map
    return load_site_map(os.path.join(ROOT, "config", "sitemaps", "sitemap-shop-v1.json"))


def _candidates_from_map(site_map, page_key):
    """지도 간선을 그대로 후보로 되돌린 것 (href = 목적지 화면 주소)."""
    pages = site_map["pages"]
    return [{"semanticKey": e["semanticKey"], "href": pages[e["to"]]["path"]} for e in pages[page_key]["edges"]]


def test_map_mismatch_detects_changed_added_removed_edges():
    """실제 화면 간선이 지도와 다르면 찾아낸다. 지도 밖으로 가는 후보는 원래 이탈이라 차이가 아니다 (리뷰 #62-3)."""
    from uxm.sitemap import SHOP
    site_map = _site_map()
    home = site_map["pages"]["home"]
    cands = _candidates_from_map(site_map, "home")
    assert reach.map_mismatch(SHOP, home, cands) is None
    assert reach.map_mismatch(SHOP, home, cands + [{"semanticKey": "본문>/shop/cart#1", "href": "/shop/cart"},
                                                   {"semanticKey": "외부>https://x#1", "href": "https://example.com/"}]) is None
    first = home["edges"][0]
    other = next(k for k, v in site_map["pages"].items() if k != first["to"])
    moved = [dict(c, href=site_map["pages"][other]["path"]) if c["semanticKey"] == first["semanticKey"] else c
             for c in cands]
    assert reach.map_mismatch(SHOP, home, moved) == {
        "added": [{"semanticKey": first["semanticKey"], "to": other}], "removed": [first]}
    extra = cands + [{"semanticKey": "본문>/shop/support#9", "href": "/shop/support"}]
    assert reach.map_mismatch(SHOP, home, extra) == {
        "added": [{"semanticKey": "본문>/shop/support#9", "to": "support"}], "removed": []}
    assert reach.map_mismatch(SHOP, home, cands[1:]) == {"added": [], "removed": [first]}


def test_reach_within_shortest_needs_max_steps_at_least_shortest():
    """maxSteps가 최단 거리보다 작으면 '최단 거리 안 도달 확률'을 낼 수 없어 null + 사유 (리뷰 #62 2차)."""
    chain = {"siteMapVersion": "t", "siteMapHash": "h4", "pages": {   # A → B → G, 최단 2
        "A": {"path": "/a", "edges": [{"semanticKey": "b", "to": "B"}]},
        "B": {"path": "/b", "edges": [{"semanticKey": "g", "to": "G"}]},
        "G": {"path": "/g", "edges": []}}}
    probs = {"A": {"b": 1.0}, "B": {"g": 1.0}}
    short = reach.estimate(chain, probs, "A", "G", 1)
    assert short["shortestSteps"] == 2 and short["reachWithinShortestProbability"] is None
    assert short["nullReasons"] == {"reachWithinShortestProbability": "MAX_STEPS_BELOW_SHORTEST"}
    assert short["reachProbability"] == 0.0   # 1번 안에는 못 닿는다 (값 자체는 maxSteps 기준 그대로)
    equal = reach.estimate(chain, probs, "A", "G", 2)   # maxSteps == 최단
    assert equal["reachWithinShortestProbability"] == 1.0 and equal["nullReasons"] == {}
    longer = reach.estimate(chain, probs, "A", "G", 5)
    assert longer["reachWithinShortestProbability"] == 1.0 and longer["nullReasons"] == {}
    cmp = reach.compare(short, short)
    assert cmp["comparable"] and cmp["deltaReachWithinShortestProbability"] is None


def test_unreachable_goal_has_null_reason():
    site_map = {"siteMapVersion": "t", "siteMapHash": "h5", "pages": {
        "A": {"path": "/a", "edges": []}, "G": {"path": "/g", "edges": []}}}
    est = reach.estimate(site_map, {"A": {"x": 1.0}}, "A", "G", 3)
    assert est["shortestSteps"] is None and est["reachWithinShortestProbability"] is None
    assert est["nullReasons"] == {"reachWithinShortestProbability": "GOAL_UNREACHABLE_ON_MAP"}


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
            probs, records, mismatches = reach.collect_page_probabilities(
                session["page"], url, site_map, backend, goal="환불 신청 화면 찾기", persona="처음 쓰는 사용자",
                skip={"refund"})
        finally:
            close_browser(session)
        assert set(probs) == set(site_map["pages"]) - {"refund"}       # 도착 화면 빼고 화면당 1번
        assert len(backend.requests) == len(probs)
        assert all(r["state"]["goal"] == "환불 신청 화면 찾기" for r in backend.requests)
        # 후보 semanticKey가 지도 간선과 같은 규칙이라 지도 밖 확률은 일부(같은 화면 앵커 등)만
        assert mismatches == {}   # 두 쇼핑몰 모두 실제 간선이 지도와 같다
        out[variant] = reach.estimate(site_map, probs, "home", "refund", 8, map_mismatches=mismatches)
        assert out[variant]["status"] == "COMPLETE"
    cmp = reach.compare(out["before"], out["after"])
    assert cmp["comparable"] and cmp["bothComplete"]
    # After 상단 메뉴 "주문·환불"이 키워드에 걸려 orders(→refund)로 가는 확률이 커진다
    assert cmp["deltaReachProbability"] > 0
    # 이동 8번이면 둘 다 거의 1이라, 최단 2번 안에 닿을 확률과 기대 단계로 차이가 보인다
    assert out["before"]["shortestSteps"] == out["after"]["shortestSteps"] == 2
    assert cmp["deltaReachWithinShortestProbability"] > 0.05 and cmp["deltaExpectedStepsGivenReach"] < 0
    assert out["after"]["mostLikelyPath"]["path"] == ["home", "orders", "refund"]


def test_real_shop_differing_from_map_is_map_mismatch(shops):
    """지도에서 간선 하나를 지우면 실제 쇼핑몰 화면과 달라져 MAP_MISMATCH로 나온다."""
    pytest.importorskip("playwright")
    import copy
    from uxm.browser import close_browser, open_browser
    site_map = copy.deepcopy(_site_map())
    dropped = site_map["pages"]["home"]["edges"].pop(0)
    session = open_browser(shops["before"] + "/shop/home")
    try:
        probs, _, mismatches = reach.collect_page_probabilities(
            session["page"], shops["before"], site_map, KeywordBackend(), goal="환불 신청 화면 찾기",
            persona="처음 쓰는 사용자", skip={"refund"})
    finally:
        close_browser(session)
    assert mismatches == {"home": {"added": [dropped], "removed": []}}
    assert reach.estimate(site_map, probs, "home", "refund", 8, map_mismatches=mismatches)["status"] == "MAP_MISMATCH"
