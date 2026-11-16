"""browser.py 테스트. 모델 없이 쇼핑몰 서버만 띄워서 돈다.

테스트가 Before/After 쇼핑몰을 직접 띄우고 끈다 (개발용 8001/8002와 겹치지 않게 18001/18002).
Playwright가 없으면 건너뛴다.

    .venv/bin/python -m pytest -q tests/test_browser.py
"""
import os
import socket
import subprocess
import sys
import time

import pytest

pytest.importorskip("playwright")

from uxm import browser  # noqa: E402
from uxm.candidates import extract  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTS = {"before": 18001, "after": 18002}


def _wait_port(port, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"shop server on {port} did not start")


@pytest.fixture(scope="module")
def shops():
    procs = []
    for variant, port in PORTS.items():
        cwd = os.path.join(ROOT, "shop", f"moro-{variant}")
        procs.append(subprocess.Popen([sys.executable, "server.py", "--port", str(port)], cwd=cwd,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    try:
        for port in PORTS.values():
            _wait_port(port)
        yield {v: f"http://127.0.0.1:{p}" for v, p in PORTS.items()}
    finally:
        for p in procs:
            p.terminate()
            p.wait(timeout=5)


@pytest.fixture
def session(shops):
    s = browser.open_browser(shops["before"] + "/shop/home")
    yield s
    browser.close_browser(s)


def _candidate(page, label):
    return next(c for c in extract(page)["candidates"] if c["label"] == label)


def _assert_shape(r):
    assert set(r) == {"result", "reason", "executed", "error", "fromPath", "toPath", "startedAt", "finishedAt"}
    assert "+09:00" in r["startedAt"] and "+09:00" in r["finishedAt"]


# ---------------------------------------------------------------- 화면 이름표

def test_page_key_strips_query_and_prefix():
    assert browser.page_key("http://127.0.0.1:8001/shop/orders?testMode=nochange") == "orders"
    assert browser.page_key("http://127.0.0.1:8002/shop/home/") == "home"


def test_page_key_and_signature_same_for_before_after(shops):
    keys = {}
    for variant, base in shops.items():
        s = browser.open_browser(base + "/shop/mypage")
        try:
            keys[variant] = (browser.page_key(s["page"].url), browser.page_signature(s["page"]))
        finally:
            browser.close_browser(s)
    assert keys["before"] == keys["after"] == ("mypage", "mypage_default")


def test_page_signature_test_mode_only_on_its_page(shops):
    """계약: testMode 적용 화면만 {pageKey}_{모드}, 나머지는 _default (sessionStorage로 이동 후에도 유지)."""
    s = browser.open_browser(shops["before"] + "/shop/support?testMode=deadend")
    try:
        page = s["page"]
        assert browser.page_signature(page) == "support_deadend"
        page.evaluate("() => sessionStorage.setItem('testMode', 'deadend')")
        page.goto(shops["before"] + "/shop/home")
        assert browser.page_signature(page) == "home_default"
        page.goto(shops["before"] + "/shop/support")
        assert browser.page_signature(page) == "support_deadend"
    finally:
        browser.close_browser(s)


def test_extract_dom_index_runtime_only(session):
    page = session["page"]
    assert all("domIndex" not in c for c in extract(page)["candidates"])   # 계약 8.1: 기본 출력에 없음
    cands = extract(page, keep_dom_index=True)["candidates"]
    assert cands and all(isinstance(c["domIndex"], int) for c in cands)


# ---------------------------------------------------------------- 클릭 (완료 기준 1)

def test_click_menu_navigates_to_orders(session):
    page = session["page"]
    r = browser.click(page, _candidate(page, "이용정보"))
    _assert_shape(r)
    assert (r["result"], r["reason"], r["executed"]) == ("NAVIGATED", None, True)
    assert (r["fromPath"], r["toPath"]) == ("/shop/home", "/shop/orders")
    assert browser.page_key(page.url) == "orders"


def test_click_moved_element_not_found_and_not_clicked(session):
    page = session["page"]
    c = dict(_candidate(page, "이용정보"))
    c["bbox"] = {**c["bbox"], "x": c["bbox"]["x"] + 300}
    r = browser.click(page, c)
    assert (r["result"], r["reason"], r["executed"]) == ("CLICK_FAILED", "ELEMENT_NOT_FOUND", False)
    assert browser.page_key(page.url) == "home"


def test_click_dangerous_rejected(session):
    page = session["page"]
    c = {**_candidate(page, "이용정보"), "label": "회원 탈퇴"}
    r = browser.click(page, c)
    assert (r["result"], r["reason"], r["executed"]) == ("CLICK_FAILED", "REJECTED_UNSAFE", False)
    assert browser.page_key(page.url) == "home"


def test_click_out_of_scope_href_not_clicked(session):
    page = session["page"]
    c = {**_candidate(page, "이용정보"), "href": "https://example.com/"}
    r = browser.click(page, c)
    assert (r["result"], r["reason"], r["executed"]) == ("CLICK_FAILED", "OUT_OF_SCOPE", False)


def test_click_go_back_candidate_without_bbox_is_not_clicked(session):
    page = session["page"]
    c = {"optionId": "option_06", "label": "이전에 방문한 화면으로 돌아가기", "href": "/shop/home", "bbox": None}
    r = browser.click(page, c)
    assert (r["result"], r["executed"]) == ("CLICK_FAILED", False)


def test_click_no_change(session):
    """눌러도 주소가 안 바뀌는 링크 (testMode=nochange 쿠폰함이 생기기 전 임시 재현)."""
    page = session["page"]
    page.evaluate("""() => { const a = document.createElement('a'); a.href = '/shop/coupons'; a.textContent = '쿠폰함';
        Object.assign(a.style, {position: 'fixed', left: '20px', top: '300px', zIndex: 99999, background: '#fff'});
        a.addEventListener('click', e => e.preventDefault()); document.querySelector('main').append(a); }""")
    r = browser.click(page, _candidate(page, "쿠폰함"), timeout_ms=1000)
    assert (r["result"], r["reason"], r["executed"]) == ("NO_CHANGE", None, True)
    assert r["toPath"] == "/shop/home"


# ---------------------------------------------------------------- 뒤로 가기 (완료 기준 3)

def test_go_back_at_start_has_no_history(session):
    page = session["page"]
    r = browser.go_back(page, "/shop/home")
    _assert_shape(r)
    assert (r["result"], r["reason"]) == ("BACK_FAILED", "NO_HISTORY")
    assert browser.page_key(page.url) == "home"   # 새 탭의 about:blank로 빠지지 않고 시작 화면에 남는다


def test_go_back_returns_to_expected(session):
    page = session["page"]
    assert browser.click(page, _candidate(page, "이용정보"))["result"] == "NAVIGATED"
    r = browser.go_back(page, "/shop/home")
    assert (r["result"], r["reason"], r["toPath"]) == ("NAVIGATED", None, "/shop/home")
    # 돌아온 화면에서 바로 후보를 다시 뽑을 수 있어야 한다
    assert any(c["label"] == "이용정보" for c in extract(page)["candidates"])


def test_go_back_wrong_destination(session):
    page = session["page"]
    browser.click(page, _candidate(page, "이용정보"))
    r = browser.go_back(page, "/shop/support")
    assert (r["result"], r["reason"]) == ("BACK_FAILED", "WRONG_DESTINATION")


def test_reload(session):
    r = browser.reload(session["page"])
    _assert_shape(r)
    assert (r["result"], r["toPath"]) == ("NAVIGATED", "/shop/home")


# ---------------------------------------------------------------- 화면 저장 (완료 기준 4)

def test_capture_with_go_back_candidate(session, tmp_path):
    page = session["page"]
    cands = extract(page)["candidates"]
    back = {"optionId": f"option_{len(cands) + 1:02d}", "label": "이전에 방문한 화면으로 돌아가기", "bbox": None}
    probs = {c["optionId"]: 1 / (len(cands) + 1) for c in cands + [back]}
    out = browser.capture(page, str(tmp_path), 1, cands + [back], probs, cands[0]["optionId"])
    assert out["error"] is None
    assert (out["screenshotRef"], out["overlayRef"]) == ("screenshots/1.png", "screenshots/1_overlay.png")
    assert (tmp_path / "screenshots" / "1.png").exists() and (tmp_path / "screenshots" / "1_overlay.png").exists()
    assert out["screenshotSize"] == {"width": 1440, "height": 900} and out["scroll"] == {"x": 0, "y": 0}
    assert page.locator(".uxm-overlay").count() == 0   # 배지는 지워졌다
    assert extract(page)["candidates"] == cands          # 다음 추출에 영향 없음


def test_capture_failure_returns_error_without_paths(shops, tmp_path):
    s = browser.open_browser(shops["before"] + "/shop/home")
    page = s["page"]
    browser.close_browser(s)   # 닫힌 페이지 → 저장 실패
    out = browser.capture(page, str(tmp_path), 1, [], {}, None)
    assert out["error"] and out["screenshotRef"] is None and out["overlayRef"] is None


def test_click_on_closed_page_is_action_error_not_no_change(shops):
    s = browser.open_browser(shops["before"] + "/shop/home")
    page = s["page"]
    c = _candidate(page, "이용정보")
    browser.close_browser(s)
    r = browser.click(page, c, timeout_ms=1000)
    assert (r["result"], r["reason"]) == ("CLICK_FAILED", "ACTION_ERROR")


def test_capture_without_probs_saves_raw_only(session, tmp_path):
    out = browser.capture(session["page"], str(tmp_path), 9, [], None, None)
    assert out["error"] is None and out["overlayRef"] is None
    assert (tmp_path / "screenshots" / "9.png").exists()
    assert not (tmp_path / "screenshots" / "9_overlay.png").exists()
