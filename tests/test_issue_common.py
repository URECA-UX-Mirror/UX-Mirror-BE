"""Issue 공통 생성·중복 규칙: 실제 모델이나 브라우저 없이 검증한다."""
import copy
import json
from pathlib import Path

import pytest

from api.integrations import Integrations
from uxm.analysis import create_issue, detect_issues, merge_issues


@pytest.fixture
def run_and_step():
    sample = Path(__file__).resolve().parents[1] / "results" / "runs" / "sample_before"
    run = json.loads((sample / "run.json").read_text(encoding="utf-8"))
    step = json.loads((sample / "steps.jsonl").read_text(encoding="utf-8").splitlines()[0])
    return run, step


def issue(run, step, issue_type="CHOICE_AMBIGUITY", evidence=None):
    return create_issue(run, issue_type, evidence if evidence is not None else {"candidateCount": 2},
                        step["timestamp"], step=step)


def test_screen_issue_contains_contract_fields_and_copies_evidence(run_and_step):
    run, step = run_and_step
    evidence = {"candidateCount": 2, "nested": {"value": 1}}
    original = copy.deepcopy((run, step, evidence))
    result = issue(run, step, evidence=evidence)
    assert set(result) == {"schemaVersion", "issueId", "runId", "issueType", "stepId", "relatedStepIds", "pagePath",
                           "pageKey", "pageSignature", "title", "description", "evidence", "reviewPriority",
                           "occurrenceCount", "createdAt"}
    assert result["schemaVersion"] == run["schemaVersion"] and result["runId"] == run["runId"]
    assert result["stepId"] == step["stepId"] and result["relatedStepIds"] == [step["stepId"]]
    assert result["pageKey"] == step["page"]["pageKey"] and result["pagePath"] == step["page"]["path"]
    assert result["reviewPriority"] == "GENERAL_REVIEW" and result["occurrenceCount"] == 1
    assert (run, step, evidence) == original
    result["evidence"]["nested"]["value"] = 2
    assert evidence["nested"]["value"] == 1
    assert issue(run, step, evidence=evidence)["issueId"] == result["issueId"]


def test_run_level_issues_have_no_step_or_page(run_and_step):
    run, _ = run_and_step
    for kind in ("EXTRA_STEPS", "GOAL_NOT_REACHED"):
        result = create_issue(run, kind, {}, run["finishedAt"])
        assert all(result[k] is None for k in ("stepId", "pageKey", "pagePath", "pageSignature"))
        assert result["relatedStepIds"] == []


def test_merge_preserves_first_observation_and_collects_steps_without_mutating(run_and_step):
    run, step = run_and_step
    next_step = copy.deepcopy(step)
    next_step["stepId"] = step["stepId"] + 1
    first = issue(run, step, evidence={"candidateCount": 2})
    second = issue(run, next_step, evidence={"candidateCount": 3})
    originals = copy.deepcopy([first, second])
    result = merge_issues([first, second, None])
    assert len(result) == 1 and result[0]["occurrenceCount"] == 2
    assert result[0]["stepId"] == step["stepId"]
    assert result[0]["relatedStepIds"] == [step["stepId"], next_step["stepId"]]
    assert result[0]["evidence"] == first["evidence"]
    assert [first, second] == originals


def test_deviations_distinguish_destination_branches(run_and_step):
    run, step = run_and_step
    a = issue(run, step, "REFERENCE_PATH_DEVIATION", {"fromPageKey": "home", "toPageKey": "support"})
    b = issue(run, step, "REFERENCE_PATH_DEVIATION", {"fromPageKey": "home", "toPageKey": "mypage"})
    assert a["issueId"] != b["issueId"]
    assert len(merge_issues([a, b, a])) == 2
    assert merge_issues([a, b, a])[0]["occurrenceCount"] == 2
    with pytest.raises(ValueError):
        issue(run, step, "REFERENCE_PATH_DEVIATION", {"fromPageKey": "home"})


def test_repeated_visit_merge_keeps_visit_count_and_causes(run_and_step):
    run, step = run_and_step
    destination = {"pageKey": "home", "pageSignature": "home_default", "path": "/shop/home"}
    first_step = copy.deepcopy(step)
    first_step["stepId"] = 2
    first_step["page"].update(pageKey="orders", pageSignature="orders_default", path="/shop/orders")
    second_step = copy.deepcopy(first_step)
    second_step["stepId"] = 4
    second_step["page"].update(pageKey="mypage", pageSignature="mypage_default", path="/shop/mypage")
    first_evidence = {"pageSignature": "home_default", "visitCount": 2, "visitCauses": ["MODEL_BACK"]}
    second_evidence = {"pageSignature": "home_default", "visitCount": 3, "visitCauses": ["MODEL_NAVIGATION"]}
    original = copy.deepcopy((first_step, second_step, destination, first_evidence, second_evidence))
    first = create_issue(run, "REPEATED_VISIT", first_evidence, step["timestamp"],
                         step=first_step, destination_page=destination)
    second = create_issue(run, "REPEATED_VISIT", second_evidence, step["timestamp"],
                          step=second_step, destination_page=destination)
    result = merge_issues([first, second])[0]
    assert result["pageKey"] == "home" and result["pagePath"] == "/shop/home"
    assert result["pageSignature"] == "home_default" and result["stepId"] == 2
    assert result["relatedStepIds"] == [2, 4] and result["occurrenceCount"] == 2
    assert result["evidence"] == {"pageSignature": "home_default", "visitCount": 3,
                                  "visitCauses": ["MODEL_BACK", "MODEL_NAVIGATION"]}
    assert (first_step, second_step, destination, first_evidence, second_evidence) == original
    with pytest.raises(ValueError):
        create_issue(run, "REPEATED_VISIT", first_evidence, step["timestamp"], step=first_step)


def test_sort_priority_and_goal_not_reached_deduplication(run_and_step):
    run, step = run_and_step
    goal = create_issue(run, "GOAL_NOT_REACHED", {"status": "BLOCKED"}, run["finishedAt"])
    extra = create_issue(run, "EXTRA_STEPS", {"extraSteps": 1}, run["finishedAt"])
    repeated = issue(run, step, "REPEATED_VISIT", {"pageSignature": step["page"]["pageSignature"], "visitCount": 2})
    choice = issue(run, step)
    merged = merge_issues([choice, extra, goal, repeated, goal])
    assert [i["issueType"] for i in merged] == ["GOAL_NOT_REACHED", "REPEATED_VISIT", "EXTRA_STEPS", "CHOICE_AMBIGUITY"]
    assert merged[0]["occurrenceCount"] == 2 and merged[0]["reviewPriority"] == "PRIORITY_REVIEW"


def test_invalid_run_cannot_generate_issue_and_analysis_is_ready(run_and_step):
    run, step = run_and_step
    invalid = {**run, "validity": "INVALID"}
    assert issue(invalid, step) is None
    assert detect_issues(invalid, [step], []) == []
    assert Integrations().detect_issues() is detect_issues


def test_unknown_type_and_wrong_run_are_rejected(run_and_step):
    run, step = run_and_step
    with pytest.raises(ValueError):
        issue(run, step, "UNKNOWN")
    with pytest.raises(ValueError):
        issue({**run, "runId": "another"}, step)
