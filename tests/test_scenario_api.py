"""외부 사이트 시나리오 테스트 실행 API (#74, 팀 문서 「외부 사이트 시나리오 테스트」 4.3).

사이트 설정은 실제 `config/sites/cafe24-ons.json`을 쓰고, 루프·브라우저·사전 검사는 tests/test_api.py의 가짜를 쓴다.
실제 화면 사전 검사(1곳 확인)는 아래 test_preflight_* 에서 가짜 브라우저로 본다.
"""
import os
import sys

import pytest

pytest.importorskip("fastapi")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from fastapi.testclient import TestClient  # noqa: E402

from api.errors import ApiError  # noqa: E402
from api.main import create_app  # noqa: E402
from api.preflight import check_goal_screens  # noqa: E402
from api.sites import resolve_site  # noqa: E402
from test_api import FakeIntegrations as ApiFakes  # noqa: E402

SITE = "cafe24-ons"
BASE = "https://ecudemo360127.cafe24.com"
REVIEW = {"path": "/board/review/list.html", "text": "상품 리뷰"}


class FakeIntegrations(ApiFakes):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.opened = []

    def goal_preflight(self):
        def check(verification, base_urls, scope=None):
            self.preflight_calls.append((verification, dict(base_urls), scope.site_id if scope else None))
            if self.preflight_error is not None:
                raise self.preflight_error
        return check

    def browser(self):
        open_browser, close_browser = super().browser()

        def opener(url):
            self.opened.append(url)
            return open_browser(url)
        return opener, close_browser


@pytest.fixture
def client(tmp_path):
    integ = FakeIntegrations()
    app = create_app(results_dir=str(tmp_path), integrations=integ)
    with TestClient(app) as c:
        yield c, integ, app


def scenario(**over) -> dict:
    return {"experimentKey": "scn_review", "variant": "before", "siteId": SITE,
            "goal": "이 안마의자를 실제로 써 본 사람들의 후기가 궁금하다", "verification": dict(REVIEW),
            "personaId": "novice-shopper", **over}


# ---------------------------------------------------------------- 실행

def test_scenario_run_uses_site_settings(client):
    c, integ, app = client
    r = c.post("/api/runs", json=scenario())
    assert r.status_code == 201, r.text
    assert r.json()["siteId"] == SITE and r.json()["mode"] == "SCENARIO" and r.json()["variant"] == "before"
    app.state.runner.wait(5)
    run = c.get(f"/api/runs/{r.json()['runId']}").json()
    assert (run["siteId"], run["mode"], run["startPath"]) == (SITE, "SCENARIO", "/")
    # 사이트 버전 값은 사이트 설정 meta (내부 주소는 Run에 저장하지 않는다)
    assert run["siteVersion"] == "cafe24-ons-live"
    assert run["comparisonContext"]["candidatePolicyVersion"] == "candidate-scenario-v1"
    assert BASE not in str(run)
    # 지도 기준: 사이트별 지도로 SITE_MAP 경로 기준
    ref = run["referencePathConfig"]
    assert ref["basis"] == "SITE_MAP" and ref["siteMapVersion"] == "sitemap-cafe24-ons-v1"
    assert ref["startPageKey"] == "home" and ref["goalPageKey"] == "review"
    # 루프는 사이트 주소에서 시작하고 siteId로 사이트 범위를 고른다
    assert integ.received_settings["startUrl"] == BASE + "/"
    assert integ.received_settings["shopBaseUrl"] == BASE and integ.received_settings["siteId"] == SITE
    # 사전 검사는 사이트 1곳만
    (verification, urls, site), = integ.preflight_calls
    assert urls == {"scenario": BASE} and site == SITE and verification["path"] == REVIEW["path"]
    listed = c.get("/api/runs", params={"experimentKey": "scn_review"}).json()["items"][0]
    assert (listed["siteId"], listed["mode"]) == (SITE, "SCENARIO")


def test_version_compare_run_is_unchanged(client):
    c, integ, app = client
    r = c.post("/api/runs", json={"experimentKey": "moro", "variant": "after", "goalId": "refund",
                                  "personaId": "novice-shopper"})
    assert r.status_code == 201 and "siteId" not in r.json() and "mode" not in r.json()
    app.state.runner.wait(5)
    run = c.get(f"/api/runs/{r.json()['runId']}").json()
    assert "siteId" not in run and "mode" not in run and run["siteVersion"] == "shop-v1"
    assert integ.received_settings["startUrl"].endswith(":8002/shop/home")
    item = c.get("/api/runs", params={"experimentKey": "moro"}).json()["items"][0]
    assert item["siteId"] is None and item["mode"] is None


@pytest.mark.parametrize("over, message", [
    ({"variant": "after"}, "variant=before"),
    ({"goalId": "refund", "goal": None, "verification": None}, "직접 입력"),
    ({"verification": {"path": "/product/detail.html", "text": "상품"}}, "등록된 화면이 아닙니다"),
    ({"verification": {"path": "/shop/refund", "text": "환불"}}, "등록된 화면이 아닙니다"),
    ({"startPath": "/board/notice/list.html"}, "시작 화면"),
    ({"siteId": "unknown-site"}, "등록되지 않은 사이트"),
    ({"siteId": "moro-market"}, "버전 비교용"),
    ({"siteId": "../moro-market"}, None),
])
def test_scenario_request_rules(client, over, message):
    c, integ, _ = client
    r = c.post("/api/runs", json={k: v for k, v in scenario(**over).items() if v is not None})
    assert r.status_code == 422 and r.json()["error"]["code"] == "CONTRACT_VALIDATION_ERROR", r.text
    if message:
        assert message in r.json()["error"]["message"]
    assert integ.preflight_calls == [] and integ.opened == []   # 검사 전에 막혀 브라우저를 띄우지 않는다


def test_goal_unreachable_on_site_map_is_422(client):
    """지도상 홈에서 갈 길이 없는 화면(qna)은 실행 전에 422 (#71 규칙). /api/sites의 goalSelectable=false와 같다."""
    c, integ, _ = client
    r = c.post("/api/runs", json=scenario(verification={"path": "/board/product/list.html", "text": "Q&A"}))
    assert r.status_code == 422 and "경로가 없습니다" in r.json()["error"]["message"]
    assert integ.preflight_calls == []


def test_preflight_failure_creates_no_run(client):
    c, integ, app = client
    integ.preflight_error = ApiError(503, "BROWSER_ERROR", "도착 화면을 읽지 못했습니다", retryable=True)
    r = c.post("/api/runs", json=scenario())
    assert r.status_code == 503 and r.json()["error"]["retryable"] is True
    assert c.get("/api/runs").json()["count"] == 0


def test_same_key_cannot_mix_scenario_and_version_compare(client):
    c, _, app = client
    assert c.post("/api/runs", json=scenario(experimentKey="mix")).status_code == 201
    app.state.runner.wait(5)
    r = c.post("/api/runs", json={"experimentKey": "mix", "variant": "after", "goal": scenario()["goal"],
                                  "verification": {"path": "/shop/refund", "text": "환불 / 반품 신청"},
                                  "personaId": "novice-shopper"})
    assert r.status_code == 422 and "siteId" in r.json()["error"]["message"]


def test_scenario_runs_are_excluded_from_comparison(client):
    c, _, app = client
    assert c.post("/api/runs", json=scenario(experimentKey="scn_cmp")).status_code == 201
    app.state.runner.wait(5)
    r = c.get("/api/comparisons/scn_cmp")
    assert r.status_code == 404 and r.json()["error"]["code"] == "RUN_NOT_FOUND"
    assert "시나리오" in r.json()["error"]["message"]


# ---------------------------------------------------------------- 사이트 목록

def test_sites_lists_scenario_sites_without_internal_values(client):
    c, _, _ = client
    items = c.get("/api/sites").json()["items"]
    assert [s["siteId"] for s in items] == [SITE]   # MORO MARKET은 버전 비교용이라 없음
    site = items[0]
    assert site["name"] == "ONS 공식몰 (cafe24 데모)" and site["mode"] == "SCENARIO" and site["startPage"] == "home"
    pages = {p["pageKey"]: p for p in site["pages"]}
    assert pages["review"] == {"pageKey": "review", "path": "/board/review/list.html", "label": "상품 리뷰",
                               "goalText": "상품 리뷰", "goalSelectable": True, "goalUnavailableReason": None}
    assert pages["home"]["goalText"] is None and pages["brand"]["goalText"] is None
    assert not pages["home"]["goalSelectable"] and not pages["brand"]["goalSelectable"]   # 시작 화면·확인 글자 없음
    assert pages["qna"]["goalSelectable"] is False and pages["qna"]["goalText"] == "Q&A"   # 지도상 홈에서 갈 수 없음
    assert [k for k, p in pages.items() if p["goalSelectable"]] == ["products", "review", "event", "basket"]
    # 사이트 지도 진단: 시작 화면에 보이는 링크로는 닿지 않는 화면과 그 이유
    assert {k: p["goalUnavailableReason"] for k, p in pages.items()} == {
        "home": "START_PAGE", "products": None, "review": None, "qna": "UNREACHABLE_ON_SITE_MAP", "event": None,
        "guide": "UNREACHABLE_ON_SITE_MAP", "company": "UNREACHABLE_ON_SITE_MAP", "basket": None,
        "brand": "NO_GOAL_TEXT"}
    assert all(p["goalSelectable"] == (p["goalUnavailableReason"] is None) for p in pages.values())
    assert site["siteMap"] == {"available": True, "siteMapVersion": "sitemap-cafe24-ons-v1",
                               "unreachablePages": ["qna", "guide", "company"]}
    text = str(site)
    # 사이트 주소는 FE가 입력 링크로 사이트를 찾도록 내려 준다(#95). 내부 버전 값·여는 주소의 쿼리는 여전히 없음
    assert site["siteUrl"] == BASE and site["allowedHosts"] == ["ecudemo360127.cafe24.com"]
    assert "meta" not in site and "board_no" not in text and "cafe24-ons-live" not in text


# ---------------------------------------------------------------- 사전 검사 1곳 (가짜 브라우저)

class Page:
    def __init__(self, url, texts):
        self.url, self.texts = url, texts

    def get_by_text(self, text, exact=False):
        hits = [t for t in self.texts if (t == text if exact else text in t)]
        return Hits(hits)

    def locator(self, sel):
        return self


class Hits:
    def __init__(self, hits):
        self.hits = hits

    def count(self):
        return len(self.hits)

    def nth(self, i):
        return self

    def is_visible(self):
        return True


def fake_browser(pages: dict, opened: list, closed: list):
    def open_browser(url, timeout_ms=None):
        opened.append(url)
        return {"page": pages[url]}
    return open_browser, closed.append


def test_preflight_opens_registered_url_once_without_before_after_label():
    scope = resolve_site(SITE)
    url = BASE + "/board/review/list.html?board_no=4"   # 쿼리가 있어야 열리는 게시판
    opened, closed = [], []
    browser = fake_browser({url: Page(url, ["상품 리뷰"])}, opened, closed)
    check_goal_screens({**REVIEW, "exact": False, "scope": "page"}, {"scenario": BASE}, browser, scope=scope)
    assert opened == [url] and len(closed) == 1
    browser = fake_browser({url: Page(url, ["다른 글자"])}, [], [])
    with pytest.raises(ApiError) as e:
        check_goal_screens({**REVIEW, "exact": False, "scope": "page"}, {"scenario": BASE}, browser, scope=scope)
    assert e.value.status == 422
    assert e.value.message == "도착 화면(/board/review/list.html)에 '상품 리뷰' 글자가 없습니다."


def test_preflight_redirect_and_unregistered_page():
    scope = resolve_site(SITE)
    url = BASE + "/order/basket.html"
    browser = fake_browser({url: Page(BASE + "/member/login.html", ["Login"])}, [], [])   # 로그인 화면으로 이동
    with pytest.raises(ApiError) as e:
        check_goal_screens({"path": "/order/basket.html", "text": "장바구니", "exact": False, "scope": "page"},
                           {"scenario": BASE}, browser, scope=scope)
    assert e.value.status == 422 and e.value.message.startswith("도착 화면(/order/basket.html)을 열면 다른 화면")
    opened = []
    with pytest.raises(ApiError) as e:
        check_goal_screens({"path": "/product/detail.html", "text": "x", "exact": False, "scope": "page"},
                           {"scenario": BASE}, fake_browser({}, opened, []), scope=scope)
    assert e.value.status == 422 and opened == []


def test_sites_without_site_map_does_not_judge_reachability(client, monkeypatch):
    """지도가 없으면 닿는지는 판단하지 않는다 (UNREACHABLE_ON_SITE_MAP 없음, unreachablePages null)."""
    import api.sites as sites
    monkeypatch.setattr(sites, "site_map_of", lambda scope: None)
    c, _, _ = client
    site = c.get("/api/sites").json()["items"][0]
    reasons = {p["pageKey"]: p["goalUnavailableReason"] for p in site["pages"]}
    assert reasons["qna"] is None and reasons["home"] == "START_PAGE" and reasons["brand"] == "NO_GOAL_TEXT"
    assert site["siteMap"] == {"available": False, "siteMapVersion": None, "unreachablePages": None}


@pytest.mark.parametrize("path", ["/board/review/list.html?board_no=5", "/board/review/list.html?board_no=4",
                                  "/board/review/list.html/"])
def test_scenario_accepts_only_exact_registered_path(client, path):
    """쿼리·끝 '/'가 붙은 경로는 같은 화면으로 구분되더라도 받지 않는다 (Run 기록에 다른 주소가 남지 않게)."""
    c, integ, _ = client
    r = c.post("/api/runs", json=scenario(verification={"path": path, "text": "상품 리뷰"}))
    assert r.status_code == 422 and "쿼리 없는 등록 경로" in r.json()["error"]["message"]
    assert "/board/review/list.html" in r.json()["error"]["message"] and integ.preflight_calls == []
    r = c.post("/api/runs", json=scenario(startPath=path, experimentKey="scn_start"))
    assert r.status_code == 422 and "시작 화면" in r.json()["error"]["message"]


def test_unreachable_pages_come_from_site_map_record(client, monkeypatch):
    """닿지 않는 화면은 지도 파일에 기록된 unreachablePages를 그대로 쓴다 (규칙을 한 곳으로)."""
    import api.sites as sites
    real = sites.site_map_of

    def recorded_only_event(scope):
        m = dict(real(scope))
        m["unreachablePages"] = ["event"]
        return m
    monkeypatch.setattr(sites, "site_map_of", recorded_only_event)
    c, _, _ = client
    site = c.get("/api/sites").json()["items"][0]
    assert site["siteMap"]["unreachablePages"] == ["event"]
    reasons = {p["pageKey"]: p["goalUnavailableReason"] for p in site["pages"]}
    assert reasons["event"] == "UNREACHABLE_ON_SITE_MAP" and reasons["qna"] is None



@pytest.mark.parametrize("variant_url, hosts, expected", [
    ("https://ecudemo360127.cafe24.com", ["ecudemo360127.cafe24.com"], "https://ecudemo360127.cafe24.com"),
    ("https://ecudemo360127.cafe24.com/some/path?x=1", ["ecudemo360127.cafe24.com"], "https://ecudemo360127.cafe24.com"),
    ("https://Shop.Example.com:8443", ["shop.example.com"], "https://shop.example.com:8443"),
    ("https://shop.example.com", ["other.example.com"], None),        # allowedHosts에 없는 호스트
    ("http://127.0.0.1:8001", ["127.0.0.1"], None),                    # 내 컴퓨터
    ("http://localhost:8002", ["localhost"], None),
    ("http://10.0.0.5:30001", ["10.0.0.5"], None),                     # 사설망 (내부 서버)
    ("http://192.168.0.10", ["192.168.0.10"], None),
    ("https://user:pw@shop.example.com", ["shop.example.com"], None),  # 인증 정보가 든 주소
    ("ftp://shop.example.com", ["shop.example.com"], None),
])
def test_site_url_is_only_a_public_address(variant_url, hosts, expected):
    """siteUrl은 공개 주소일 때만 내려 준다. 내부 서버·내 컴퓨터를 사이트로 등록해도 응답에 주소가 나가지 않는다 (#95)."""
    import dataclasses
    from api.sites import public_site_url
    scope = resolve_site(SITE)
    fake = type("Scope", (), {"base_url": variant_url,
                              "config": dataclasses.replace(scope.config, allowed_hosts=tuple(hosts))})()
    assert public_site_url(fake) == expected


def test_site_url_fields_do_not_change_run_rules(client):
    """주소 필드는 찾기용일 뿐, 실행은 여전히 등록된 siteId로만 한다 (POST /api/runs는 주소를 받지 않음)."""
    c, _, _ = client
    r = c.post("/api/runs", json=scenario(siteUrl=BASE))
    assert r.status_code == 422 and r.json()["error"]["code"] == "CONTRACT_VALIDATION_ERROR"
