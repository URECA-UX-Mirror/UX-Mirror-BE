"""자동 개선 수정 A: 열린 화면에서 상단 메뉴 버튼 글자만 바꾼다 (설계 v2.1 §4.3, #104).

후보 걸러 내기 전용이다. 발표 수치·실제 Run은 실제 변형 사이트(B, `scripts/build_shop.py --label`)만 쓴다.
"""

# 1단계에서 바꿀 수 있는 버튼은 이용정보 하나다. B 생성기(`relabel_nav`·`parity_ok`)가 이 버튼만 바꾸기 때문
EDITABLE_KEY = "상단 메뉴>/shop/orders#1"
# relabel_nav와 같은 대상: 상단 메뉴(header nav[data-active-classes])의 order-history 링크
_NAV_LINK = 'header nav[data-active-classes] a[data-path="order-history"][href="/shop/orders"]'

_APPLY_JS = """([sel, label]) => {
  const found = document.querySelectorAll(sel);
  if (found.length !== 1) return found.length;
  found[0].textContent = label;
  return 1;
}"""


def normalize_label(label: str) -> str:
    """후보 추출(`uxm/candidates.py` labelOf)과 같은 공백 정리: 앞뒤 공백 제거, 연속 공백 1칸."""
    return " ".join(label.split())


def apply_label(page, semantic_key: str, label: str) -> None:
    """열린 화면의 상단 메뉴 이용정보 버튼 글자를 `label`로 바꾼다. 화면을 열 때마다 다시 불러야 한다.

    바꿀 수 없으면 ValueError — 루프는 그 후보를 INVALID_CANDIDATE(LABEL_NOT_APPLIED)로 처리한다.
    """
    if semantic_key != EDITABLE_KEY:
        raise ValueError(f"1단계에서 바꿀 수 없는 버튼: {semantic_key}")
    new = normalize_label(label or "")
    if not new:
        raise ValueError("빈 이름")
    n = page.evaluate(_APPLY_JS, [_NAV_LINK, new])
    if n != 1:
        raise ValueError(f"상단 메뉴 이용정보 버튼이 {n}개 ({page.url})")
