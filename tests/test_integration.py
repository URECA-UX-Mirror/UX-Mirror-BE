"""실제 쇼핑몰 + 실제 브라우저(browser.py) + 가짜 모델로 에이전트 루프를 돌리는 통합 테스트 (#13).

tests/test_agent.py는 가짜 화면(FakePage)으로 루프 규칙을 본다. 여기서는 1번·2번을 합쳤을 때
같은 시나리오가 실제 화면에서도 그대로 도는지와, testMode가 모델 요청·저장 경로로 새지 않는지 본다.
쇼핑몰 서버는 tests/conftest.py의 shops fixture가 띄운다.
"""
import glob
import json
import os
import re
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("playwright")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from api import run_factory  # noqa: E402
from api.presets import Presets  # noqa: E402
from uxm import browser  # noqa: E402
from uxm.agent import agent_settings, run_agent  # noqa: E402
from uxm.mock import MockBackend  # noqa: E402
from uxm.storage import RunStore  # noqa: E402

HOME_ORDERS, HOME_SUPPORT, HOME_MYPAGE = "상단 메뉴>/shop/orders#1", "상단 메뉴>/shop/support#1", "상단 메뉴>/shop/mypage#1"
MYPAGE_ORDERS, COUPON, REFUND = "사이드 메뉴>/shop/orders#1", "사이드 메뉴>/shop/coupons#1", "본문>/shop/refund#1"
LEAK = re.compile(r"testmode|nochange|deadend", re.I)


def real_run(tmp_path, shops, variant, answers, test_mode=None):
    """scripts/run_c.py와 같은 순서로 READY Run을 만들고 실제 브라우저로 루프를 돌린다."""
    presets = Presets()
    goal, persona = presets.goal("refund"), presets.persona("novice-shopper")
    req = SimpleNamespace(viewport=None, maxSteps=8, variant=variant, experimentKey="it_exp",
                          startPath=goal["startPath"])
    model = {"resultOrigin": "MOCK", "modelVersion": None, "modelEndpointId": "mock", "temperature": 1.52,
             "goalJudgmentMode": "DOM_ONLY"}
    run = run_factory.build_run(req, goal, persona, model)
    store = RunStore(run["runId"], runs_dir=str(tmp_path))
    store.create(run)
    base = shops[variant]
    start_url = base + run["startPath"] + (f"?testMode={test_mode}" if test_mode else "")
    settings = agent_settings(run, goal, store.dir, start_url, base)
    session = browser.open_browser(start_url)
    try:
        result = run_agent(settings, MockBackend(answers), session["page"])
    finally:
        browser.close_browser(session)
    return result, store


def assert_no_test_mode_leak(result, store):
    """계약 v1.3.5: testMode Run에서도 모델 요청과 정규화 경로에 testMode·deadend·nochange가 없다.
    pageSignature·candidateSetId는 Backend 전용이라 상태를 포함해도 된다."""
    requests = glob.glob(os.path.join(store.dir, "raw", "model_request_step_*.json"))
    assert requests
    for f in requests:
        assert not LEAK.search(open(f, encoding="utf-8").read()), f
    paths = [v["path"] for v in result["summary"]["path"]]
    for s in store.read_steps():
        paths += [s["page"]["path"], s["action"]["fromPath"], s["action"]["toPath"]]
    for p in paths:
        assert p is None or (not LEAK.search(p) and "?" not in p), p


def test_c0_mock_after_reaches_refund_with_real_browser(tmp_path, shops):
    """C0-MOCK: 가짜 모델로 After 홈 → 주문·환불 → 환불. 실제 모델로 같은 경로를 보는 C1은 행사 당일."""
    result, store = real_run(tmp_path, shops, "after", [HOME_ORDERS, REFUND])
    assert (result["status"], result["terminationReason"]) == ("SUCCESS", "GOAL_VERIFIED")
    assert result["summary"]["stepCount"] == 2
    assert [v["pageKey"] for v in result["summary"]["path"]] == ["home", "orders", "refund"]
    steps = store.read_steps()
    assert "domIndex" not in json.dumps(steps)
    for s in steps:   # 판단 화면마다 원본·배지 사진
        assert os.path.isfile(store.path(*s["page"]["screenshotRef"].split("/")))
        assert os.path.isfile(store.path(*s["page"]["overlayScreenshotRef"].split("/")))
    goal = next(e for e in store.read_events() if e["eventType"] == "GOAL_REACHED")
    assert os.path.isfile(store.path(*goal["details"]["screenshotRef"].split("/")))   # 도착 화면 사진


def test_c6_nochange_coupon_with_real_browser(tmp_path, shops):
    result, store = real_run(tmp_path, shops, "before", [HOME_MYPAGE, COUPON, MYPAGE_ORDERS, REFUND],
                             test_mode="nochange")
    assert result["status"] == "SUCCESS" and result["summary"]["stepCount"] == 4   # NO_CHANGE도 실제로 누름
    no_change, retry = store.read_steps()[1:3]
    assert (no_change["action"]["result"], no_change["action"]["executed"]) == ("NO_CHANGE", True)
    assert no_change["page"]["pageSignature"] == "mypage_nochange"
    assert retry["candidateSet"]["candidateSetChanged"] is True
    assert COUPON not in [c["semanticKey"] for c in retry["candidateSet"]["candidates"]]
    assert_no_test_mode_leak(result, store)


def test_c4_deadend_system_back_with_real_browser(tmp_path, shops):
    result, store = real_run(tmp_path, shops, "before", [HOME_SUPPORT, HOME_ORDERS, REFUND], test_mode="deadend")
    assert result["status"] == "SUCCESS"
    sm = result["summary"]
    assert (sm["systemBackCount"], sm["modelBackCount"], sm["stepCount"]) == (1, 0, 3)
    assert [v["visitCause"] for v in sm["path"]][:3] == ["RUN_START", "MODEL_NAVIGATION", "SYSTEM_BACK_RECOVERY"]
    assert sm["path"][1]["pageSignature"] == "support_deadend"
    assert_no_test_mode_leak(result, store)
