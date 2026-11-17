"""에이전트 루프 테스트 (#7). 서버·브라우저·모델 없이 돈다.

가짜 쇼핑몰(FakePage)과 가짜 모델(MockBackend)로 테스트 시나리오 C0-MOCK, C4~C7, C9~C11 상황을 만든다.
실제 브라우저로 도는 확인은 scripts/run_c.py로 따로 한다.
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from api import run_factory  # noqa: E402
from api.presets import Presets, success_condition  # noqa: E402
from uxm.agent import Env, agent_settings, run_agent  # noqa: E402
from uxm.mock import MockBackend  # noqa: E402
from uxm.storage import RunStore, StorageError  # noqa: E402

BASE = "http://127.0.0.1:8001"
# 실제 쇼핑몰과 같은 영역·목적지 (후보 수는 줄임). support는 deadend처럼 후보 0개
LINKS = {
    "/shop/home": [("상단 메뉴", "이용정보", "/shop/orders"), ("상단 메뉴", "고객센터", "/shop/support"),
                   ("상단 메뉴", "마이페이지", "/shop/mypage")],
    "/shop/mypage": [("경로 표시", "홈", "/shop/home"), ("사이드 메뉴", "주문/배송 조회", "/shop/orders"),
                     ("사이드 메뉴", "쿠폰함", "/shop/coupons")],
    "/shop/orders": [("경로 표시", "홈", "/shop/home"), ("본문", "환불 신청하기", "/shop/refund")],
    "/shop/support": [],
    "/shop/refund": [("경로 표시", "홈", "/shop/home")],
}
TITLES = {"/shop/home": "MORO MARKET", "/shop/mypage": "마이페이지 | MORO MARKET",
          "/shop/orders": "주문/배송 조회 | MORO MARKET", "/shop/support": "고객센터 | MORO MARKET",
          "/shop/refund": "환불 신청 | MORO MARKET"}

HOME_ORDERS, HOME_SUPPORT, HOME_MYPAGE = "상단 메뉴>/shop/orders#1", "상단 메뉴>/shop/support#1", "상단 메뉴>/shop/mypage#1"
CRUMB_HOME, MYPAGE_ORDERS, COUPON = "경로 표시>/shop/home#1", "사이드 메뉴>/shop/orders#1", "사이드 메뉴>/shop/coupons#1"
REFUND = "본문>/shop/refund#1"


class FakePage:
    def __init__(self, start: str, behavior: dict | None = None, reload_ok: bool = True, title_override=None):
        self.history, self.i = [start], 0
        self.behavior = behavior or {}      # href → nochange / timeout / outside / error
        self.reload_ok = reload_ok
        self.title_override = title_override

    @property
    def path(self):
        return self.history[self.i]

    @property
    def url(self):
        return BASE + self.path

    def title(self):
        return self.title_override or TITLES.get(self.path, "MORO MARKET")

    def go(self, path):
        self.history = self.history[:self.i + 1] + [path]
        self.i += 1


def _res(result, page, from_path, reason=None, executed=True):
    return {"result": result, "reason": reason, "executed": executed, "error": None, "fromPath": from_path,
            "toPath": page.path, "startedAt": "t", "finishedAt": "t"}


def fake_env(testmode_page=None) -> Env:
    def extract(page):
        cands, seen = [], {}
        for region, label, href in LINKS.get(page.path, []):
            base = f"{region}>{href}"
            seen[base] = seen.get(base, 0) + 1
            cands.append({"optionId": f"option_{len(cands) + 1:02d}", "label": label, "region": region,
                          "context": None, "href": href, "domIndex": 7,
                          "bbox": {"x": 10, "y": 10 * len(cands) + 10, "width": 80, "height": 20},
                          "semanticKey": f"{base}#{seen[base]}"})
        return {"url": page.url, "title": page.title(), "candidates": cands,
                "excluded": [{"label": "회원 탈퇴", "region": "사이드 메뉴", "reason": "DANGEROUS_ACTION"}]}

    def click(page, cand, timeout_ms=10000):
        start, kind = page.path, page.behavior.get(cand["href"])
        if kind == "nochange":
            return _res("NO_CHANGE", page, start)
        if kind == "error":
            return _res("CLICK_FAILED", page, start, "ACTION_ERROR", executed=False)
        if kind == "outside":
            page.go("/elsewhere")
            return _res("CLICK_FAILED", page, start, "OUT_OF_SCOPE")
        page.go(cand["href"])
        if kind == "timeout":
            return _res("CLICK_FAILED", page, start, "PAGE_TIMEOUT")
        return _res("NAVIGATED", page, start)

    def go_back(page, expected_path, timeout_ms=10000):
        start = page.path
        if page.i == 0:
            return _res("BACK_FAILED", page, start, "NO_HISTORY")
        page.i -= 1
        if page.path != expected_path:
            return _res("BACK_FAILED", page, start, "WRONG_DESTINATION")
        return _res("NAVIGATED", page, start)

    def reload(page, timeout_ms=10000):
        return _res("NAVIGATED" if page.reload_ok else "CLICK_FAILED", page, page.path,
                    None if page.reload_ok else "PAGE_TIMEOUT")

    def capture(page, run_dir, step_id, candidates, probs=None, chosen=None):
        ref = f"screenshots/{step_id}.png"
        with open(os.path.join(run_dir, *ref.split("/")), "wb") as f:
            f.write(b"png")
        return {"screenshotRef": ref, "overlayRef": None if probs is None else f"screenshots/{step_id}_overlay.png",
                "viewport": {"width": 1440, "height": 900}, "scroll": {"x": 0, "y": 0},
                "screenshotSize": {"width": 1440, "height": 900}, "capturedAt": "2026-11-17T10:00:00.000+09:00",
                "error": None}

    def page_key(url):
        return url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]

    def page_signature(page):
        key = page_key(page.url)
        return f"{key}_nochange" if testmode_page == key else f"{key}_default"

    def verify_goal(page, success):
        ok = page.path == success["path"]
        return ok, {"pathMatched": ok, "textVisible": ok}

    return Env(extract=extract, verify_goal=verify_goal, click=click, go_back=go_back, reload=reload,
               capture=capture, page_key=page_key, page_signature=page_signature)


def start_run(tmp_path, goal_id="refund", **limits):
    """실행 API(api/runner.py)처럼 READY Run을 만들고 run_agent에 넘길 settings를 만든다."""
    presets = Presets()
    goal, persona = presets.goal(goal_id), presets.persona("novice-shopper")
    req = SimpleNamespace(viewport=None, maxSteps=limits.pop("maxSteps", 8), variant="before",
                          experimentKey="test_exp", startPath=goal["startPath"])
    model = {"resultOrigin": "MOCK", "modelVersion": None, "modelEndpointId": "mock", "temperature": 1.52,
             "goalJudgmentMode": "DOM_ONLY"}
    run = run_factory.build_run(req, goal, persona, model)
    run["limits"].update(limits)
    store = RunStore(run["runId"], runs_dir=str(tmp_path))
    store.create(run)
    settings = agent_settings(run, goal, store.dir, BASE + run["startPath"], BASE)
    return settings, store


def test_importing_loop_does_not_load_playwright():
    """루프를 불러오기만 해서는 playwright를 불러오지 않는다 (playwright 없는 PC에서 실행 API가 떠야 함, #12·#14).

    실제로 루프를 불러오는 실행 API(`api.main` → `api.runner`)까지 확인한다 (#17 리뷰).
    `api.main`은 fastapi가 필요하므로 fastapi가 있을 때만 포함한다.
    이 테스트 프로세스에는 이미 playwright가 올라와 있을 수 있어서 새 파이썬으로 확인한다.
    """
    import subprocess
    code = ("import sys, importlib.util; import uxm.agent, api.run_factory\n"
            "if importlib.util.find_spec('fastapi'): import api.main, api.runner\n"
            "print(any(m == 'playwright' or m.startswith('playwright.') for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False", out.stderr


def test_agent_settings_matches_api_presets(tmp_path):
    """실행 API(api/runner.py)도 agent_settings를 쓴다. 성공 조건은 preset의 verification과 같아야 한다."""
    settings, store = start_run(tmp_path)
    goal = Presets().goal("refund")
    assert settings["successCondition"] == success_condition(goal)
    assert (settings["runDir"], settings["startPath"], settings["resultOrigin"]) == (store.dir, "/shop/home", "MOCK")


def _requests(store) -> list[dict]:
    raw = store.path("raw")
    names = sorted(n for n in os.listdir(raw) if n.startswith("model_request_step_"))
    return [json.load(open(os.path.join(raw, n), encoding="utf-8")) for n in names]


def test_default_preset_request_is_unchanged(tmp_path):
    """#39: 기존 preset(환불·초보)은 모델 요청의 목표·사용자 유형 문장이 이전(prompts 상수)과 글자까지 같다."""
    from uxm import prompts
    result, store, _ = go(tmp_path, [HOME_ORDERS, REFUND])
    assert result["status"] == "SUCCESS"
    for req in _requests(store):
        assert (req["state"]["goal"], req["state"]["persona"]) == (prompts.GOAL, prompts.PERSONA)


def test_model_request_uses_run_goal_and_persona(tmp_path):
    """#39: 모델 요청에는 상수가 아니라 Run의 목표·사용자 유형이 들어간다."""
    settings, store = start_run(tmp_path)
    settings.update(goal="주문한 상품의 배송 상태 확인하기", persona="온라인 쇼핑에 익숙한 30대 직장인")
    result = run_agent(settings, MockBackend([HOME_ORDERS, REFUND]), FakePage("/shop/home"), fake_env())
    assert result["status"] == "SUCCESS" and result["comparisonContext"]["leakCheckPassed"] is True
    reqs = _requests(store)
    assert len(reqs) == 2
    for req in reqs:
        assert req["state"]["goal"] == "주문한 상품의 배송 상태 확인하기"
        assert req["state"]["persona"] == "온라인 쇼핑에 익숙한 30대 직장인"


def _direct_input_run(tmp_path, verification, reference=None):
    """직접 입력한 목표처럼 preset 없이 Run의 verification으로 판정하는 settings."""
    settings, store = start_run(tmp_path)
    run = dict(settings["run"], goalId=None, goal="주문 내역 화면 찾기", verification=verification,
               referencePathConfig=reference)
    return agent_settings(run, None, store.dir, BASE + run["startPath"], BASE), store


def test_agent_settings_uses_run_verification_without_preset(tmp_path):
    """#39: Run에 verification이 있으면 preset 없이도 그 성공 조건으로 판정한다. 허용 경로가 없으면 경로 지표는 null."""
    v = {"type": "PATH_AND_VISIBLE_TEXT", "path": "/shop/orders", "text": "주문/배송 조회"}
    settings, store = _direct_input_run(tmp_path, v)
    assert settings["successCondition"] == {"path": "/shop/orders", "text": "주문/배송 조회",
                                            "exact": False, "scope": "page"}
    result = run_agent(settings, MockBackend([HOME_ORDERS]), FakePage("/shop/home"), fake_env())
    steps, _, _ = check_contract(result, store)
    sm = result["summary"]
    assert (result["status"], sm["stepCount"]) == ("SUCCESS", 1)
    assert sm["referencePathDeviationCount"] is None
    assert (sm["extraSteps"], sm["extraNavigationCost"], sm["attemptedExtraSteps"]) == (None, None, None)
    assert steps[0]["decision"]["goalPathProbability"] is None
    assert _requests(store)[0]["state"]["goal"] == "주문 내역 화면 찾기"


def test_run_verification_wins_over_preset(tmp_path):
    """#39: Run에 verification이 있으면 preset보다 Run 값을 쓴다 (허용 경로가 있으면 경로 지표는 그대로 계산)."""
    v = {"type": "PATH_AND_VISIBLE_TEXT", "path": "/shop/orders", "text": "주문/배송 조회"}
    ref = {"version": "refpath-orders-test", "allowedPaths": [["home", "orders"]], "shortestAllowedSteps": 1}
    settings, store = start_run(tmp_path)
    run = dict(settings["run"], verification=v, referencePathConfig=ref)
    settings = agent_settings(run, Presets().goal("refund"), store.dir, BASE + "/shop/home", BASE)
    result = run_agent(settings, MockBackend([HOME_ORDERS]), FakePage("/shop/home"), fake_env())
    assert result["status"] == "SUCCESS"
    assert (result["summary"]["extraSteps"], result["summary"]["referencePathDeviationCount"]) == (0, 0)


def test_agent_settings_requires_success_condition(tmp_path):
    settings, store = start_run(tmp_path)
    run = dict(settings["run"], goalId=None)
    with pytest.raises(ValueError):
        agent_settings(run, None, store.dir, BASE + "/shop/home", BASE)


def go(tmp_path, answers, page=None, env=None, delay=0.0, **limits):
    settings, store = start_run(tmp_path, **limits)
    backend = MockBackend(answers, delay=delay)
    result = run_agent(settings, backend, page or FakePage("/shop/home"), env or fake_env())
    return result, store, backend


def check_contract(result, store):
    """모든 Run에 공통인 계약 검사 (8.1·8.2·11.2·6.4)."""
    steps, events, run = store.read_steps(), store.read_events(), store.read_run()
    assert run["status"] == result["status"] and run["terminationReason"] == result["terminationReason"]
    assert [s["stepId"] for s in steps] == list(range(1, len(steps) + 1))
    assert "domIndex" not in json.dumps(steps)
    counted = 0
    for s in steps:
        cs, d, a = s["candidateSet"], s["decision"], s["action"]
        sig = s["page"]["pageSignature"]   # 계약 8.2 {pageSignature}_initial / _retry_{n}
        assert cs["candidateSetId"] == f"{sig}_initial" or cs["candidateSetId"].startswith(f"{sig}_retry_")
        n = len(cs["candidates"])
        assert cs["candidateCountCollected"] == n + cs["excludedCandidateCount"]
        assert cs["candidateCountSent"] == n + (1 if cs["actionSetIncludesBack"] else 0)
        assert cs["excludedCandidateCount"] == sum(cs["exclusionReasons"].values())
        assert [c["optionId"] for c in cs["candidates"]] == [f"option_{i:02d}" for i in range(1, n + 1)]
        selected = [c["optionId"] for c in cs["candidates"] if c["selected"]]
        if d["selectedActionType"] == "GO_BACK":
            assert selected == [] and d["selectedOptionId"] == cs["backOptionId"]
        else:
            assert selected == [d["selectedOptionId"]]
        assert a["executedOptionId"] == (a["selectedOptionId"] if a["executed"] else None)
        assert d["resultOrigin"] == "MOCK"
        if a["executed"] and (a["actionType"] == "CLICK" or a["result"] == "NAVIGATED"):
            counted += 1
    sm = result["summary"]
    assert sm["stepCount"] == counted == result["progress"]["stepCount"]
    assert sm["stepRecordCount"] == len(steps)
    assert sm["backCount"] == sm["modelBackCount"] + sm["systemBackCount"]
    assert sm["navigationCost"] == sm["successfulNavigationClickCount"] + sm["backCount"]
    assert events[0]["eventType"] == "RUN_STARTED"
    assert events[-1]["eventType"] == ("RUN_FAILED" if result["status"] == "ERROR" else "RUN_FINISHED")
    assert "validity" not in result   # API가 정한다
    return steps, events, run


# ---------------------------------------------------------------- C0-MOCK (GATE)

def test_c0_mock_reaches_refund_in_two_steps(tmp_path):
    result, store, _ = go(tmp_path, [HOME_ORDERS, REFUND])
    steps, events, run = check_contract(result, store)
    assert (result["status"], result["terminationReason"]) == ("SUCCESS", "GOAL_VERIFIED")
    assert result["summary"]["goalReached"] is True and result["summary"]["stepCount"] == 2
    assert result["summary"]["extraSteps"] == 0 and result["summary"]["referencePathDeviationCount"] == 0
    assert result["comparisonContext"]["leakCheckPassed"] is True
    assert [v["pageKey"] for v in result["summary"]["path"]] == ["home", "orders", "refund"]
    assert all(os.path.isfile(store.path(*s["page"]["screenshotRef"].split("/"))) for s in steps)
    assert os.path.isfile(store.path("raw", "model_request_step_001.json"))
    goal = next(e for e in events if e["eventType"] == "GOAL_REACHED")
    assert goal["details"]["screenshotRef"] == "screenshots/3.png"
    # 첫 화면엔 GO_BACK 없음, 두 번째 화면엔 있음
    assert [s["candidateSet"]["actionSetIncludesBack"] for s in steps] == [False, True]
    assert steps[0]["decision"]["goalPathProbability"] is not None


# ---------------------------------------------------------------- 종료 조건

def test_c5_max_steps(tmp_path):
    result, store, _ = go(tmp_path, [HOME_SUPPORT], maxSteps=1)
    check_contract(result, store)
    assert (result["status"], result["terminationReason"]) == ("MAX_STEPS", "MAX_STEPS_REACHED")
    assert result["summary"]["goalReached"] is False and result["summary"]["extraSteps"] is None
    assert result["summary"]["referencePathDeviationCount"] == 1   # home → support는 허용 경로 밖


def test_c7_revisit_limit(tmp_path):
    result, store, _ = go(tmp_path, [HOME_MYPAGE, CRUMB_HOME, HOME_MYPAGE, CRUMB_HOME])
    _, events, _ = check_contract(result, store)
    assert (result["status"], result["terminationReason"]) == ("BLOCKED", "REVISIT_LIMIT_REACHED")
    assert result["summary"]["stepCount"] == 4 and result["summary"]["revisitCount"] == 3
    assert sum(e["eventType"] == "PAGE_REVISITED" for e in events) == 3


def test_c9_model_back_budget(tmp_path):
    # 시나리오 C9 흐름은 mypage에 3번째로 들어가서, 기본값(같은 화면 3번)이면 C7 규칙으로 먼저 멈춘다.
    # BACK 예산만 보려고 반복 제한을 4로 올린다 (C10처럼 테스트 설정값만 변경. 팀 확인 필요)
    answers = [HOME_MYPAGE, MYPAGE_ORDERS, "GO_BACK", MYPAGE_ORDERS, "GO_BACK", MYPAGE_ORDERS, REFUND]
    result, store, _ = go(tmp_path, answers, maxSamePageVisits=4)
    steps, events, _ = check_contract(result, store)
    assert result["status"] == "SUCCESS" and result["summary"]["modelBackCount"] == 2
    after_two_backs = steps[5]   # 두 번째 BACK 뒤 mypage에서의 판단
    assert after_two_backs["candidateSet"]["actionSetIncludesBack"] is False
    assert sum(e["eventType"] == "BACK_EXECUTED" and e["eventOrigin"] == "MODEL_DECISION" for e in events) == 2


def test_c10_model_timeout_is_valid_timeout(tmp_path):
    result, store, _ = go(tmp_path, [HOME_ORDERS], delay=0.15, modelTimeoutMs=100)
    check_contract(result, store)
    assert (result["status"], result["terminationReason"]) == ("TIMEOUT", "MODEL_TIMEOUT")
    assert result["error"]["code"] == "MODEL_TIMEOUT"
    assert store.read_steps() == []   # 행동 실행 없음
    run = run_factory.finalize(dict(store.read_run()))
    assert (run["validity"], run["invalidReason"]) == ("VALID", None)


def test_c11_page_load_failed_is_invalid(tmp_path):
    page = FakePage("/shop/home", behavior={"/shop/orders": "timeout"}, reload_ok=False)
    result, store, _ = go(tmp_path, [HOME_ORDERS], page=page)
    steps, _, _ = check_contract(result, store)
    assert (result["status"], result["terminationReason"]) == ("ERROR", "PAGE_LOAD_FAILED")
    assert result["error"]["code"] == "PAGE_TIMEOUT"
    assert (steps[-1]["action"]["result"], steps[-1]["action"]["reason"]) == ("CLICK_FAILED", "PAGE_TIMEOUT")
    run = run_factory.finalize(dict(store.read_run()))
    assert (run["validity"], run["invalidReason"]) == ("INVALID", "BROWSER_CRASH")


def test_page_timeout_then_reload_continues(tmp_path):
    page = FakePage("/shop/home", behavior={"/shop/orders": "timeout"}, reload_ok=True)
    result, store, _ = go(tmp_path, [HOME_ORDERS, REFUND], page=page)
    check_contract(result, store)
    assert result["status"] == "SUCCESS" and result["summary"]["stepCount"] == 2


# ---------------------------------------------------------------- 실패 후 다시 판단

def test_c6_no_change_retries_without_that_candidate(tmp_path):
    page = FakePage("/shop/home", behavior={"/shop/coupons": "nochange"})
    result, store, _ = go(tmp_path, [HOME_MYPAGE, COUPON, MYPAGE_ORDERS, REFUND], page=page,
                          env=fake_env(testmode_page="mypage"))
    steps, _, _ = check_contract(result, store)
    assert result["status"] == "SUCCESS" and result["summary"]["stepCount"] == 4   # NO_CHANGE도 실제로 누름
    no_change, retry = steps[1], steps[2]
    assert no_change["action"]["result"] == "NO_CHANGE"
    assert no_change["candidateSet"]["candidateSetId"] == "mypage_nochange_initial"
    assert retry["candidateSet"]["candidateSetId"] == "mypage_nochange_retry_1"
    assert retry["candidateSet"]["candidateSetChanged"] is True
    assert COUPON not in [c["semanticKey"] for c in retry["candidateSet"]["candidates"]]
    assert retry["candidateSet"]["backOptionId"] == "option_03"   # 01부터 다시 매겨 GO_BACK 번호가 안 겹침
    req = json.load(open(store.path("raw", "model_request_step_003.json"), encoding="utf-8"))
    assert req["state"]["previousFailedActions"] == ["쿠폰함 선택 → 화면 변화 없음"]
    assert "nochange" not in json.dumps(req, ensure_ascii=False)


def test_c4_deadend_system_back_then_success(tmp_path):
    result, store, _ = go(tmp_path, [HOME_SUPPORT, HOME_ORDERS, REFUND])
    steps, events, _ = check_contract(result, store)
    assert result["status"] == "SUCCESS"
    sm = result["summary"]
    assert (sm["systemBackCount"], sm["modelBackCount"], sm["stepCount"]) == (1, 0, 3)
    assert len(steps) == 3   # 시스템 BACK은 Step 없음
    sys_back = [e for e in events if e["eventType"] == "BACK_EXECUTED" and e["eventOrigin"] == "SYSTEM_RECOVERY"]
    assert len(sys_back) == 1 and sys_back[0]["stepId"] is None
    assert [v["visitCause"] for v in sm["path"]][:3] == ["RUN_START", "MODEL_NAVIGATION", "SYSTEM_BACK_RECOVERY"]


def test_no_candidates_at_start_is_blocked(tmp_path):
    result, store, _ = go(tmp_path, [], page=FakePage("/shop/support"))
    check_contract(result, store)
    assert (result["status"], result["terminationReason"]) == ("BLOCKED", "NO_VALID_CANDIDATES")


def test_out_of_scope_click_recovers_with_system_back(tmp_path):
    page = FakePage("/shop/home", behavior={"/shop/support": "outside"})
    result, store, _ = go(tmp_path, [HOME_SUPPORT, HOME_ORDERS, REFUND], page=page)
    steps, _, _ = check_contract(result, store)
    assert result["status"] == "SUCCESS" and result["summary"]["systemBackCount"] == 1
    assert HOME_SUPPORT not in [c["semanticKey"] for c in steps[1]["candidateSet"]["candidates"]]


def test_action_error_is_browser_error(tmp_path):
    page = FakePage("/shop/home", behavior={"/shop/orders": "error"})
    result, store, _ = go(tmp_path, [HOME_ORDERS], page=page)
    steps, _, _ = check_contract(result, store)
    assert (result["status"], result["terminationReason"]) == ("ERROR", "BROWSER_ERROR")
    assert steps[0]["action"]["error"]["code"] == "BROWSER_ERROR"


# ---------------------------------------------------------------- 모델·누출·저장 오류

def test_leak_check_failed_does_not_call_model(tmp_path):
    page = FakePage("/shop/home", title_override="testMode=nochange")
    result, store, backend = go(tmp_path, [HOME_ORDERS], page=page)
    check_contract(result, store)
    assert (result["status"], result["terminationReason"]) == ("ERROR", "LEAK_CHECK_FAILED")
    assert result["comparisonContext"]["leakCheckPassed"] is False and backend.calls == 0
    run = run_factory.finalize(dict(store.read_run()))
    assert (run["validity"], run["invalidReason"]) == ("INVALID", "LEAK_CHECK_FAILED")


def test_invalid_choice_is_not_executed(tmp_path):
    bad = {"choice": "option_99", "probabilities": {"option_01": 0.5, "option_02": 0.3, "option_03": 0.2}}
    result, store, _ = go(tmp_path, [bad])
    check_contract(result, store)
    assert (result["terminationReason"], result["error"]["code"]) == ("MODEL_ERROR", "INVALID_CHOICE")
    assert store.read_steps() == []


def test_go_back_only_probability_keeps_metrics_warning(tmp_path):
    # #20(계약 12.3): 클릭 후보 확률 합이 0이면 클릭 전용 지표는 null + NO_CLICK_PROBABILITY_MASS.
    # 루프 내부 함수를 직접 부르지 않고 실제 Run의 저장된 Step으로 확인한다 (루프 정리 때 안 깨지게).
    # orders 화면 후보: option_01 홈, option_02 환불, option_03 GO_BACK
    back_only = {"choice": "option_03", "probabilities": {"option_01": 0.0, "option_02": 0.0, "option_03": 1.0}}
    result, store, _ = go(tmp_path, [HOME_ORDERS, back_only, HOME_ORDERS, REFUND])
    steps, _, _ = check_contract(result, store)
    assert result["status"] == "SUCCESS"
    d = steps[1]["decision"]
    assert steps[1]["page"]["pageKey"] == "orders" and d["selectedActionType"] == "GO_BACK"
    assert d["clickOnlyNormalizedEntropy"] is None and d["clickOnlyTop2Margin"] is None
    assert [w["code"] for w in d["metricWarnings"]] == ["NO_CLICK_PROBABILITY_MASS"]
    assert steps[0]["decision"]["metricWarnings"] == []   # 보통 화면은 경고 없음


def test_bad_probabilities_is_model_response_invalid(tmp_path):
    bad = {"choice": "option_01", "probabilities": {"option_01": 0.9}}
    result, _, _ = go(tmp_path, [bad])
    assert result["error"]["code"] == "MODEL_RESPONSE_INVALID"


def test_model_server_error_is_unavailable(tmp_path):
    result, _, _ = go(tmp_path, [])   # 대본이 비어 있음 → 백엔드 예외
    assert (result["terminationReason"], result["error"]["code"]) == ("MODEL_ERROR", "MODEL_UNAVAILABLE")
    assert result["comparisonContext"]["leakCheckPassed"] is True   # 요청은 누출 검사를 통과했음


def test_storage_failure_ends_run(tmp_path, monkeypatch):
    def broken(self, step):
        raise StorageError("disk full")
    monkeypatch.setattr(RunStore, "append_step", broken)
    result, store, _ = go(tmp_path, [HOME_ORDERS, REFUND])
    assert (result["status"], result["terminationReason"]) == ("ERROR", "STORAGE_FAILED")
    assert result["error"]["code"] == "STORAGE_ERROR"
    run = run_factory.finalize(dict(store.read_run()))
    assert (run["validity"], run["invalidReason"]) == ("INVALID", "STORAGE_FAILURE")


def test_run_timeout(tmp_path):
    result, _, _ = go(tmp_path, [HOME_MYPAGE, CRUMB_HOME], delay=0.02, runTimeoutMs=10)
    assert (result["status"], result["terminationReason"]) == ("TIMEOUT", "RUN_TIMEOUT")
