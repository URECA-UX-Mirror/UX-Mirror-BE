"""계약 형식의 가짜 탐색 데이터와 분석 인터페이스 준비 상태를 확인한다."""
import copy
import inspect
import json
from pathlib import Path

import pytest

from api.integrations import Integrations
from uxm import analysis
from uxm.storage import SCHEMA_VERSION


SAMPLE_DIR = Path(__file__).resolve().parents[1] / "results" / "runs" / "sample_before"


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def sample_bundle():
    """저장 모듈의 MOCK 샘플을 매 테스트마다 독립된 객체로 읽는다."""
    return {
        "run": json.loads((SAMPLE_DIR / "run.json").read_text(encoding="utf-8")),
        "steps": _read_jsonl(SAMPLE_DIR / "steps.jsonl"),
        "events": _read_jsonl(SAMPLE_DIR / "events.jsonl"),
    }


@pytest.fixture
def invalid_bundle(sample_bundle):
    bundle = copy.deepcopy(sample_bundle)
    bundle["run"].update(
        status="ERROR", terminationReason="BROWSER_ERROR", validity="INVALID",
        invalidReason="BROWSER_CRASH", replacementRunId=None,
        error={"code": "BROWSER_ERROR", "message": "테스트 오류", "retryable": False,
               "details": None, "occurredAt": bundle["run"]["finishedAt"]},
    )
    return bundle


@pytest.fixture
def comparison_pair(sample_bundle):
    before = {**copy.deepcopy(sample_bundle), "issues": []}
    after = copy.deepcopy(before)
    after["run"].update(runId="sample_after", variant="after")
    for record in after["steps"] + after["events"]:
        record["runId"] = "sample_after"
    for step in after["steps"]:
        for candidate in step["candidateSet"]["candidates"]:
            if candidate["semanticKey"] == "상단 메뉴>/shop/orders#1":
                candidate["label"] = "주문·환불"
    return before, after


@pytest.fixture
def system_back_event(sample_bundle):
    return {
        "schemaVersion": SCHEMA_VERSION, "eventId": "event_system_back", "runId": sample_bundle["run"]["runId"],
        "stepId": None, "timestamp": "2026-11-16T10:00:30+09:00", "eventType": "BACK_EXECUTED",
        "eventOrigin": "SYSTEM_RECOVERY", "details": {"result": "NAVIGATED", "fromPageSignature": "support_default",
                                                           "toPageSignature": "home_default"},
    }


def test_fixture_run_step_event_contract(sample_bundle, invalid_bundle, system_back_event):
    run, steps, events = (sample_bundle[k] for k in ("run", "steps", "events"))
    assert run["schemaVersion"] == SCHEMA_VERSION and run["validity"] == "VALID"
    assert run["resultOrigin"] == "MOCK" and run["summary"]["path"]
    assert all(s["runId"] == run["runId"] and s["candidateSet"]["candidates"] for s in steps)
    assert all(e["runId"] == run["runId"] for e in events)
    assert invalid_bundle["run"]["validity"] == "INVALID"
    assert system_back_event["eventOrigin"] == "SYSTEM_RECOVERY" and system_back_event["stepId"] is None


def test_comparison_fixture_keeps_matching_candidates(comparison_pair):
    before, after = comparison_pair
    assert before["run"]["experimentKey"] == after["run"]["experimentKey"]
    assert before["run"]["runId"] != after["run"]["runId"]
    assert after["run"]["variant"] == "after"
    b = before["steps"][0]["candidateSet"]["candidates"]
    a = after["steps"][0]["candidateSet"]["candidates"]
    assert [c["semanticKey"] for c in b] == [c["semanticKey"] for c in a]
    assert b[0]["label"] == "이용정보" and a[0]["label"] == "주문·환불"


def test_public_interfaces_without_mutating_inputs(sample_bundle, comparison_pair, system_back_event):
    assert list(inspect.signature(analysis.detect_issues).parameters) == ["run", "steps", "events"]
    assert list(inspect.signature(analysis.compare).parameters) == ["before", "after"]
    inputs = (sample_bundle["run"], sample_bundle["steps"], sample_bundle["events"] + [system_back_event])
    snapshot = copy.deepcopy(inputs)
    assert isinstance(analysis.detect_issues(*inputs), list)
    assert inputs == snapshot
    snapshot = copy.deepcopy(comparison_pair)
    assert isinstance(analysis.compare(*comparison_pair), dict)
    assert comparison_pair == snapshot


def test_api_does_not_treat_stubs_as_ready():
    integrations = Integrations()
    assert integrations.detect_issues() is analysis.detect_issues
    assert integrations.compare() is analysis.compare


def test_functions_can_be_enabled_independently(monkeypatch):
    def implemented_detect(run, steps, events):
        return []

    monkeypatch.setattr(analysis, "detect_issues", implemented_detect)
    integrations = Integrations()
    assert integrations.detect_issues() is implemented_detect
    assert integrations.compare() is analysis.compare
