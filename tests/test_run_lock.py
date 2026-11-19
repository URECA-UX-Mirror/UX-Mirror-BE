"""프로세스 사이 "한 번에 Run 하나" (#122).

API 서버 안의 Run, 서버에서 직접 돌리는 스크립트(자동 개선 검증 `run_verification`)의 Run, Optimization이
같은 결과 폴더의 파일 잠금 하나를 쓴다. 다른 프로세스가 잡고 있으면 새 Run은 409, 배포는 멈춘다.
잡은 프로세스가 죽으면 운영체제가 잠금을 풀어 주므로 남아서 막히지 않는다.
"""
import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from api.errors import ApiError  # noqa: E402
from api.run_lock import RunLock  # noqa: E402


class OtherProcess:
    """다른 프로세스가 잠금을 잡고 있는 상황. with 블록이 끝나면 그 프로세스를 끝낸다."""

    def __init__(self, results_dir, run_id="run_external", kill=False):
        self.results_dir, self.run_id, self.kill = str(results_dir), run_id, kill

    def __enter__(self):
        code = textwrap.dedent(f"""
            import sys; sys.path.insert(0, {ROOT!r})
            from api.run_lock import RunLock
            lock = RunLock({self.results_dir!r})
            ok = lock.acquire({{"kind": "SCRIPT", "runId": {self.run_id!r}}})
            print("held" if ok else "busy", flush=True)
            sys.stdin.read()
        """)
        self.proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     text=True)
        assert self.proc.stdout.readline().strip() == "held"
        return self

    def __exit__(self, *exc):
        if self.kill:
            self.proc.kill()
        else:
            self.proc.stdin.close()
        self.proc.wait(10)


def test_lock_is_reentrant_within_a_process_and_free_after_release(tmp_path):
    lock = RunLock(str(tmp_path))
    assert lock.holder() is None
    assert lock.acquire({"kind": "IMPROVEMENT", "runId": None})
    assert lock.acquire({"kind": "RUN", "runId": "run_a"})      # 같은 프로세스 안에서는 겹쳐 잡을 수 있다
    assert lock.holder()["runId"] == "run_a" and lock.holder()["pid"] == os.getpid()
    lock.release()
    assert lock.holder()["kind"] == "IMPROVEMENT" and lock.holder()["runId"] is None   # 바깥 잠금은 남는다
    lock.release()
    assert lock.holder() is None
    with pytest.raises(RuntimeError):
        lock.release()


def test_other_process_holding_blocks_and_is_reported(tmp_path):
    with OtherProcess(tmp_path):
        lock = RunLock(str(tmp_path))
        assert lock.acquire({"kind": "RUN", "runId": "run_b"}) is False
        h = lock.holder()
        assert h["runId"] == "run_external" and h["kind"] == "SCRIPT" and h["pid"] != os.getpid()
    assert RunLock(str(tmp_path)).holder() is None


def test_lock_is_released_when_holder_process_dies(tmp_path):
    with OtherProcess(tmp_path, kill=True):
        assert RunLock(str(tmp_path)).acquire({"kind": "RUN"}) is False
    lock = RunLock(str(tmp_path))
    assert lock.acquire({"kind": "RUN", "runId": "run_c"})      # 죽은 프로세스의 잠금은 남지 않는다
    lock.release()


def test_status_cli_for_deploy(tmp_path):
    cmd = [sys.executable, "-m", "api.run_lock", "status", "--results", str(tmp_path)]
    free = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    assert free.returncode == 0 and free.stdout.strip() == ""
    with OtherProcess(tmp_path, run_id="run_cli"):
        busy = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    assert busy.returncode == 1 and "run_cli" in busy.stdout


# ---- API·실행기·검증 함수·Optimization이 같은 잠금을 쓴다

@pytest.fixture
def api(tmp_path):
    from fastapi.testclient import TestClient
    from api.main import create_app
    from test_api import FakeIntegrations
    integ = FakeIntegrations(issues=[])
    app = create_app(results_dir=str(tmp_path), integrations=integ)
    with TestClient(app) as c:
        yield c, integ, tmp_path


def preset_run(**over):
    body = {"experimentKey": "exp_lock_01", "variant": "before", "goalId": "refund", "personaId": "novice-shopper",
            "maxSteps": 5}
    body.update(over)
    return body


def test_api_run_is_refused_while_another_process_runs(api):
    c, _, tmp_path = api
    with OtherProcess(tmp_path, run_id="run_script_1"):
        r = c.post("/api/runs", json=preset_run())
        assert r.status_code == 409 and r.json()["error"]["code"] == "RUN_ALREADY_ACTIVE"
        assert c.get("/api/health").json()["activeRun"] == "run_script_1"   # 배포·FE가 다른 프로세스 Run도 본다
    assert c.get("/api/runs").json()["items"] == []                        # 거절된 Run은 만들어지지 않는다
    r = c.post("/api/runs", json=preset_run())
    assert r.status_code == 201


def test_runner_holds_lock_during_run_and_releases_after(api):
    import threading
    c, integ, tmp_path = api
    integ.gate = threading.Event()
    r = c.post("/api/runs", json=preset_run())
    assert r.status_code == 201
    probe = subprocess.run([sys.executable, "-m", "api.run_lock", "status", "--results", str(tmp_path)], cwd=ROOT,
                           capture_output=True, text=True)
    assert probe.returncode == 1 and r.json()["runId"] in probe.stdout        # 다른 프로세스에서도 보인다
    integ.gate.set()
    c.app.state.runner.wait(10)
    assert RunLock(str(tmp_path)).holder() is None


def test_runner_releases_lock_when_run_fails(api):
    c, integ, tmp_path = api
    integ.agent_raises = True
    assert c.post("/api/runs", json=preset_run()).status_code == 201
    c.app.state.runner.wait(10)
    assert RunLock(str(tmp_path)).holder() is None


def test_restart_recovery_skips_run_owned_by_live_process(tmp_path):
    """서버가 다시 뜰 때 READY·RUNNING Run을 무효 처리하지만, 다른 프로세스가 지금 돌리는 Run은 건드리지 않는다."""
    from api.runner import Runner
    from api.store import RunStore
    from test_api import FakeIntegrations
    store = RunStore(str(tmp_path))
    for rid in ("run_live", "run_dead"):
        os.makedirs(store.run_dir(rid))
        with open(os.path.join(store.run_dir(rid), "run.json"), "w", encoding="utf-8") as f:
            json.dump({"runId": rid, "status": "RUNNING", "terminationReason": None, "progress": {}}, f)
    with OtherProcess(tmp_path, run_id="run_live"):
        fixed = Runner(store, FakeIntegrations()).recover_interrupted()
    assert "run_live" not in fixed and "run_dead" in fixed
    assert store.load_run("run_live")["status"] == "RUNNING"


def test_verification_holds_lock_across_its_runs(tmp_path):
    """검증 함수는 v1·최종안 Run 사이 빈틈에도 잠금을 놓지 않는다 (그 사이 FE Run이 끼어들지 못함)."""
    from api import improvement
    from api.runner import Runner
    from api.store import RunStore
    import test_improvement_verify as t
    integ = t.RecordingIntegrations(issues=[])
    runner = Runner(RunStore(str(tmp_path)), integ)
    real_create, seen = runner.create, []

    def create(run, goal):
        probe = subprocess.run([sys.executable, "-m", "api.run_lock", "status", "--results", str(tmp_path)], cwd=ROOT,
                               capture_output=True, text=True)
        seen.append(probe.returncode)   # Run을 만들기 직전(앞 Run이 끝난 뒤 빈틈)에도 잠겨 있어야 한다
        return real_create(run, goal)
    runner.create = create
    improvement.run_verification(t.frozen(), t.FINAL_URL, "주문·조회", t.goals(), runner=runner)
    assert seen == [1, 1, 1, 1]
    assert RunLock(str(tmp_path)).holder() is None


def test_verification_refused_while_another_process_runs(tmp_path):
    from api import improvement
    from api.runner import Runner
    from api.store import RunStore
    import test_improvement_verify as t
    integ = t.RecordingIntegrations(issues=[])
    runner = Runner(RunStore(str(tmp_path)), integ)
    with OtherProcess(tmp_path, run_id="run_fe"):
        with pytest.raises(ApiError) as e:
            improvement.run_verification(t.frozen(), t.FINAL_URL, "주문·조회", t.goals(), runner=runner)
    assert e.value.code == "RUN_ALREADY_ACTIVE" and e.value.runs == []
    assert integ.opened == [] and runner.store.list_runs() == []


def test_optimization_refused_while_another_process_runs(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from api import main as api_main
    from api.main import create_app
    from test_optimizer import FakeIntegrations as OptIntegrations, label_snapshot, registry
    monkeypatch.setattr(api_main, "_model_reachable", lambda cfg: True)
    results = tmp_path / "results"
    app = create_app(results_dir=str(results), integrations=OptIntegrations(), site_registry=registry(tmp_path))
    app.state.optimizer.snapshot = lambda inst, frozen: label_snapshot(inst["label"])
    with TestClient(app) as c:
        with OtherProcess(results):
            r = c.post("/api/optimizations")
            assert r.status_code == 409 and r.json()["error"]["code"] == "RUN_ALREADY_ACTIVE"
        assert app.state.optimizer.store.list() == []          # 거절된 Optimization은 저장되지 않는다
        r = c.post("/api/optimizations")
        assert r.status_code == 202
        app.state.optimizer.wait(10)
        assert RunLock(str(results)).holder() is None           # 평가가 끝나면 잠금을 놓는다
