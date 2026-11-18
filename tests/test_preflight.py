"""실행 전 검사를 실제 쇼핑몰·브라우저로 확인한다 (api/preflight.py).

Before 상단 메뉴는 "이용정보", After는 "주문·환불"이라는 두 쇼핑몰의 유일한 차이를 이용해
어느 쪽에서 글자가 없는지 메시지가 맞게 나오는지 본다. 쇼핑몰은 tests/conftest.py의 shops fixture가 띄운다.
"""
import os
import socket
import sys
import threading
import time

import pytest

pytest.importorskip("playwright")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from api import preflight  # noqa: E402
from api.errors import ApiError  # noqa: E402
from api.preflight import PAGE_TIMEOUT_MS, check_goal_screens  # noqa: E402
from uxm.browser import close_browser, open_browser  # noqa: E402

BROWSER = (open_browser, close_browser)


def v(path, text, exact=False, scope="page"):
    return {"type": "PATH_AND_VISIBLE_TEXT", "path": path, "text": text, "exact": exact, "scope": scope}


def check(verification, urls):
    check_goal_screens(verification, urls, BROWSER)


def error_of(verification, urls) -> ApiError:
    return error_of_with(verification, urls, BROWSER)


def error_of_with(verification, urls, browser) -> ApiError:
    with pytest.raises(ApiError) as e:
        check_goal_screens(verification, urls, browser)
    return e.value


def test_goal_text_visible_on_both_shops(shops):
    check(v("/shop/refund", "환불 / 반품 신청"), shops)   # 환불 preset과 같은 조건
    check(v("/shop/orders", "주문 / 배송 조회"), shops)   # orders 제목 (FE 자동 채우기 값)


def test_text_missing_in_before(shops):
    e = error_of(v("/shop/home", "주문·환불"), shops)   # After 상단 메뉴에만 있음
    assert (e.status, e.code) == (422, "CONTRACT_VALIDATION_ERROR")
    assert e.message == "Before 도착 화면(/shop/home)에 '주문·환불' 글자가 없습니다."


def test_text_missing_in_after_with_exact(shops):
    # After 홈에는 "이용정보 · 주문/배송" 카드만 있어 정확 일치로는 없다. Before는 상단 메뉴가 정확히 "이용정보"
    e = error_of(v("/shop/home", "이용정보", exact=True), shops)
    assert e.status == 422
    assert e.message == "After 도착 화면(/shop/home)에 '이용정보' 글자가 없습니다 (정확 일치)."
    check(v("/shop/home", "이용정보"), shops)   # 부분 일치면 둘 다 통과


def test_scope_main_ignores_header(shops):
    check(v("/shop/home", "김모로 님"), shops)   # 상단 헤더에 있음
    e = error_of(v("/shop/home", "김모로 님", scope="main"), shops)
    assert e.status == 422 and "<main> 안" in e.message


def test_unknown_page_rejected_without_browser():
    calls = []
    with pytest.raises(ApiError) as e:
        check_goal_screens(v("/shop/cart", "장바구니"), {"before": "x", "after": "y"},
                           (lambda url, **kw: calls.append(url), lambda s: None))
    assert e.value.status == 422 and "쇼핑몰에 없는 화면" in e.value.message
    assert calls == []


def test_shop_down_is_retryable_503(shops):
    with socket.socket() as s:   # 아무도 듣지 않는 포트
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    e = error_of(v("/shop/refund", "환불 / 반품 신청"), {"before": shops["before"], "after": f"http://127.0.0.1:{port}"})
    assert (e.status, e.code, e.retryable) == (503, "BROWSER_ERROR", True)
    assert e.message == "After 도착 화면을 읽지 못했습니다"


def test_redirected_page_rejected():
    class Page:
        url = "http://127.0.0.1:1/shop/home"   # /shop/refund를 열었는데 홈으로 이동했다고 가정
    closed = []
    with pytest.raises(ApiError) as e:
        check_goal_screens(v("/shop/refund", "환불 / 반품 신청"), {"before": "http://x", "after": "http://y"},
                           (lambda url, **kw: {"page": Page()}, closed.append))
    assert e.value.status == 422 and "Before" in e.value.message and "다른 화면" in e.value.message
    assert len(closed) == 1   # 실패해도 브라우저는 닫는다


def test_timeout_is_retryable_503(monkeypatch):
    """open_browser가 Playwright TimeoutError를 던지면(쇼핑몰이 응답하지 않음) 503 + 재시도 가능."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    seen = []

    def slow_open(url, timeout_ms=None):
        seen.append(timeout_ms)
        raise PlaywrightTimeout("Timeout 10000ms exceeded")
    e = error_of_with(v("/shop/refund", "환불 / 반품 신청"), {"before": "http://x", "after": "http://y"},
                      (slow_open, lambda s: None))
    assert (e.status, e.code, e.retryable) == (503, "BROWSER_ERROR", True)
    assert e.message == "Before 도착 화면을 읽지 못했습니다"
    assert seen == [PAGE_TIMEOUT_MS]   # 실행 전 검사용 짧은 제한을 넘긴다


@pytest.fixture
def silent_server():
    """연결은 받지만 응답을 보내지 않는 서버."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    conns, stop = [], threading.Event()

    def accept():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conns.append(srv.accept()[0])
            except OSError:
                pass
    t = threading.Thread(target=accept, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.getsockname()[1]}"
    stop.set()
    t.join()
    for c in conns:
        c.close()
    srv.close()


def test_unresponsive_shop_times_out_as_503(silent_server, monkeypatch):
    """실제 브라우저로 응답 없는 쇼핑몰을 열면 제한 시간 안에 503 + 재시도 가능으로 끝난다."""
    monkeypatch.setattr(preflight, "PAGE_TIMEOUT_MS", 2000)
    started = time.monotonic()
    e = error_of(v("/shop/refund", "환불 / 반품 신청"), {"before": silent_server, "after": silent_server})
    elapsed = time.monotonic() - started
    assert (e.status, e.code, e.retryable) == (503, "BROWSER_ERROR", True)
    assert e.message == "Before 도착 화면을 읽지 못했습니다"   # Before에서 멈추고 After는 열지 않는다
    assert elapsed < 15   # Playwright 기본 30초를 기다리지 않는다
