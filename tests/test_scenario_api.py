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
                               "goalText": "상품 리뷰", "goalSelectable": True}
    assert pages["home"]["goalText"] is None and pages["brand"]["goalText"] is None
    assert not pages["home"]["goalSelectable"] and not pages["brand"]["goalSelectable"]   # 시작 화면·확인 글자 없음
    assert pages["qna"]["goalSelectable"] is False and pages["qna"]["goalText"] == "Q&A"   # 지도상 홈에서 갈 수 없음
    assert [k for k, p in pages.items() if p["goalSelectable"]] == ["products", "review", "event", "basket"]
    assert site["siteMap"] == {"available": True, "siteMapVersion": "sitemap-cafe24-ons-v1"}
    text = str(site)
    assert "cafe24.com" not in text and "meta" not in site and "board_no" not in text   # 주소·내부 값 노출 안 함


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
