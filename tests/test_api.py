"""실행 API 테스트 (계약 17장). 모델·브라우저·에이전트 없이 가짜 연결 모듈로 돈다."""
import json
import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from api.comparison import final_run  # noqa: E402
from api.integrations import Integrations  # noqa: E402
from api.main import create_app  # noqa: E402
from api.presets import Presets, success_condition  # noqa: E402
from api.run_factory import decide_validity  # noqa: E402
from uxm.goal import GOALS  # noqa: E402
from uxm.prompts import GOAL, PERSONA  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def make_step(run_id: str, step_id: int, page_key: str = "home") -> dict:
    return {
        "schemaVersion": "1.3.2", "runId": run_id, "stepId": step_id,
        "page": {"pageKey": page_key, "pageSignature": f"{page_key}_default", "path": f"/shop/{page_key}",
                 "screenshotRef": f"screenshots/{step_id}.png",
                 "overlayScreenshotRef": f"screenshots/{step_id}_overlay.png"},
        "candidateSet": {"candidates": []}, "decision": {}, "action": {"result": "NAVIGATED"},
        "statusAfterStep": "RUNNING", "error": None,
    }


class FakeIntegrations(Integrations):
    """agent_result: run_agent가 돌려줄 dict. gate가 있으면 set될 때까지 실행을 붙잡는다."""

    def __init__(self, agent_result=None, *, has_agent=True, has_browser=True, open_fails=False,
                 agent_raises=False, issues=None, compare_result=None, has_compare=True, compare_raises=False):
        super().__init__()
        self.agent_result = agent_result if agent_result is not None else {
            "status": "SUCCESS", "terminationReason": "GOAL_VERIFIED",
            "summary": {"goalReached": True, "detectedIssueTypes": []},
            "comparisonContext": {"leakCheckPassed": True}}
        self.has_agent, self.has_browser, self.open_fails = has_agent, has_browser, open_fails
        self.agent_raises = agent_raises
        self.issues = issues
        self.compare_result, self.has_compare, self.compare_raises = compare_result, has_compare, compare_raises
        self.gate: threading.Event | None = None
        self.received_settings = None
        self.closed = 0

    def agent(self):
        if not self.has_agent:
            return None

        def run_agent(settings, backend, page):
            self.received_settings = settings
            if self.gate is not None:
                self.gate.wait(5)
            if self.agent_raises:
                raise RuntimeError("boom")
            shots = os.path.join(settings["runDir"], "screenshots")
            for name in ("1.png", "1_overlay.png"):
                with open(os.path.join(shots, name), "wb") as f:
                    f.write(PNG)
            with open(os.path.join(settings["runDir"], "steps.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(make_step(settings["runId"], 1), ensure_ascii=False) + "\n")
            return dict(self.agent_result)
        return run_agent

    def browser(self):
        if not self.has_browser:
            return None

        def open_browser(url):
            if self.open_fails:
                raise RuntimeError("server down")
            return {"page": object()}

        def close_browser(session):
            self.closed += 1
        return open_browser, close_browser

    def backend(self, cfg):
        return object()

    def detect_issues(self):
        if self.issues is None:
            return None
        return lambda run, steps: list(self.issues)

    def compare(self):
        if not self.has_compare:
            return None

        def compare(before, after):
            if self.compare_raises:
                raise ValueError("bad")
            return self.compare_result or {"experimentKey": before["run"]["experimentKey"],
                                           "beforeRunId": before["run"]["runId"],
                                           "afterRunId": after["run"]["runId"]}
        return compare


def body(**over) -> dict:
    return {"experimentKey": "exp_001", "variant": "before", "goalId": "refund",
            "personaId": "novice-shopper", **over}


@pytest.fixture
def make_client(tmp_path):
    clients = []

    def _make(integrations=None):
        integ = integrations or FakeIntegrations()
        app = create_app(results_dir=str(tmp_path), integrations=integ)
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client, integ, app
    yield _make
    for c in clients:
        c.__exit__(None, None, None)


def run_and_wait(client, app, **over) -> dict:
    r = client.post("/api/runs", json=body(**over))
    assert r.status_code == 201, r.text
    app.state.runner.wait(5)
    return client.get(f"/api/runs/{r.json()['runId']}").json()


# ---- presets

def test_presets_match_existing_code():
    p = Presets()
    goal = p.goal("refund")
    assert goal["goal"] == GOAL
    assert p.persona("novice-shopper")["persona"] == PERSONA
    assert success_condition(goal) == GOALS["refund"]
    assert goal["referencePathConfig"]["shortestAllowedSteps"] == 2


def test_presets_api_hides_backend_only_fields(make_client):
    client, _, _ = make_client()
    data = client.get("/api/presets").json()
    assert data == {"goals": [{"goalId": "refund", "goal": GOAL}],
                    "personas": [{"personaId": "novice-shopper", "persona": PERSONA}]}


# ---- 생성·실행

def test_create_returns_ready_and_runs_to_success(make_client):
    client, integ, app = make_client(FakeIntegrations(issues=[
        {"issueType": "CHOICE_AMBIGUITY"}, {"issueType": "GOAL_NOT_REACHED"}]))
    r = client.post("/api/runs", json=body())
    assert r.status_code == 201
    created = r.json()
    assert created["status"] == "READY" and created["maxSteps"] == 8 and created["variant"] == "before"
    app.state.runner.wait(5)

    run = client.get(f"/api/runs/{created['runId']}").json()
    assert run["status"] == "SUCCESS" and run["validity"] == "VALID" and run["invalidReason"] is None
    assert run["goal"] == GOAL and run["persona"] == PERSONA and run["promptVersion"] == "v2"
    assert run["viewport"] == {"width": 1440, "height": 900}
    assert run["comparisonContext"]["leakCheckPassed"] is True
    assert run["referencePathConfig"]["version"] == "refpath-v1"
    assert run["startedAt"] and run["finishedAt"]
    assert run["summary"]["detectedIssueTypes"] == ["GOAL_NOT_REACHED", "CHOICE_AMBIGUITY"]
    assert integ.closed == 1

    s = integ.received_settings
    assert s["successCondition"] == {"path": "/shop/refund", "text": "환불 / 반품 신청"}
    assert s["startUrl"].endswith(":8001/shop/home")

    issues = client.get(f"/api/runs/{created['runId']}/issues").json()
    assert issues["analysisStatus"] == "COMPLETED"
    assert [i["issueType"] for i in issues["items"]] == ["GOAL_NOT_REACHED", "CHOICE_AMBIGUITY"]


def test_ready_run_has_null_leak_check_before_execution(make_client):
    integ = FakeIntegrations()
    integ.gate = threading.Event()
    client, _, app = make_client(integ)
    run_id = client.post("/api/runs", json=body()).json()["runId"]
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["comparisonContext"]["leakCheckPassed"] is None and run["validity"] is None
    assert client.get(f"/api/runs/{run_id}/issues").json() == {
        "runId": run_id, "analysisStatus": "PENDING", "items": []}
    integ.gate.set()
    app.state.runner.wait(5)


@pytest.mark.parametrize("over", [
    {"variant": "middle"}, {"maxSteps": 0}, {"maxSteps": 16}, {"goalId": "unknown"},
    {"personaId": "unknown"}, {"experimentKey": "../evil"}, {"experimentKey": ""},
    {"viewport": {"width": 1366, "height": 768}}, {"startPath": "/shop/cart"}, {"startPath": "/admin"},
    {"promptVersion": "v3"}, {"goal": "문구 직접 전달"},
])
def test_create_rejects_invalid_request(make_client, over):
    client, _, _ = make_client()
    r = client.post("/api/runs", json=body(**over))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "CONTRACT_VALIDATION_ERROR"


def test_create_accepts_other_start_page(make_client):
    client, integ, app = make_client()
    run = run_and_wait(client, app, startPath="/shop/mypage", variant="after")
    assert run["startPath"] == "/shop/mypage"
    assert integ.received_settings["startUrl"].endswith(":8002/shop/mypage")


def test_create_returns_503_when_agent_missing(make_client):
    client, _, _ = make_client(FakeIntegrations(has_agent=False))
    r = client.post("/api/runs", json=body())
    assert r.status_code == 503 and r.json()["error"]["code"] == "BROWSER_ERROR"
    assert client.get("/api/runs").json()["count"] == 0


def test_only_one_active_run(make_client):
    integ = FakeIntegrations()
    integ.gate = threading.Event()
    client, _, app = make_client(integ)
    assert client.post("/api/runs", json=body()).status_code == 201
    r = client.post("/api/runs", json=body(variant="after"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "RUN_ALREADY_ACTIVE"
    assert client.get("/api/health").json()["activeRun"] is not None
    integ.gate.set()
    app.state.runner.wait(5)
    assert client.post("/api/runs", json=body(variant="after")).status_code == 201
    app.state.runner.wait(5)


def test_valid_run_cannot_be_rerun(make_client):
    client, _, app = make_client()
    run_and_wait(client, app)
    r = client.post("/api/runs", json=body())
    assert r.status_code == 409 and r.json()["error"]["code"] == "EXPERIMENT_RUN_EXISTS"


def test_invalid_run_is_kept_and_linked_to_rerun(make_client):
    integ = FakeIntegrations(open_fails=True)
    client, _, app = make_client(integ)
    first = run_and_wait(client, app)
    assert first["status"] == "ERROR" and first["terminationReason"] == "BROWSER_ERROR"
    assert first["validity"] == "INVALID" and first["invalidReason"] == "BROWSER_CRASH"
    assert first["error"]["code"] == "BROWSER_ERROR"
    assert client.get(f"/api/runs/{first['runId']}/issues").json()["items"] == []

    integ.open_fails = False
    second = run_and_wait(client, app)
    assert second["validity"] == "VALID"
    first = client.get(f"/api/runs/{first['runId']}").json()
    assert first["replacementRunId"] == second["runId"]
    assert second["replacementRunId"] is None


@pytest.mark.parametrize("result,status,validity,invalid", [
    ({"status": "BLOCKED", "terminationReason": "PAGE_LOAD_FAILED"}, "ERROR", "INVALID", "BROWSER_CRASH"),
    ({"status": "TIMEOUT", "terminationReason": "MODEL_TIMEOUT"}, "TIMEOUT", "VALID", None),
    ({"status": "MAX_STEPS", "terminationReason": "MAX_STEPS_REACHED"}, "MAX_STEPS", "VALID", None),
    ({"status": "ERROR", "terminationReason": "LEAK_CHECK_FAILED",
      "comparisonContext": {"leakCheckPassed": False}}, "ERROR", "INVALID", "LEAK_CHECK_FAILED"),
    ({"status": "ERROR", "terminationReason": "MODEL_ERROR",
      "error": {"code": "MODEL_RESPONSE_INVALID"}}, "ERROR", "INVALID", "CORRUPTED_RESPONSE"),
    ({"status": "ERROR", "terminationReason": "OUT_OF_SCOPE_NAVIGATION"}, "ERROR", "VALID", None),
    ({}, "ERROR", "INVALID", "BROWSER_CRASH"),   # 종료 상태 없이 끝남
])
def test_termination_maps_to_validity(make_client, result, status, validity, invalid):
    client, _, app = make_client(FakeIntegrations(agent_result=result, issues=[]))
    run = run_and_wait(client, app)
    assert (run["status"], run["validity"], run["invalidReason"]) == (status, validity, invalid)


def test_agent_exception_ends_run_as_error(make_client):
    client, integ, app = make_client(FakeIntegrations(agent_raises=True))
    run = run_and_wait(client, app)
    assert run["status"] == "ERROR" and run["validity"] == "INVALID"
    assert integ.closed == 1
    assert client.post("/api/runs", json=body()).status_code == 201   # 활성 Run이 풀렸음
    app.state.runner.wait(5)


def test_decide_validity_table():
    assert decide_validity("ERROR", "MODEL_ERROR", {"code": "MODEL_UNAVAILABLE"}) == ("INVALID", "INFRASTRUCTURE_FAILURE")
    assert decide_validity("ERROR", "STORAGE_FAILED", None) == ("INVALID", "STORAGE_FAILURE")
    assert decide_validity("SUCCESS", "GOAL_VERIFIED", None) == ("VALID", None)


# ---- 조회

def test_run_not_found_and_unsafe_id(make_client):
    client, _, _ = make_client()
    for rid in ("nope", "..", "a%2F..%2Fb"):
        r = client.get(f"/api/runs/{rid}")
        assert r.status_code == 404 and r.json()["error"]["code"] == "RUN_NOT_FOUND"
        assert set(r.json()["error"]) == {"code", "message", "retryable", "details", "occurredAt"}


def test_steps_skip_unfinished_line(make_client, tmp_path):
    client, _, app = make_client()
    run = run_and_wait(client, app)
    with open(tmp_path / "runs" / run["runId"] / "steps.jsonl", "a", encoding="utf-8") as f:
        f.write('{"stepId": 2, "page": ')   # 쓰는 중인 줄
    data = client.get(f"/api/runs/{run['runId']}/steps").json()
    assert data["count"] == 1 and [s["stepId"] for s in data["items"]] == [1]


def test_screenshots(make_client, tmp_path):
    client, _, app = make_client()
    run_id = run_and_wait(client, app)["runId"]
    r = client.get(f"/api/runs/{run_id}/screenshots/1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == PNG
    assert client.get(f"/api/runs/{run_id}/screenshots/1?kind=overlay").status_code == 200
    assert client.get(f"/api/runs/{run_id}/screenshots/2").status_code == 404
    assert client.get(f"/api/runs/{run_id}/screenshots/1?kind=raw").status_code == 422

    # Step에 적힌 경로가 실행 폴더 밖을 가리키면 내보내지 않는다
    step = make_step(run_id, 3)
    step["page"]["screenshotRef"] = "../../../../etc/hosts"
    with open(tmp_path / "runs" / run_id / "steps.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(step) + "\n")
    assert client.get(f"/api/runs/{run_id}/screenshots/3").status_code == 404


def test_list_runs_filters(make_client):
    client, _, app = make_client()
    run_and_wait(client, app)
    run_and_wait(client, app, variant="after")
    assert client.get("/api/runs").json()["count"] == 2
    items = client.get("/api/runs?experimentKey=exp_001&variant=after").json()["items"]
    assert len(items) == 1 and items[0]["variant"] == "after" and items[0]["goalReached"] is True


def test_health(make_client):
    client, _, _ = make_client()
    h = client.get("/api/health").json()
    assert set(h) >= {"status", "backend", "model", "browser", "activeRun", "checkedAt"}
    assert h["browser"]["ready"] in (True, False) and h["activeRun"] is None


def test_cors_allows_frontend_dev_server(make_client):
    client, _, _ = make_client()
    r = client.get("/api/presets", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_interrupted_run_is_invalidated_on_startup(tmp_path):
    integ = FakeIntegrations()
    integ.gate = threading.Event()
    app = create_app(results_dir=str(tmp_path), integrations=integ)
    with TestClient(app) as client:
        run_id = client.post("/api/runs", json=body()).json()["runId"]
    path = tmp_path / "runs" / run_id / "run.json"
    run = json.loads(path.read_text(encoding="utf-8"))
    run["status"] = "RUNNING"   # 서버가 실행 중에 꺼진 상황
    path.write_text(json.dumps(run), encoding="utf-8")
    integ.gate.set()
    app.state.runner.wait(5)
    run["status"] = "RUNNING"
    path.write_text(json.dumps(run), encoding="utf-8")

    with TestClient(create_app(results_dir=str(tmp_path), integrations=FakeIntegrations())) as client:
        fixed = client.get(f"/api/runs/{run_id}").json()
        assert fixed["status"] == "ERROR" and fixed["validity"] == "INVALID"
        assert client.post("/api/runs", json=body()).status_code == 201


# ---- 비교

def test_comparison_errors(make_client):
    integ = FakeIntegrations()
    client, _, app = make_client(integ)
    r = client.get("/api/comparisons/exp_001")
    assert r.status_code == 404 and r.json()["error"]["code"] == "RUN_NOT_FOUND"

    run_and_wait(client, app)
    r = client.get("/api/comparisons/exp_001")   # after 없음
    assert r.status_code == 409 and r.json()["error"]["code"] == "COMPARISON_RUN_INVALID"

    integ.gate = threading.Event()
    client.post("/api/runs", json=body(variant="after"))
    r = client.get("/api/comparisons/exp_001")
    assert r.status_code == 409 and r.json()["error"]["code"] == "COMPARISON_NOT_READY"
    integ.gate.set()
    app.state.runner.wait(5)


def test_comparison_uses_final_valid_run_and_saves(make_client, tmp_path):
    integ = FakeIntegrations()
    client, _, app = make_client(integ)
    run_and_wait(client, app)
    integ.open_fails = True
    broken = run_and_wait(client, app, variant="after")
    assert client.get("/api/comparisons/exp_001").json()["error"]["code"] == "COMPARISON_RUN_INVALID"

    integ.open_fails = False
    rerun = run_and_wait(client, app, variant="after")
    r = client.get("/api/comparisons/exp_001")
    assert r.status_code == 200
    assert r.json()["afterRunId"] == rerun["runId"] != broken["runId"]
    assert (tmp_path / "comparisons" / "exp_001.json").exists()


@pytest.mark.parametrize("kw", [{"has_compare": False}, {"compare_raises": True}])
def test_comparison_failure_is_500(make_client, kw):
    client, _, app = make_client(FakeIntegrations(**kw))
    run_and_wait(client, app)
    run_and_wait(client, app, variant="after")
    r = client.get("/api/comparisons/exp_001")
    assert r.status_code == 500 and r.json()["error"]["code"] == "ANALYSIS_ERROR"
    assert "experimentKey" not in r.json()


def test_final_run_follows_chain():
    runs = [
        {"runId": "a", "createdAt": "1", "replacementRunId": "b", "validity": "INVALID"},
        {"runId": "b", "createdAt": "2", "replacementRunId": "c", "validity": "INVALID"},
        {"runId": "c", "createdAt": "3", "replacementRunId": None, "validity": "VALID"},
    ]
    assert final_run(runs)["runId"] == "c"
    assert final_run([]) is None
