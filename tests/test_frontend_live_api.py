"""실제 FastAPI 응답으로 프론트 화면을 검증한다 (별도 실행 선택).

UXM_FRONTEND_PATH에 npm ci가 끝난 프론트 경로를 지정하고 실행한다.
프론트 화면과 Playwright Chromium, uvicorn이 필요하며 저장 데이터는 임시 폴더에만 만든다.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from api.integrations import Integrations
from api.runner import Runner
from api.store import RunStore
from test_analysis_api_integration import seed_run


FRONTEND = os.getenv("UXM_FRONTEND_PATH")
pytestmark = pytest.mark.skipif(not FRONTEND, reason="UXM_FRONTEND_PATH 미설정: 프론트 실연동은 별도 실행")
ROOT = Path(__file__).resolve().parents[1]


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait(port, process):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("통합 테스트 서버가 시작 중 종료되었습니다.")
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError("통합 테스트 서버가 시작되지 않았습니다.")


def _pair(base, case):
    store = RunStore(str(base / case))
    runner = Runner(store, Integrations())
    for variant in ("before", "after"):
        run, _ = seed_run(store, variant, missing_orders=case == "missing_screen" and variant == "after",
                          candidate_mismatch=case == "candidate_mismatch" and variant == "after",
                          system_back_limit=0 if case == "missing_screen" else None,
                          temperature=0.5 if case == "global_mismatch" and variant == "after" else None,
                          probability_shift=0.02 if case == "complete" and variant == "after" else 0)
        _move(store, run, case + "_" + variant, case)
        runner.analyze(run)
        target = base / "runs" / run["runId"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(store.run_dir(run["runId"]), target)


def _move(store, run, new_id, experiment):
    old = Path(store.run_dir(run["runId"]))
    for name in ("steps.jsonl", "events.jsonl"):
        rows = [json.loads(line) for line in (old / name).read_text(encoding="utf-8").splitlines()]
        for row in rows:
            row["runId"] = new_id
        (old / name).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    run.update(runId=new_id, experimentKey=experiment)
    old.rename(store.run_dir(new_id))
    store.save_run(run)


@pytest.fixture(scope="module")
def environment(tmp_path_factory):
    if not FRONTEND:
        pytest.skip("프론트 경로 미설정")
    sync_api = pytest.importorskip("playwright.sync_api")
    frontend = Path(FRONTEND).resolve()
    if not (frontend / "node_modules" / ".bin" / "vite").exists():
        pytest.fail("프론트 경로에서 npm ci를 먼저 실행하세요.")
    base = tmp_path_factory.mktemp("frontend-api")
    for case in ("complete", "missing_screen", "candidate_mismatch", "global_mismatch"):
        _pair(base, case)
    store = RunStore(str(base))
    for case in ("pending", "failed", "invalid", "nochange"):
        run, _ = seed_run(store, case, invalid=case == "invalid")
        _move(store, run, case + "_before", case)
        if case == "nochange":
            run["thresholdConfig"]["entropyThreshold"] = 0.7
            store.save_run(run)
            Runner(store, Integrations()).analyze(run)
        elif case in ("failed", "invalid"):
            store.save_issues(run["runId"], {"schemaVersion": run["schemaVersion"], "runId": run["runId"],
                                             "analysisStatus": "FAILED" if case == "failed" else "COMPLETED", "items": []})
    backend_port, frontend_port = _port(), _port()
    env = os.environ.copy()
    env.update(UXM_RESULTS_DIR=str(base), UXM_CORS_ORIGINS=f"http://127.0.0.1:{frontend_port}")
    processes = []
    try:
        processes.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1",
                                           "--port", str(backend_port)], cwd=ROOT, env=env,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        _wait(backend_port, processes[-1])
        env.update(VITE_USE_MOCK="false", VITE_API_BASE_URL=f"http://127.0.0.1:{backend_port}")
        processes.append(subprocess.Popen(["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port",
                                           str(frontend_port), "--strictPort"], cwd=frontend, env=env,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        _wait(frontend_port, processes[-1])
        with sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                yield f"http://127.0.0.1:{frontend_port}", browser
            finally:
                browser.close()
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                if process is processes[-1] and len(processes) > 1:
                    os.killpg(process.pid, 15)
                else:
                    process.terminate()
            process.wait(timeout=5)


@pytest.fixture
def page(environment):
    base, browser = environment
    page = browser.new_page()
    yield base, page
    page.close()


def test_result_evidence_links_even_without_navigation(page):
    base, page = page
    page.goto(base + "/runs/nochange_before/result", wait_until="networkidle")
    expect = pytest.importorskip("playwright.sync_api").expect
    expect(page.get_by_text("MOCK", exact=True)).to_be_visible()
    issues = page.get_by_role("button", name="일반 검토 메뉴 선택 확률 분산")
    assert issues.count() == 2
    issues.nth(1).click()
    image = page.get_by_alt_text("Step 2 근거 화면")
    expect(image).to_be_visible()
    expect(image).to_have_attribute("src", re.compile(r"/api/runs/nochange_before/screenshots/2"))
    assert image.evaluate("img => img.complete && img.naturalWidth > 0")
    page.get_by_role("button", name="Step 3 보기").click()
    expect(page.get_by_alt_text("Step 3 근거 화면")).to_be_visible()


@pytest.mark.parametrize("case,expected", [("pending", "분석 대기 중"), ("failed", "분석에 실패했습니다"),
                                          ("invalid", "무효 처리된 실행")])
def test_result_statuses(page, case, expected):
    base, page = page
    page.goto(base + f"/runs/{case}_before/result", wait_until="networkidle")
    pytest.importorskip("playwright.sync_api").expect(page.get_by_text(expected, exact=True).first).to_be_visible()


@pytest.mark.parametrize("case,expected", [("complete", "모든 화면을 비교했습니다"),
    ("missing_screen", "After에서 방문하지 않은 화면입니다"),
    ("candidate_mismatch", "후보 구성이 달라 비교할 수 없습니다"),
    ("global_mismatch", "비교 조건이 일치하지 않습니다")])
def test_comparison_states(page, case, expected):
    base, page = page
    page.goto(base + f"/comparisons/{case}", wait_until="networkidle")
    expect = pytest.importorskip("playwright.sync_api").expect
    expect(page.get_by_text(expected, exact=False).first).to_be_visible()
    expect(page.get_by_text("MOCK", exact=True)).to_be_visible()
    if case == "complete":
        expect(page.get_by_text("+2.0%p", exact=True).first).to_be_visible()
        images = page.locator('img[src*="/screenshots/"]')
        assert images.count() >= 2
        assert "complete_before" in images.nth(0).get_attribute("src")
        assert "complete_after" in images.nth(1).get_attribute("src")
    elif case in ("candidate_mismatch", "global_mismatch"):
        expect(page.get_by_text("계산 불가", exact=False).first).to_be_visible()
