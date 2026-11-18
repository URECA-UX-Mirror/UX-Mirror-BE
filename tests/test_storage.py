"""storage.py와 가짜 실행 샘플(results/runs/sample_before) 계약 검사 (#3). 모델·브라우저·서버 없이 돈다."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from uxm.storage import (RUNS_DIR, SCHEMA_VERSION, RunStore, StorageError, list_run_ids,  # noqa: E402
                         mark_storage_failed, scrub)

SAMPLE = "sample_before"

RUN_FIELDS = {
    "schemaVersion", "runId", "experimentKey", "variant", "resultOrigin", "siteVersion", "modelVersion",
    "modelEndpointId", "promptVersion", "temperature", "decisionMode", "goalJudgmentMode", "goalId", "goal", "verification",
    "personaId", "persona", "startPath", "viewport", "limits", "thresholdConfig", "analysisVersion",
    "referencePathConfig", "comparisonContext", "status", "createdAt", "startedAt", "finishedAt",
    "terminationReason", "progress", "summary", "error", "validity", "invalidReason", "replacementRunId",
}
STEP_FIELDS = {"schemaVersion", "runId", "stepId", "timestamp", "page", "candidateSet", "decision", "action",
               "statusAfterStep", "error"}
PAGE_FIELDS = {"pageKey", "pageSignature", "path", "title", "viewport", "screenshotSize", "scroll",
               "screenshotRef", "overlayScreenshotRef", "capturedAt"}
CANDIDATE_SET_FIELDS = {"candidateSetId", "candidateSetChanged", "candidateScope", "candidateCountCollected",
                        "candidateCountSent", "excludedCandidateCount", "exclusionReasons",
                        "actionSetIncludesBack", "backOptionId", "candidates"}
STEP_CANDIDATE_FIELDS = {"optionId", "label", "region", "context", "href", "bbox", "semanticKey",
                         "actionType", "probability", "selected"}
DECISION_FIELDS = {"selectedOptionId", "selectedActionType", "mode", "resultOrigin", "goalJudgmentMode",
                   "modelGoalReached", "modelGoalYesProb", "verifiedGoalReached", "goalJudgmentMismatch",
                   "normalizedEntropy", "clickOnlyNormalizedEntropy", "goBackProbability", "top2Margin",
                   "clickOnlyTop2Margin", "goalPathProbability", "modelLatencyMs", "metricWarnings"}
ACTION_FIELDS = {"selectedOptionId", "executedOptionId", "actionType", "eventOrigin", "result", "reason",
                 "executed", "fromPath", "toPath", "fromPageSignature", "toPageSignature", "startedAt",
                 "finishedAt", "error"}
EVENT_FIELDS = {"schemaVersion", "eventId", "runId", "stepId", "timestamp", "eventType", "eventOrigin", "details"}
EVENT_TYPES = {"RUN_STARTED", "STEP_CAPTURED", "DECISION_MADE", "ACTION_COMPLETED", "BACK_EXECUTED",
               "BACK_FAILED", "PAGE_REVISITED", "GOAL_REACHED", "RUN_FINISHED", "RUN_FAILED"}


def _has_key(obj, key) -> bool:
    if isinstance(obj, dict):
        return key in obj or any(_has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_key(v, key) for v in obj)
    return False


# ---------------------------------------------------------------- storage.py

@pytest.fixture
def store(tmp_path):
    s = RunStore("run_test_001", runs_dir=str(tmp_path))
    s.create({"runId": "run_test_001", "status": "READY"})
    return s


def test_create_makes_folders_and_refuses_overwrite(store):
    assert os.path.isdir(store.screenshots_dir) and os.path.isdir(store.path("raw"))
    with pytest.raises(StorageError):
        store.create({"runId": "run_test_001", "status": "READY"})


def test_open_dir_continues_run_created_by_api(tmp_path):
    run_dir = tmp_path / "run_api_001"          # API(PR #5)가 run.json만 만들어 둔 상태
    run_dir.mkdir()
    (run_dir / "run.json").write_text('{"runId": "run_api_001", "status": "RUNNING"}', encoding="utf-8")
    s = RunStore.open_dir(str(run_dir))
    assert s.run_id == "run_api_001" and os.path.isdir(s.screenshots_dir)
    s.append_step({"stepId": 1})
    assert s.read_run()["status"] == "RUNNING" and len(s.read_steps()) == 1


def test_run_id_must_be_safe(tmp_path):
    with pytest.raises(ValueError):
        RunStore("../escape", runs_dir=str(tmp_path))


def test_write_run_replaces_whole_file(store):
    store.write_run({"runId": "run_test_001", "status": "RUNNING"})
    assert store.read_run()["status"] == "RUNNING"
    assert not os.path.exists(store.path("run.json.tmp"))


def test_incomplete_last_line_is_not_read(store):
    store.append_step({"stepId": 1})
    with open(store.path("steps.jsonl"), "a", encoding="utf-8") as f:
        f.write('{"stepId": 2, "page": {"pa')        # 쓰다가 끊긴 줄
    assert [s["stepId"] for s in store.read_steps()] == [1]


def test_missing_jsonl_reads_empty(store):
    assert store.read_events() == []


def test_runtime_and_secret_keys_are_not_saved(store):
    cand = {"optionId": "option_01", "label": "이용정보", "domIndex": 7}
    store.append_step({"stepId": 1, "candidateSet": {"candidates": [cand]}, "headers": {"authorization": "x"}})
    saved = store.read_steps()[0]
    assert not _has_key(saved, "domIndex") and not _has_key(saved, "authorization")
    assert cand["domIndex"] == 7     # 메모리의 후보는 그대로 (browser.click이 계속 씀)


def test_nan_is_storage_error(store):
    with pytest.raises(StorageError):
        store.append_step({"stepId": 1, "probability": float("nan")})
    assert store.read_steps() == []


def test_failed_atomic_write_leaves_no_tmp(store, monkeypatch):
    import uxm.storage as st

    def always_locked(src, dst):          # API가 run.json을 계속 열고 있는 상황
        raise PermissionError("locked")
    monkeypatch.setattr(st.os, "replace", always_locked)
    monkeypatch.setattr(st.time, "sleep", lambda s: None)
    with pytest.raises(StorageError):
        store.write_run({"runId": "run_test_001", "status": "RUNNING"})
    assert not os.path.exists(store.path("run.json.tmp"))
    monkeypatch.undo()
    assert store.read_run()["status"] == "READY"     # 기존 run.json은 그대로


def test_save_raw_returns_relative_ref(store):
    ref = store.save_raw(3, "request", {"state": {}})
    assert ref == "raw/model_request_step_003.json"
    assert os.path.isfile(store.path("raw", "model_request_step_003.json"))


def test_mark_storage_failed_matches_validity_table():
    run = mark_storage_failed({"status": "RUNNING"}, OSError("disk full"))
    assert (run["status"], run["terminationReason"], run["error"]["code"], run["validity"], run["invalidReason"]) \
        == ("ERROR", "STORAGE_FAILED", "STORAGE_ERROR", "INVALID", "STORAGE_FAILURE")


def test_scrub_keeps_other_fields():
    assert scrub({"a": [{"domIndex": 1, "b": 2}]}) == {"a": [{"b": 2}]}


# ---------------------------------------------------------------- 샘플 (3·4·5번이 쓰는 형식)

@pytest.fixture(scope="module")
def sample():
    s = RunStore(SAMPLE)
    if not s.exists():
        pytest.skip("샘플 없음: scripts/make_sample_run.py 실행")
    return s.read_run(), s.read_steps(), s.read_events(), s


def test_sample_is_listed():
    if os.path.isdir(os.path.join(RUNS_DIR, SAMPLE)):
        assert SAMPLE in list_run_ids()


def test_sample_run_fields(sample):
    run, steps, _, _ = sample
    assert set(run) == RUN_FIELDS
    assert run["schemaVersion"] == SCHEMA_VERSION and run["resultOrigin"] == "MOCK"
    assert run["verification"] == {"type": "PATH_AND_VISIBLE_TEXT", "path": "/shop/refund",
                                   "text": "환불 / 반품 신청", "exact": False, "scope": "page"}
    assert (run["status"], run["terminationReason"], run["validity"]) == ("SUCCESS", "GOAL_VERIFIED", "VALID")
    assert run["comparisonContext"]["leakCheckPassed"] is True
    assert run["summary"]["detectedIssueTypes"] == []          # 분석 전 (issues.json 없음)
    assert run["thresholdConfig"]["configuredAt"] == "2026-11-15T09:00:00+09:00"   # API와 같은 표기
    assert run["progress"]["stepRecordCount"] == len(steps) == run["summary"]["stepRecordCount"]


def test_sample_steps_fields_and_candidate_rules(sample):
    _, steps, _, store = sample
    assert [s["stepId"] for s in steps] == list(range(1, len(steps) + 1))
    for s in steps:
        assert set(s) == STEP_FIELDS and set(s["page"]) == PAGE_FIELDS
        assert set(s["decision"]) == DECISION_FIELDS and set(s["action"]) == ACTION_FIELDS
        cs = s["candidateSet"]
        assert set(cs) == CANDIDATE_SET_FIELDS
        sig = s["page"]["pageSignature"]   # 계약 8.2 {pageSignature}_initial / _retry_{n}
        assert cs["candidateSetId"] == f"{sig}_initial" or cs["candidateSetId"].startswith(f"{sig}_retry_")
        for c in cs["candidates"]:
            assert set(c) == STEP_CANDIDATE_FIELDS and c["bbox"]["width"] > 0
        # 8.2 불변조건
        n = len(cs["candidates"])
        assert cs["candidateCountCollected"] == n + cs["excludedCandidateCount"]
        assert cs["candidateCountSent"] == n + (1 if cs["actionSetIncludesBack"] else 0)
        assert cs["actionSetIncludesBack"] == (cs["backOptionId"] is not None)
        assert cs["excludedCandidateCount"] == sum(cs["exclusionReasons"].values())
        ids = [c["optionId"] for c in cs["candidates"]] + ([cs["backOptionId"]] if cs["backOptionId"] else [])
        assert len(ids) == len(set(ids))
        # 8.1 선택 규칙
        selected = [c for c in cs["candidates"] if c["selected"]]
        d = s["decision"]
        if d["selectedActionType"] == "GO_BACK":
            assert not selected and d["selectedOptionId"] == cs["backOptionId"]
        else:
            assert [c["optionId"] for c in selected] == [d["selectedOptionId"]]
        # 11.2 실행 규칙
        a = s["action"]
        assert a["executedOptionId"] == (a["selectedOptionId"] if a["executed"] else None)
        assert a["actionType"] == d["selectedActionType"]
        # 스크린샷 참조가 실제 파일
        assert os.path.isfile(store.path(*s["page"]["screenshotRef"].split("/")))
        assert os.path.isfile(store.path(*s["page"]["overlayScreenshotRef"].split("/")))


def test_sample_summary_invariants(sample):
    run, steps, _, _ = sample
    sm, shortest = run["summary"], run["referencePathConfig"]["shortestAllowedSteps"]
    assert sm["backCount"] == sm["modelBackCount"] + sm["systemBackCount"]
    assert sm["navigationCost"] == sm["successfulNavigationClickCount"] + sm["backCount"]
    assert sm["extraSteps"] == max(0, sm["successfulNavigationClickCount"] - shortest)
    assert sm["extraNavigationCost"] == max(0, sm["navigationCost"] - shortest)
    assert sm["attemptedExtraSteps"] == max(0, sm["stepCount"] - shortest)
    counted = sum(1 for s in steps if s["action"]["executed"] and
                  (s["action"]["actionType"] == "CLICK" or s["action"]["result"] == "NAVIGATED"))
    assert sm["stepCount"] == counted == run["progress"]["stepCount"]
    assert [v["sequence"] for v in sm["path"]] == list(range(len(sm["path"])))


def test_sample_events(sample):
    run, _, events, _ = sample
    for e in events:
        assert set(e) == EVENT_FIELDS and e["eventType"] in EVENT_TYPES and e["runId"] == run["runId"]
    assert events[0]["eventType"] == "RUN_STARTED" and events[-1]["eventType"] == "RUN_FINISHED"


def test_sample_has_no_runtime_fields_or_testmode_in_paths(sample):
    run, steps, events, _ = sample
    assert not any(_has_key(x, "domIndex") for x in [run, *steps, *events])
    paths = [p for s in steps for p in (s["page"]["path"], s["action"]["fromPath"], s["action"]["toPath"])]
    paths += [v["path"] for v in run["summary"]["path"]]
    assert not any(w in p for p in paths for w in ("testMode", "nochange", "deadend", "?"))


def test_sample_raw_requests_pass_leak_check(sample):
    from uxm.leak import assert_no_leak
    _, steps, _, store = sample
    for s in steps:
        with open(store.path("raw", f"model_request_step_{s['stepId']:03d}.json"), encoding="utf-8") as f:
            assert_no_leak(json.load(f))
