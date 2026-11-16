"""실제 분석·비교 함수를 API와 파일 저장을 통해 함께 검증한다."""
import copy
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.integrations import Integrations
from api.main import create_app
from api.runner import Runner
from api.store import RunStore
from uxm.metrics import decision_metrics
from uxm.storage import read_jsonl


SAMPLE = Path(__file__).resolve().parents[1] / "results" / "runs" / "sample_before"


def seed_run(store, variant, *, missing_orders=False, candidate_mismatch=False,
             temperature=None, probability_shift=0, invalid=False):
    """저장된 MOCK 실행을 독립된 임시 결과 폴더에 복제한다."""
    run = json.loads((SAMPLE / "run.json").read_text(encoding="utf-8"))
    run.update(runId=f"integration_{variant}", variant=variant, experimentKey="integration_pair")
    if temperature is not None:
        run["temperature"] = temperature
    if invalid:
        run.update(validity="INVALID", status="ERROR", terminationReason="BROWSER_ERROR",
                   invalidReason="BROWSER_CRASH", summary=None)
    steps = read_jsonl(str(SAMPLE / "steps.jsonl"))
    events = read_jsonl(str(SAMPLE / "events.jsonl"))
    for item in steps + events:
        item["runId"] = run["runId"]
    if candidate_mismatch:
        mypage = next(step for step in steps if step["page"]["pageKey"] == "mypage")
        candidate = mypage["candidateSet"]["candidates"][-1]
        candidate["href"] = "/shop/alternative"
        candidate["semanticKey"] = candidate["region"] + ">/shop/alternative#1"
    if probability_shift:
        home = steps[0]
        candidates = home["candidateSet"]["candidates"]
        candidates[0]["probability"] += probability_shift
        candidates[1]["probability"] -= probability_shift
        probs = {candidate["optionId"]: candidate["probability"] for candidate in candidates}
        home["decision"].update(decision_metrics(probs))
        home["decision"]["goalPathProbability"] += probability_shift
    if missing_orders:
        steps = [step for step in steps if step["page"]["pageKey"] != "orders"]
        # orders에 도착한 직후 제한으로 종료해 orders에서 판단한 Step은 없다.
        run.update(status="MAX_STEPS", terminationReason="MAX_STEPS_REACHED")
        run["summary"].update(goalReached=False, stepCount=4, stepRecordCount=4,
                              modelCallCount=4, clickCount=3, successfulNavigationClickCount=2,
                              navigationCost=3, extraSteps=None, extraNavigationCost=None,
                              path=run["summary"]["path"][:-1])
    store.save_run(run)
    directory = Path(store.run_dir(run["runId"]))
    for name, rows in (("steps.jsonl", steps), ("events.jsonl", events)):
        (directory / name).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                                      encoding="utf-8")
    shutil.copytree(SAMPLE / "screenshots", directory / "screenshots", dirs_exist_ok=True)
    return run, steps


@pytest.mark.parametrize("case", ["complete", "missing_screen", "candidate_mismatch", "global_mismatch"])
def test_analyze_read_and_compare_through_api(tmp_path, case):
    store = RunStore(str(tmp_path))
    before, before_steps = seed_run(store, "before")
    after, _ = seed_run(store, "after", missing_orders=case == "missing_screen",
                        candidate_mismatch=case == "candidate_mismatch",
                        temperature=0.5 if case == "global_mismatch" else None)
    originals = copy.deepcopy((before, after))
    # Runner가 실제 분석 함수를 호출하고 결과를 기록한다.
    runner = Runner(store, Integrations())
    for run in (before, after):
        runner.analyze(copy.deepcopy(run))
    assert (before, after) == originals
    app = create_app(results_dir=str(tmp_path), integrations=Integrations())
    with TestClient(app) as client:
        for run in (before, after):
            run_id = run["runId"]
            issue_response = client.get(f"/api/runs/{run_id}/issues")
            run_response = client.get(f"/api/runs/{run_id}")
            assert issue_response.status_code == run_response.status_code == 200
            issues = issue_response.json()
            stored = store.load_issues(run_id)
            assert issues == {key: stored[key] for key in ("runId", "analysisStatus", "items")}
            assert issues["analysisStatus"] == "COMPLETED" and issues["items"]
            assert set(run_response.json()["summary"]["detectedIssueTypes"]) == {
                issue["issueType"] for issue in issues["items"]}
            assert all(issue["runId"] == run_id for issue in issues["items"])
        step_response = client.get(f"/api/runs/{before['runId']}/steps")
        assert step_response.status_code == 200
        assert step_response.json()["items"][0]["stepId"] == before_steps[0]["stepId"]
        screenshot = client.get(f"/api/runs/{before['runId']}/screenshots/1")
        assert screenshot.status_code == 200 and screenshot.headers["content-type"] == "image/png"
        response = client.get("/api/comparisons/integration_pair")
        assert response.status_code == 200
        result = response.json()
        assert result == json.loads((tmp_path / "comparisons/integration_pair.json").read_text())
        assert result["beforeRunId"] == before["runId"] and result["afterRunId"] == after["runId"]
        assert len(result["conditionChecks"]) == 21
        assert result["runComparison"]["before"]["detectedIssueTypes"] == store.load_run(before["runId"])["summary"]["detectedIssueTypes"]
        screens = {item["pageKey"]: item for item in result["screenComparisons"]}
        if case == "complete":
            assert result["comparisonStatus"] == "COMPLETED" and result["conditionsMatched"]
            assert screens["home"]["screenComparable"]
            assert screens["home"]["candidateComparisons"][0]["deltaProbability"] == 0
        elif case == "missing_screen":
            assert result["comparisonStatus"] == "PARTIAL" and result["conditionsMatched"]
            assert screens["orders"]["reason"] == "NOT_VISITED_IN_AFTER"
            assert screens["orders"]["metrics"] is None and screens["orders"]["candidateComparisons"] == []
        elif case == "candidate_mismatch":
            assert result["comparisonStatus"] == "PARTIAL" and result["conditionsMatched"]
            assert screens["home"]["screenComparable"]
            assert screens["mypage"]["reason"] == "CANDIDATE_SET_MISMATCH"
            assert all(item["deltaProbability"] is None for item in screens["mypage"]["candidateComparisons"])
        else:
            assert result["comparisonStatus"] == "PARTIAL" and not result["conditionsMatched"]
            assert screens["home"]["reason"] == "GLOBAL_CONDITIONS_MISMATCH"
            assert all(item["deltaProbability"] is None for item in screens["home"]["candidateComparisons"])
            assert all(value is None for key, value in screens["home"]["metrics"].items() if key.startswith("delta"))


def test_invalid_run_is_not_analyzed_or_compared(tmp_path):
    store = RunStore(str(tmp_path))
    invalid, _ = seed_run(store, "before", invalid=True)
    after, _ = seed_run(store, "after")
    runner = Runner(store, Integrations())
    runner.analyze(invalid)
    runner.analyze(after)
    with TestClient(create_app(results_dir=str(tmp_path), integrations=Integrations())) as client:
        issues = client.get(f"/api/runs/{invalid['runId']}/issues").json()
        assert issues["analysisStatus"] == "COMPLETED" and issues["items"] == []
        comparison = client.get("/api/comparisons/integration_pair")
        assert comparison.status_code == 409
        assert comparison.json()["error"]["code"] == "COMPARISON_RUN_INVALID"


def test_comparison_waits_for_actual_analysis(tmp_path):
    store = RunStore(str(tmp_path))
    before, _ = seed_run(store, "before")
    seed_run(store, "after")
    Runner(store, Integrations()).analyze(before)
    with TestClient(create_app(results_dir=str(tmp_path), integrations=Integrations())) as client:
        issues = client.get("/api/runs/integration_after/issues").json()
        assert issues["analysisStatus"] == "PENDING" and issues["items"] == []
        comparison = client.get("/api/comparisons/integration_pair")
        assert comparison.status_code == 409
        assert comparison.json()["error"]["code"] == "COMPARISON_NOT_READY"


@pytest.mark.parametrize("shift", [0.02, -0.02])
def test_comparison_delta_is_after_minus_before_through_api(tmp_path, shift):
    store = RunStore(str(tmp_path))
    before, _ = seed_run(store, "before")
    after, _ = seed_run(store, "after", probability_shift=shift)
    runner = Runner(store, Integrations())
    runner.analyze(before)
    runner.analyze(after)
    with TestClient(create_app(results_dir=str(tmp_path), integrations=Integrations())) as client:
        response = client.get("/api/comparisons/integration_pair")
        assert response.status_code == 200
        home = next(screen for screen in response.json()["screenComparisons"] if screen["pageKey"] == "home")
        first, second = home["candidateComparisons"][:2]
        assert first["deltaProbability"] == pytest.approx(shift)
        assert second["deltaProbability"] == pytest.approx(-shift)
        assert home["metrics"]["deltaGoalPathProbability"] == pytest.approx(shift)
