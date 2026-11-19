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


# ---- check_layout: 이름 외에는 v1 기준과 같은지 (A·B 공통)
import os  # noqa: E402
import socket  # noqa: E402
import sys  # noqa: E402

from api.site_instances import snapshot_from_extracted  # noqa: E402
from uxm.candidates import VIEWPORT  # noqa: E402
from uxm.label_edit import check_layout  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import check_site_instances as chk  # noqa: E402

LABELS = {"C1": "주문·배송", "C2": "주문·환불", "C3": "주문·반품", "C4": "주문 관리"}
OVERFLOW_LABEL = "교환반품환불신청"   # 14px 한글 8자 = 96px > 84px 칸


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def variant_sites():
    """B: 이미 만들어 둔 변형 사이트 C1~C4 (build_shop.py --label로 생성, parity_ok 통과본)."""
    targets = [(cid, os.path.join(ROOT, "shop", chk.SITE_FOLDERS[cid], "server.py"), "127.0.0.1", _free_port())
               for cid in LABELS]
    procs = []
    try:
        failed = chk.start_servers(targets, procs)
        assert failed == {}
        yield {cid: f"http://127.0.0.1:{port}" for cid, _, _, port in targets}
    finally:
        chk.stop_servers(procs)


@pytest.fixture(scope="module")
def baseline(shops):
    s = browser.open_browser(shops["before"] + "/shop/home")
    try:
        return snapshot_from_extracted(extract(s["page"]), "/shop/home", dict(VIEWPORT), "candidate-v2")
    finally:
        browser.close_browser(s)


def _check(url, baseline, label, apply=None):
    """url 화면을 열어 (apply가 있으면 A로 이름을 바꾼 뒤) 검사. (결과, 대상 버튼 bbox)"""
    s = browser.open_browser(url + "/shop/home")
    try:
        page = s["page"]
        if apply is not None:
            apply_label(page, EDITABLE_KEY, apply)
        target = next(c for c in extract(page)["candidates"] if c["semanticKey"] == EDITABLE_KEY)
        return check_layout(baseline, page, EDITABLE_KEY, label), target["bbox"]
    finally:
        browser.close_browser(s)


def test_v1_itself_is_valid(shops, baseline):
    assert _check(shops["before"], baseline, "이용정보")[0] == {"valid": True, "reasons": []}


def test_label_not_applied_is_caught(shops, baseline):
    """이름이 실제로 안 바뀌면 v1 그대로 통과하지 않는다 (label 인자가 필요한 이유)."""
    assert _check(shops["before"], baseline, "주문·환불")[0] == {"valid": False, "reasons": ["TARGET_LABEL_MISMATCH"]}


@pytest.mark.parametrize("cid", sorted(LABELS))
def test_a_and_b_give_same_result(shops, baseline, variant_sites, cid):
    """완료 조건: C1~C4 이름으로 A(v1에 글자 바꾸기)와 B(실제 변형 사이트)의 검사 결과·대상 위치가 같다."""
    label = LABELS[cid]
    a = _check(shops["before"], baseline, label, apply=label)
    b = _check(variant_sites[cid], baseline, label)
    assert a[0] == b[0] == {"valid": True, "reasons": []}
    assert a[1] == b[1]


def test_overflowing_label_is_rejected(shops, baseline):
    """완료 조건: 84px 칸을 넘는 이름은 bbox가 그대로라도 LABEL_OVERFLOW로 탈락."""
    result, bbox = _check(shops["before"], baseline, OVERFLOW_LABEL, apply=OVERFLOW_LABEL)
    assert result == {"valid": False, "reasons": ["LABEL_OVERFLOW"]}
    assert bbox == next(c["bbox"] for c in baseline["candidates"] if c["semanticKey"] == EDITABLE_KEY)


def test_label_whitespace_does_not_cause_mismatch(shops, baseline):
    assert _check(shops["before"], baseline, " 주문  관리 ", apply="주문 관리")[0] == {"valid": True, "reasons": []}


def test_viewport_mismatch_is_reported_alone(shops, baseline):
    s = browser.open_browser(shops["before"] + "/shop/home")
    try:
        page = s["page"]
        page.set_viewport_size({"width": 1280, "height": 800})
        apply_label(page, EDITABLE_KEY, "주문·환불")
        assert check_layout(baseline, page, EDITABLE_KEY, "주문·환불") == {"valid": False, "reasons": ["VIEWPORT_MISMATCH"]}
    finally:
        browser.close_browser(s)


def test_check_layout_rejects_other_keys(shops, baseline, page):
    with pytest.raises(ValueError):
        check_layout(baseline, page, "상단 메뉴>/shop/support#1", "고객센터")
