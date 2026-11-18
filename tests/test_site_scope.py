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


def test_open_must_match_path():
    with pytest.raises(sitemap.SiteMapError) as e:
        sitemap.site_config_from_dict(cfg(pages={"home": "/", "a": {"path": "/a.html", "open": "/b.html?x=1"}}))
    assert any("open" in r for r in e.value.reasons)
    c = sitemap.site_config_from_dict(cfg(pages={"home": "/", "a": {"path": "/a.html", "open": "/a.html?x=1"}}))
    assert c.open_path("a") == "/a.html?x=1" and c.open_path("home") == "/"


def _extracted(edges_by_page):
    """{page: [(semanticKey, href)]} → build_site_map에 넣을 후보 목록."""
    return {"live": {k: [{"semanticKey": s, "href": h} for s, h in v] for k, v in edges_by_page.items()}}


def test_unreachable_pages_recorded_when_not_required():
    data = cfg(pages={"home": "/", "a": "/a.html", "b": "/b.html"}, requireAllReachable=False)
    c = sitemap.site_config_from_dict(data)
    m = sitemap.build_site_map(c, _extracted({"home": [("본문>/a.html#1", "/a.html")], "a": [], "b": []}),
                               version="v", generated_at="t")
    assert m["unreachablePages"] == ["b"]
    sitemap.validate_site_map(m)   # 추가 필드는 해시에 안 들어감
    strict = sitemap.site_config_from_dict(cfg(pages={"home": "/", "a": "/a.html", "b": "/b.html"}))
    with pytest.raises(sitemap.SiteMapError):   # 기본(MORO 포함)은 지금처럼 실패
        sitemap.build_site_map(strict, _extracted({"home": [("본문>/a.html#1", "/a.html")], "a": [], "b": []}),
                               version="v", generated_at="t")


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
    assert c.start_page == "home" and c.global_nav_policy == "ALL_PAGES" and not c.require_all_reachable
    assert c.open_path("review") == "/board/review/list.html?board_no=4" and c.open_path("guide") == "/shopinfo/guide.html"
    assert c.page_texts["review"] == "상품 리뷰" and c.page_labels["qna"] == "상품 문의 (Q&A)"
    assert "home" not in c.page_texts and "brand" not in c.page_texts   # 도착 확인 글자가 없는 화면


@pytest.mark.parametrize("url,key", [
    (f"{BASE}/", "home"),
    (f"{BASE}", "home"),
    (f"{BASE}/board/review/list.html?board_no=4", "review"),
    ("/board/product/list.html?board_no=6", "qna"),
    (f"{BASE}/shopinfo/guide.html/", "guide"),
])
def test_registered_pages(cafe, url, key):
    assert cafe.registered_key(url) == key
    assert cafe.page_key(url) == key


@pytest.mark.parametrize("url", [
    f"{BASE}/myshop/index.html",                              # 등록하지 않은 화면 (로그인으로 넘어감)
    f"{BASE}/member/login.html?noMemberOrder&returnUrl=%2Fmyshop%2Findex.html",   # 리다이렉트 도착지도 등록 밖
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


# ---- 허용 판정: #70 ScenarioSafety와 같은 규칙 (경로+쿼리 완전 일치)

@pytest.mark.parametrize("href", [
    "/board/review/list.html?board_no=4", f"{BASE}/board/review/list.html?board_no=4",
    "/shopinfo/guide.html", "/", "/product/list.html?cate_no=23",
])
def test_registered_url_allowed(cafe, href):
    assert cafe.url_rejection(f"{BASE}/", href) is None and cafe.link_allowed(href)


@pytest.mark.parametrize("href,why", [
    ("/board/review/list.html", "OUT_OF_SCOPE"),                       # 등록한 쿼리 누락
    ("/board/review/list.html?board_no=5", "OUT_OF_SCOPE"),            # 쿼리 값 변경
    ("/board/review/list.html?board_no=4&page=2", "OUT_OF_SCOPE"),     # 쿼리 추가
    ("/shopinfo/guide.html?next=evil", "OUT_OF_SCOPE"),
    ("/board/review/list.html;jsessionid=AB?board_no=4", "OUT_OF_SCOPE"),   # 경로 매개변수 (한계, #70과 같음)
    ("/a/%2e%2e/shopinfo/guide.html", "OUT_OF_SCOPE"), ("/%2fshopinfo/guide.html", "OUT_OF_SCOPE"),
    ("//other.example/shopinfo/guide.html", "OUT_OF_SCOPE"),
    (f"https://{HOST}.evil.test/shopinfo/guide.html", "OUT_OF_SCOPE"),
    (f"https://{HOST}@evil.test/shopinfo/guide.html", "OUT_OF_SCOPE"),
    (f"http://{HOST}/shopinfo/guide.html", "OUT_OF_SCOPE"),           # 스킴(포트) 다름
    ("/exec/front/Order/Basket", "DANGEROUS_PATH"), ("/order/orderform.html", "DANGEROUS_PATH"),
    ("#", "NOT_NAVIGATION"), ("", "NOT_NAVIGATION"), (None, "NOT_NAVIGATION"),
])
def test_unregistered_or_ambiguous_url_rejected(cafe, href, why):
    assert cafe.url_rejection(f"{BASE}/", href) == why and not cafe.link_allowed(href)


def test_current_page_must_be_same_origin(cafe):
    assert cafe.url_rejection("https://other.example/", "/shopinfo/guide.html") == "OUT_OF_SCOPE"


def test_arrival_requires_registered_url(cafe):
    assert cafe.arrival_rejection(f"{BASE}/board/review/list.html?board_no=4") is None
    assert cafe.arrival_rejection(f"{BASE}/board/review/list.html") == "OUT_OF_SCOPE"
    assert cafe.arrival_rejection(f"{BASE}/member/login.html?returnUrl=%2Fmyshop") == "OUT_OF_SCOPE"   # 리다이렉트
    assert cafe.arrival_rejection(f"{BASE}/exec/front/x") == "DANGEROUS_PATH"
    assert cafe.in_scope(f"{BASE}/order/basket.html", BASE)
    assert not cafe.in_scope(f"{BASE}/myshop/index.html", BASE)            # 등록 밖
    assert not cafe.in_scope("http://evil.example/order/basket.html", BASE)  # 다른 사이트


@pytest.mark.parametrize("attrs,why", [
    ({"onclick": None, "download": None, "target": None, "role": None}, None),
    ({"onclick": None, "download": None, "target": "_self", "role": None}, None),
    ({"onclick": "submit()", "download": None, "target": None, "role": None}, "UNSUPPORTED_LINK_ACTION"),
    ({"onclick": "", "download": None, "target": None, "role": None}, "UNSUPPORTED_LINK_ACTION"),
    ({"onclick": None, "download": "", "target": None, "role": None}, "UNSUPPORTED_LINK_ACTION"),
    ({"onclick": None, "download": None, "target": "_blank", "role": None}, "UNSUPPORTED_LINK_ACTION"),
    ({"onclick": None, "download": None, "target": None, "role": "button"}, "UNSUPPORTED_LINK_ACTION"),
    ({"onclick": None}, "DOM_ATTRIBUTES_MISSING"), (None, "DOM_ATTRIBUTES_MISSING"),
])
def test_link_action_rejection(attrs, why):
    assert SiteScope.link_action_rejection(attrs or {}) == why


@pytest.mark.parametrize("label,why", [("상품 리뷰", None), ("0 장바구니", None), ("장바구니 담기", "DANGEROUS_ACTION"),
                                       ("바로 구매", "DANGEROUS_ACTION"), ("주문하기", "DANGEROUS_ACTION"),
                                       ("Log Out", "DANGEROUS_ACTION"), ("CHECKOUT", "DANGEROUS_ACTION")])
def test_label_rejection(label, why):
    assert SiteScope.label_rejection(label) == why


def test_global_nav_policy(cafe):
    assert cafe.global_nav_allowed("/board/review/list.html")   # ALL_PAGES
    start_only = SiteScope(sitemap.site_config_from_dict(cfg()))
    assert start_only.global_nav_allowed("") and start_only.global_nav_allowed("/")
    assert not start_only.global_nav_allowed("/a.html")


def test_js_args_lists_registered_urls(cafe):
    args = cafe.js_args()
    assert args["mode"] == "pages" and args["origin"] == f"https://{HOST}:443"
    assert "/" in args["urls"] and "/board/review/list.html?board_no=4" in args["urls"]
    assert "/board/review/list.html" not in args["urls"] and "/myshop/index.html" not in args["urls"]


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
    assert not browser._link_allowed("/board/review/list.html", cafe)   # 등록한 쿼리 누락
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


# ---------------------------------------------------------------- 사이트별 지도 기준 (run_factory)

def test_site_map_reference_per_site(tmp_path, cafe):
    import json
    from api import run_factory
    c = cafe.config
    pages = {"home": {"path": "/", "edges": [{"semanticKey": "상단 메뉴>/board/review/list.html?board_no=4#1",
                                              "to": "review"}]},
             "review": {"path": "/board/review/list.html", "edges": []},
             "qna": {"path": "/board/product/list.html", "edges": []}}
    site_map = {"siteMapVersion": c.site_map_version, "siteMapHash": sitemap.site_map_hash(pages), **c.meta,
                "startPageKey": "home", "generatedAt": "t", "pages": pages}
    path = tmp_path / "map.json"
    path.write_text(json.dumps(site_map, ensure_ascii=False), encoding="utf-8")
    vp = c.meta["viewport"]
    ref = run_factory.site_map_reference("/", {"path": "/board/review/list.html"}, vp, str(path), site_id="cafe24-ons")
    assert ref["basis"] == "SITE_MAP" and ref["goalPageKey"] == "review" and ref["shortestSteps"] == 1
    with pytest.raises(ValueError):   # 지도는 정상인데 닿지 않는 화면 → 실행 전 422
        run_factory.site_map_reference("/", {"path": "/board/product/list.html"}, vp, str(path), site_id="cafe24-ons")
    # MORO MARKET 조건으로 읽으면 조건 불일치 → 기준 없음(null)
    assert run_factory.site_map_reference("/shop/home", {"path": "/shop/refund"}, vp, str(path)) is None
    with pytest.raises(ValueError):
        run_factory.site_map_reference("/", {"path": "/x"}, vp, site_id="no-such-site")


def test_site_map_reference_missing_map_is_null():
    from api import run_factory
    assert run_factory.site_map_reference("/", {"path": "/board/review/list.html"}, {"width": 1440, "height": 900},
                                          "C:/no/such/map.json", site_id="cafe24-ons") is None


# ---------------------------------------------------------------- 후보 추출 (브라우저, 서버 없이 route로 HTML 제공)

PAGE_HTML = """<!doctype html><html><body style="margin:0">
<header><a href="/">로고</a> <a href="/board/review/list.html?board_no=4">상품 리뷰</a>
  <a href="#">고객지원</a> <a href="/myshop/index.html">마이쇼핑</a></header>
<main>
  <a href="../board/product/list.html?board_no=6">문의하기</a>
  <a href="../board/product/list.html;jsessionid=AB12?board_no=6">세션 문의</a>
  <a href="/board/review/list.html?board_no=4&amp;page=2">리뷰 2쪽</a>
  <a href="https://other.example/x">외부 링크</a>
  <a href="/product/abc/9/">상품 상세</a>
  <a href="/order/basket.html">장바구니</a>
  <a href="/order/basket.html" onclick="add()">담아 두기</a>
  <a href="/board/gallery/list.html?board_no=8" target="_blank">이벤트 새 창</a>
  <a href="/exec/front/Order/Basket">실행 링크</a>
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
                   ("본문", "/board/product/list.html?board_no=6"),   # 상대 주소 → 경로+쿼리
                   ("본문", "/order/basket.html")}
    reasons = {e["label"]: e["reason"] for e in out["excluded"]}
    expected = {"고객지원": "NOT_NAVIGATION", "외부 링크": "NOT_NAVIGATION", "장바구니 담기": "NOT_NAVIGATION",
                "자바스크립트": "NOT_NAVIGATION", "마이쇼핑": "OUT_OF_SCOPE", "상품 상세": "OUT_OF_SCOPE",
                "세션 문의": "OUT_OF_SCOPE", "리뷰 2쪽": "OUT_OF_SCOPE", "실행 링크": "DANGEROUS_PATH",
                "담아 두기": "UNSUPPORTED_LINK_ACTION", "이벤트 새 창": "UNSUPPORTED_LINK_ACTION"}
    for label, why in expected.items():
        assert reasons[label] == why, label
    keys = {c["semanticKey"] for c in out["candidates"]}
    assert "본문>/board/product/list.html?board_no=6#1" in keys
    assert all("attrs" not in c for c in out["candidates"])   # 링크 속성은 판정에만 쓰고 후보에 남기지 않음


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


# ---------------------------------------------------------------- 리다이렉트·복귀 (#71 리뷰 1·2, 서버 없이 route)

OTHER = "https://other.example"
HOME_HTML = """<!doctype html><html><body style="margin:0"><header>
<a href="/product/list.html?cate_no=23">제품</a> <a href="/board/review/list.html?board_no=4">상품 리뷰</a>
<a href="/board/gallery/list.html?board_no=8">이벤트</a></header><main><h1>홈</h1></main></body></html>"""
PRODUCTS_HTML = "<!doctype html><html><body><main><h2>제품</h2></main></body></html>"


def _html(route, body):
    route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)


def _redirect(route, location):
    route.fulfill(status=302, headers={"Location": location})


def _site(route):
    """cafe24 화면 흉내: 리뷰는 외부 도메인으로, 이벤트는 같은 경로의 미등록 쿼리로 리다이렉트."""
    url = route.request.url
    if url.startswith(OTHER):
        return _html(route, "<!doctype html><html><body><h1>외부</h1></body></html>")
    if not url.startswith(BASE):
        return route.fulfill(status=404, body="")
    if "/board/review/list.html" in url:
        return _redirect(route, f"{OTHER}/landing")
    if "/board/gallery/list.html?board_no=8" in url:
        return _redirect(route, f"{BASE}/board/gallery/list.html?board_no=99")
    if "/product/list.html" in url:
        return _html(route, PRODUCTS_HTML)
    return _html(route, HOME_HTML)


@pytest.fixture
def site_page():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.route("**/*", _site)
        page = context.new_page()
        page.goto(f"{BASE}/", wait_until="networkidle")
        yield page
        browser.close()


def _candidate(page, scope, href):
    from uxm.candidates import extract
    return next(c for c in extract(page, scope=scope)["candidates"] if c["href"] == href)


def test_same_path_unregistered_query_redirect_is_out_of_scope(site_page, cafe):
    """리뷰 1: 경로는 같고 쿼리만 미등록 값으로 바뀐 리다이렉트도 NO_CHANGE가 아니라 OUT_OF_SCOPE."""
    from uxm import browser
    res = browser.click(site_page, _candidate(site_page, cafe, "/board/gallery/list.html?board_no=8"),
                        timeout_ms=5000, scope=cafe)
    assert (res["result"], res["reason"], res["executed"]) == ("CLICK_FAILED", "OUT_OF_SCOPE", True)
    assert site_page.url.endswith("/board/gallery/list.html?board_no=99")


def test_back_from_external_redirect_returns_to_registered_page(site_page, cafe):
    """리뷰 2: 외부 도메인으로 리다이렉트 → 뒤로 가기 → 등록된 원래 화면이면 NAVIGATED."""
    from uxm import browser
    res = browser.click(site_page, _candidate(site_page, cafe, "/board/review/list.html?board_no=4"),
                        timeout_ms=5000, scope=cafe)
    # route로 준 다른 출처 302는 크롬 오류 화면(chrome-error://)에 닿을 수 있다. 어느 쪽이든 사이트 밖이다
    assert (res["result"], res["reason"]) == ("CLICK_FAILED", "OUT_OF_SCOPE") and not site_page.url.startswith(BASE)
    back = browser.go_back(site_page, "/", timeout_ms=5000, scope=cafe)
    assert back["result"] == "NAVIGATED", back
    assert site_page.url.rstrip("/") == BASE


def test_registered_navigation_still_navigates(site_page, cafe):
    from uxm import browser
    res = browser.click(site_page, _candidate(site_page, cafe, "/product/list.html?cate_no=23"),
                        timeout_ms=5000, scope=cafe)
    assert res["result"] == "NAVIGATED" and browser.page_key(site_page.url, cafe) == "products"


def test_loop_recovers_from_external_redirect_and_records_events(site_page, cafe, tmp_path):
    """루프 전체: 리뷰(외부 리다이렉트) → 시스템 뒤로 가기·Event 기록 → 제품 목록 도착 (실제 브라우저, 가짜 모델)."""
    from types import SimpleNamespace
    from api import run_factory
    from api.presets import Presets
    from uxm.agent import agent_settings, run_agent
    from uxm.mock import MockBackend
    from uxm.storage import RunStore, read_jsonl
    presets = Presets()
    req = SimpleNamespace(viewport=None, maxSteps=6, variant="before", experimentKey="scope_test", startPath="/")
    model = {"resultOrigin": "MOCK", "modelVersion": None, "modelEndpointId": "mock", "temperature": 1.52,
             "goalJudgmentMode": "DOM_ONLY"}
    run = run_factory.build_run(req, presets.goal("refund"), presets.persona("novice-shopper"), model)
    run.update(goalId=None, goal="안마의자 종류를 둘러보고 싶다", siteId="cafe24-ons", referencePathConfig=None,
               verification={"type": "PATH_AND_VISIBLE_TEXT", "path": "/product/list.html", "text": "제품",
                             "exact": False, "scope": "page"})
    store = RunStore(run["runId"], runs_dir=str(tmp_path))
    store.create(run)
    settings = agent_settings(run, None, store.dir, f"{BASE}/", BASE)
    backend = MockBackend(["상단 메뉴>/board/review/list.html?board_no=4#1", "상단 메뉴>/product/list.html?cate_no=23#1"])
    result = run_agent(settings, backend, site_page)   # env 없음 → siteId로 SiteScope Env

    assert (result["status"], result["terminationReason"]) == ("SUCCESS", "GOAL_VERIFIED"), result
    assert result["summary"]["systemBackCount"] == 1
    assert [v["pageKey"] for v in result["summary"]["path"]] == ["home", "home", "products"]
    events = read_jsonl(os.path.join(store.dir, "events.jsonl"))
    assert any(e["eventType"] == "ACTION_COMPLETED" and e["details"].get("reason") == "OUT_OF_SCOPE" for e in events)
    assert any(e["eventType"] == "BACK_EXECUTED" and e["eventOrigin"] == "SYSTEM_RECOVERY" for e in events)
    assert not any(e["eventType"] == "BACK_FAILED" for e in events)   # 리뷰 2: 정상 복귀를 실패로 보지 않음
