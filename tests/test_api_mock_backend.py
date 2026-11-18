"""실행 API 가짜 모델 모드 (`ZORO_BACKEND=mock`, #53)."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from api import run_factory  # noqa: E402
from api.integrations import Integrations  # noqa: E402
from api.mock_model import MOCK_MODEL_VERSION, HeuristicMockBackend  # noqa: E402
from uxm.metrics import validate_probabilities  # noqa: E402

SAMPLE_RAW = os.path.join(ROOT, "results", "runs", "sample_before", "raw")


def sample_request(step: int) -> dict:
    with open(os.path.join(SAMPLE_RAW, f"model_request_step_{step:03d}.json"), encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("step", [1, 2, 3, 4, 5])
def test_mock_backend_answers_valid_and_deterministic(step):
    request = sample_request(step)
    backend = HeuristicMockBackend()
    first, second = backend.decide(request, 1.52), backend.decide(request, 1.52)
    assert first == second   # 같은 요청 → 같은 응답
    answer = first["answers"]["next_click"]
    options = list(request["questions"]["next_click"]["criteria"])
    validate_probabilities(answer["probabilities"], options)
    assert answer["choice"] in options
    assert answer["probabilities"][answer["choice"]] == max(answer["probabilities"].values())


def test_mock_backend_prefers_goal_words_and_ranks_back_last():
    request = {"state": {"goal": "최근 구매한 상품의 환불 신청 화면 찾기", "persona": "초보 사용자"},
               "questions": {"next_click": {"type": "choice", "criteria": {
                   "option_01": "[경로 표시] 홈", "option_02": "[본문] 환불 신청하기",
                   "option_03": "[브라우저] 이전 화면으로 돌아가기"}}}}
    probs = HeuristicMockBackend().decide(request)["answers"]["next_click"]["probabilities"]
    assert probs["option_02"] > probs["option_01"] > probs["option_03"]


def test_model_settings_records_mock_origin(monkeypatch):
    monkeypatch.setenv("ZORO_BACKEND", "mock")
    s = run_factory.model_settings()
    assert (s["resultOrigin"], s["modelVersion"], s["modelEndpointId"]) == ("MOCK", MOCK_MODEL_VERSION, "mock")
    monkeypatch.setenv("ZORO_BACKEND", "http")
    assert run_factory.model_settings()["resultOrigin"] == "LIVE"   # 실제 모델 설정은 그대로


def test_integrations_uses_mock_backend(monkeypatch):
    monkeypatch.setenv("ZORO_BACKEND", "mock")
    backend = Integrations().backend(run_factory.model_settings()["config"])
    assert isinstance(backend, HeuristicMockBackend)


def test_health_reports_mock_model_ready(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from api.main import create_app
    monkeypatch.setenv("ZORO_BACKEND", "mock")
    with TestClient(create_app(results_dir=str(tmp_path))) as client:
        model = client.get("/api/health").json()["model"]
    assert model == {"ready": True, "modelVersion": MOCK_MODEL_VERSION, "endpointId": "mock"}


def test_api_runs_end_to_end_with_mock_model(monkeypatch, tmp_path, shops):
    """모델 없이 실제 쇼핑몰·브라우저·루프·분석을 API로 끝까지 돌리고, 결과는 MOCK으로 남는다."""
    pytest.importorskip("playwright")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from api import runner
    from api.main import create_app
    monkeypatch.setenv("ZORO_BACKEND", "mock")
    monkeypatch.setitem(runner.SHOP_URLS, "before", shops["before"])
    monkeypatch.setitem(runner.SHOP_URLS, "after", shops["after"])
    app = create_app(results_dir=str(tmp_path))   # 실제 Integrations (가짜로 바꿔 끼우지 않음)
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "READY"
        run_ids = []
        for variant in ("before", "after"):
            r = client.post("/api/runs", json={"experimentKey": "mock_e2e", "variant": variant,
                                               "goalId": "refund", "personaId": "novice-shopper"})
            assert r.status_code == 201, r.text
            app.state.runner.wait(60)
            assert app.state.runner.active_run_id is None, f"{variant} 실행이 60초 안에 끝나지 않음"
            run_ids.append(r.json()["runId"])
        for run_id in run_ids:
            run = client.get(f"/api/runs/{run_id}").json()
            assert run["resultOrigin"] == "MOCK" and run["modelVersion"] == MOCK_MODEL_VERSION
            assert run["validity"] == "VALID", (run["status"], run["terminationReason"], run["error"])
            assert client.get(f"/api/runs/{run_id}/steps").json()["count"] >= 1
            assert client.get(f"/api/runs/{run_id}/issues").json()["analysisStatus"] == "COMPLETED"
        cmp = client.get("/api/comparisons/mock_e2e")
        assert cmp.status_code == 200, cmp.text
        assert cmp.json()["resultOrigin"] == "MOCK"
