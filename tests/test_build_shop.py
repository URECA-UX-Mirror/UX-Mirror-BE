"""build_shop.py 라벨 변형 (기획안 v1.3 4.4). 브라우저 없이 HTML만 비교한다."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build_shop as bs  # noqa: E402

LABELS = ["이용정보", "주문·배송", "주문·환불", "주문·반품", "주문 관리"]   # C0~C4


@pytest.fixture(scope="module")
def base():
    return {key: bs.build_page(bs.DEFAULT_SRC, "", key) for key in bs.PAGES}


@pytest.mark.parametrize("label", LABELS)
def test_variant_differs_only_in_nav_label(base, label):
    pages = {key: bs.relabel_nav(html, label) for key, html in base.items()}
    assert bs.parity_ok(base, pages, label)


def test_home_card_and_side_menu_keep_original_label(base):
    home = bs.relabel_nav(base["home"], "주문 관리")
    assert "이용정보 · 주문/배송" in home                       # 홈 카드 문구는 그대로
    refund = bs.relabel_nav(base["refund"], "주문 관리")
    assert "이용정보 (주문내역)" in refund                       # 사이드 메뉴도 그대로


def test_c2_variant_matches_p0_after(base):
    """After는 Before 원본 + 이름 변경으로 만든다. 저장소의 생성 결과(moro-after)와 같아야 한다."""
    for key, html in base.items():
        with open(os.path.join(bs.SHOP, "moro-after", "pages", f"{key}.html"), encoding="utf-8", newline="") as f:
            assert bs.relabel_nav(html, "주문·환불") == f.read(), key


@pytest.mark.parametrize("proj", ["moro-before", "moro-after"])
def test_refuses_to_overwrite_p0_sites(proj):
    with pytest.raises(SystemExit):
        bs.build_variant(bs.DEFAULT_SRC, "주문·배송", os.path.join(bs.SHOP, proj))


@pytest.mark.parametrize("proj", ["moro-before", "moro-after"])
def test_refuses_symlink_to_p0_sites(tmp_path, proj):
    link = tmp_path / "link"
    link.symlink_to(os.path.join(bs.SHOP, proj))
    with pytest.raises(SystemExit):
        bs.build_variant(bs.DEFAULT_SRC, "주문·배송", str(link), force=True)


def test_refuses_existing_output_without_force(tmp_path):
    out = tmp_path / "C1"
    bs.build_variant(bs.DEFAULT_SRC, "주문·배송", str(out))
    with pytest.raises(SystemExit):
        bs.build_variant(bs.DEFAULT_SRC, "주문·반품", str(out))
    assert "주문·배송" in (out / "pages" / "home.html").read_text(encoding="utf-8")   # 기존 C1 그대로
    bs.build_variant(bs.DEFAULT_SRC, "주문·반품", str(out), force=True)
    assert "주문·반품" in (out / "pages" / "home.html").read_text(encoding="utf-8")


def test_writes_variant_project(tmp_path):
    bs.build_variant(bs.DEFAULT_SRC, "주문·배송", str(tmp_path / "C1"))
    assert sorted(os.listdir(tmp_path / "C1" / "pages")) == sorted(f"{k}.html" for k in bs.PAGES)
    assert (tmp_path / "C1" / "server.py").exists()
