"""테스트 시나리오 A (G0-A, 브라우저 없이 하드코딩 요청).

각 후보의 semanticKey는 비교용 백엔드 전용이며 모델 요청에는 들어가지 않는다.
"""
from . import prompts as P

HOME_BEFORE = [
    ("상단 메뉴", "이용정보", "menu_orders"),
    ("상단 메뉴", "고객센터", "menu_support"),
    ("상단 메뉴", "마이페이지", "menu_mypage"),
]
HOME_AFTER = [
    ("상단 메뉴", "주문·환불", "menu_orders"),
    ("상단 메뉴", "고객센터", "menu_support"),
    ("상단 메뉴", "마이페이지", "menu_mypage"),
]
ORDERS = [
    ("본문", "환불 신청", "btn_refund"),
    ("본문", "배송 조회", "btn_tracking"),
    ("상단 메뉴", "홈", "menu_home"),
]

SCENARIOS = {
    "A1": dict(kind="choice", title="명확한 화면 (orders)", page=("주문내역", "/shop/orders"),
               candidates=ORDERS, expect_top="btn_refund"),
    "A2": dict(kind="choice", title="모호한 화면 (home, Before)", page=("쇼핑몰 홈", "/shop/home"),
               candidates=HOME_BEFORE),
    "A3": dict(kind="choice", title="같은 화면 (home, After)", page=("쇼핑몰 홈", "/shop/home"),
               candidates=HOME_AFTER),
    "A4": dict(kind="choice", title="무관한 메뉴만 있음", page=("쇼핑몰 홈", "/shop/home"),
               candidates=[("상단 메뉴", "신상품", "menu_new"), ("상단 메뉴", "기획전", "menu_event"),
                           ("상단 메뉴", "브랜드", "menu_brand")]),
    "A5": dict(kind="choice", title="A2 후보 순서 변경", page=("쇼핑몰 홈", "/shop/home"),
               candidates=[HOME_BEFORE[2], HOME_BEFORE[0], HOME_BEFORE[1]], compare_with="A2"),
    "A6": dict(kind="choice", title="A2 3회 반복", page=("쇼핑몰 홈", "/shop/home"),
               candidates=HOME_BEFORE, repeat=3),
    "A7": dict(kind="choice", title="막다른 화면 (support)", page=("고객센터", "/shop/support"),
               page_text="환불은 주문내역에서 신청할 수 있습니다.",
               candidates=[("본문", "홈으로", "menu_home")], go_back=True,
               history=[{"steps": ["홈", "고객센터 방문"]}]),
    "A8": dict(kind="choice", title="A2 + 실패 이력", page=("쇼핑몰 홈", "/shop/home"),
               candidates=HOME_BEFORE, compare_with="A2",
               failed=[{"steps": ["고객센터 방문", "환불 메뉴 없음", "뒤로 감"]}]),
    "A9": dict(kind="yesno", title="목표 판단 YES (refund)", page=("환불 신청", "/shop/refund"),
               page_text="환불 신청\n환불할 주문을 선택하고 사유를 입력한 뒤 신청하세요.\n주문번호 20261101-0042 무선 이어폰",
               expect="yes"),
    "A10": dict(kind="yesno", title="목표 판단 NO (orders)", page=("주문내역", "/shop/orders"),
                page_text="주문내역\n주문번호 20261101-0042 무선 이어폰 배송완료\n환불 신청 · 배송 조회",
                expect="no"),
}


def build_request(sid: str) -> tuple[dict, dict]:
    """(모델 요청 payload, 백엔드 전용 메타) 반환."""
    s = SCENARIOS[sid]
    title, path = s["page"]
    state = {
        "pageTitle": title,
        "currentPath": path,
        "goal": P.GOAL,
        "persona": P.PERSONA,
        "recentActions": [P.describe_history(h) for h in s.get("history", [])],
        "previousFailedActions": [P.describe_history(h) for h in s.get("failed", [])],
    }
    if s.get("page_text"):
        state["visibleText"] = s["page_text"]

    meta = {"scenario": sid, "promptVersion": P.PROMPT_VERSION, "optionMap": {}, "backOptionId": None}

    if s["kind"] == "yesno":
        q = {"goal_reached": {"type": "noul", "instructions": P.GOAL_CHECK_INSTRUCTIONS}}
        return {"state": state, "questions": q}, meta

    criteria = {}
    for i, (region, text, skey) in enumerate(s["candidates"], 1):
        oid = f"option_{i:02d}"
        criteria[oid] = P.describe_candidate(region, text)
        meta["optionMap"][oid] = skey
    if s.get("go_back"):
        oid = f"option_{len(criteria) + 1:02d}"
        criteria[oid] = P.GO_BACK_LABEL
        meta["optionMap"][oid] = "GO_BACK"
        meta["backOptionId"] = oid
    q = {"next_click": {"type": "choice", "instructions": P.NEXT_CLICK_INSTRUCTIONS, "criteria": criteria}}
    return {"state": state, "questions": q}, meta
