"""자동 개선 최종안 검증 Run (#101, 설계 v2.1 §4.6 (a)).

목표마다 v1(C0 등록 사이트)과 최종안 사이트로 실제 Run 1쌍을 순서대로 만든다. 실제 브라우저·모델 대신
tests/test_api.py의 가짜 실행 환경을 쓰고, 연 주소만 기록해 어느 사이트로 실행했는지 본다.
"""
import copy
import json
import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from api import improvement, run_factory  # noqa: E402
from api.errors import ApiError  # noqa: E402
from api.presets import Presets  # noqa: E402
from api.runner import Runner  # noqa: E402
from api.site_instances import SiteRegistry  # noqa: E402
from api.store import RunStore  # noqa: E402
from test_api import FakeIntegrations  # noqa: E402

FINAL_URL = "http://127.0.0.1:18077"
V1_URL = SiteRegistry().base_url("site-c0")


class RecordingIntegrations(FakeIntegrations):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.opened = []

    def browser(self):
        open_browser, close_browser = super().browser()

        def record(url):
            self.opened.append(url)
            return open_browser(url)
        return record, close_browser


@pytest.fixture
def env(tmp_path):
    integ = RecordingIntegrations(issues=[])
    runner = Runner(RunStore(str(tmp_path)), integ)
    return runner, integ, tmp_path


def frozen(**over):
    settings = run_factory.model_settings()
    f = {"improvementId": "imp_test_01", "personaId": "novice-shopper", "persona": "온라인 쇼핑몰 이용 경험이 적은 사용자",
         "startPath": "/shop/home", "viewport": {"width": 1440, "height": 900}, "limits": {"maxSteps": 6},
         "modelMetadata": {"modelVersion": settings["modelVersion"], "modelEndpointId": settings["modelEndpointId"],
                           "temperature": settings["temperature"], "promptVersion": run_factory.PROMPT_VERSION,
                           "goalJudgmentMode": settings["goalJudgmentMode"]}}
    f.update(over)
    return f


def goals():
    refund = Presets().goal("refund")
    support = copy.deepcopy(refund)
    support.update(goalId="s4-support", goal="상품이 불량이라 문의를 남기고 싶다",
                   verification={"type": "PATH_AND_VISIBLE_TEXT", "path": "/shop/support", "text": "고객센터",
                                 "exact": False, "scope": "page"},
                   referencePathConfig=None)
    return ["refund", support]


def test_runs_v1_then_final_for_each_goal(env):
    runner, integ, _ = env
    out = improvement.run_verification(frozen(), FINAL_URL, "주문·조회", goals(), runner=runner)
    assert [(r["goalId"], r["executionRole"], r["variant"]) for r in out] == [
        ("refund", "BASELINE", "before"), ("refund", "CANDIDATE", "after"),
        ("s4-support", "BASELINE", "before"), ("s4-support", "CANDIDATE", "after")]
    assert integ.opened == [V1_URL + "/shop/home", FINAL_URL + "/shop/home"] * 2
    assert [r["experimentKey"] for r in out] == ["imp_test_01-refund"] * 2 + ["imp_test_01-s4-support"] * 2
    assert all(r["status"] == "SUCCESS" and r["validity"] == "VALID" for r in out)
    assert runner.active_run_id is None


def test_run_records_frozen_conditions_and_improvement_context(env):
    runner, _, _ = env
    out = improvement.run_verification(frozen(), FINAL_URL, "주문·조회", goals()[:1], runner=runner)
    base, cand = (runner.store.load_run(r["runId"]) for r in out)
    for run in (base, cand):
        assert (run["goalId"], run["personaId"], run["startPath"]) == ("refund", "novice-shopper", "/shop/home")
        assert run["persona"] == "온라인 쇼핑몰 이용 경험이 적은 사용자"
        assert run["limits"]["maxSteps"] == 6 and run["limits"]["runTimeoutMs"] == run_factory.DEFAULT_LIMITS["runTimeoutMs"]
        assert run["viewport"] == {"width": 1440, "height": 900}
        assert "optimizationContext" not in run
    assert base["improvementContext"] == {"improvementId": "imp_test_01", "goalId": "refund", "label": "이용정보",
                                          "siteInstanceId": "site-c0", "executionRole": "BASELINE"}
    assert cand["improvementContext"] == {"improvementId": "imp_test_01", "goalId": "refund", "label": "주문·조회",
                                          "siteInstanceId": improvement.FINAL_SITE_INSTANCE_ID,
                                          "executionRole": "CANDIDATE"}


def test_final_site_address_is_not_stored(env):
    """최종안 주소는 내부 값이라 Run·결과 파일에 남기지 않는다 (siteInstanceId·이름만)."""
    runner, _, tmp_path = env
    out = improvement.run_verification(frozen(), FINAL_URL, "주문·조회", goals()[:1], runner=runner)
    assert FINAL_URL not in json.dumps(out)
    for root, _, files in os.walk(tmp_path):
        for name in files:
            with open(os.path.join(root, name), encoding="utf-8", errors="ignore") as f:
                assert "18077" not in f.read(), name


def test_runner_site_lookup_is_restored(env):
    runner, _, _ = env
    before = lambda sid: "http://x" if sid == "keep" else None   # noqa: E731
    runner.site_base_url = before
    improvement.run_verification(frozen(), FINAL_URL, "주문·조회", goals()[:1], runner=runner)
    assert runner.site_base_url is before


@pytest.mark.parametrize("url", ["ftp://127.0.0.1:18077", "http://user:pw@127.0.0.1:18077", "127.0.0.1:18077", "", None])
def test_rejects_bad_site_url_before_any_run(env, url):
    runner, integ, _ = env
    with pytest.raises(ValueError):
        improvement.run_verification(frozen(), url, "주문·조회", goals(), runner=runner)
    assert integ.opened == [] and runner.store.list_runs() == []


@pytest.mark.parametrize("bad", [
    {"improvementId": ""}, {"improvementId": "a/b"}, {"persona": None}, {"startPath": "shop"},
    {"limits": {}}, {"label": None},
])
def test_rejects_bad_inputs_before_any_run(env, bad):
    runner, integ, _ = env
    label = bad.pop("label", "주문·조회")
    with pytest.raises(ValueError):
        improvement.run_verification(frozen(**bad), FINAL_URL, label, goals(), runner=runner)
    with pytest.raises(ValueError):
        improvement.run_verification(frozen(), FINAL_URL, "주문·조회", [], runner=runner)
    with pytest.raises(ValueError):
        improvement.run_verification(frozen(), FINAL_URL, "주문·조회", ["no-such-goal"], runner=runner)
    assert integ.opened == [] and runner.store.list_runs() == []


def test_model_settings_must_match_frozen(env):
    runner, integ, _ = env
    f = frozen()
    f["modelMetadata"]["temperature"] = 0.77
    with pytest.raises(ValueError, match="temperature"):
        improvement.run_verification(f, FINAL_URL, "주문·조회", goals(), runner=runner)
    assert integ.opened == []


def test_other_active_run_is_refused(env):
    runner, integ, _ = env
    integ.gate = threading.Event()
    first = improvement.run_verification   # 첫 검증을 다른 스레드에서 붙잡아 둔다
    t = threading.Thread(target=lambda: first(frozen(improvementId="imp_a"), FINAL_URL, "주문·조회", goals()[:1],
                                              runner=runner))
    t.start()
    try:
        for _ in range(100):
            if runner.active_run_id:
                break
            threading.Event().wait(0.02)
        with pytest.raises(ApiError) as e:
            improvement.run_verification(frozen(improvementId="imp_b"), FINAL_URL, "주문·조회", goals()[:1], runner=runner)
        assert e.value.code == "RUN_ALREADY_ACTIVE"
    finally:
        integ.gate.set()
        t.join(10)


def test_timeout_stops_and_reports(env):
    runner, integ, _ = env
    integ.gate = threading.Event()
    try:
        with pytest.raises(TimeoutError):
            improvement.run_verification(frozen(), FINAL_URL, "주문·조회", goals(), runner=runner, wait_s=0.2)
    finally:
        integ.gate.set()
        runner.wait(10)
    assert len(runner.store.list_runs()) == 1   # 끝나지 않은 Run 뒤로는 만들지 않는다


def test_verification_runs_open_in_existing_run_and_comparison_api(env):
    """같은 결과 폴더를 쓰는 API에서 검증 Run을 조회·비교할 수 있다 (FE 재생: experimentKey·variant)."""
    from fastapi.testclient import TestClient
    from api.main import create_app
    runner, integ, tmp_path = env
    out = improvement.run_verification(frozen(), FINAL_URL, "주문·조회", goals()[:1], runner=runner)
    with TestClient(create_app(results_dir=str(tmp_path), integrations=integ)) as c:
        run = c.get(f"/api/runs/{out[1]['runId']}").json()
        assert run["improvementContext"]["executionRole"] == "CANDIDATE" and run["variant"] == "after"
        assert c.get(f"/api/comparisons/{out[0]['experimentKey']}").status_code == 200
        assert {r["runId"] for r in c.get("/api/runs").json()["items"]} == {r["runId"] for r in out}
