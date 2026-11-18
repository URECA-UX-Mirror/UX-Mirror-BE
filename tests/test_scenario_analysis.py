"""외부 시나리오 분석과 저장·조회 계약을 고정 데이터로 검증한다."""
import copy
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.integrations import Integrations
from api.main import create_app
from api.run_factory import site_map_reference
from api.runner import Runner
from api.store import RunStore
from uxm.analysis import detect_issues
from uxm.reference import reference
from uxm.storage import read_jsonl


SAMPLE = Path(__file__).resolve().parents[1] / "results/runs/sample_before"


@pytest.fixture
def scenario():
    run = json.loads((SAMPLE / "run.json").read_text(encoding="utf-8"))
    steps = read_jsonl(str(SAMPLE / "steps.jsonl"))
    events = read_jsonl(str(SAMPLE / "events.jsonl"))
    run.update(siteId="cafe24-ons", mode="SCENARIO", referencePathConfig=None,
               startPath="/", verification={"type": "URL_AND_TEXT", "path": "/board/review/list.html", "text": "상품 리뷰"})
    run["summary"].update(extraSteps=None, extraNavigationCost=None, attemptedExtraSteps=None,
                          referencePathDeviationCount=None, detourMoveCount=None)
    pages = {"home": ("home", "/"), "mypage": ("products", "/product/list.html"),
             "orders": ("event", "/board/gallery/list.html"),
             "refund": ("review", "/board/review/list.html")}
    for page in run["summary"]["path"]:
        page["pageKey"], page["path"] = pages[page["pageKey"]]
    for step in steps:
        page = step["page"]
        page["pageKey"], page["path"] = pages[page["pageKey"]]
        step["decision"]["goalPathProbability"] = None
    return run, steps, events


def test_no_map_keeps_other_issues_and_never_mutates_input(scenario):
    run, steps, events = scenario
    run.update(status="MAX_STEPS", terminationReason="MAX_STEPS_REACHED")
    run["summary"]["goalReached"] = False
    steps[0]["decision"].update(clickOnlyNormalizedEntropy=1.0, clickOnlyTop2Margin=0.0, metricWarnings=[])
    snapshot = copy.deepcopy(scenario)
    issues = detect_issues(run, steps, events)
    types = {item["issueType"] for item in issues}
    assert types == {"CHOICE_AMBIGUITY", "REPEATED_VISIT", "GOAL_NOT_REACHED"}
    assert scenario == snapshot
    assert run["summary"]["extraSteps"] is None
    assert all(step["decision"]["goalPathProbability"] is None for step in steps)


def test_issue_evidence_and_steps_are_returned_by_api(tmp_path, scenario):
    run, steps, events = scenario
    store = RunStore(str(tmp_path))
    store.save_run(copy.deepcopy(run))
    directory = Path(store.run_dir(run["runId"]))
    for name, rows in (("steps.jsonl", steps), ("events.jsonl", events)):
        (directory / name).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    shutil.copytree(SAMPLE / "screenshots", directory / "screenshots", dirs_exist_ok=True)
    expected = detect_issues(run, steps, events)
    Runner(store, Integrations()).analyze(copy.deepcopy(run))
    with TestClient(create_app(results_dir=str(tmp_path), integrations=Integrations())) as client:
        issues = client.get(f"/api/runs/{run['runId']}/issues").json()
        saved_steps = client.get(f"/api/runs/{run['runId']}/steps").json()["items"]
        screenshot = client.get(f"/api/runs/{run['runId']}/screenshots/1")
    assert issues["analysisStatus"] == "COMPLETED" and issues["items"] == expected
    assert any(issue["issueType"] == "REPEATED_VISIT" for issue in expected)
    assert {issue["stepId"] for issue in expected if issue["stepId"]} <= {step["stepId"] for step in saved_steps}
    assert screenshot.status_code == 200 and screenshot.headers["content-type"] == "image/png"


@pytest.mark.parametrize("state", ["pending", "empty", "failed", "invalid"])
def test_analysis_status_and_empty_results_via_api(tmp_path, scenario, state):
    run, _, _ = scenario
    run["summary"]["path"] = run["summary"]["path"][:1]
    if state == "invalid":
        run.update(validity="INVALID", status="ERROR", terminationReason="BROWSER_ERROR", summary=None)
    store = RunStore(str(tmp_path))
    store.save_run(copy.deepcopy(run))
    integrations = Integrations()
    if state == "failed":
        class FailingIntegrations(Integrations):
            def detect_issues(self):
                def fail(*args):
                    raise ValueError("fixture analysis failure")
                return fail
        integrations = FailingIntegrations()
    if state != "pending":
        Runner(store, integrations).analyze(copy.deepcopy(run))
    with TestClient(create_app(results_dir=str(tmp_path), integrations=Integrations())) as client:
        response = client.get(f"/api/runs/{run['runId']}/issues")
        stored_run = client.get(f"/api/runs/{run['runId']}").json()
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["analysisStatus"] == {"pending": "PENDING", "empty": "COMPLETED",
                                                  "failed": "FAILED", "invalid": "COMPLETED"}[state]
    assert stored_run["validity"] == ("INVALID" if state == "invalid" else "VALID")
    if state == "empty":
        assert stored_run["summary"]["detectedIssueTypes"] == []
        assert stored_run["summary"]["extraSteps"] is None


def test_external_site_map_uses_site_page_keys(scenario):
    run, steps, events = scenario
    run["referencePathConfig"] = site_map_reference(
        "/", run["verification"], {"width": 1440, "height": 900}, site_id="cafe24-ons")
    assert run["referencePathConfig"] is not None
    run["summary"].update(goalReached=True, extraSteps=1)
    assert reference(run)[0]
    issues = detect_issues(run, steps, events)
    extra = next(item for item in issues if item["issueType"] == "EXTRA_STEPS")
    assert extra["evidence"]["basis"] == "SITE_MAP"
    assert not any(item["issueType"] == "REFERENCE_PATH_DEVIATION" for item in issues)
    run["verification"]["path"] = "/shopinfo/guide.html"
    assert not reference(run)[0]
    assert not any(item["issueType"] == "EXTRA_STEPS" for item in detect_issues(run, steps, events))


def test_unknown_site_does_not_validate_map(scenario):
    run, _, _ = scenario
    run["referencePathConfig"] = site_map_reference(
        "/", run["verification"], {"width": 1440, "height": 900}, site_id="cafe24-ons")
    run["siteId"] = "unknown-site"
    assert not reference(run)[0]
