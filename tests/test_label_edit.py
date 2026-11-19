"""uxm/label_edit (#104): 수정 A — 상단 메뉴 이용정보 버튼 글자만 바꾸기."""
import pytest

pytest.importorskip("playwright")

from uxm import browser  # noqa: E402
from uxm.candidates import extract  # noqa: E402
from uxm.label_edit import EDITABLE_KEY, apply_label, normalize_label  # noqa: E402

PAGES = ["home", "orders", "refund", "support", "mypage"]
NAV_JS = """() => [...document.querySelectorAll('header nav[data-active-classes] a')].map(a => a.textContent.trim())"""
OUTSIDE_JS = """() => [...document.querySelectorAll('a[data-path="order-history"]')]
    .filter(a => !a.closest('header')).map(a => a.textContent.trim())"""


@pytest.fixture
def page(shops):
    s = browser.open_browser(shops["before"] + "/shop/home")
    try:
        yield s["page"]
    finally:
        browser.close_browser(s)


def test_normalize_label_matches_candidate_label_rule():
    assert normalize_label("  주문·환불 ") == "주문·환불"
    assert normalize_label("주문   관리") == "주문 관리"


@pytest.mark.parametrize("key", ["상단 메뉴>/shop/support#1", "상단 메뉴>/shop/mypage#1",
                                 "사이드 메뉴>/shop/orders#1", "상단 메뉴>/shop/orders#2", ""])
def test_only_editable_key_is_accepted(page, key):
    before = page.evaluate(NAV_JS)
    with pytest.raises(ValueError):
        apply_label(page, key, "주문·환불")
    assert page.evaluate(NAV_JS) == before   # 아무것도 바꾸지 않았다


@pytest.mark.parametrize("label", ["", "   ", None])
def test_empty_label_is_rejected(page, label):
    with pytest.raises(ValueError):
        apply_label(page, EDITABLE_KEY, label)


def test_missing_button_raises(page):
    page.set_content("<header><nav data-active-classes='x'><a href='/shop/home'>홈</a></nav></header>")
    with pytest.raises(ValueError):
        apply_label(page, EDITABLE_KEY, "주문·환불")


@pytest.mark.parametrize("key", PAGES)
def test_applies_only_top_nav_label_on_every_page(shops, page, key):
    """B(relabel_nav)와 같게 모든 화면의 상단 메뉴만 바꾸고, 본문·사이드 메뉴의 같은 링크는 그대로 둔다."""
    page.goto(shops["before"] + f"/shop/{key}")
    nav, outside = page.evaluate(NAV_JS), page.evaluate(OUTSIDE_JS)
    apply_label(page, EDITABLE_KEY, "  주문·환불 ")
    assert page.evaluate(NAV_JS) == [("주문·환불" if t == "이용정보" else t) for t in nav]
    assert page.evaluate(OUTSIDE_JS) == outside


def test_extracted_candidate_has_new_label_and_same_key(shops, page):
    """semanticKey는 라벨과 무관하다 — 바꾼 뒤에도 같은 키로 새 이름이 후보에 나온다."""
    base = {c["semanticKey"]: c for c in extract(page)["candidates"]}
    apply_label(page, EDITABLE_KEY, "주문  관리")
    after = {c["semanticKey"]: c for c in extract(page)["candidates"]}
    assert after.keys() == base.keys()
    assert after[EDITABLE_KEY]["label"] == "주문 관리"
    assert all(after[k]["label"] == base[k]["label"] for k in base if k != EDITABLE_KEY)


def test_label_reverts_after_navigation_so_loop_must_reapply(shops, page):
    apply_label(page, EDITABLE_KEY, "주문·환불")
    page.goto(shops["before"] + "/shop/home")
    assert "이용정보" in page.evaluate(NAV_JS)
