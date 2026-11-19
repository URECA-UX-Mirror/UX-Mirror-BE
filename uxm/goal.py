"""목표 도달 판정 (기획안 7.2.2, 10.3.10). 마커 없이 '주소 + 화면에 보이는 글자'로 판정한다.

- 성공 조건은 목표마다 지정한다: 주소(path)가 일치하고, 지정한 글자가 화면에 실제로 보일 것.
  사이트를 고칠 필요가 없어 실제 사이트에도 쓸 수 있다. URL만으로는 성공 처리하지 않는다.
- 성공 조건은 채점 전용. 모델 입력에는 목표 문구(prompts.GOAL)만 들어간다.
- DOM_ONLY: 모델 YES/NO를 호출하지 않는다. modelGoalReached=None.
- MODEL_AND_DOM: 모델 YES/NO도 호출해 기록하고, 화면 판정과 다르면 goalJudgmentMismatch=True.
- 성공 여부(goalReached)는 언제나 화면 판정 결과를 따른다. 모델 YES만으로 성공 처리하지 않는다.

변경 이력
- G0-A(2026-11-15): A9 PASS, A10 FAIL(orders 화면 오탐) → DOM_ONLY 확정.
- 2026-11-15: data-test-goal 마커 방식 → 주소 + 보이는 글자 방식 (실제 사이트 적용성, 팀 공유 필요)
"""
from urllib.parse import urlsplit

# 목표별 성공 조건. 화면 입력(대시보드)으로 받게 되면 이 형식으로 넘긴다.
# 계약 2.3 `verification`에서 type(PATH_AND_VISIBLE_TEXT 고정)만 뺀 모양.
GOALS = {
    "refund": {
        "path": "/shop/refund",
        "text": "환불 / 반품 신청",   # refund 화면 제목(h1). mypage에도 같은 글자의 링크가 있어 주소 조건이 함께 필요
        "exact": False,
        "scope": "page",
    },
}
DEFAULT_GOAL_ID = "refund"

# exact: 글자 정확 일치 여부(false면 부분 일치). scope: 글자를 찾는 범위(page = 화면 전체, main = <main> 안)
DEFAULT_MATCH = {"exact": False, "scope": "page"}
SCOPES = ("page", "main")


def _with_defaults(success: dict) -> dict:
    full = {**DEFAULT_MATCH, **success}
    if full["scope"] not in SCOPES:
        raise ValueError(f"unknown verification scope {full['scope']!r} (page/main)")
    return full


def success_condition(goal_preset: dict) -> dict:
    """목표 preset(`config/presets.json`)의 `verification`을 `verify_goal`이 받는 `{"path", "text", "exact", "scope"}`로 바꾼다.

    실행 API(`api/presets.py`)와 루프 설정(`uxm/agent.py` `agent_settings`)이 같이 쓴다.
    exact·scope가 없으면 기본값(exact=false, scope=page).
    """
    v = goal_preset["verification"]
    return _with_defaults({k: v[k] for k in ("path", "text", "exact", "scope") if k in v})


def path_matches(url: str, path: str) -> bool:
    return urlsplit(url).path.rstrip("/") == path.rstrip("/")


def verify_goal(page, success: dict | None = None) -> tuple[bool, dict]:
    """Playwright Page가 성공 조건을 만족하는지. (도착 여부, 근거)

    같은 글자를 가진 요소가 여러 개면 모두 확인해서 하나라도 보이면 글자 조건 통과 (계약 2.3).
    """
    success = _with_defaults(success or GOALS[DEFAULT_GOAL_ID])
    path_ok = path_matches(page.url, success["path"])
    root = page.locator("main") if success["scope"] == "main" else page
    loc = root.get_by_text(success["text"], exact=success["exact"])
    text_ok = any(loc.nth(i).is_visible() for i in range(loc.count()))
    return path_ok and text_ok, {"pathMatched": path_ok, "textVisible": text_ok, "successCondition": success}


def judge_goal(mode: str, verified: bool, model_yes_prob: float | None = None) -> dict:
    """한 화면의 목표 판정 레코드.

    mode=DOM_ONLY이면 model_yes_prob는 받지 않는다(호출 자체를 하지 않아야 한다).
    mode=MODEL_AND_DOM이면 호출한 모델의 P(yes)를 넘긴다.
    """
    if mode == "DOM_ONLY":
        if model_yes_prob is not None:
            raise ValueError("DOM_ONLY에서는 모델 YES/NO를 호출하지 않는다")
        model = None
    elif mode == "MODEL_AND_DOM":
        if model_yes_prob is None:
            raise ValueError("MODEL_AND_DOM에는 모델 P(yes)가 필요하다")
        model = model_yes_prob >= 0.5
    else:
        raise ValueError(f"unknown goalJudgmentMode {mode!r}")

    return {
        "goalJudgmentMode": mode,
        "modelGoalReached": model,
        "modelGoalYesProb": model_yes_prob,
        "verifiedGoalReached": verified,
        "goalJudgmentMismatch": model is not None and model != verified,
        "goalReached": verified,   # 항상 화면 판정 결과와 일치
    }


def should_call_model(mode: str) -> bool:
    return mode == "MODEL_AND_DOM"
