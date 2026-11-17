"""비교 가능 조건과 실제 API 저장을 계약 수준에서 검증한다."""
import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.comparison import build_comparison
from api.integrations import Integrations
from api.main import create_app
from api.store import RunStore
from uxm.analysis import compare
from uxm.storage import read_jsonl


@pytest.fixture
def pair():
    folder = Path(__file__).resolve().parents[1] / "results/runs/sample_before"
    before = {"run": json.loads((folder / "run.json").read_text()),
              "steps": read_jsonl(str(folder / "steps.jsonl")), "issues": []}
    before["run"]["verification"] = {"type": "PATH_AND_VISIBLE_TEXT", "path": "/shop/refund",
                                     "text": "환불 / 반품 신청", "exact": False, "scope": "page"}
    after = copy.deepcopy(before)
    after["run"].update(runId="sample_after", variant="after")
    for step in after["steps"]:
        step["runId"] = "sample_after"
    return before, after


def screen(result, key):
    return next(s for s in result["screenComparisons"] if s["pageKey"] == key)


def test_same_conditions_and_labels_are_comparable(pair):
    b, a = pair
    snapshot = copy.deepcopy(pair)
    a["steps"][0]["candidateSet"]["candidates"][0]["label"] = "주문·환불"
    snapshot = copy.deepcopy(pair)
    result = compare(b, a)
    assert pair == snapshot
    assert result["comparisonStatus"] == "COMPLETED"
    assert len(result["conditionChecks"]) == 21 and result["conditionsMatched"]
    assert [x["pageKey"] for x in result["screenComparisons"]] == ["home", "mypage", "orders"]
    home = screen(result, "home")
    assert home["candidateComparisons"][0]["afterLabel"] == "주문·환불"
    assert home["candidateComparisons"][0]["deltaProbability"] == 0
    assert home["metrics"]["deltaNormalizedEntropy"] == 0
    assert "delta" not in result["runComparison"]


@pytest.mark.parametrize("field,value,code", [
    ("modelVersion", "different", "MODEL_VERSION_MATCHED"),
    ("temperature", 0.1, "TEMPERATURE_MATCHED"),
    ("persona", "different", "PERSONA_MATCHED"),
    ("resultOrigin", "REAL", "RESULT_ORIGIN_MATCHED"),
])
def test_global_mismatch_disables_deltas_but_preserves_observations(pair, field, value, code):
    b, a = pair
    a["run"][field] = value
    result = compare(b, a)
    assert not result["conditionsMatched"] and result["comparisonStatus"] == "PARTIAL"
    assert not next(c for c in result["conditionChecks"] if c["code"] == code)["matched"]
    for item in result["screenComparisons"]:
        assert item["reason"] == "GLOBAL_CONDITIONS_MISMATCH"
        assert all(c["deltaProbability"] is None for c in item["candidateComparisons"])
        assert all(v is None for k, v in item["metrics"].items() if k.startswith("delta"))
    assert result["runComparison"]["before"]["goalReached"] is True
    if field == "resultOrigin":
        assert result["resultOrigin"] is None


def test_leak_check_requires_true_and_empty_steps_are_partial(pair):
    b, a = pair
    a["run"]["comparisonContext"]["leakCheckPassed"] = None
    b["steps"], a["steps"] = [], []
    result = compare(b, a)
    assert result["screenComparisons"] == [] and result["comparisonStatus"] == "PARTIAL"
    assert not result["layoutMatched"]
    assert not next(c for c in result["conditionChecks"] if c["code"] == "MODEL_PAYLOAD_LEAK_CHECK_PASSED")["matched"]


def test_one_sided_screen_and_reason_priority(pair):
    b, a = pair
    a["steps"] = [s for s in a["steps"] if s["page"]["pageKey"] != "orders"]
    a["run"]["temperature"] = 0.5
    item = screen(compare(b, a), "orders")
    assert item["reason"] == "NOT_VISITED_IN_AFTER" and item["metrics"] is None
    assert item["candidateComparisons"] == [] and item["afterStepId"] is None


@pytest.mark.parametrize("change,reason", [
    ("missing", "MISSING_SEMANTIC_KEY"), ("scope", "CANDIDATE_SCOPE_MISMATCH"),
    ("set", "CANDIDATE_SET_MISMATCH"), ("order", "CANDIDATE_ORDER_MISMATCH"),
    ("region", "CANDIDATE_REGION_MISMATCH"), ("href", "DESTINATION_MISMATCH"),
    ("back", "ACTION_SET_MISMATCH"), ("bbox", "LAYOUT_MISMATCH"),
])
def test_nonhome_screen_reasons(pair, change, reason):
    b, a = pair
    step = next(s for s in a["steps"] if s["page"]["pageKey"] == "mypage")
    cs = step["candidateSet"]
    candidates = cs["candidates"]
    if change == "missing": candidates[0]["semanticKey"] = None
    if change == "scope": cs["candidateScope"] = []
    if change == "set": candidates.pop()
    if change == "order": candidates[0], candidates[1] = candidates[1], candidates[0]
    if change == "region": candidates[0]["region"] = "other"
    if change == "href": candidates[0]["href"] = "/other"
    if change == "back": cs["actionSetIncludesBack"] = not cs["actionSetIncludesBack"]
    if change == "bbox": candidates[0]["bbox"]["x"] += 3
    item = screen(compare(b, a), "mypage")
    assert item["reason"] == reason and not item["screenComparable"]
    assert all(c["deltaProbability"] is None for c in item["candidateComparisons"])
    assert all(v is None for k, v in item["metrics"].items() if k.startswith("delta"))


def test_layout_boundary_and_normalized_href(pair):
    b, a = pair
    home = a["steps"][0]["candidateSet"]["candidates"][0]
    home["bbox"]["x"] += 2
    home["href"] += "/?a=1"
    result = compare(b, a)
    assert result["layoutMatched"] and screen(result, "home")["screenComparable"]
    home["bbox"]["x"] += 0.01
    result = compare(b, a)
    assert not result["layoutMatched"] and screen(result, "home")["reason"] == "GLOBAL_CONDITIONS_MISMATCH"


def test_earliest_full_candidate_set_and_null_metrics(pair):
    b, a = pair
    retry = copy.deepcopy(a["steps"][0])
    retry["stepId"] = 0
    retry["candidateSet"]["candidateSetChanged"] = True
    retry["candidateSet"]["candidates"].pop()
    a["steps"].insert(0, retry)
    a["steps"][1]["decision"]["goalPathProbability"] = None
    item = screen(compare(b, a), "home")
    assert item["afterStepId"] == 1 and item["screenComparable"]
    assert item["metrics"]["deltaGoalPathProbability"] is None


def test_click_probability_warning_keeps_click_metrics_null(pair):
    b, a = pair
    decision = a["steps"][0]["decision"]
    decision.update(clickOnlyNormalizedEntropy=None, clickOnlyTop2Margin=None,
                    metricWarnings=[{"code": "NO_CLICK_PROBABILITY_MASS", "message": "no mass"}])
    metrics = screen(compare(b, a), "home")["metrics"]
    assert metrics["afterClickOnlyNormalizedEntropy"] is None
    assert metrics["deltaClickOnlyNormalizedEntropy"] is None
    assert metrics["deltaClickOnlyTop2Margin"] is None


def test_invalid_pair_rejected(pair):
    b, a = pair
    a["run"]["validity"] = "INVALID"
    with pytest.raises(ValueError): compare(b, a)
    a["run"]["validity"] = "VALID"
    a["run"]["experimentKey"] = "different"
    with pytest.raises(ValueError): compare(b, a)


def test_actual_api_comparison_is_saved(pair, tmp_path):
    b, a = pair
    store = RunStore(str(tmp_path))
    for bundle in (b, a):
        run = bundle["run"]
        store.save_run(run)
        directory = Path(store.run_dir(run["runId"]))
        (directory / "steps.jsonl").write_text("".join(json.dumps(s) + "\n" for s in bundle["steps"]))
        store.save_issues(run["runId"], {"schemaVersion": run["schemaVersion"], "runId": run["runId"],
                                         "analysisStatus": "COMPLETED", "items": []})
    result = build_comparison(store, Integrations(), b["run"]["experimentKey"])
    assert result["comparisonStatus"] == "COMPLETED"
    saved = json.loads((tmp_path / "comparisons" / (b["run"]["experimentKey"] + ".json")).read_text())
    assert saved == result
    with TestClient(create_app(results_dir=str(tmp_path), integrations=Integrations())) as client:
        response = client.get(f"/api/comparisons/{b['run']['experimentKey']}")
        assert response.status_code == 200
        assert response.json()["comparisonStatus"] == "COMPLETED"
        saved = json.loads((tmp_path / "comparisons" / (b["run"]["experimentKey"] + ".json")).read_text())
        assert saved == response.json()


def test_get_returns_none_through_null_reference_path():
    from uxm.comparison import _get
    assert _get({"referencePathConfig": None}, "referencePathConfig.version") is None


def condition(result, code):
    return next(check for check in result["conditionChecks"] if check["code"] == code)


@pytest.mark.parametrize("before_id,after_id", [(None, None), ("preset-a", None), ("preset-a", "preset-b")])
def test_same_content_matches_despite_different_ids(pair, before_id, after_id):
    before, after = pair
    before["run"].update(goalId=before_id, personaId=before_id)
    after["run"].update(goalId=after_id, personaId=after_id)
    result = compare(before, after)
    assert condition(result, "GOAL_MATCHED")["matched"]
    assert condition(result, "PERSONA_MATCHED")["matched"]
    assert result["conditionsMatched"] and result["comparisonStatus"] == "COMPLETED"


@pytest.mark.parametrize("field,value,code", [("goal", "다른 목표", "GOAL_MATCHED"),
                                                ("persona", "다른 사용자", "PERSONA_MATCHED")])
def test_different_input_text_disables_comparison(pair, field, value, code):
    before, after = pair
    after["run"][field] = value
    result = compare(before, after)
    assert condition(result, code)["matched"] is False
    assert result["comparisonStatus"] == "PARTIAL"
    home = screen(result, "home")
    assert home["reason"] == "GLOBAL_CONDITIONS_MISMATCH"
    assert all(c["deltaProbability"] is None for c in home["candidateComparisons"])
    assert all(v is None for k, v in home["metrics"].items() if k.startswith("delta"))


@pytest.mark.parametrize("field,value", [("type", "UNKNOWN"), ("path", "/shop/orders"),
                                          ("text", "다른 문구"), ("exact", True), ("scope", "main")])
def test_verification_field_changes_prevent_goal_match(pair, field, value):
    before, after = pair
    after["run"]["verification"][field] = value
    result = compare(before, after)
    assert condition(result, "GOAL_MATCHED")["matched"] is False
    assert result["comparisonStatus"] == "PARTIAL"


@pytest.mark.parametrize("side", ["before", "after", "both"])
@pytest.mark.parametrize("invalid", ["missing", "null", "incomplete"])
def test_legacy_or_incomplete_verification_cannot_be_assumed_equal(pair, side, invalid):
    before, after = pair
    for bundle in ([before, after] if side == "both" else [before if side == "before" else after]):
        if invalid == "missing":
            del bundle["run"]["verification"]
        elif invalid == "null":
            bundle["run"]["verification"] = None
        else:
            del bundle["run"]["verification"]["scope"]
    snapshot = copy.deepcopy(pair)
    result = compare(before, after)
    check = condition(result, "GOAL_MATCHED")
    assert check["matched"] is False and "확인할 수 없습니다" in check["message"]
    assert result["comparisonStatus"] == "PARTIAL"
    assert pair == snapshot


@pytest.mark.parametrize("case,matched", [("both_null", True), ("one_null", False),
                                           ("same", True), ("version", False),
                                           ("paths", False), ("shortest", False), ("missing", False)])
def test_reference_configuration_compares_full_setting(pair, case, matched):
    before, after = pair
    if case == "both_null":
        for bundle in pair:
            bundle["run"]["referencePathConfig"] = None
            bundle["run"]["summary"].update(extraSteps=None, extraNavigationCost=None,
                                           referencePathDeviationCount=None)
            for step in bundle["steps"]:
                step["decision"]["goalPathProbability"] = None
    elif case == "one_null":
        after["run"]["referencePathConfig"] = None
    elif case == "version":
        after["run"]["referencePathConfig"]["version"] = "different"
    elif case == "paths":
        after["run"]["referencePathConfig"]["allowedPaths"].pop()
    elif case == "shortest":
        after["run"]["referencePathConfig"]["shortestAllowedSteps"] += 1
    elif case == "missing":
        del after["run"]["referencePathConfig"]
    result = compare(before, after)
    assert condition(result, "REFERENCE_PATH_CONFIG_MATCHED")["matched"] is matched
    if case == "both_null":
        assert result["conditionsMatched"] and result["comparisonStatus"] == "COMPLETED"
        for outcome in result["runComparison"].values():
            assert outcome["extraSteps"] is None and outcome["referencePathDeviationCount"] is None
        assert screen(result, "home")["metrics"]["deltaGoalPathProbability"] is None
    elif not matched:
        assert result["comparisonStatus"] == "PARTIAL"
        assert screen(result, "home")["metrics"]["deltaNormalizedEntropy"] is None
