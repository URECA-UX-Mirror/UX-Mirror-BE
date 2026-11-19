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
from api.errors import ApiError  # noqa: E402
from api.integrations import Integrations  # noqa: E402
from api.main import create_app  # noqa: E402
from api.presets import Presets, success_condition  # noqa: E402
from api import run_factory  # noqa: E402
from api.run_factory import decide_validity  # noqa: E402
from uxm import sitemap  # noqa: E402
from uxm.goal import GOALS  # noqa: E402
from uxm.prompts import GOAL, PERSONA  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def make_step(run_id: str, step_id: int, page_key: str = "home") -> dict:
    return {
        "schemaVersion": "1.3.5", "runId": run_id, "stepId": step_id,
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
        self.received_events = None
        self.closed = 0
        self.issues_fail = False
        self.write_valid_then_raise = False
        self.saved_validity = None   # 루프가 실행 중 run.json에 써 두는 validity (예: "VALID")
        self.preflight_calls = []
        self.preflight_error = None

    def agent(self):
        if not self.has_agent:
            return None

        def run_agent(settings, backend, page):
            self.received_settings = settings
            if self.gate is not None:
                self.gate.wait(5)
            if self.write_valid_then_raise:   # 루프가 VALID를 저장한 뒤 예외
                path = os.path.join(settings["runDir"], "run.json")
                with open(path, encoding="utf-8") as f:
                    saved = json.load(f)
                saved.update(status="SUCCESS", terminationReason="GOAL_VERIFIED", validity="VALID")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(saved, f)
                raise RuntimeError("boom after save")
            if self.agent_raises:
                raise RuntimeError("boom")
            if self.saved_validity is not None:
                path = os.path.join(settings["runDir"], "run.json")
                with open(path, encoding="utf-8") as f:
                    saved = json.load(f)
                saved.update(validity=self.saved_validity, invalidReason=None)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(saved, f)
            shots = os.path.join(settings["runDir"], "screenshots")
            for name in ("1.png", "1_overlay.png"):
                with open(os.path.join(shots, name), "wb") as f:
                    f.write(PNG)
            with open(os.path.join(settings["runDir"], "steps.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(make_step(settings["runId"], 1), ensure_ascii=False) + "\n")
            with open(os.path.join(settings["runDir"], "events.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"eventId": "event_01", "eventType": "BACK_EXECUTED",
                                    "eventOrigin": "SYSTEM_RECOVERY"}) + "\n")
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

    def goal_preflight(self):
        """실제 화면 검사는 tests/test_preflight.py에서. 여기서는 호출만 기록하고 preflight_error가 있으면 던진다."""
        def check(verification, base_urls):
            self.preflight_calls.append(verification)
            if self.preflight_error is not None:
                raise self.preflight_error
        return check

    def backend(self, cfg):
        return object()

    def detect_issues(self):
        if self.issues is None:
            return None

        def detect(run, steps, events):
            self.received_events = events
            if self.issues_fail:
                raise ValueError("analysis bug")
            return list(self.issues)
        return detect

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
    with_test = client.get("/api/presets?includeTest=true").json()
    assert [g["goalId"] for g in with_test["goals"]] == ["refund", "refund-from-mypage-test"]


def test_c3_test_preset_runs_from_mypage(make_client):
    client, integ, app = make_client()
    run = run_and_wait(client, app, goalId="refund-from-mypage-test", startPath="/shop/mypage")
    assert run["goalId"] == "refund-from-mypage-test" and run["goal"] == GOAL
    assert run["referencePathConfig"] == {"basis": "ALLOWED_PATHS", "version": "refpath-mypage-test-v1",
                                          "allowedPaths": [["mypage", "refund"], ["mypage", "orders", "refund"]],
                                          "shortestAllowedSteps": 1}
    assert integ.received_settings["successCondition"] == GOALS["refund"]


def test_c3_test_preset_fixes_start_path(make_client):
    client, integ, app = make_client()
    r = client.post("/api/runs", json=body(goalId="refund-from-mypage-test", startPath="/shop/home"))
    assert r.status_code == 422 and r.json()["error"]["code"] == "CONTRACT_VALIDATION_ERROR"
    run = run_and_wait(client, app, goalId="refund-from-mypage-test")   # startPath 생략 → preset 값
    assert run["startPath"] == "/shop/mypage"
    assert integ.received_settings["startUrl"].endswith(":8001/shop/mypage")


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
    assert s["successCondition"] == {"path": "/shop/refund", "text": "환불 / 반품 신청", "exact": False, "scope": "page"}
    assert s["startUrl"].endswith(":8001/shop/home")

    assert integ.received_events == [{"eventId": "event_01", "eventType": "BACK_EXECUTED",
                                      "eventOrigin": "SYSTEM_RECOVERY"}]
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


# ---- 직접 입력한 목표·사용자 유형

DIRECT_V = {"path": "/shop/orders", "text": "주문/배송 조회"}


def direct(**over) -> dict:
    b = {"experimentKey": "exp_direct", "variant": "before", "goal": "  주문 내역 화면 찾기 ",
         "persona": "쇼핑몰을 처음 쓰는 사용자", "verification": DIRECT_V, **over}
    return {k: v for k, v in b.items() if v is not None}


def test_direct_input_is_normalized_and_saved(make_client):
    client, integ, app = make_client()
    r = client.post("/api/runs", json=direct(verification={"path": " /shop/orders ", "text": " 주문/배송 조회 "}))
    assert r.status_code == 201, r.text
    app.state.runner.wait(5)
    run = client.get(f"/api/runs/{r.json()['runId']}").json()
    assert (run["goalId"], run["goal"], run["personaId"], run["persona"]) == (
        None, "주문 내역 화면 찾기", None, "쇼핑몰을 처음 쓰는 사용자")
    assert run["verification"] == {"type": "PATH_AND_VISIBLE_TEXT", "path": "/shop/orders",
                                   "text": "주문/배송 조회", "exact": False, "scope": "page"}
    assert run["referencePathConfig"]["basis"] == "SITE_MAP" and run["startPath"] == "/shop/home"
    assert integ.received_settings["successCondition"] == {
        "path": "/shop/orders", "text": "주문/배송 조회", "exact": False, "scope": "page"}
    assert integ.preflight_calls == [run["verification"]]   # 정리된 성공 조건으로 실행 전 검사


# ---- 실행 전 검사 (「남은 일과 분업」 7번, FE OI-9). 실제 화면 판정은 tests/test_preflight.py

def test_preset_run_skips_preflight(make_client):
    client, integ, app = make_client()
    run_and_wait(client, app)
    assert integ.preflight_calls == []   # preset 성공 조건은 로드 때 검사 (#35)


@pytest.mark.parametrize("error,status,retryable", [
    (ApiError(422, "CONTRACT_VALIDATION_ERROR", "After 도착 화면(/shop/orders)에 '없는 글자' 글자가 없습니다."), 422, False),
    (ApiError(503, "BROWSER_ERROR", "Before 도착 화면을 읽지 못했습니다", retryable=True), 503, True),
])
def test_preflight_failure_creates_no_run(make_client, error, status, retryable):
    client, integ, app = make_client()
    integ.preflight_error = error
    r = client.post("/api/runs", json=direct())
    assert r.status_code == status
    assert (r.json()["error"]["code"], r.json()["error"]["retryable"]) == (error.code, retryable)
    assert r.json()["error"]["message"] == error.message
    assert client.get("/api/runs").json()["count"] == 0 and app.state.runner.active_run_id is None


def test_preflight_not_run_while_another_run_active(make_client):
    client, integ, app = make_client()
    integ.gate = threading.Event()
    assert client.post("/api/runs", json=body()).status_code == 201
    try:
        r = client.post("/api/runs", json=direct())
        assert r.status_code == 409 and r.json()["error"]["code"] == "RUN_ALREADY_ACTIVE"
        assert integ.preflight_calls == []   # 브라우저를 띄우지 않고 바로 409
    finally:
        integ.gate.set()
        app.state.runner.wait(5)


# ---- 경로 기준: preset은 ALLOWED_PATHS, 직접 입력은 SITE_MAP (설계 v2.1 4.1, #50)

VP = {"width": 1440, "height": 900}


def _site_map(**over) -> dict:
    with open(run_factory.SITE_MAP_PATH, encoding="utf-8") as f:
        data = {**json.load(f), **over}
    data["siteMapHash"] = sitemap.site_map_hash(data["pages"])
    return data


def _write_map(tmp_path, data) -> str:
    path = tmp_path / "map.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_direct_input_saves_site_map_reference(make_client):
    client, _, app = make_client()
    r = client.post("/api/runs", json=direct())
    assert r.status_code == 201, r.text
    app.state.runner.wait(5)
    ref = client.get(f"/api/runs/{r.json()['runId']}").json()["referencePathConfig"]
    assert {k: ref[k] for k in ("basis", "startPageKey", "goalPageKey", "shortestSteps")} == {
        "basis": "SITE_MAP", "startPageKey": "home", "goalPageKey": "orders", "shortestSteps": 1}
    site_map = sitemap.load_site_map(run_factory.SITE_MAP_PATH)
    assert (ref["siteMapVersion"], ref["siteMapHash"]) == (site_map["siteMapVersion"], site_map["siteMapHash"])
    assert ref["distances"] == sitemap.distances(site_map["pages"], "orders")


def test_preset_reference_keeps_allowed_paths():
    ref = Presets().goal("refund")["referencePathConfig"]
    assert ref["basis"] == "ALLOWED_PATHS" and ref["shortestAllowedSteps"] == 2


def test_site_map_shortest_steps_matches_preset():
    """같은 환불 목표면 지도 최단 거리가 preset 허용 경로 최단(2)과 같다 (설계 2.2 검증 4)."""
    ref = run_factory.site_map_reference("/shop/home", {"path": "/shop/refund"}, VP)
    assert ref["shortestSteps"] == Presets().goal("refund")["referencePathConfig"]["shortestAllowedSteps"]


@pytest.mark.parametrize("data,start,goal_path", [
    (None, "/shop/home", "/shop/orders"),                          # 지도 파일 없음
    ({"siteMapHash": "sha256:bad"}, "/shop/home", "/shop/orders"),  # 해시 불일치 (아래에서 덮어씀)
    ({"siteVersion": "shop-v2"}, "/shop/home", "/shop/orders"),    # 사이트 다름
    ({"candidatePolicyVersion": "candidate-v1"}, "/shop/home", "/shop/orders"),
    ({"viewport": {"width": 375, "height": 812}}, "/shop/home", "/shop/orders"),
    ({}, "/shop/mypage", "/shop/orders"),                          # 지도 시작 화면이 아님
    ({}, "/shop/home", "/shop/cart"),                              # 도착 화면이 지도에 없음
])
def test_site_map_error_runs_without_reference(tmp_path, data, start, goal_path):
    if data is None:
        path = str(tmp_path / "missing.json")
    else:
        built = _site_map(**data)
        if "siteMapHash" in data:
            built["siteMapHash"] = data["siteMapHash"]
        path = _write_map(tmp_path, built)
    assert run_factory.site_map_reference(start, {"path": goal_path}, VP, path) is None


def test_unreachable_goal_rejected_before_run(make_client, tmp_path, monkeypatch):
    pages = _site_map()["pages"]
    for page in pages.values():   # support로 들어가는 간선을 모두 없앤 지도
        page["edges"] = [e for e in page["edges"] if e["to"] != "support"]
    monkeypatch.setattr(run_factory, "SITE_MAP_PATH", _write_map(tmp_path, _site_map(pages=pages)))
    client, _, app = make_client()
    r = client.post("/api/runs", json=direct(verification={"path": "/shop/support", "text": "고객센터"}))
    assert r.status_code == 422, r.text
    assert "경로가 없습니다" in r.json()["error"]["message"]
    assert app.state.store.list_runs() == []


def test_preset_run_copies_verification(make_client):
    client, _, app = make_client()
    run = run_and_wait(client, app)
    assert run["verification"] == Presets().goal("refund")["verification"]


def test_mixed_preset_goal_and_direct_persona(make_client):
    client, _, app = make_client()
    run = run_and_wait(client, app, personaId=None, persona="급한 사용자")
    assert (run["goalId"], run["personaId"], run["persona"]) == ("refund", None, "급한 사용자")


@pytest.mark.parametrize("over", [
    {"goalId": "refund"},                                  # goalId·goal 둘 다
    {"goal": None, "verification": None},                  # 둘 다 없음
    {"verification": None},                                # 직접 입력인데 성공 조건 없음
    {"goal": None, "goalId": "refund"},                    # preset인데 성공 조건 보냄
    {"personaId": "novice-shopper"}, {"persona": None},
    {"goal": "   "}, {"persona": ""},
    {"goal": "가" * 201}, {"persona": "가" * 201},
    {"goal": "after 화면에서 환불 찾기"}, {"goal": "variant 확인"}, {"persona": "before 사용자"},
    {"verification": {"path": "/orders", "text": "주문"}},
    {"verification": {"path": "/shop/orders", "text": "  "}},
    {"verification": {"path": "/shop/orders", "text": "주문", "scope": "body"}},
    {"verification": {"path": "/shop/orders", "text": "주문", "type": "URL_ONLY"}},
    {"verification": {"path": "/shop/orders", "text": "가" * 101}},
    {"startPath": "/shop/cart"},
])
def test_direct_input_rejected(make_client, over):
    client, _, app = make_client()
    r = client.post("/api/runs", json=direct(**over))
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "CONTRACT_VALIDATION_ERROR"
    assert app.state.store.list_runs() == []


@pytest.mark.parametrize("over,ok", [
    ({}, True),
    ({"goal": "주문 내역 화면 찾기"}, True),                # 공백만 다르면 정리 후 같다
    ({"goal": "배송 조회 화면 찾기"}, False),
    ({"persona": "다른 사용자"}, False),
    ({"verification": {**DIRECT_V, "exact": True}}, False),
    ({"goal": None, "verification": None, "goalId": "refund"}, False),
])
def test_after_must_match_before_inputs(make_client, over, ok):
    client, _, app = make_client()
    assert client.post("/api/runs", json=direct()).status_code == 201
    app.state.runner.wait(5)
    r = client.post("/api/runs", json=direct(variant="after", **over))
    assert r.status_code == (201 if ok else 422), r.text
    app.state.runner.wait(5)


@pytest.mark.parametrize("start", ["/shop/mypage", "/shop/orders", "/shop/support", "/shop/refund"])
def test_refund_goal_rejects_other_start_page(make_client, start):
    """refund의 허용 경로는 모두 home에서 시작하므로 다른 화면에서는 실행하지 않는다."""
    client, _, _ = make_client()
    r = client.post("/api/runs", json=body(startPath=start))
    assert r.status_code == 422 and r.json()["error"]["code"] == "CONTRACT_VALIDATION_ERROR"


def test_refund_goal_starts_from_home(make_client):
    client, integ, app = make_client()
    run = run_and_wait(client, app, variant="after")   # startPath 생략 → /shop/home
    assert run["startPath"] == "/shop/home"
    from api import runner
    assert integ.received_settings["startUrl"] == runner.SHOP_URLS["after"] + "/shop/home"   # 설정된 after 사이트
    assert all(p[0] == "home" for p in run["referencePathConfig"]["allowedPaths"])
    run = run_and_wait(client, app, experimentKey="exp_002", startPath="/shop/home/")
    assert run["startPath"] == "/shop/home"


def test_presets_require_start_path_matching_allowed_paths(tmp_path):
    base = json.load(open(os.path.join(ROOT, "config", "presets.json"), encoding="utf-8"))
    for broken in ({"startPath": "/shop/mypage"}, {"startPath": None}):
        data = json.loads(json.dumps(base))
        data["goals"][0].update(broken)
        path = tmp_path / "presets.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(ValueError):
            Presets(str(path))


@pytest.mark.parametrize("broken", [
    {"scope": "body"}, {"scope": None}, {"exact": "false"}, {"type": "PATH_ONLY"},
    {"path": "refund"}, {"path": None}, {"text": ""}, {"text": "  "},
])
def test_presets_reject_invalid_verification(tmp_path, broken):
    """잘못된 목표 판정 조건은 실행 중이 아니라 preset 로드(서버 시작) 때 실패한다 (#35)."""
    base = json.load(open(os.path.join(ROOT, "config", "presets.json"), encoding="utf-8"))
    data = json.loads(json.dumps(base))
    data["goals"][0]["verification"].update(broken)
    path = tmp_path / "presets.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        Presets(str(path))


def test_presets_require_allowed_paths_ending_at_goal(tmp_path):
    """허용 경로의 마지막 화면과 도착 판정 주소가 다르면 로드 실패 (#36 리뷰)."""
    base = json.load(open(os.path.join(ROOT, "config", "presets.json"), encoding="utf-8"))
    for change in ({"verification": {"path": "/shop/orders"}}, {"allowedPaths": [["home", "orders"]]}):
        data = json.loads(json.dumps(base))
        goal = data["goals"][0]
        if "verification" in change:
            goal["verification"].update(change["verification"])
        else:
            goal["referencePathConfig"]["allowedPaths"] = change["allowedPaths"]
        path = tmp_path / "presets.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(ValueError, match="도착 화면"):
            Presets(str(path))


def test_presets_allow_missing_exact_and_scope(tmp_path):
    """exact·scope는 생략할 수 있고 기본값(exact=false, scope=page)으로 판정한다."""
    base = json.load(open(os.path.join(ROOT, "config", "presets.json"), encoding="utf-8"))
    data = json.loads(json.dumps(base))
    for g in data["goals"]:
        g["verification"].pop("exact", None)
        g["verification"].pop("scope", None)
    path = tmp_path / "presets.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    goal = Presets(str(path)).goal("refund")
    assert success_condition(goal) == {"path": "/shop/refund", "text": "환불 / 반품 신청", "exact": False, "scope": "page"}


def test_goal_screenshot_returns_arrival_image(make_client, tmp_path):
    """OI-3: 도착 화면은 Step이 없어 GOAL_REACHED Event의 사진을 준다 (#55)."""
    client, _, app = make_client()
    run = run_and_wait(client, app)
    run_dir = app.state.store.run_dir(run["runId"])
    with open(os.path.join(run_dir, "screenshots", "goal.png"), "wb") as f:
        f.write(PNG)
    with open(os.path.join(run_dir, "events.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"eventId": "event_99", "eventType": "GOAL_REACHED",
                            "details": {"screenshotRef": "screenshots/goal.png"}}) + "\n")
    r = client.get(f"/api/runs/{run['runId']}/screenshots/goal")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == PNG
    assert client.get(f"/api/runs/{run['runId']}/screenshots/1").status_code == 200   # stepId 경로는 그대로


@pytest.mark.parametrize("ref", [None, "screenshots/missing.png", "../../outside.png"])
def test_goal_screenshot_404_without_arrival_image(make_client, ref):
    client, _, app = make_client()
    run = run_and_wait(client, app)
    if ref is not None:
        with open(os.path.join(app.state.store.run_dir(run["runId"]), "events.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"eventId": "event_99", "eventType": "GOAL_REACHED",
                                "details": {"screenshotRef": ref}}) + "\n")
    r = client.get(f"/api/runs/{run['runId']}/screenshots/goal")
    assert r.status_code == 404 and r.json()["error"]["code"] == "RUN_NOT_FOUND"
    assert client.get("/api/runs/no_such_run/screenshots/goal").status_code == 404


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


@pytest.mark.parametrize("result,invalid", [
    ({"status": "ERROR", "terminationReason": "LEAK_CHECK_FAILED"}, "LEAK_CHECK_FAILED"),
    ({"status": "ERROR", "terminationReason": "MODEL_ERROR", "error": {"code": "MODEL_UNAVAILABLE"}},
     "INFRASTRUCTURE_FAILURE"),
    ({"status": "ERROR", "terminationReason": "MODEL_ERROR", "error": {"code": "INVALID_CHOICE"}},
     "CORRUPTED_RESPONSE"),
    ({"status": "ERROR", "terminationReason": "BROWSER_ERROR"}, "BROWSER_CRASH"),
    ({"status": "ERROR", "terminationReason": "STORAGE_FAILED"}, "STORAGE_FAILURE"),
    ({"status": "SUCCESS", "terminationReason": "LEAK_CHECK_FAILED"}, "LEAK_CHECK_FAILED"),   # status도 사유로 확정
])
@pytest.mark.parametrize("where", ["result", "run.json"])
def test_wrong_valid_from_agent_is_recomputed(make_client, result, invalid, where):
    """루프가 결과나 run.json에 VALID를 넘겨도 종료 사유·error.code로 다시 정한다."""
    integ = FakeIntegrations(issues=[])
    if where == "result":
        integ.agent_result = {**result, "validity": "VALID", "invalidReason": None}
    else:
        integ.agent_result = dict(result)
        integ.saved_validity = "VALID"
    client, _, app = make_client(integ)
    run = run_and_wait(client, app)
    assert (run["status"], run["validity"], run["invalidReason"]) == ("ERROR", "INVALID", invalid)
    assert client.post("/api/runs", json=body()).status_code == 201   # 무효라서 다시 실행 가능
    app.state.runner.wait(5)


def test_wrong_invalid_from_agent_is_recomputed(make_client):
    """정상 종료인데 루프가 INVALID를 넘겨도 VALID다 (결과가 기대와 다를 뿐인 실행은 재실행 금지)."""
    result = {"status": "MAX_STEPS", "terminationReason": "MAX_STEPS_REACHED",
              "validity": "INVALID", "invalidReason": "BROWSER_CRASH"}
    client, _, app = make_client(FakeIntegrations(agent_result=result, issues=[]))
    run = run_and_wait(client, app)
    assert (run["status"], run["validity"], run["invalidReason"]) == ("MAX_STEPS", "VALID", None)


def test_agent_exception_ends_run_as_error(make_client):
    client, integ, app = make_client(FakeIntegrations(agent_raises=True))
    run = run_and_wait(client, app)
    assert run["status"] == "ERROR" and run["validity"] == "INVALID"
    assert integ.closed == 1
    assert client.post("/api/runs", json=body()).status_code == 201   # 활성 Run이 풀렸음
    app.state.runner.wait(5)


def test_failure_after_agent_saved_valid_is_invalid(make_client):
    integ = FakeIntegrations(issues=[])
    integ.write_valid_then_raise = True
    client, _, app = make_client(integ)
    run = run_and_wait(client, app)
    assert (run["status"], run["validity"], run["invalidReason"]) == ("ERROR", "INVALID", "BROWSER_CRASH")
    integ.write_valid_then_raise = False
    assert client.post("/api/runs", json=body()).status_code == 201   # 무효라서 다시 실행 가능
    app.state.runner.wait(5)


def test_analysis_failure_is_recorded(make_client):
    integ = FakeIntegrations(issues=[])
    integ.issues_fail = True
    client, _, app = make_client(integ)
    run = run_and_wait(client, app)
    assert client.get(f"/api/runs/{run['runId']}/issues").json()["analysisStatus"] == "FAILED"


def test_get_issues_never_writes_files(make_client, tmp_path):
    integ = FakeIntegrations()   # 실행할 때 분석 모듈이 없음
    client, _, app = make_client(integ)
    run = run_and_wait(client, app)
    run_dir = tmp_path / "runs" / run["runId"]
    before = {p.name: p.stat().st_mtime_ns for p in run_dir.iterdir() if p.is_file()}
    integ.issues = [{"issueType": "CHOICE_AMBIGUITY"}]   # 나중에 분석 모듈이 생겨도
    assert client.get(f"/api/runs/{run['runId']}/issues").json() == {
        "runId": run["runId"], "analysisStatus": "PENDING", "items": []}
    after = {p.name: p.stat().st_mtime_ns for p in run_dir.iterdir() if p.is_file()}
    assert before == after and "issues.json" not in after


def test_run_summary_saved_before_issues_completed(make_client, tmp_path):
    integ = FakeIntegrations(issues=[{"issueType": "EXTRA_STEPS"}])
    client, _, app = make_client(integ)
    store = app.state.store
    original = store.save_issues
    seen = {}

    def save_issues(run_id, doc):   # COMPLETED를 쓰는 순간 run.json 요약이 이미 맞아야 한다
        if doc["analysisStatus"] == "COMPLETED":
            seen["types"] = store.load_run(run_id)["summary"]["detectedIssueTypes"]
        original(run_id, doc)
    store.save_issues = save_issues
    run_and_wait(client, app)
    assert seen["types"] == ["EXTRA_STEPS"]


def test_post_hoc_analysis_keeps_summary_consistent(make_client, tmp_path, monkeypatch):
    integ = FakeIntegrations()   # 실행할 때는 분석 모듈 없음
    client, _, app = make_client(integ)
    before = run_and_wait(client, app)
    run_and_wait(client, app, variant="after")

    import scripts.analyze_runs as analyze_runs
    monkeypatch.setattr(analyze_runs, "Integrations",
                        lambda: FakeIntegrations(issues=[{"issueType": "CHOICE_AMBIGUITY"}, {"issueType": "EXTRA_STEPS"}]))
    assert analyze_runs.main(["--all", "--results-dir", str(tmp_path)]) == 0

    stored = json.loads((tmp_path / "runs" / before["runId"] / "run.json").read_text(encoding="utf-8"))
    assert stored["summary"]["detectedIssueTypes"] == []   # 원본 run.json은 그대로
    shown = client.get(f"/api/runs/{before['runId']}").json()
    assert shown["summary"]["detectedIssueTypes"] == ["EXTRA_STEPS", "CHOICE_AMBIGUITY"]

    captured = {}

    def compare_spy():
        def compare(b, a):
            captured["before"] = b["run"]["summary"]["detectedIssueTypes"]
            captured["after"] = a["run"]["summary"]["detectedIssueTypes"]
            return {"experimentKey": "exp_001"}
        return compare
    integ.compare = compare_spy
    assert client.get("/api/comparisons/exp_001").status_code == 200
    assert captured == {"before": ["EXTRA_STEPS", "CHOICE_AMBIGUITY"], "after": ["EXTRA_STEPS", "CHOICE_AMBIGUITY"]}


def test_analyze_script_writes_only_issues(make_client, tmp_path, monkeypatch):
    client, _, app = make_client()   # 분석 모듈 없이 실행
    run = run_and_wait(client, app)
    run_json = tmp_path / "runs" / run["runId"] / "run.json"
    mtime = run_json.stat().st_mtime_ns

    import scripts.analyze_runs as analyze_runs
    monkeypatch.setattr(analyze_runs, "Integrations", lambda: FakeIntegrations(issues=[{"issueType": "EXTRA_STEPS"}]))
    assert analyze_runs.main(["--all", "--results-dir", str(tmp_path)]) == 0
    issues = client.get(f"/api/runs/{run['runId']}/issues").json()
    assert issues["analysisStatus"] == "COMPLETED" and issues["items"] == [{"issueType": "EXTRA_STEPS"}]
    assert run_json.stat().st_mtime_ns == mtime


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
    integ = FakeIntegrations(issues=[])
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
    integ = FakeIntegrations(issues=[])
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


def test_comparison_waits_for_issue_analysis(make_client):
    integ = FakeIntegrations()   # 분석 모듈 없음 → 두 Run 모두 PENDING
    client, _, app = make_client(integ)
    run_and_wait(client, app)
    run_and_wait(client, app, variant="after")
    r = client.get("/api/comparisons/exp_001")
    assert r.status_code == 409 and r.json()["error"]["code"] == "COMPARISON_NOT_READY"


def test_comparison_with_failed_analysis_is_500(make_client):
    integ = FakeIntegrations(issues=[])
    client, _, app = make_client(integ)
    run_and_wait(client, app)
    integ.issues_fail = True
    run_and_wait(client, app, variant="after")
    r = client.get("/api/comparisons/exp_001")
    assert r.status_code == 500 and r.json()["error"]["code"] == "ANALYSIS_ERROR"


@pytest.mark.parametrize("kw", [{"has_compare": False}, {"compare_raises": True}])
def test_comparison_failure_is_500(make_client, kw):
    client, _, app = make_client(FakeIntegrations(issues=[], **kw))
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


# ---- 고정 v2 사이트 제거 (#124): after 사이트는 서버 설정(SHOP_AFTER_URL)으로만
def test_after_run_needs_configured_site(make_client, monkeypatch):
    from api import runner
    monkeypatch.setitem(runner.SHOP_URLS, "after", None)
    client, _, _ = make_client()
    r = client.post("/api/runs", json=body(variant="after"))
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "SITE_NOT_CONFIGURED"
    assert client.get("/api/runs").json()["items"] == []   # Run을 만들지 않았다


def test_before_run_works_without_after_site(make_client, monkeypatch):
    from api import runner
    monkeypatch.setitem(runner.SHOP_URLS, "after", None)
    client, _, app = make_client()
    assert run_and_wait(client, app, variant="before")["status"] == "SUCCESS"


def test_after_run_records_after_site_label(make_client, monkeypatch):
    """Run에는 after 주소 대신 사이트 이름을 남긴다 (#128 리뷰)."""
    from api import runner
    monkeypatch.setattr(runner, "SHOP_AFTER_LABEL", "주문 환불")
    client, _, app = make_client()
    run = run_and_wait(client, app, variant="after")
    assert run["afterSite"] == {"label": "주문 환불"}
    assert "afterSite" not in run_and_wait(client, app, variant="before")


def test_after_runs_from_different_sites_do_not_mix(make_client, monkeypatch):
    """E처럼 예전 after(afterSite 없음)나 다른 after 사이트의 Run이 있는 키에는 after를 다시 만들지 않는다 (#128 리뷰)."""
    from api import runner
    integ = FakeIntegrations(issues=[])
    client, _, app = make_client(integ)
    run_and_wait(client, app)
    integ.open_fails = True
    broken = run_and_wait(client, app, variant="after")   # INVALID라 원래는 다시 만들 수 있다
    store = app.state.runner.store
    legacy = store.load_run(broken["runId"])
    legacy.pop("afterSite")                                # 예전 고정 v2 after Run처럼
    store.save_run(legacy)
    integ.open_fails = False
    r = client.post("/api/runs", json=body(variant="after"))
    assert r.status_code == 422 and "after 사이트가 다릅니다" in r.json()["error"]["message"]

    monkeypatch.setattr(runner, "SHOP_AFTER_LABEL", "주문 환불")
    r = client.post("/api/runs", json=body(experimentKey="exp_new", variant="after"))
    assert r.status_code == 201   # 새 실험 키는 된다


def test_after_run_needs_site_label(make_client, monkeypatch):
    """after 주소만 있고 이름(SHOP_AFTER_LABEL)이 없으면 만들지 않는다 — 이름 없는 사이트끼리 섞이지 않게 (#128 리뷰)."""
    from api import runner
    monkeypatch.setattr(runner, "SHOP_AFTER_LABEL", None)
    client, _, _ = make_client()
    r = client.post("/api/runs", json=body(variant="after"))
    assert r.status_code == 503 and r.json()["error"]["code"] == "SITE_NOT_CONFIGURED"
    assert client.post("/api/runs", json=body(variant="before")).status_code == 201   # before는 상관없음
