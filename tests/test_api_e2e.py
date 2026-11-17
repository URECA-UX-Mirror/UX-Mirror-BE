"""실행 API E2E 통합 테스트 (#31).

모델만 가짜(MockBackend)로 두고 나머지는 실제 모듈을 그대로 쓴다.
POST /api/runs → 실행기 스레드 → 루프(uxm/agent.py) → 브라우저(uxm/browser.py) → 쇼핑몰
→ 저장(uxm/storage.py) → 분석(uxm/analysis.py) → 조회·비교 API(uxm/comparison.py).

tests/test_analysis_api_integration.py는 루프·브라우저를 가짜로 끼우고,
tests/test_integration.py는 API를 거치지 않는다. 여기서는 두 경로를 한 번에 지난다.
쇼핑몰 서버는 tests/conftest.py의 shops fixture가 띄운다.
"""
import os
import sys

import pytest

pytest.importorskip("playwright")
pytest.importorskip("fastapi")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from fastapi.testclient import TestClient  # noqa: E402

from api import run_factory, runner  # noqa: E402
from api.integrations import Integrations  # noqa: E402
from api.main import create_app  # noqa: E402
from uxm.mock import MockBackend  # noqa: E402

HOME_ORDERS, HOME_SUPPORT = "상단 메뉴>/shop/orders#1", "상단 메뉴>/shop/support#1"
SUPPORT_ORDERS, REFUND = "본문>/shop/orders#1", "본문>/shop/refund#1"
PNG = b"\x89PNG\r\n\x1a\n"

# Before는 고객센터를 거쳐 돌아가고(참조 경로 이탈), After는 주문·환불로 바로 간다.
SCRIPTS = {
    "before": [HOME_SUPPORT, SUPPORT_ORDERS, REFUND],
    "after": [HOME_ORDERS, REFUND],
}


class MockModelIntegrations(Integrations):
    """루프·브라우저·분석·비교는 실제 모듈. 모델만 실행 순서대로 대본을 꺼내 쓰는 가짜."""

    def __init__(self, scripts):
        super().__init__()
        self.scripts = list(scripts)

    def backend(self, cfg):
        return MockBackend(self.scripts.pop(0))


@pytest.fixture
def api(tmp_path, shops, monkeypatch):
    monkeypatch.setitem(runner.SHOP_URLS, "before", shops["before"])
    monkeypatch.setitem(runner.SHOP_URLS, "after", shops["after"])
    real_settings = run_factory.model_settings
    # 가짜 모델로 만든 결과는 MOCK으로 기록한다 (API 기본값은 LIVE)
    monkeypatch.setattr(run_factory, "model_settings",
                        lambda: {**real_settings(), "resultOrigin": "MOCK", "modelEndpointId": "mock"})
    integ = MockModelIntegrations([SCRIPTS["before"], SCRIPTS["after"]])
    app = create_app(results_dir=str(tmp_path), integrations=integ)
    with TestClient(app) as client:
        yield client, app


def start(client, app, variant, key="e2e_exp"):
    r = client.post("/api/runs", json={"experimentKey": key, "variant": variant,
                                       "goalId": "refund", "personaId": "novice-shopper"})
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["status"] == "READY" and created["variant"] == variant
    app.state.runner.wait(60)
    return created["runId"]


def check_run(client, run_id, variant, page_keys):
    run = client.get(f"/api/runs/{run_id}").json()
    assert (run["status"], run["terminationReason"], run["validity"]) == ("SUCCESS", "GOAL_VERIFIED", "VALID")
    assert run["invalidReason"] is None and run["resultOrigin"] == "MOCK"
    assert run["startPath"] == "/shop/home"
    assert run["comparisonContext"]["leakCheckPassed"] is True
    assert run["summary"]["goalReached"] is True
    assert [v["pageKey"] for v in run["summary"]["path"]] == page_keys

    steps = client.get(f"/api/runs/{run_id}/steps").json()
    assert steps["count"] == len(SCRIPTS[variant]) == run["summary"]["stepCount"]
    assert [s["page"]["pageKey"] for s in steps["items"]] == page_keys[:-1]
    for s in steps["items"]:   # 판단 화면마다 원본·배지 사진이 API로 나온다
        for kind in ("original", "overlay"):
            shot = client.get(f"/api/runs/{run_id}/screenshots/{s['stepId']}?kind={kind}")
            assert shot.status_code == 200 and shot.content.startswith(PNG)

    issues = client.get(f"/api/runs/{run_id}/issues").json()
    assert issues["analysisStatus"] == "COMPLETED"
    types = [i["issueType"] for i in issues["items"]]
    assert run["summary"]["detectedIssueTypes"] == list(dict.fromkeys(types))   # 요약과 Issue 목록 일치
    return run, types


def test_api_runs_real_loop_and_compares_before_after(api):
    """3번 완료 기준: POST → 루프 → SUCCESS, 같은 experimentKey로 Before/After → 비교 API."""
    client, app = api
    before_id = start(client, app, "before")
    after_id = start(client, app, "after")

    before, before_types = check_run(client, before_id, "before", ["home", "support", "orders", "refund"])
    after, after_types = check_run(client, after_id, "after", ["home", "orders", "refund"])
    assert "REFERENCE_PATH_DEVIATION" in before_types   # 고객센터 경유는 참조 경로 이탈
    assert "REFERENCE_PATH_DEVIATION" not in after_types

    listed = client.get("/api/runs?experimentKey=e2e_exp").json()
    assert {i["runId"] for i in listed["items"]} == {before_id, after_id}

    r = client.get("/api/comparisons/e2e_exp")
    assert r.status_code == 200, r.text
    cmp = r.json()
    assert (cmp["beforeRunId"], cmp["afterRunId"]) == (before_id, after_id)
    assert cmp["conditionsMatched"] is True, [c for c in cmp["conditionChecks"] if not c["matched"]]
    assert cmp["resultOrigin"] == "MOCK"
    screens = {s["pageKey"]: s for s in cmp["screenComparisons"]}
    home = screens["home"]
    assert home["screenComparable"] is True and home["candidateSetMatched"] is True
    labels = {c["semanticKey"]: (c["beforeLabel"], c["afterLabel"]) for c in home["candidateComparisons"]}
    assert labels[HOME_ORDERS] == ("이용정보", "주문·환불")   # 두 쇼핑몰의 유일한 차이
    assert all(c["deltaProbability"] is not None for c in home["candidateComparisons"])
    assert screens["support"]["reason"] == "NOT_VISITED_IN_AFTER"
    assert cmp["comparisonStatus"] == "PARTIAL"   # support는 Before에만 있음
    assert cmp["runComparison"]["before"]["stepCount"] == 3
    assert cmp["runComparison"]["after"]["stepCount"] == 2
    assert os.path.isfile(os.path.join(app.state.store.comparisons_dir, "e2e_exp.json"))

    again = client.post("/api/runs", json={"experimentKey": "e2e_exp", "variant": "before",
                                           "goalId": "refund", "personaId": "novice-shopper"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "EXPERIMENT_RUN_EXISTS"
