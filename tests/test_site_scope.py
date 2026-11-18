"""사이트별 탐색 범위 (#67, 외부 사이트 시나리오 테스트).

- 사이트를 넘기지 않으면 MORO MARKET 기존 규칙과 같다 (회귀)
- 사이트 설정의 등록된 화면(pages)으로 가는 링크만 후보·클릭 범위 (리뷰 1)
- 상단 메뉴 정책은 사이트별 (리뷰 2)
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from uxm import sitemap  # noqa: E402
from uxm.site_scope import SiteScope, load_scope  # noqa: E402

HOST = "ecudemo360127.cafe24.com"
BASE = f"https://{HOST}"


def cfg(**over):
    data = {"name": "t", "pages": {"home": "/", "a": {"path": "/a.html", "label": "에이", "text": "A 화면"}},
            "startPage": "home", "pathPrefix": "/", "allowedHosts": [HOST], "variants": {"live": BASE},
            "siteMapVersion": "v", "out": "x.json"}
    data.update(over)
    return data


# ---------------------------------------------------------------- 사이트 설정 (SiteConfig)

def test_moro_market_config_unchanged():
    """기존 문자열 형식 설정은 그대로 읽힌다 (지도 해시는 경로만 쓰므로 영향 없음)."""
    shop = sitemap.SHOP
    assert shop.pages == {"home": "/shop/home", "mypage": "/shop/mypage", "orders": "/shop/orders",
                          "support": "/shop/support", "refund": "/shop/refund"}
    assert shop.global_nav_policy == "START_ONLY" and shop.page_labels == {} and shop.page_texts == {}
    assert shop.display_name == "moro-market"
    sitemap.load_site_map(os.path.join(ROOT, shop.out))   # 기존 지도 파일 해시 그대로 통과


def test_page_objects_split_into_path_label_text():
    c = sitemap.site_config_from_dict(cfg(displayName="테스트몰", globalNavPolicy="ALL_PAGES"))
    assert c.pages == {"home": "/", "a": "/a.html"}
    assert c.page_labels == {"a": "에이"} and c.page_texts == {"a": "A 화면"}
    assert c.display_name == "테스트몰" and c.global_nav_policy == "ALL_PAGES"


@pytest.mark.parametrize("pages,needle", [
    ({"home": "/", "a": {"label": "에이"}}, "path"),
    ({"home": "/", "a": {"path": "a.html"}}, "path"),
    ({"home": "/", "a": {"path": "/a.html", "label": " "}}, "label"),
    ({"home": "/", "a": 3}, "path"),
])
def test_bad_page_entries_rejected(pages, needle):
    with pytest.raises(sitemap.SiteMapError) as e:
        sitemap.site_config_from_dict(cfg(pages=pages))
    assert any(needle in r for r in e.value.reasons)


def test_bad_global_nav_policy_rejected():
    with pytest.raises(sitemap.SiteMapError) as e:
        sitemap.site_config_from_dict(cfg(globalNavPolicy="SOMETIMES"))
    assert any("globalNavPolicy" in r for r in e.value.reasons)


def test_config_page_key_ignores_session_suffix():
    c = sitemap.site_config_from_dict(cfg())
    assert c.page_key("/a.html;jsessionid=ABC?x=1") == "a"
    assert c.page_key(f"{BASE}/a.html") == "a"
    assert c.page_key("https://other.example/a.html") is None


# ---------------------------------------------------------------- SiteScope

@pytest.fixture
def cafe():
    return load_scope("cafe24-ons")


def test_cafe24_config_loads(cafe):
    c = cafe.config
    assert c.start_page == "home" and c.global_nav_policy == "ALL_PAGES"
    assert c.page_texts["review"] == "상품 리뷰" and c.page_labels["qna"] == "상품 문의 (Q&A)"
    assert "home" not in c.page_texts and "brand" not in c.page_texts   # 도착 확인 글자가 없는 화면


@pytest.mark.parametrize("url,key", [
    (f"{BASE}/", "home"),
    (f"{BASE}", "home"),
    (f"{BASE}/board/review/list.html?board_no=4", "review"),
    ("/board/product/list.html?board_no=6", "qna"),
    (f"{BASE}/member/login.html?noMemberOrder&returnUrl=%2Fmyshop%2Findex.html", "login"),   # 마이쇼핑 리다이렉트
    (f"{BASE}/shopinfo/guide.html/", "guide"),
])
def test_registered_pages(cafe, url, key):
    assert cafe.registered_key(url) == key
    assert cafe.page_key(url) == key


@pytest.mark.parametrize("url", [
    f"{BASE}/myshop/index.html",                              # 등록하지 않은 화면 (로그인으로 넘어감)
    f"{BASE}/product/%EB%B2%A8%EB%A1%9C%EC%95%84/9/category/23/",   # 상품 상세
    f"{BASE}/board/review/read.html?no=1",                    # 게시글 상세
    "https://other.example/",                                 # 다른 도메인
    None, "",
])
def test_unregistered_or_foreign_rejected(cafe, url):
    assert cafe.registered_key(url) is None
    assert not cafe.link_allowed(url)


def test_page_key_falls_back_to_path_for_unregistered(cafe):
    assert cafe.page_key(f"{BASE}/myshop/index.html") == "/myshop/index.html"


def test_in_scope_checks_origin_and_registration(cafe):
    assert cafe.in_scope(f"{BASE}/order/basket.html", BASE)
    assert not cafe.in_scope(f"{BASE}/myshop/index.html", BASE)            # 등록 밖
    assert not cafe.in_scope("http://evil.example/order/basket.html", BASE)  # 다른 사이트


def test_global_nav_policy(cafe):
    assert cafe.global_nav_allowed("/board/review/list.html")   # ALL_PAGES
    start_only = SiteScope(sitemap.site_config_from_dict(cfg()))
    assert start_only.global_nav_allowed("") and start_only.global_nav_allowed("/")
    assert not start_only.global_nav_allowed("/a.html")


def test_js_args_lists_registered_paths(cafe):
    args = cafe.js_args()
    assert args["mode"] == "pages" and args["hosts"] == [HOST]
    assert "/" in args["paths"] and "/board/review/list.html" in args["paths"]
    assert "/myshop/index.html" not in args["paths"]


@pytest.mark.parametrize("site_id", ["no-such-site", "../moro-market", "a/b"])
def test_unknown_or_unsafe_site_id_rejected(site_id):
    with pytest.raises(ValueError):
        load_scope(site_id)


def test_no_site_id_means_moro_market():
    assert load_scope(None) is None and load_scope("") is None


# ---------------------------------------------------------------- 브라우저 함수·루프 연결 (브라우저를 띄우지 않음)

def test_browser_defaults_unchanged_and_scoped(cafe):
    pytest.importorskip("playwright")
    from uxm import browser
    assert browser.page_key("http://127.0.0.1:8001/shop/orders?testMode=x") == "orders"   # 기존 규칙
    assert browser._link_allowed("/shop/refund") and not browser._link_allowed("/board/review/list.html")
    assert browser.page_key(f"{BASE}/board/review/list.html?board_no=4", cafe) == "review"
    assert browser._link_allowed("/board/review/list.html?board_no=4", cafe)
    assert not browser._link_allowed("/shop/refund", cafe)
    assert browser._in_scope("http://127.0.0.1:8001/shop/home", "http://127.0.0.1:8001")
    assert not browser._in_scope(f"{BASE}/myshop/index.html", BASE, cafe)


def test_default_env_uses_scope_only_when_given(cafe):
    pytest.importorskip("playwright")
    from uxm import agent, browser
    plain = agent.default_env()
    assert plain.click is browser.click and plain.page_key is browser.page_key   # 기존 함수 그대로
    scoped = agent.default_env(cafe)
    assert scoped.page_key(f"{BASE}/shopinfo/guide.html") == "guide"
    assert scoped.extract.keywords == {"scope": cafe} and scoped.click.keywords == {"scope": cafe}


def test_agent_settings_carries_site_id():
    from uxm.agent import agent_settings
    run = {"runId": "r", "startPath": "/", "goal": "g", "persona": "p", "limits": {}, "viewport": {},
           "goalJudgmentMode": "DOM_ONLY", "temperature": 1.52, "promptVersion": "v2", "resultOrigin": "LIVE",
           "verification": {"path": "/board/review/list.html", "text": "상품 리뷰"}, "siteId": "cafe24-ons"}
    assert agent_settings(run, None, "d", BASE + "/", BASE)["siteId"] == "cafe24-ons"
    run.pop("siteId")
    assert agent_settings(run, None, "d", BASE + "/", BASE)["siteId"] is None


# ---------------------------------------------------------------- 후보 추출 (브라우저, 서버 없이 route로 HTML 제공)

PAGE_HTML = """<!doctype html><html><body style="margin:0">
<header><a href="/">로고</a> <a href="/board/review/list.html?board_no=4">상품 리뷰</a>
  <a href="#">고객지원</a> <a href="/myshop/index.html">마이쇼핑</a></header>
<main>
  <a href="../board/product/list.html;jsessionid=AB12?board_no=6">문의하기</a>
  <a href="https://other.example/x">외부 링크</a>
  <a href="/product/abc/9/">상품 상세</a>
  <a href="/order/basket.html">장바구니</a>
  <button>장바구니 담기</button>
  <a href="javascript:void(0)">자바스크립트</a>
</main></body></html>"""


@pytest.fixture
def routed_page():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
        page.route(f"{BASE}/**", lambda r: r.fulfill(status=200, content_type="text/html; charset=utf-8",
                                                     body=PAGE_HTML))
        yield page
        browser.close()


def test_extract_registered_links_only(routed_page, cafe):
    from uxm.candidates import extract
    routed_page.goto(f"{BASE}/shopinfo/guide.html")
    out = extract(routed_page, scope=cafe)
    got = {(c["region"], c["href"]) for c in out["candidates"]}
    assert got == {("상단 메뉴", "/"), ("상단 메뉴", "/board/review/list.html?board_no=4"),
                   ("본문", "/board/product/list.html?board_no=6"),   # 상대 주소·세션 꼬리 정규화
                   ("본문", "/order/basket.html")}
    reasons = {e["label"]: e["reason"] for e in out["excluded"]}
    for label in ("고객지원", "마이쇼핑", "외부 링크", "상품 상세", "장바구니 담기", "자바스크립트"):
        assert reasons[label] == "NOT_NAVIGATION", label
    keys = {c["semanticKey"] for c in out["candidates"]}
    assert "본문>/board/product/list.html?board_no=6#1" in keys   # 세션 값이 달라도 같은 semanticKey


def test_extract_start_only_policy_and_self_link(routed_page):
    from uxm.candidates import extract
    scope = SiteScope(sitemap.site_config_from_dict(cfg(
        pages={"home": "/", "review": "/board/review/list.html", "basket": "/order/basket.html"})))
    routed_page.goto(f"{BASE}/order/basket.html")
    out = extract(routed_page, scope=scope)
    assert [c["href"] for c in out["candidates"]] == []   # 상단 메뉴는 시작 화면 아님, 장바구니는 현재 화면
    reasons = {e["label"]: e["reason"] for e in out["excluded"]}
    assert reasons["상품 리뷰"] == "GLOBAL_NAV_NOT_START" and reasons["장바구니"] == "SELF_LINK"


def test_extract_without_scope_keeps_shop_rule(routed_page):
    """scope가 없으면 기존 규칙: /shop/ 링크만 → 이 화면에는 후보가 없다."""
    from uxm.candidates import extract
    routed_page.goto(f"{BASE}/")
    out = extract(routed_page)
    assert out["candidates"] == []
    assert {e["reason"] for e in out["excluded"]} <= {"NOT_NAVIGATION", "GLOBAL_NAV_NOT_START"}
