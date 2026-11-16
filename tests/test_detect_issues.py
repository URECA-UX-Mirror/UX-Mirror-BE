"""5가지 UX 후보 규칙을 고정 MOCK 기록으로 검증한다."""
import copy
import json
from pathlib import Path

import pytest

from api.integrations import Integrations
from api.runner import Runner
from api.store import RunStore
from uxm.analysis import detect_issues
from uxm.storage import read_jsonl


@pytest.fixture
def record():
    sample = Path(__file__).resolve().parents[1] / "results" / "runs" / "sample_before"
    run = json.loads((sample / "run.json").read_text(encoding="utf-8"))
    steps = read_jsonl(str(sample / "steps.jsonl"))
    events = read_jsonl(str(sample / "events.jsonl"))
    return run, steps, events


def types(result):
    return [issue["issueType"] for issue in result]


def simple(record):
    run, steps, _ = copy.deepcopy(record)
    step = steps[0]
    run["summary"].update(goalReached=True, extraSteps=0, path=[{
        "sequence": 0, "pageKey": "home", "pageSignature": "home_default", "path": "/shop/home",
        "enteredBy": None, "visitCause": "RUN_START", "stepId": None,
    }])
    step["decision"].update(clickOnlyNormalizedEntropy=0.1, clickOnlyTop2Margin=0.3, metricWarnings=[])
    return run, step


def visit(sequence, key, cause, step_id, signature=None):
    return {"sequence": sequence, "pageKey": key, "pageSignature": signature or f"{key}_default",
            "path": f"/shop/{key}", "enteredBy": None if cause == "RUN_START" else
            ("CLICK" if cause == "MODEL_NAVIGATION" else "GO_BACK"), "visitCause": cause, "stepId": step_id}


def test_d2_equal_allowed_candidates_is_general_review(record):
    run, step = simple(record)
    step["candidateSet"]["candidates"] = [step["candidateSet"]["candidates"][0],
                                           step["candidateSet"]["candidates"][2]]
    for candidate in step["candidateSet"]["candidates"]:
        candidate["probability"] = 0.5
    step["decision"].update(clickOnlyNormalizedEntropy=1.0, clickOnlyTop2Margin=0.0)
    result = detect_issues(run, [step], [])
    assert types(result) == ["CHOICE_AMBIGUITY"]
    assert result[0]["reviewPriority"] == "GENERAL_REVIEW"
    assert result[0]["evidence"] == {"clickOnlyNormalizedEntropy": 1.0, "clickOnlyTop2Margin": 0.0,
                                      "candidateCount": 2, "entropyThreshold": 0.8}


@pytest.mark.parametrize("mode", ["low", "null", "warning", "few"])
def test_ambiguity_negative_cases(record, mode):
    run, step = simple(record)
    step["decision"]["clickOnlyNormalizedEntropy"] = 0.9
    if mode == "low":
        step["decision"]["clickOnlyNormalizedEntropy"] = 0.79
    elif mode == "null":
        step["decision"]["clickOnlyNormalizedEntropy"] = None
    elif mode == "warning":
        step["decision"]["metricWarnings"] = [{"code": "NO_CLICK_PROBABILITY_MASS", "message": "test"}]
    elif mode == "few":
        step["candidateSet"]["candidates"] = step["candidateSet"]["candidates"][:1]
        step["candidateSet"]["actionSetIncludesBack"] = True
    assert "CHOICE_AMBIGUITY" not in types(detect_issues(run, [step], []))


def test_ambiguity_checks_each_step_and_merges_observations(record):
    run, step = simple(record)
    run["thresholdConfig"]["entropyThreshold"] = 0.95
    step["decision"]["clickOnlyNormalizedEntropy"] = 0.94
    later = copy.deepcopy(step)
    later["stepId"] = 2
    later["decision"]["clickOnlyNormalizedEntropy"] = 1.0
    retry = copy.deepcopy(later)
    retry["stepId"] = 3
    retry["candidateSet"]["candidateSetChanged"] = True
    issues = detect_issues(run, [step, later, retry], [])
    ambiguity = next(i for i in issues if i["issueType"] == "CHOICE_AMBIGUITY")
    assert ambiguity["stepId"] == 2
    assert ambiguity["relatedStepIds"] == [2, 3] and ambiguity["occurrenceCount"] == 2
    assert ambiguity["evidence"]["entropyThreshold"] == 0.95


def test_deviation_uses_current_stack_after_back(record):
    run, step = simple(record)
    step["decision"]["selectedOptionId"] = "option_02"  # support
    run["summary"]["path"] += [visit(1, "support", "MODEL_NAVIGATION", 1),
                                 visit(2, "home", "SYSTEM_BACK_RECOVERY", None)]
    orders_step = copy.deepcopy(step)
    orders_step["stepId"] = 2
    orders_step["decision"]["selectedOptionId"] = "option_01"
    run["summary"]["path"].append(visit(3, "orders", "MODEL_NAVIGATION", 2))
    result = detect_issues(run, [step, orders_step], [])
    assert types(result) == ["REFERENCE_PATH_DEVIATION"]
    assert result[0]["evidence"]["fromPageKey"] == "home"
    assert result[0]["evidence"]["toPageKey"] == "support"
    assert result[0]["evidence"]["selectedSemanticKey"] == "상단 메뉴>/shop/support#1"


@pytest.mark.parametrize("cause", ["MODEL_NAVIGATION", "MODEL_BACK"])
def test_model_revisit_uses_destination_and_linked_step(record, cause):
    run, step = simple(record)
    run["summary"]["path"] += [visit(1, "orders", "MODEL_NAVIGATION", 1)]
    returning = copy.deepcopy(step)
    returning["stepId"] = 2
    returning["page"].update(pageKey="orders", pageSignature="orders_default", path="/shop/orders")
    returning["action"].update(actionType="GO_BACK" if cause == "MODEL_BACK" else "CLICK", result="NAVIGATED")
    run["summary"]["path"].append(visit(2, "home", cause, 2))
    if cause == "MODEL_NAVIGATION":
        run["referencePathConfig"]["allowedPaths"].append(["home", "orders", "home"])
    result = detect_issues(run, [step, returning], [])
    repeated = next(i for i in result if i["issueType"] == "REPEATED_VISIT")
    assert repeated["pageKey"] == "home" and repeated["stepId"] == 2
    assert repeated["evidence"]["visitCount"] == 2 and cause in repeated["evidence"]["visitCauses"]


def test_system_back_alone_is_not_repeated_issue(record):
    run, step = simple(record)
    run["summary"]["path"] += [visit(1, "orders", "MODEL_NAVIGATION", 1),
                                 visit(2, "home", "SYSTEM_BACK_RECOVERY", None)]
    assert "REPEATED_VISIT" not in types(detect_issues(run, [step], []))


@pytest.mark.parametrize("status,reached,extra,expected", [
    ("SUCCESS", True, 1, ["EXTRA_STEPS"]), ("SUCCESS", True, 0, []),
    ("MAX_STEPS", False, None, ["GOAL_NOT_REACHED"]), ("BLOCKED", False, None, ["GOAL_NOT_REACHED"]),
    ("TIMEOUT", False, None, []), ("ERROR", False, None, []),
])
def test_run_level_rules(record, status, reached, extra, expected):
    run, step = simple(record)
    run["status"] = status
    run["terminationReason"] = {"MAX_STEPS": "MAX_STEPS_REACHED", "BLOCKED": "NO_VALID_CANDIDATES"}.get(status)
    run["summary"].update(goalReached=reached, extraSteps=extra)
    assert types(detect_issues(run, [step], [])) == expected


def test_invalid_and_unfinished_runs(record):
    run, steps, events = copy.deepcopy(record)
    run["validity"] = "INVALID"
    assert detect_issues(run, steps, events) == []
    run.update(validity=None, status="RUNNING")
    with pytest.raises(ValueError):
        detect_issues(run, steps, events)


def test_existing_mock_run_and_api_analysis_are_consistent(record, tmp_path):
    run, steps, events = record
    before = copy.deepcopy(record)
    result = detect_issues(run, steps, events)
    assert "REPEATED_VISIT" in types(result)
    assert "EXTRA_STEPS" in types(result)
    assert record == before
    store = RunStore(str(tmp_path))
    store.save_run(copy.deepcopy(run))
    run_dir = Path(store.run_dir(run["runId"]))
    for name, data in (("steps.jsonl", steps), ("events.jsonl", events)):
        (run_dir / name).write_text("".join(json.dumps(row) + "\n" for row in data), encoding="utf-8")
    Runner(store, Integrations()).analyze(copy.deepcopy(run))
    issues_doc = store.load_issues(run["runId"])
    assert issues_doc["analysisStatus"] == "COMPLETED"
    assert issues_doc["items"] == result
    assert store.load_run(run["runId"])["summary"]["detectedIssueTypes"] == types(result)
