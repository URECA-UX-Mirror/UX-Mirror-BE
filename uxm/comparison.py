"""저장된 Run의 관찰값으로 계약상 Before/After 비교를 계산한다."""
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlsplit

from uxm.goal import SCOPES
from uxm.reference import reference as _reference


_CONDITIONS = (
    ("MODEL_VERSION_MATCHED", ("modelVersion",)),
    ("MODEL_ENDPOINT_MATCHED", ("modelEndpointId",)),
    ("DECISION_MODE_MATCHED", ("decisionMode",)),
    ("PROMPT_VERSION_MATCHED", ("promptVersion",)),
    ("GOAL_MATCHED", ("goal",)),
    ("PERSONA_MATCHED", ("persona",)),
    ("GOAL_JUDGMENT_MODE_MATCHED", ("goalJudgmentMode",)),
    ("MAX_STEPS_MATCHED", ("limits.maxSteps",)),
    ("BACK_LIMITS_MATCHED", ("limits.maxModelBackActions", "limits.maxSystemBackActions")),
    ("START_PATH_MATCHED", ("startPath",)),
    ("BROWSER_STATE_MATCHED", ("comparisonContext.initialBrowserStateId",)),
    ("VIEWPORT_MATCHED", ("viewport",)),
    ("SITE_VERSION_MATCHED", ("siteVersion",)),
    ("LAYOUT_MATCHED", ("comparisonContext.layoutVersion",)),
    ("MODEL_PAYLOAD_LEAK_CHECK_PASSED", ("comparisonContext.leakCheckPassed",)),
    ("TEMPERATURE_MATCHED", ("temperature",)),
    ("THRESHOLD_CONFIG_MATCHED", ("thresholdConfig.version",)),
    ("ANALYSIS_VERSION_MATCHED", ("analysisVersion",)),
    ("REFERENCE_PATH_CONFIG_MATCHED", ("referencePathConfig",)),
    ("CANDIDATE_POLICY_MATCHED", ("comparisonContext.candidatePolicyVersion",)),
    ("RESULT_ORIGIN_MATCHED", ("resultOrigin",)),
)
_METRICS = ("NormalizedEntropy", "ClickOnlyNormalizedEntropy", "Top2Margin",
            "ClickOnlyTop2Margin", "GoBackProbability", "GoalPathProbability")
_OUTCOME = ("goalReached", "stepCount", "successfulNavigationClickCount", "modelBackCount",
            "systemBackCount", "backCount", "navigationCost", "extraSteps", "extraNavigationCost",
            "referencePathDeviationCount", "path", "detectedIssueTypes")


def _get(obj, path):
    for part in path.split("."):
        if obj is None:   # 예: 직접 입력한 목표의 referencePathConfig=None
            return None
        obj = obj[part]
    return obj


def _representatives(steps):
    result = {}
    for step in steps:
        key = step["page"]["pageKey"]
        if key not in result or (result[key]["candidateSet"]["candidateSetChanged"]
                                 and not step["candidateSet"]["candidateSetChanged"]):
            result[key] = step
    return result


def _keys(step):
    return [candidate.get("semanticKey") for candidate in step["candidateSet"]["candidates"]]


def _boxes_match(before, after):
    if not before or not after or _keys(before) != _keys(after):
        return False
    if any(not key for key in _keys(before)) or len(set(_keys(before))) != len(_keys(before)):
        return False
    for b, a in zip(before["candidateSet"]["candidates"], after["candidateSet"]["candidates"]):
        bb, ab = b.get("bbox"), a.get("bbox")
        if not isinstance(bb, dict) or not isinstance(ab, dict):
            return False
        for field in ("x", "y", "width", "height"):
            if not isinstance(bb.get(field), (int, float)) or not isinstance(ab.get(field), (int, float)):
                return False
            if abs(bb[field] - ab[field]) > 2:
                return False
    return True


_VERIFICATION_FIELDS = ("type", "path", "text", "exact", "scope")


def _verification(run):
    """저장된 성공 조건만 사용한다. 미완성·과거 Run은 비교 가능하다고 가정하지 않는다."""
    value = run.get("verification")
    if not isinstance(value, dict) or any(k not in value for k in _VERIFICATION_FIELDS):
        return None
    if (value["type"] != "PATH_AND_VISIBLE_TEXT" or not isinstance(value["path"], str)
            or not value["path"].startswith("/shop/") or not isinstance(value["text"], str)
            or not value["text"].strip() or not isinstance(value["exact"], bool)
            or value["scope"] not in SCOPES):
        return None
    return tuple(value[k] for k in _VERIFICATION_FIELDS)


def _conditions(b, a, bs, ass, references):
    checks = []
    for code, paths in _CONDITIONS:
        if code == "GOAL_MATCHED":
            before_goal, after_goal = b.get("goal"), a.get("goal")
            before_verification, after_verification = _verification(b), _verification(a)
            if (not isinstance(before_goal, str) or not before_goal.strip()
                    or not isinstance(after_goal, str) or not after_goal.strip()
                    or before_verification is None or after_verification is None):
                checks.append({"code": code, "matched": False,
                               "message": "목표 문장 또는 저장된 성공 조건을 확인할 수 없습니다."})
                continue
            bv, av = [before_goal, before_verification], [after_goal, after_verification]
        elif code == "PERSONA_MATCHED":
            bv, av = [b.get("persona")], [a.get("persona")]
            if any(not isinstance(value, str) or not value.strip() for value in (bv[0], av[0])):
                checks.append({"code": code, "matched": False,
                               "message": "저장된 사용자 유형 문장을 확인할 수 없습니다."})
                continue
        elif code == "REFERENCE_PATH_CONFIG_MATCHED":
            (before_known, before_ref, before_note), (after_known, after_ref, after_note) = references
            if not before_known or not after_known:
                checks.append({"code": code, "matched": False,
                               "message": f"Before: {before_note or '확인됨'}; After: {after_note or '확인됨'}"})
                continue
            bv, av = [before_ref], [after_ref]
        else:
            bv, av = [_get(b, path) for path in paths], [_get(a, path) for path in paths]
        matched = bv == av
        if code == "MODEL_PAYLOAD_LEAK_CHECK_PASSED":
            matched = bv[0] is True and av[0] is True
        elif code == "LAYOUT_MATCHED":
            matched = matched and _boxes_match(bs.get("home"), ass.get("home"))
        message = None if matched else f"Before={bv!r}, After={av!r}" + (
            "; home 후보 순서 또는 위치가 일치하지 않거나 home 기록이 없습니다." if code == "LAYOUT_MATCHED" else "")
        if code == "REFERENCE_PATH_CONFIG_MATCHED" and (before_note or after_note):
            note = "; ".join(dict.fromkeys(n for n in (before_note, after_note) if n))
            message = f"{message}; {note}" if message else note
        checks.append({"code": code, "matched": matched, "message": message})
    return checks


def _href(value):
    if value is None:
        return None
    path = urlsplit(value).path
    return path.rstrip("/") or "/"


def _screen_reason(b, a, matched):
    if b is None:
        return "NOT_VISITED_IN_BEFORE"
    if a is None:
        return "NOT_VISITED_IN_AFTER"
    if not matched:
        return "GLOBAL_CONDITIONS_MISMATCH"
    bc, ac = b["candidateSet"], a["candidateSet"]
    bk, ak = _keys(b), _keys(a)
    if (any(not k for k in bk + ak) or len(set(bk)) != len(bk) or len(set(ak)) != len(ak)):
        return "MISSING_SEMANTIC_KEY"
    if bc["candidateScope"] != ac["candidateScope"]:
        return "CANDIDATE_SCOPE_MISMATCH"
    if set(bk) != set(ak):
        return "CANDIDATE_SET_MISMATCH"
    if bk != ak:
        return "CANDIDATE_ORDER_MISMATCH"
    if any(x.get("region") != y.get("region") for x, y in zip(bc["candidates"], ac["candidates"])):
        return "CANDIDATE_REGION_MISMATCH"
    if any(_href(x.get("href")) != _href(y.get("href")) for x, y in zip(bc["candidates"], ac["candidates"])):
        return "DESTINATION_MISMATCH"
    if bc["actionSetIncludesBack"] != ac["actionSetIncludesBack"]:
        return "ACTION_SET_MISMATCH"
    if not _boxes_match(b, a):
        return "LAYOUT_MISMATCH"
    return None


def _screen(key, b, a, matched):
    reason = _screen_reason(b, a, matched)
    comparable = reason is None
    bk, ak = _keys(b) if b else [], _keys(a) if a else []
    backs = (b["candidateSet"]["actionSetIncludesBack"], a["candidateSet"]["actionSetIncludesBack"]) if b and a else None
    result = {"pageKey": key, "beforeStepId": b["stepId"] if b else None,
              "afterStepId": a["stepId"] if a else None, "screenComparable": comparable,
              "reason": reason, "candidateSetMatched": bool(b and a and bk == ak and all(bk)
                                                               and len(set(bk)) == len(bk)),
              "actionSetIncludesBack": backs[0] if backs and backs[0] == backs[1] else None,
              "candidateComparisons": [], "metrics": None}
    if not b or not a:
        return result
    # 보존 가능한 유효 키는 원래 순서로 합친다. 키가 없는 후보는 위치별로 따로 남긴다.
    bs, ass = b["candidateSet"]["candidates"], a["candidateSet"]["candidates"]
    pairs, used = [], set()
    for bc in bs:
        key_value = bc.get("semanticKey")
        idx = next((i for i, ac in enumerate(ass) if i not in used and key_value and ac.get("semanticKey") == key_value), None)
        if idx is not None:
            used.add(idx)
        pairs.append((bc, ass[idx] if idx is not None else None))
    pairs.extend((None, ac) for i, ac in enumerate(ass) if i not in used)
    for bc, ac in pairs:
        bp = bc.get("probability") if bc else None
        ap = ac.get("probability") if ac else None
        can_compare = comparable and bc is not None and ac is not None and bp is not None and ap is not None
        result["candidateComparisons"].append({
            "semanticKey": (bc or ac).get("semanticKey"), "beforeLabel": bc.get("label") if bc else None,
            "afterLabel": ac.get("label") if ac else None, "beforeProbability": bp, "afterProbability": ap,
            "deltaProbability": ap - bp if can_compare else None, "comparable": can_compare,
            "reason": reason if reason else (None if can_compare else "CANDIDATE_SET_MISMATCH"),
        })
    metrics = {}
    for name in _METRICS:
        field = name[0].lower() + name[1:]
        bv, av = b["decision"].get(field), a["decision"].get(field)
        metrics["before" + name], metrics["after" + name] = bv, av
        can_compare = comparable and bv is not None and av is not None
        if name == "GoBackProbability":
            can_compare = can_compare and backs == (True, True)
        metrics["delta" + name] = av - bv if can_compare else None
    result["metrics"] = metrics
    return result


def compare(before, after):
    """분석 완료된 Before/After 묶음에서 ComparisonResult를 생성한다."""
    b, a = before["run"], after["run"]
    if any(run.get("validity") != "VALID" or run.get("status") in ("READY", "RUNNING")
           or run.get("summary") is None for run in (b, a)):
        raise ValueError("종료된 유효 Run만 비교할 수 있습니다.")
    if b["experimentKey"] != a["experimentKey"] or b["variant"] != "before" or a["variant"] != "after":
        raise ValueError("같은 실험의 Before/After Run이 필요합니다.")
    for bundle in (before, after):
        if any(step["runId"] != bundle["run"]["runId"] for step in bundle["steps"]):
            raise ValueError("Step과 Run의 runId가 다릅니다.")
    bs, ass = _representatives(before["steps"]), _representatives(after["steps"])
    references = (_reference(b), _reference(a))
    checks = _conditions(b, a, bs, ass, references)
    matched = all(check["matched"] for check in checks)
    keys = list(bs) + [key for key in ass if key not in bs]
    if "home" in keys:
        keys.remove("home")
        keys.insert(0, "home")
    screens = [_screen(key, bs.get(key), ass.get(key), matched) for key in keys]
    invalid_site_map = []
    for run, ref_result in zip((b, a), references):
        ref = run.get("referencePathConfig")
        invalid_site_map.append(isinstance(ref, dict) and ref.get("basis") == "SITE_MAP"
                                and not ref_result[0])
    for screen in screens:
        metrics = screen["metrics"]
        if metrics:
            for side, invalid in zip(("before", "after"), invalid_site_map):
                if invalid:
                    metrics[side + "GoalPathProbability"] = None
                    metrics["deltaGoalPathProbability"] = None
    complete = matched and any(s["pageKey"] == "home" and s["screenComparable"] for s in screens) and all(
        s["screenComparable"] for s in screens)
    def outcome(run, invalid):
        result = {"status": run["status"], "terminationReason": run["terminationReason"],
                  **{key: deepcopy(run["summary"][key]) for key in _OUTCOME},
                  "detourMoveCount": run["summary"].get("detourMoveCount")}
        if invalid:
            for key in ("extraSteps", "extraNavigationCost", "referencePathDeviationCount", "detourMoveCount"):
                result[key] = None
        return result
    return {"schemaVersion": b["schemaVersion"], "experimentKey": b["experimentKey"],
            "beforeRunId": b["runId"], "afterRunId": a["runId"],
            "comparisonStatus": "COMPLETED" if complete else "PARTIAL", "conditionsMatched": matched,
            "conditionChecks": checks, "layoutMatched": next(c["matched"] for c in checks if c["code"] == "LAYOUT_MATCHED"),
            "screenComparisons": screens, "runComparison": {"before": outcome(b, invalid_site_map[0]),
                                                               "after": outcome(a, invalid_site_map[1])},
            "beforeResultOrigin": b["resultOrigin"], "afterResultOrigin": a["resultOrigin"],
            "resultOrigin": b["resultOrigin"] if b["resultOrigin"] == a["resultOrigin"] else None,
            "createdAt": datetime.now(timezone.utc).isoformat()}
