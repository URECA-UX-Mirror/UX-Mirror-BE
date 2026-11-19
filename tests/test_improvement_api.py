"""자동 개선 실행·리포트 API (#130).

API 쪽은 가짜 실행 프로세스(Run 잠금을 잡고 상태·리포트를 쓰는 작은 스크립트)로, 실행 스크립트 쪽
(`scripts/improve_live.run_job`)은 루프·마무리를 가짜로 바꿔 끼워 본다. 최종안 사이트 빌드·서빙은 실제로 한다.
"""
import json
import os
import subprocess
import sys
import textwrap
import time
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from api.run_lock import RunLock  # noqa: E402
import improve_live  # noqa: E402

FAKE_DRIVER = textwrap.dedent('''
    import json, os, sys, time
    sys.path.insert(0, {root!r})
    from api.run_lock import RunLock
    args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
    imp_id, results = args["--improvement-id"], args["--results-dir"]
    mode, delay = os.environ.get("FAKE_MODE", "ok"), float(os.environ.get("FAKE_DELAY", "0.5"))
    job = os.path.join(results, "improvement_jobs", imp_id)
    def status(**kw):
        p = os.path.join(job, "status.json"); s = json.load(open(p, encoding="utf-8")); s.update(kw)
        json.dump(s, open(p + ".tmp", "w", encoding="utf-8"), ensure_ascii=False); os.replace(p + ".tmp", p)
    lock = RunLock(results)
    if not lock.acquire({{"kind": "IMPROVEMENT", "runId": None, "improvementId": imp_id}}):
        status(status="FAILED", error={{"code": "RUN_ALREADY_ACTIVE", "message": "busy"}}); sys.exit(1)
    status(status="RUNNING", phase="LOOP", pid=os.getpid(), startedAt="t1", phases=[{{"phase": "LOOP", "at": "t1"}}])
    if mode == "hang":
        time.sleep(60)
    time.sleep(delay)
    if mode == "fail":
        lock.release()
        status(status="FAILED", phase="LOOP", finishedAt="t2", error={{"code": "ConnectionError", "message": "모델 서버 연결 실패"}})
        sys.exit(1)
    out = os.path.join(results, "improvements", imp_id); os.makedirs(out)
    json.dump({{"schemaVersion": "improvement-v1", "improvementId": imp_id, "status": "COMPLETED",
               "summary": {{"result": "IMPROVED", "beforeLabel": "이용정보", "afterLabel": "주문 환불",
                           "main": {{"before": 0.4, "after": 0.6}}}}}},
              open(os.path.join(out, "improvement.json"), "w", encoding="utf-8"), ensure_ascii=False)
    lock.release()   # 실제 실행 스크립트와 같은 순서: 잠금을 푼 뒤 '끝남'을 쓴다
    status(status="COMPLETED", phase="DONE", finishedAt="t2")
''').format(root=ROOT)


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import create_app
    from test_api import FakeIntegrations
    driver = tmp_path / "fake_driver.py"
    driver.write_text(FAKE_DRIVER, encoding="utf-8")
    results = tmp_path / "results"
    clients = []

    def _make(mode="ok", delay="0.5"):
        monkeypatch.setenv("FAKE_MODE", mode)
        monkeypatch.setenv("FAKE_DELAY", delay)
        app = create_app(results_dir=str(results), integrations=FakeIntegrations(issues=[]))
        app.state.improvements.driver = [sys.executable, str(driver)]
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        return c, app, results
    yield _make
    for c in clients:
        c.__exit__(None, None, None)


def wait_status(c, imp_id, want, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        v = c.get(f"/api/improvements/{imp_id}").json()
        if v["status"] in want:
            return v
        time.sleep(0.1)
    raise AssertionError(f"{imp_id} 상태가 {want}가 되지 않음: {v}")


def test_start_runs_and_blocks_others_until_done(make_client):
    c, app, results = make_client(delay="1.5")
    r = c.post("/api/improvements")
    assert r.status_code == 202
    v = r.json()
    imp_id = v["improvementId"]
    assert imp_id.startswith("imp-live-") and v["status"] in ("QUEUED", "RUNNING")
    # 시작 응답이 올 때 실행 프로세스는 이미 Run 잠금을 잡고 있다 → 다른 실행은 바로 막힌다
    assert c.post("/api/improvements").status_code == 409
    run = {"experimentKey": "exp_imp_block", "variant": "before", "goalId": "refund", "personaId": "novice-shopper",
           "maxSteps": 5}
    rr = c.post("/api/runs", json=run)
    assert rr.status_code == 409 and rr.json()["error"]["code"] == "RUN_ALREADY_ACTIVE"
    h = c.get("/api/health").json()
    assert h["activeRunOwner"]["kind"] == "IMPROVEMENT" and h["activeRun"] is None
    done = wait_status(c, imp_id, ("COMPLETED",))
    assert done["phase"] == "DONE" and done["error"] is None
    assert done["improvement"]["summary"]["afterLabel"] == "주문 환불"
    items = c.get("/api/improvements").json()["items"]
    assert items[0]["improvementId"] == imp_id and items[0]["summary"]["result"] == "IMPROVED"
    assert RunLock(str(results)).holder() is None
    assert c.post("/api/runs", json=run).status_code == 201   # 끝나면 다시 실행할 수 있다


def test_response_has_no_internal_details(make_client):
    c, _, _ = make_client(delay="0.2")
    imp_id = c.post("/api/improvements").json()["improvementId"]
    v = wait_status(c, imp_id, ("COMPLETED",))
    text = json.dumps(v, ensure_ascii=False) + json.dumps(c.get("/api/improvements").json(), ensure_ascii=False)
    for bad in ("pid", "log.txt", "127.0.0.1", "plan.json"):
        assert bad not in text


def test_refused_while_another_process_runs(make_client):
    from test_run_lock import OtherProcess
    c, _, results = make_client()
    with OtherProcess(results, run_id="run_fe"):
        r = c.post("/api/improvements")
        assert r.status_code == 409 and r.json()["error"]["code"] == "RUN_ALREADY_ACTIVE"
    assert c.get("/api/improvements").json()["items"] == []


def test_failed_run_is_reported_and_releases_lock(make_client):
    c, _, results = make_client(mode="fail", delay="0.1")
    imp_id = c.post("/api/improvements").json()["improvementId"]
    v = wait_status(c, imp_id, ("FAILED",))
    assert v["error"]["code"] == "ConnectionError" and v["improvement"] is None
    time.sleep(0.3)
    assert RunLock(str(results)).holder() is None


def test_killed_run_becomes_interrupted(make_client):
    c, app, results = make_client(mode="hang")
    imp_id = c.post("/api/improvements").json()["improvementId"]
    proc = app.state.improvements._procs[imp_id]
    proc.kill()
    proc.wait(10)
    v = wait_status(c, imp_id, ("INTERRUPTED",))
    assert v["error"]["code"] == "IMPROVEMENT_INTERRUPTED"
    assert RunLock(str(results)).holder() is None       # 죽은 프로세스의 잠금은 남지 않는다


def test_existing_results_are_listed(make_client):
    c, _, results = make_client()
    folder = results / "improvements" / "imp-1119b"
    folder.mkdir(parents=True)
    (folder / "improvement.json").write_text(json.dumps({
        "schemaVersion": "improvement-v1", "improvementId": "imp-1119b", "status": "COMPLETED",
        "createdAt": "2026-11-20T00:05:39+09:00", "finishedAt": "2026-11-20T00:06:58+09:00",
        "summary": {"result": "IMPROVED", "beforeLabel": "이용정보", "afterLabel": "주문 환불", "main": {}}}),
        encoding="utf-8")
    v = c.get("/api/improvements/imp-1119b").json()
    assert v["status"] == "COMPLETED" and v["improvement"]["improvementId"] == "imp-1119b"
    assert [i["improvementId"] for i in c.get("/api/improvements").json()["items"]] == ["imp-1119b"]


@pytest.mark.parametrize("bad", ["nope", "..%2F..%2Fetc", "a b"])
def test_unknown_or_unsafe_id_is_404(make_client, bad):
    c, _, _ = make_client()
    r = c.get(f"/api/improvements/{bad}")
    assert r.status_code == 404


# ---------------------------------------------------------------- 실행 스크립트 (scripts/improve_live.py)

PLAN = {"improvementId": "imp-1119b", "status": "FROZEN", "persona": "p",
        "verification": {"frozen": {"improvementId": "imp-1119b"}, "goals": ["S1"]}}


def test_make_plan_changes_only_ids():
    plan = improve_live.make_plan(PLAN, "imp-live-x")
    assert plan["improvementId"] == "imp-live-x" and plan["verification"]["frozen"]["improvementId"] == "imp-live-x"
    assert plan["live"]["basedOnPlan"] == "imp-1119b" and plan["persona"] == "p" and PLAN["improvementId"] == "imp-1119b"
    with pytest.raises(ValueError):
        improve_live.make_plan({**PLAN, "status": "DRAFT"}, "imp-live-x")


class FakeImprove:
    def __init__(self, label="주문 환불", loop_error=None):
        self.label, self.loop_error, self.calls = label, loop_error, []

    def cmd_loop(self, args):
        self.calls.append(("loop", args.plan, args.force))
        if self.loop_error:
            raise self.loop_error
        plan = json.load(open(args.plan, encoding="utf-8"))
        out = os.path.join(args.results_dir, "improvements", plan["improvementId"])
        os.makedirs(out)
        json.dump({"summary": {"afterLabel": self.label}}, open(os.path.join(out, "improvement.json"), "w"))

    def cmd_finalize(self, args):
        self.calls.append(("finalize", args.final_url, args.skip_verify))


class FakeServer:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def template(tmp_path):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(PLAN), encoding="utf-8")
    return str(p)


def test_run_job_loop_then_final_site_then_finalize(tmp_path):
    results, imp, served, server = str(tmp_path / "r"), FakeImprove(), [], FakeServer()

    def serve(label, folder):
        served.append(label)
        assert (RunLock(results).holder() or {}).get("improvementId") == "imp-live-a"   # 끝까지 잠금을 잡고 있다
        return "http://127.0.0.1:18999", server
    s = improve_live.run_job("imp-live-a", results, template_path=template(tmp_path), improve=imp, serve=serve)
    assert s["status"] == "COMPLETED" and [p["phase"] for p in s["phases"]] == [
        "LOOP", "BUILD_FINAL_SITE", "OFFICIAL_AND_VERIFY", "DONE"]
    assert served == ["주문 환불"] and server.terminated
    assert imp.calls[0][2] is False and imp.calls[1] == ("finalize", "http://127.0.0.1:18999", False)
    saved = json.load(open(os.path.join(results, "improvement_jobs", "imp-live-a", "status.json"), encoding="utf-8"))
    assert saved["status"] == "COMPLETED" and "18999" not in json.dumps(saved)
    assert RunLock(results).holder() is None


def test_run_job_without_adopted_label_skips_final_site(tmp_path):
    results, imp = str(tmp_path / "r"), FakeImprove(label=None)
    s = improve_live.run_job("imp-live-b", results, template_path=template(tmp_path), improve=imp,
                             serve=lambda *a: pytest.fail("채택 이름이 없으면 최종안 사이트를 만들지 않는다"))
    assert s["status"] == "COMPLETED" and [p["phase"] for p in s["phases"]] == ["LOOP", "DONE"]
    assert [c[0] for c in imp.calls] == ["loop"]


def test_run_job_failure_is_recorded_without_addresses(tmp_path):
    results = str(tmp_path / "r")
    imp = FakeImprove(loop_error=SystemExit("동결한 조건과 다릅니다: 모델 http://10.0.0.9:9000 연결 127.0.0.1:8001"))
    s = improve_live.run_job("imp-live-c", results, template_path=template(tmp_path), improve=imp)
    assert s["status"] == "FAILED" and s["error"]["code"] == "CONDITION_MISMATCH"
    assert "10.0.0.9" not in s["error"]["message"] and "8001" not in s["error"]["message"]
    assert RunLock(results).holder() is None


def test_run_job_refuses_when_another_process_runs(tmp_path):
    from test_run_lock import OtherProcess
    results = str(tmp_path / "r")
    os.makedirs(results)
    with OtherProcess(results):
        s = improve_live.run_job("imp-live-d", results, template_path=template(tmp_path), improve=FakeImprove())
    assert s["status"] == "FAILED" and s["error"]["code"] == "RUN_ALREADY_ACTIVE"


def test_build_and_serve_real_final_site(tmp_path):
    """채택 이름으로 실제 최종안 사이트를 만들고 띄운다 (상단 메뉴 글자만 바뀜)."""
    url, proc = improve_live.build_and_serve("주문 환불", str(tmp_path))
    try:
        html = urllib.request.urlopen(url + "/shop/home", timeout=5).read().decode("utf-8")
        assert "주문 환불" in html and url.startswith("http://127.0.0.1:")
    finally:
        proc.terminate()
        proc.wait(10)


# ---------------------------------------------------------------- 입력으로 돌리기 (사용자가 넣은 고객·할 일·도착 화면)

REAL_TEMPLATE = json.load(open(os.path.join(ROOT, "results", "improvement_plan_1119b.json"), encoding="utf-8"))


def test_make_plan_uses_user_input_and_keeps_rules():
    inp = {"goal": "쿠폰이 몇 장 남았는지 보고 싶어요", "persona": "50대 이상 고객", "goalPage": "mypage"}
    plan = improve_live.make_plan(REAL_TEMPLATE, "imp-live-x", inp)
    main = plan["mainGoal"]
    assert (main["goalKey"], main["goal"], main["goalPage"]) == ("MAIN", inp["goal"], "mypage")
    assert main["verification"]["path"] == "/shop/mypage" and main["verification"]["text"]   # 그 화면의 확인 글자
    assert sorted(g["goalPage"] for g in plan["guardGoals"]) == ["orders", "refund", "support"]   # 도착 화면이 다른 3개
    assert plan["verification"]["goals"] == ["MAIN", "S4"]
    assert plan["persona"] == plan["verification"]["frozen"]["persona"] == "50대 이상 고객"
    for key in ("score", "rules", "model", "edit"):     # 측정 기준은 그대로
        assert plan[key] == REAL_TEMPLATE[key]
    assert plan["live"]["input"] == inp
    support = improve_live.make_plan(REAL_TEMPLATE, "imp-live-y", {**inp, "goalPage": "support"})
    assert support["verification"]["goals"][0] == "MAIN" and support["verification"]["goals"][1] != "S4"
    with pytest.raises(ValueError):
        improve_live.make_plan(REAL_TEMPLATE, "imp-live-z", {**inp, "goalPage": "home"})


def test_start_with_direct_input_records_it(make_client):
    c, _, results = make_client(delay="0.1")
    body = {"goal": "지난주 산 운동화가 안 맞아서 돌려보내고 싶어요", "persona": "쇼핑이 낯선 고객", "goalPage": "refund"}
    r = c.post("/api/improvements", json=body)
    assert r.status_code == 202
    v = wait_status(c, r.json()["improvementId"], ("COMPLETED",))
    assert v["input"] == body
    saved = json.load(open(results / "improvement_jobs" / v["improvementId"] / "input.json", encoding="utf-8"))
    assert saved == body


def test_start_from_diagnosis_run(make_client):
    c, app, results = make_client(delay="0.1")
    run_dir = results / "runs" / "run_before_20261120050000_ab12"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "runId": "run_before_20261120050000_ab12", "variant": "before", "status": "BLOCKED", "validity": "VALID",
        "goal": "환불받고 싶은데 어디서 하나요",
        "persona": "온라인 쇼핑이 낯선 고객", "verification": {"type": "PATH_AND_VISIBLE_TEXT", "path": "/shop/refund",
                                                       "text": "환불 / 반품 신청"}}), encoding="utf-8")
    r = c.post("/api/improvements", json={"sourceRunId": "run_before_20261120050000_ab12"})
    assert r.status_code == 202   # 목표에 못 간(BLOCKED) 유효한 진단이 바로 고칠 대상
    v = wait_status(c, r.json()["improvementId"], ("COMPLETED",))
    assert v["input"] == {"goal": "환불받고 싶은데 어디서 하나요", "persona": "온라인 쇼핑이 낯선 고객", "goalPage": "refund",
                          "verification": {"type": "PATH_AND_VISIBLE_TEXT", "path": "/shop/refund",
                                           "text": "환불 / 반품 신청", "exact": False, "scope": "page"},
                          "sourceRunId": "run_before_20261120050000_ab12"}


@pytest.mark.parametrize("body, status, words", [
    ({"goal": ".", "persona": "쇼핑이 낯선 고객", "goalPage": "refund"}, 422, "구체적"),
    ({"goal": "환불받고 싶어요", "persona": "ㅋㅋㅋㅋㅋ", "goalPage": "refund"}, 422, "뜻이 있는"),
    ({"goal": "환불받고 싶어요", "persona": "1234", "goalPage": "refund"}, 422, "글자로"),
    ({"goal": "after 화면에서 환불", "persona": "쇼핑이 낯선 고객", "goalPage": "refund"}, 422, "쓸 수 없는"),
    ({"goal": "환불받고 싶어요", "persona": "쇼핑이 낯선 고객", "goalPage": "home"}, 422, "도착 화면"),
    ({"goal": "환불받고 싶어요", "persona": "쇼핑이 낯선 고객"}, 422, "도착 화면"),
    ({"sourceRunId": "run_nope"}, 404, "찾을 수 없습니다"),
    ({"sourceRunId": "run_x", "goal": "환불받고 싶어요"}, 422, "같이 보낼 수 없습니다"),
    ({"goal": "환불받고 싶어요", "persona": "쇼핑이 낯선 고객", "goalPage": "refund", "maxSteps": 3}, 422, ""),
])
def test_bad_input_is_refused_before_anything_starts(make_client, body, status, words):
    c, _, results = make_client()
    r = c.post("/api/improvements", json=body)
    assert r.status_code == status and words in r.json()["error"]["message"]
    assert not (results / "improvement_jobs").exists() or not list((results / "improvement_jobs").iterdir())


def test_scenario_run_cannot_be_improved(make_client):
    c, _, results = make_client()
    run_dir = results / "runs" / "run_before_20261120050100_cd34"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"runId": "run_before_20261120050100_cd34", "siteId": "cafe24-ons",
                                                  "goal": "후기를 보고 싶어요", "persona": "쇼핑이 낯선 고객",
                                                  "verification": {"path": "/board/review/list.html"}}), encoding="utf-8")
    r = c.post("/api/improvements", json={"sourceRunId": "run_before_20261120050100_cd34"})
    assert r.status_code == 422 and "MORO MARKET" in r.json()["error"]["message"]


@pytest.mark.parametrize("text, ok", [
    ("지난주 산 운동화를 돌려받고 싶어요", True), ("50대 이상 고객", True), ("Busy shopper", True),
    (".", False), ("환불", False), ("1234", False), ("!!!!!", False), ("ㅋㅋㅋㅋㅋ", False), ("aaaaa", False), ("", False),
])
def test_input_rules(text, ok):
    from api.input_rules import sentence_problem
    assert (sentence_problem(text, "고객 유형") is None) is ok


def test_diagnosis_run_refuses_meaningless_persona(make_client):
    c, _, _ = make_client()
    body = {"experimentKey": "exp_rule_01", "variant": "before", "goal": "환불받고 싶어요 어디서 하나요",
            "verification": {"path": "/shop/refund", "text": "환불 / 반품 신청"}, "persona": ".", "maxSteps": 5}
    r = c.post("/api/runs", json=body)
    assert r.status_code == 422 and "구체적" in r.json()["error"]["message"]


# ---------------------------------------------------------------- #131 리뷰: 공식 결과와 심사 실행 구분 · 끄기 스위치 · 오류 문구

def test_live_and_official_results_are_marked(make_client):
    c, _, results = make_client(delay="0.1")
    folder = results / "improvements" / "imp-1119b"
    folder.mkdir(parents=True)
    (folder / "improvement.json").write_text(json.dumps({"improvementId": "imp-1119b", "status": "COMPLETED",
                                                         "createdAt": "2026-11-20T00:05:39+09:00", "summary": {}}), encoding="utf-8")
    live = c.post("/api/improvements").json()["improvementId"]
    wait_status(c, live, ("COMPLETED",))
    practice = results / "improvements" / "imp-practice-01"   # 상태 파일 없이 결과만 있는 연습 실행
    practice.mkdir(parents=True)
    (practice / "improvement.json").write_text(json.dumps({"improvementId": "imp-practice-01", "status": "COMPLETED",
                                                           "summary": {}}), encoding="utf-8")
    kinds = {i["improvementId"]: i["kind"] for i in c.get("/api/improvements").json()["items"]}
    # 공식 = 동결된 사전 등록 계획의 id만. 결과 파일만 있다고 공식으로 보지 않는다
    assert kinds == {"imp-1119b": "OFFICIAL", live: "LIVE", "imp-practice-01": "UNKNOWN"}
    assert c.get(f"/api/improvements/{live}").json()["kind"] == "LIVE"


def test_live_plan_is_not_the_official_plan():
    plan = improve_live.make_plan(REAL_TEMPLATE, "imp-live-x")
    assert plan["improvementPlanId"] == f"{REAL_TEMPLATE['improvementPlanId']}-live" and "secondRun" not in plan


def test_improvements_can_be_switched_off(make_client, monkeypatch):
    c, _, _ = make_client()
    monkeypatch.setenv("UXM_IMPROVEMENTS_ENABLED", "0")
    r = c.post("/api/improvements")
    assert r.status_code == 503 and r.json()["error"]["code"] == "IMPROVEMENTS_DISABLED"
    assert c.get("/api/improvements").status_code == 200


def test_error_message_hides_paths():
    msg = improve_live.scrub("파일 없음: /home/ubuntu/UX-Mirror-BE/results/x.json, C:\\AI\\Zoro-1 와 http://10.0.0.1:9000")
    assert "/home/" not in msg and "C:\\" not in msg and "10.0.0.1" not in msg


# ---------------------------------------------------------------- #131 리뷰 2: 원본 진단 조건 · 성공 조건 유지 · health

def _source_run(results, run_id, **fields):
    run = {"runId": run_id, "variant": "before", "status": "BLOCKED", "validity": "VALID",
           "goal": "환불받고 싶은데 어디서 하나요", "persona": "온라인 쇼핑이 낯선 고객",
           "verification": {"type": "PATH_AND_VISIBLE_TEXT", "path": "/shop/refund", "text": "환불 / 반품 신청"}}
    run.update(fields)
    folder = results / "runs" / run_id
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")


def test_custom_success_condition_of_source_run_is_kept(make_client):
    c, _, results = make_client(delay="0.1")
    custom = {"type": "PATH_AND_VISIBLE_TEXT", "path": "/shop/refund", "text": "반품 신청", "exact": True, "scope": "main"}
    _source_run(results, "run_before_20261120060000_aa11", verification=custom)
    r = c.post("/api/improvements", json={"sourceRunId": "run_before_20261120060000_aa11"})
    assert r.status_code == 202
    v = wait_status(c, r.json()["improvementId"], ("COMPLETED",))
    assert v["input"]["verification"] == custom
    plan = improve_live.make_plan(REAL_TEMPLATE, "imp-live-x", v["input"])
    assert plan["mainGoal"]["verification"] == custom   # 루프·실제 검증 Run이 같은 조건으로 판정
    assert "MAIN" in plan["verification"]["goals"]


@pytest.mark.parametrize("fields, words", [
    ({"status": "RUNNING"}, "끝나지 않았어요"),
    ({"status": "READY"}, "끝나지 않았어요"),
    ({"validity": "INVALID", "status": "ERROR"}, "결과로 쓸 수 없는"),
    ({"validity": None}, "결과로 쓸 수 없는"),
    ({"variant": "after"}, "지금 화면"),
    ({"improvementContext": {"improvementId": "imp-1119b"}}, "확인용 진단"),
    ({"optimizationContext": {"optimizationId": "opt_1"}}, "확인용 진단"),
    ({"verification": {"path": "/shop/refund"}}, "성공 조건이 없어"),
])
def test_only_finished_valid_before_diagnosis_can_start(make_client, fields, words):
    c, _, results = make_client()
    _source_run(results, "run_before_20261120060100_bb22", **fields)
    r = c.post("/api/improvements", json={"sourceRunId": "run_before_20261120060100_bb22"})
    assert r.status_code == 422 and words in r.json()["error"]["message"]
    assert not (results / "improvement_jobs").exists() or not list((results / "improvement_jobs").iterdir())


def test_nested_run_keeps_improvement_id(tmp_path):
    lock = RunLock(str(tmp_path))
    assert lock.acquire({"kind": "IMPROVEMENT", "runId": None, "improvementId": "imp-live-x"})
    assert lock.acquire({"kind": "RUN", "runId": "run_before_1"})   # 자동 개선 안의 검증 Run
    holder = lock.holder()
    assert holder["kind"] == "RUN" and holder["runId"] == "run_before_1" and holder["improvementId"] == "imp-live-x"
    info = json.load(open(tmp_path / ".active_run.json", encoding="utf-8"))   # 다른 프로세스(API)가 읽는 정보
    assert info["improvementId"] == "imp-live-x"
    lock.release()
    assert lock.holder()["kind"] == "IMPROVEMENT"
    lock.release()
    assert lock.holder() is None


def test_health_shows_improvement_during_its_verification_run(make_client):
    c, app, _ = make_client()
    lock = app.state.improvements.run_lock
    assert lock.acquire({"kind": "IMPROVEMENT", "runId": None, "improvementId": "imp-live-y"})
    assert lock.acquire({"kind": "RUN", "runId": "run_before_2"})
    try:
        owner = c.get("/api/health").json()["activeRunOwner"]
        assert owner["improvementId"] == "imp-live-y" and owner["runId"] == "run_before_2"
    finally:
        lock.release()
        lock.release()
