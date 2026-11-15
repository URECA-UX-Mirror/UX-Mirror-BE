"""모델 입력 구성. 튜닝 대상은 이 파일에만 둔다 (테스트 시나리오 '튜닝해도 되는 것').

바꿀 때는 한 번에 하나만 바꾸고 PROMPT_VERSION을 올린다.
Before/After에는 항상 같은 버전을 쓴다.
"""

PROMPT_VERSION = "v2"
# v1→v2: GOAL_CHECK_INSTRUCTIONS 문구만 변경 (A10 오탐)
# v3(폐기, 2026-11-15): 후보 설명에 소속 묶음 제목(맥락)을 붙임 → B5 orders '환불 신청하기' 0.119→0.081로 악화. v2로 되돌림.

GOAL = "최근 구매한 상품의 환불 신청 화면 찾기"
PERSONA = "온라인 쇼핑몰 이용 경험이 적고 환불 절차에 익숙하지 않은 사용자"

NEXT_CLICK_INSTRUCTIONS = (
    "현재 화면과 이전 탐색 기록을 참고하여 목표 달성에 가장 적합한 행동을 선택하세요. "
    "주어진 후보 중 하나만 선택하세요."
)

# v1: "현재 화면에서 사용자의 목표를 바로 수행할 수 있습니까?" → A10(orders) P(yes)=0.672 오탐
GOAL_CHECK_INSTRUCTIONS = "현재 화면이 사용자가 찾던 바로 그 화면입니까?"

GO_BACK_LABEL = "[브라우저] 이전 화면으로 돌아가기"


def describe_candidate(region: str, text: str) -> str:
    """후보 설명 형식. 예) [상단 메뉴] 이용정보"""
    return f"[{region}] {text}"


def describe_history(entry: dict) -> str:
    """탐색 이력 표현. 예) 고객센터 방문 → 환불 메뉴 없음 → 뒤로 감"""
    return " → ".join(entry["steps"])
