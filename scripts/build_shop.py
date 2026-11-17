"""Stitch 시안(moro market) → Before/After 쇼핑몰 프로젝트 2개 생성.

    python scripts/build_shop.py [STITCH_DIR]
    python scripts/build_shop.py --label 주문·배송 --out <폴더>   # 고도화 라벨 후보 변형 (P0 사이트는 그대로)

- 원본(Stitch export)은 수정하지 않는다. 두 프로젝트를 같은 패치로 생성해 차이가 메뉴 라벨 하나뿐이도록 보장한다.
- 생성 후 Before/After 페이지를 토큰 단위로 비교해 '이용정보' ↔ '주문·환불' 외 차이가 있으면 실패한다 (B3).

패치 내용 (양쪽 동일):
  1. data-path가 있는 <a>의 href="#"를 실제 주소로 (/shop/home 등). best/new는 페이지가 없어 '#' 유지
     + 의도가 분명한데 목적지가 비어 있던 링크 연결 (ANCHOR_LINKS, TEXT_LINKS)
  2. 페이지마다 <title> 추가 (Before/After 동일, 메뉴 라벨을 쓰지 않음)
  3. (삭제) data-test-goal 마커는 넣지 않는다. 도착 판정은 주소 + 보이는 글자 (uxm/goal.py)
  4. mypage 로그아웃 옆에 '회원 탈퇴' 버튼 (B4 위험 버튼 제외 확인용)
  5. 상단 메뉴 칸 폭 고정 (B2 bbox 동일, 기획안 '메뉴 박스 폭 동일')
  6. testMode 스크립트 (계약 v1.3.5). 첫 URL의 ?testMode=를 sessionStorage에 두고 내부 이동 동안 유지.
     내부 링크는 바꾸지 않고, 모드가 없으면 화면을 건드리지 않는다.
       nochange: mypage 사이드 메뉴 '쿠폰함' → href=/shop/coupons, 눌러도 이동 안 함 (C6, NO_CHANGE)
       deadend:  support '배송 현황 확인' 숨김 (막다른 화면)
"""
import argparse
import difflib
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOP = os.path.join(ROOT, "shop")                       # 생성 결과: shop/moro-before, shop/moro-after
DEFAULT_SRC = os.path.join(SHOP, "stitch_moro_market")  # Stitch 시안 원본 (저장소에 포함)

# 시나리오 pageKey → (Stitch 폴더, <title>)
PAGES = {
    "home": ("home", "MORO MARKET"),
    "mypage": ("my_page", "마이페이지 | MORO MARKET"),
    "support": ("customer_service", "고객센터 | MORO MARKET"),
    "orders": ("order_history", "주문/배송 조회 | MORO MARKET"),
    "refund": ("refund_request", "환불 신청 | MORO MARKET"),
}
DATA_PATH_TO_URL = {
    "home": "/shop/home", "my-page": "/shop/mypage", "customer-service": "/shop/support",
    "order-history": "/shop/orders", "refund-request": "/shop/refund",
}
# data-path가 없지만 의도가 분명한 링크. 페이지 안에 해당 id가 없는(눌러도 아무 일 없는) 앵커만 대상.
# home의 '#order-lookup'은 같은 페이지 배송조회 영역으로 스크롤되는 정상 앵커라 유지.
ANCHOR_LINKS = {
    "home": {"#order-tracking-modal": "/shop/orders",   # '이용정보 · 주문/배송' 카드
             "#order-history": "/shop/orders",          # '내 주문 내역 및 반품 신청 바로가기'
             "#customer-care": "/shop/support"},        # '고객 케어 & 1:1 상담' 카드
}
TEXT_LINKS = {
    "mypage": {"1:1 문의내역": "/shop/support"},
}
VARIANTS = {"before": ("moro-before", ""), "after": ("moro-after", "after_")}
LABEL = {"before": "이용정보", "after": "주문·환불"}


def link_data_paths(html: str) -> str:
    def fix(m):
        tag = m.group(0)
        dp = re.search(r'data-path="([^"]+)"', tag)
        if not dp or dp.group(1) not in DATA_PATH_TO_URL:
            return tag
        url = DATA_PATH_TO_URL[dp.group(1)]
        if 'href="' in tag:
            return re.sub(r'href="[^"]*"', f'href="{url}"', tag, count=1)
        return tag[:-1] + f' href="{url}">'
    return re.sub(r"<a\b[^>]*>", fix, html)


def link_anchors(html: str, mapping: dict) -> str:
    for anchor, url in mapping.items():
        assert f'id="{anchor[1:]}"' not in html, f"{anchor} is a real in-page anchor"
        html, n = re.subn(f'href="{re.escape(anchor)}"', f'href="{url}"', html)
        assert n >= 1, f"{anchor} not found"
    return html


def link_by_text(html: str, mapping: dict) -> str:
    for text, url in mapping.items():
        pat = re.compile(r'(<a\b[^>]*?)href="#"([^>]*>(?:(?!</a>).)*?' + re.escape(text) + r")", re.S)
        html, n = pat.subn(lambda m: f'{m.group(1)}href="{url}"{m.group(2)}', html, count=1)
        assert n == 1, f"link text {text!r} not found"
    return html


# B2 / 기획안 layoutMatched: 상단 메뉴 칸 폭을 라벨 길이와 무관하게 고정해 Before/After bbox를 같게 한다.
NAV_ITEM_WIDTH_PX = 84
NAV_FIXED_WIDTH_CSS = (
    "<style id=\"uxm-nav-fixed-width\">"
    "header nav[data-active-classes] > a{display:inline-block;box-sizing:border-box;"
    f"width:{NAV_ITEM_WIDTH_PX}px;text-align:center;white-space:nowrap;}}"
    "</style>"
)


def fix_nav_width(html: str) -> str:
    assert "header" in html and "nav" in html
    return html.replace("</head>", NAV_FIXED_WIDTH_CSS + "</head>", 1)


TEST_MODE_JS = r"""<script id="uxm-test-mode">
(() => {
  const q = new URLSearchParams(location.search).get('testMode');
  if (q) sessionStorage.setItem('testMode', q);
  const mode = sessionStorage.getItem('testMode');
  const path = location.pathname.replace(/\/$/, '');
  const find = (sel, text) => [...document.querySelectorAll(sel)].find(a => a.textContent.includes(text));
  if (mode === 'nochange' && path === '/shop/mypage') {
    const a = find('aside a[href="#"]', '쿠폰함');
    a.setAttribute('href', '/shop/coupons');
    a.addEventListener('click', e => e.preventDefault());
  }
  if (mode === 'deadend' && path === '/shop/support') {
    find('a[href="/shop/orders"]', '배송 현황 확인').style.display = 'none';
  }
})();
</script>"""
TEST_MODE_TARGETS = {"mypage": "쿠폰함", "support": "배송 현황 확인"}   # 스크립트가 찾는 글자가 페이지에 있어야 한다


def add_test_mode(html: str, key: str) -> str:
    assert TEST_MODE_TARGETS.get(key, "") in html, f"testMode target missing on {key}"
    return html.replace("</body>", TEST_MODE_JS + "</body>", 1)


def add_title(html: str, title: str) -> str:
    assert "<title>" not in html
    return html.replace("</head>", f"<title>{title}</title></head>", 1)


def add_withdraw_button(html: str) -> str:
    m = re.search(r"<button\b([^>]*)>(?:(?!</button>).)*?로그아웃(?:(?!</button>).)*?</button>", html, re.S)
    assert m, "mypage logout button not found"
    attrs = m.group(1)
    withdraw = (f'<button{attrs}><span class="material-symbols-outlined text-[18px]">person_remove</span>'
                f' 회원 탈퇴</button>')
    return html[:m.end()] + withdraw + html[m.end():]


def build_page(src_dir: str, prefix: str, key: str) -> str:
    folder, title = PAGES[key]
    html = open(os.path.join(src_dir, prefix + folder, "code.html"), encoding="utf-8").read()
    html = link_data_paths(html)
    html = link_anchors(html, ANCHOR_LINKS.get(key, {}))
    html = link_by_text(html, TEXT_LINKS.get(key, {}))
    html = add_title(html, title)
    html = fix_nav_width(html)
    if key == "mypage":
        html = add_withdraw_button(html)
    html = add_test_mode(html, key)
    return html


NAV_RE = re.compile(r"<nav\b[^>]*data-active-classes[^>]*>.*?</nav>", re.S)   # 상단 메뉴 (fix_nav_width와 같은 대상)


def relabel_nav(html: str, label: str) -> str:
    """상단 메뉴의 order-history 링크 글자만 바꾼다. 홈 카드·사이드 메뉴의 '이용정보'는 그대로 둔다."""
    navs = NAV_RE.findall(html)
    assert len(navs) == 1, "header nav not found"
    pat = re.compile(r'(<a\b[^>]*data-path="order-history"[^>]*>)' + re.escape(LABEL["before"]) + "(</a>)")
    new_nav, n = pat.subn(lambda m: m.group(1) + label + m.group(2), navs[0])
    assert n == 1, "order-history nav label not found"
    return html.replace(navs[0], new_nav, 1)


def token_diff(a: str, b: str):
    ta, tb = re.findall(r"<[^>]+>|[^<]+", a), re.findall(r"<[^>]+>|[^<]+", b)
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    return [("".join(ta[i1:i2]), "".join(tb[j1:j2])) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]


def write_project(proj_dir: str, pages: dict) -> None:
    out = os.path.join(proj_dir, "pages")
    os.makedirs(out, exist_ok=True)
    for key, html in pages.items():
        with open(os.path.join(out, f"{key}.html"), "w", encoding="utf-8", newline="\n") as f:
            f.write(html)
    shutil.copyfile(os.path.join(SHOP, "server.py"), os.path.join(proj_dir, "server.py"))
    print(f"[build] {proj_dir}: {len(pages)} pages")


def parity_ok(base: dict, other: dict, label: str) -> bool:
    """모든 페이지에서 base 대비 차이가 상단 메뉴 라벨 하나뿐인지 (라벨이 같으면 차이 없음)."""
    expected = [] if label == LABEL["before"] else [(LABEL["before"], label)]
    ok = True
    for key in PAGES:
        d = token_diff(base[key], other[key])
        status = "OK" if [(x.strip(), y.strip()) for x, y in d] == expected else "MISMATCH"
        ok &= status == "OK"
        print(f"[parity] {key:8} {status}  {d[:3]}")
    return ok


def build_variant(src: str, label: str, out: str, force: bool = False) -> None:
    """기획안 v1.3 4.4: before 소스로 빌드한 뒤 상단 메뉴 라벨 하나만 바꾼 사이트를 out에 만든다.

    이미 만든 변형은 siteInstanceId와 연결돼 평가에 쓰이므로, 비어 있지 않은 out은 force 없이는 덮어쓰지 않는다.
    """
    out = os.path.realpath(out)   # 심볼릭 링크로 P0 폴더를 가리키는 우회 방지
    if out in {os.path.realpath(os.path.join(SHOP, proj)) for proj, _ in VARIANTS.values()}:
        sys.exit(f"{out}: P0 Before/After 사이트는 덮어쓰지 않는다")
    if os.path.isdir(out) and os.listdir(out) and not force:
        sys.exit(f"{out}: 이미 내용이 있다. 다시 만들려면 --force")
    base = {key: build_page(src, "", key) for key in PAGES}
    pages = {key: relabel_nav(html, label) for key, html in base.items()}
    if not parity_ok(base, pages, label):
        sys.exit(f"'{label}' 변형의 차이가 메뉴 라벨 하나가 아님 → 사용 금지")
    write_project(out, pages)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("src", nargs="?", default=DEFAULT_SRC, help="Stitch 시안 폴더")
    ap.add_argument("--label", help="상단 주문 메뉴 라벨. 주면 --out에 변형 사이트 하나만 만든다")
    ap.add_argument("--out", help="--label 변형 사이트를 만들 폴더")
    ap.add_argument("--force", action="store_true", help="--out에 내용이 있어도 다시 만든다")
    args = ap.parse_args()
    if bool(args.label) != bool(args.out):
        ap.error("--label과 --out은 같이 써야 한다")
    if args.label:
        build_variant(args.src, args.label, args.out, args.force)
        return

    built = {v: {key: build_page(args.src, prefix, key) for key in PAGES} for v, (_, prefix) in VARIANTS.items()}
    for v, (proj, _) in VARIANTS.items():
        write_project(os.path.join(SHOP, proj), built[v])
    if not parity_ok(built["before"], built["after"], LABEL["after"]):
        sys.exit("Before/After 차이가 메뉴 라벨 하나가 아님 → 실행 금지 (B3)")


if __name__ == "__main__":
    main()
