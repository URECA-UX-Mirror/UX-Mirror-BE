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
        status(status="FAILED", phase="LOOP", finishedAt="t2", error={{"code": "ConnectionError", "message": "모델 서버 연결 실패"}})
        sys.exit(1)
    out = os.path.join(results, "improvements", imp_id); os.makedirs(out)
    json.dump({{"schemaVersion": "improvement-v1", "improvementId": imp_id, "status": "COMPLETED",
               "summary": {{"result": "IMPROVED", "beforeLabel": "이용정보", "afterLabel": "주문 환불",
                           "main": {{"before": 0.4, "after": 0.6}}}}}},
              open(os.path.join(out, "improvement.json"), "w", encoding="utf-8"), ensure_ascii=False)
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
