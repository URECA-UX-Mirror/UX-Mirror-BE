"""사이트 지도 (이슈 #46). 브라우저 없이 돈다.

지도 생성은 `scripts/build_sitemap.py`(쇼핑몰 서버 필요)가 하고, 여기서는 순수 로직(간선·해시·거리·검증·대조)과
Git에 고정한 지도 파일(`config/sitemaps/sitemap-shop-v1.json`)을 확인한다.
"""
import copy
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from uxm import sitemap  # noqa: E402
from uxm.sitemap import SHOP, SiteMapError  # noqa: E402

MAP_FILE = os.path.join(ROOT, "config", "sitemaps", "sitemap-shop-v1.json")
NOW = "2026-11-18T10:00:00+09:00"


def cand(semantic_key: str, href: str, label: str = "라벨") -> dict:
    return {"semanticKey": semantic_key, "href": href, "label": label}


# 실제 쇼핑몰과 같은 연결 (support·refund 포함). 라벨은 Before/After에서 다르게 둔다
def shop_candidates(top_label: str) -> dict[str, list[dict]]:
    return {
        "home": [cand("상단 메뉴>/shop/orders#1", "/shop/orders", top_label),
                 cand("상단 메뉴>/shop/mypage#1", "/shop/mypage", "마이페이지"),
                 cand("상단 메뉴>/shop/support#1", "/shop/support", "고객센터")],
        "mypage": [cand("경로 표시>/shop/home#1", "/shop/home"), cand("사이드 메뉴>/shop/orders#1", "/shop/orders"),
                   cand("사이드 메뉴>/shop/refund#1", "/shop/refund"), cand("본문>/shop/support#1", "/shop/support")],
        "orders": [cand("경로 표시>/shop/home#1", "/shop/home"), cand("본문>/shop/refund#1", "/shop/refund")],
        "support": [cand("본문>/shop/orders#1", "/shop/orders")],
        "refund": [cand("경로 표시>/shop/home#1", "/shop/home"), cand("사이드 메뉴>/shop/orders#1", "/shop/orders")],
    }


PRESET = [{"goalPage": "refund", "shortestAllowedSteps": 2,
           "allowedPaths": [["home", "orders", "refund"], ["home", "mypage", "refund"],
                            ["home", "mypage", "orders", "refund"]]}]


def build(before=None, after=None, preset=PRESET):
    extracted = {"before": before or shop_candidates("이용정보"), "after": after or shop_candidates("주문·환불")}
    return sitemap.build_site_map(SHOP, extracted, version="sitemap-test", generated_at=NOW, preset_checks=preset)


# ---------------------------------------------------------------- 간선·해시

def test_edges_keep_only_supported_pages_and_ignore_query():
    edges = sitemap.edges_from_candidates(SHOP, [
        cand("본문>/shop/orders#1", "/shop/orders?testMode=x"), cand("본문>/shop/orders#1", "/shop/orders/"),
        cand("하단>/shop/cart#1", "/shop/cart"), cand("본문>https://x.com#1", "https://example.com/shop/home"),
        cand("본문>none#1", None)])
    assert edges == [{"semanticKey": "본문>/shop/orders#1", "to": "orders"}]   # 중복 제거, 지원 화면만, 바깥 도메인 제외


def test_allowed_hosts_lets_absolute_links_on_same_site():
    config = sitemap.SiteConfig(name="t", pages={"home": "/shop/home"}, start_page="home", path_prefix="/shop/",
                                allowed_hosts=("127.0.0.1:8001",))
    assert config.page_key("http://127.0.0.1:8001/shop/home") == "home"
    assert config.page_key("http://evil.example/shop/home") is None


def test_hash_ignores_labels_edge_order_duplicates_and_metadata():
    a = build()
    shuffled = shop_candidates("완전히 다른 라벨")
    shuffled["mypage"] = list(reversed(shuffled["mypage"])) + shuffled["mypage"][:1]   # 순서·중복
    b = build(before=shuffled, after=copy.deepcopy(shuffled))
    assert a["siteMapHash"] == b["siteMapHash"]
    c = sitemap.build_site_map(SHOP, {"before": shop_candidates("x"), "after": shop_candidates("y")},
                               version="other-version", generated_at="2030-01-01T00:00:00+09:00", preset_checks=PRESET)
    assert c["siteMapHash"] == a["siteMapHash"]   # 버전·generatedAt은 해시 대상 아님
    assert a["siteMapHash"].startswith("sha256:") and len(a["siteMapHash"]) == len("sha256:") + 64


def test_hash_changes_when_edge_or_path_changes():
    base = build()["pages"]
    changed = copy.deepcopy(base)
    changed["support"]["edges"] = []
    assert sitemap.site_map_hash(changed) != sitemap.site_map_hash(base)
    moved = copy.deepcopy(base)
    moved["orders"]["path"] = "/shop/orders2"
    assert sitemap.site_map_hash(moved) != sitemap.site_map_hash(base)


def test_hash_rule_is_exact():
    """#46 규칙: pages만, sort_keys, ensure_ascii=False, 공백 없는 separators, UTF-8 SHA-256."""
    import hashlib
    pages = {"b": {"path": "/b", "edges": [{"to": "a", "semanticKey": "본문>/a#1"}]}, "a": {"path": "/a", "edges": []}}
    text = '{"a":{"edges":[],"path":"/a"},"b":{"edges":[{"semanticKey":"본문>/a#1","to":"a"}],"path":"/b"}}'
    assert sitemap.site_map_hash(pages) == "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 생성 검증 (실패하면 파일을 만들지 않음)

def test_build_fails_when_page_extraction_failed():
    before = shop_candidates("이용정보")
    before["support"] = None
    with pytest.raises(SiteMapError) as e:
        build(before=before)
    assert any("support" in r and "추출 실패" in r for r in e.value.reasons)


def test_build_fails_when_before_after_edges_differ():
    after = shop_candidates("주문·환불")
    after["home"].append(cand("본문>/shop/refund#1", "/shop/refund"))
    with pytest.raises(SiteMapError) as e:
        build(after=after)
    assert any("'home' 간선이" in r for r in e.value.reasons)


def test_build_fails_when_page_unreachable():
    before, after = shop_candidates("이용정보"), shop_candidates("주문·환불")
    for side in (before, after):
        side["home"] = [c for c in side["home"] if "support" not in c["href"]]
        side["mypage"] = [c for c in side["mypage"] if "support" not in c["href"]]
    with pytest.raises(SiteMapError) as e:
        build(before=before, after=after)
    assert any("'support'로 가는 길이 없음" in r for r in e.value.reasons)


def test_build_fails_when_preset_allowed_paths_disagree():
    with pytest.raises(SiteMapError) as e:
        build(preset=[{"goalPage": "refund", "shortestAllowedSteps": 1, "allowedPaths": [["home", "refund"]]}])
    reasons = " ".join(e.value.reasons)
    assert "최단 거리 2 ≠ preset shortestAllowedSteps 1" in reasons and "home → refund" in reasons


# ---------------------------------------------------------------- 거리

def test_distances_for_refund_and_orders_goals():
    pages = build()["pages"]
    refund = sitemap.distances(pages, "refund")
    assert (refund["home"], refund["mypage"], refund["orders"], refund["refund"]) == (2, 1, 1, 0)
    orders = sitemap.distances(pages, "orders")
    assert (orders["home"], orders["mypage"], orders["orders"]) == (1, 1, 0)


def test_distances_unreachable_is_none_and_unknown_goal_is_error():
    pages = build()["pages"]
    pages = {**pages, "island": {"path": "/shop/island", "edges": []}}
    assert sitemap.distances(pages, "refund")["island"] is None
    with pytest.raises(SiteMapError):
        sitemap.distances(pages, "nowhere")


# ---------------------------------------------------------------- 읽기

def test_load_rejects_tampered_or_missing_file(tmp_path):
    data = build()
    good = tmp_path / "good.json"
    good.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert sitemap.load_site_map(str(good))["siteMapHash"] == data["siteMapHash"]

    tampered = copy.deepcopy(data)
    tampered["pages"]["orders"]["edges"] = []
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SiteMapError) as e:
        sitemap.load_site_map(str(bad))
    assert any("해시 불일치" in r for r in e.value.reasons)

    with pytest.raises(SiteMapError):
        sitemap.load_site_map(str(tmp_path / "none.json"))


# ---------------------------------------------------------------- 고정된 지도 파일

def test_committed_site_map_is_valid_and_matches_presets():
    data = sitemap.load_site_map(MAP_FILE)
    assert data["siteMapVersion"] == "sitemap-v1" and data["startPageKey"] == "home"
    assert set(data["pages"]) == set(SHOP.pages)
    assert {k: data[k] for k in SHOP.meta} == SHOP.meta   # 실행과 같은 조건으로 만들었는지
    with open(os.path.join(ROOT, "config", "presets.json"), encoding="utf-8") as f:
        refund = next(g for g in json.load(f)["goals"] if g["goalId"] == "refund")
    ref = refund["referencePathConfig"]
    assert sitemap.distances(data["pages"], "refund")["home"] == ref["shortestAllowedSteps"]
    assert all(sitemap.is_path_on_map(data["pages"], route) for route in ref["allowedPaths"])
    # 모든 지원 화면은 home에서 갈 수 있다
    assert all(sitemap.distances(data["pages"], key)["home"] is not None for key in SHOP.pages)


# ---------------------------------------------------------------- 실제 Run과 대조 (2단계)

def step(page_key: str, candidates: list[dict], signature: str | None = None, set_id: str | None = None) -> dict:
    sig = signature or f"{page_key}_default"
    return {"runId": "run_x", "stepId": 1, "page": {"pageKey": page_key, "pageSignature": sig},
            "candidateSet": {"candidateSetId": set_id or f"{sig}_initial", "candidates": candidates}}


def test_compare_with_steps_finds_mismatch_and_skips_test_mode_and_retry():
    site_map = build()
    ok = step("orders", shop_candidates("x")["orders"])
    wrong = step("orders", shop_candidates("x")["orders"][:1])
    test_mode = step("support", [], signature="support_deadend")
    retry = step("orders", [], set_id="orders_default_retry_1")
    unknown = step("cart", [])
    problems = sitemap.compare_with_steps(site_map, SHOP, [ok, wrong, test_mode, retry, unknown])
    assert len(problems) == 2
    assert "지도에만" in problems[0] and "'cart'" in problems[1]
