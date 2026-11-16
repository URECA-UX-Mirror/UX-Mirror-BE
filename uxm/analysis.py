"""UX 분석·비교의 공개 인터페이스.

완료된 Run에서 UX 검토 후보를 탐지하고 Before/After를 비교한다.
"""
import copy
import hashlib
import json


ISSUE_ORDER = (
    "GOAL_NOT_REACHED", "REFERENCE_PATH_DEVIATION", "REPEATED_VISIT",
    "EXTRA_STEPS", "CHOICE_AMBIGUITY",
)
ISSUE_METADATA = {
    "GOAL_NOT_REACHED": ("목표 미달성", "설정한 탐색 제한 안에 목표에 도달하지 못했습니다.", "PRIORITY_REVIEW"),
    "REFERENCE_PATH_DEVIATION": ("기준 경로 이탈", "모델이 허용 경로와 다른 분기로 이동했습니다.", "PRIORITY_REVIEW"),
    "REPEATED_VISIT": ("화면 재방문", "모델 행동으로 이전에 방문한 화면을 다시 방문했습니다.", "PRIORITY_REVIEW"),
    "EXTRA_STEPS": ("추가 탐색 단계", "목표까지 허용 최단 경로보다 많은 이동이 발생했습니다.", "GENERAL_REVIEW"),
    "CHOICE_AMBIGUITY": ("메뉴 선택 확률 분산", "여러 클릭 후보에 모델의 선택 확률이 분산되었습니다.", "GENERAL_REVIEW"),
}
RUN_ISSUES = {"GOAL_NOT_REACHED", "EXTRA_STEPS"}


def _issue_key(run_id: str, issue_type: str, page_signature: str | None, evidence: dict) -> tuple:
    """기준 경로 이탈은 출발·도착 pageKey 쌍으로 다른 분기를 구분한다."""
    if issue_type == "GOAL_NOT_REACHED":
        return run_id, issue_type
    if issue_type == "REFERENCE_PATH_DEVIATION":
        if not evidence.get("fromPageKey") or not evidence.get("toPageKey"):
            raise ValueError("경로 이탈에는 fromPageKey와 toPageKey가 필요합니다.")
        return run_id, page_signature, issue_type, evidence["fromPageKey"], evidence["toPageKey"]
    return run_id, page_signature, issue_type


def create_issue(run: dict, issue_type: str, evidence: dict, created_at: str, *,
                 step: dict | None = None) -> dict | None:
    """이미 관찰된 근거를 계약 Issue로 변환한다. 입력 데이터는 변경하지 않는다.

    created_at은 관찰 시각(또는 Run 종료 시각)이다. 동일 입력에 동일 ID를
    만들며, 여러 관찰은 merge_issues에 발생 순서대로 전달한다.
    """
    if run.get("validity") == "INVALID":
        return None
    if issue_type not in ISSUE_METADATA:
        raise ValueError(f"알 수 없는 Issue 유형: {issue_type}")
    if not created_at:
        raise ValueError("Issue 관찰 시각이 필요합니다.")
    if issue_type in RUN_ISSUES:
        if step is not None:
            raise ValueError("Run 전체 Issue에는 Step을 지정하지 않습니다.")
        step_id = page_path = page_key = page_signature = None
    else:
        if step is None:
            raise ValueError("화면 Issue에는 관련 Step이 필요합니다.")
        page = step["page"]
        step_id, page_path = step["stepId"], page["path"]
        page_key, page_signature = page["pageKey"], page["pageSignature"]
        if step["runId"] != run["runId"]:
            raise ValueError("Step과 Run의 runId가 다릅니다.")
    key = _issue_key(run["runId"], issue_type, page_signature, evidence)
    digest = hashlib.sha256(json.dumps(key, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]
    title, description, priority = ISSUE_METADATA[issue_type]
    return {
        "schemaVersion": run["schemaVersion"], "issueId": f"issue_{digest}", "runId": run["runId"],
        "issueType": issue_type, "stepId": step_id, "relatedStepIds": [step_id] if step_id is not None else [],
        "pagePath": page_path, "pageKey": page_key, "pageSignature": page_signature,
        "title": title, "description": description, "evidence": copy.deepcopy(evidence),
        "reviewPriority": priority, "occurrenceCount": 1, "createdAt": created_at,
    }


def merge_issues(issues: list[dict | None]) -> list[dict]:
    """발생 순서로 중복을 합치고 최초 관찰 정보와 반복 방문 이력을 보존한다."""
    merged = {}
    for issue in issues:
        if issue is None:
            continue
        key = _issue_key(issue["runId"], issue["issueType"], issue["pageSignature"], issue["evidence"])
        if key not in merged:
            merged[key] = copy.deepcopy(issue)
            continue
        existing = merged[key]
        existing["occurrenceCount"] += issue["occurrenceCount"]
        for step_id in issue["relatedStepIds"]:
            if step_id not in existing["relatedStepIds"]:
                existing["relatedStepIds"].append(step_id)
        if issue["issueType"] == "REPEATED_VISIT":
            evidence, new_evidence = existing["evidence"], issue["evidence"]
            if "visitCount" in new_evidence:
                evidence["visitCount"] = max(evidence.get("visitCount", 0), new_evidence["visitCount"])
            if "visitCauses" in new_evidence:
                causes = evidence.setdefault("visitCauses", [])
                for cause in new_evidence["visitCauses"]:
                    if cause not in causes:
                        causes.append(cause)
    return sorted(merged.values(), key=lambda issue: ISSUE_ORDER.index(issue["issueType"]))


def detect_issues(run: dict, steps: list[dict], events: list[dict]) -> list[dict]:
    """종료된 Run의 관찰 기록으로 Issue를 탐지하며 입력은 수정하지 않는다.

    이력은 summary.path를 기준으로 복원한다. 시스템 BACK도 방문 이력에 반영하되
    그 복귀 자체는 모델의 반복 방문 Issue로 만들지 않는다.
    """
    if run.get("validity") == "INVALID":
        return []
    if run.get("validity") != "VALID" or run.get("status") in ("READY", "RUNNING"):
        raise ValueError("종료된 유효 Run만 분석할 수 있습니다.")
    summary = run["summary"]
    if summary is None:
        raise ValueError("유효 Run의 summary가 필요합니다.")
    if any(step["runId"] != run["runId"] for step in steps):
        raise ValueError("Step과 Run의 runId가 다릅니다.")
    if any(event["runId"] != run["runId"] for event in events):
        raise ValueError("Event와 Run의 runId가 다릅니다.")

    found = []
    threshold = run["thresholdConfig"]
    # 화면 상태별 최초 전체 후보 집합만 판정한다. 실패 후보를 뺀 재판단은 제외한다.
    evaluated = set()
    for step in steps:
        signature = step["page"]["pageSignature"]
        candidate_set, decision = step["candidateSet"], step["decision"]
        if candidate_set["candidateSetChanged"] or signature in evaluated:
            continue
        evaluated.add(signature)
        entropy = decision["clickOnlyNormalizedEntropy"]
        candidates = candidate_set["candidates"]
        warnings = decision["metricWarnings"]
        if (entropy is not None and entropy >= threshold["entropyThreshold"]
                and len(candidates) >= threshold["minCandidateCount"]
                and not any(w["code"] == "NO_CLICK_PROBABILITY_MASS" for w in warnings)):
            found.append(create_issue(run, "CHOICE_AMBIGUITY", {
                "clickOnlyNormalizedEntropy": entropy, "clickOnlyTop2Margin": decision["clickOnlyTop2Margin"],
                "candidateCount": len(candidates), "entropyThreshold": threshold["entropyThreshold"],
            }, step["timestamp"], step=step))

    by_step = {step["stepId"]: step for step in steps}
    route, visits, causes = [], {}, {}
    allowed = run["referencePathConfig"]["allowedPaths"]
    for visit in summary["path"]:
        key, signature, cause = visit["pageKey"], visit["pageSignature"], visit["visitCause"]
        if cause == "RUN_START":
            route = [key]
        elif cause == "MODEL_NAVIGATION":
            route.append(key)
        elif cause in ("MODEL_BACK", "SYSTEM_BACK_RECOVERY"):
            # 같은 화면으로의 시스템 복구는 스택을 줄이지 않는다.
            if cause != "SYSTEM_BACK_RECOVERY" or not route or route[-1] != key:
                if len(route) < 2 or route[-2] != key:
                    raise ValueError("BACK 방문이 이전 탐색 경로와 일치하지 않습니다.")
                route.pop()
        else:
            raise ValueError(f"알 수 없는 visitCause: {cause}")
        visits[signature] = visits.get(signature, 0) + 1
        causes.setdefault(signature, []).append(cause)
        if cause not in ("MODEL_NAVIGATION", "MODEL_BACK"):
            continue
        step = by_step.get(visit["stepId"])
        if step is None:
            raise ValueError("모델 방문에 연결된 Step이 없습니다.")
        if visits[signature] > 1:
            # 재방문 Issue의 위치는 행동 전 화면이 아니라 다시 들어간 화면이다.
            destination_step = {**step, "page": {**step["page"], "pageKey": key,
                                                   "pageSignature": signature, "path": visit["path"]}}
            found.append(create_issue(run, "REPEATED_VISIT", {
                "pageSignature": signature, "visitCount": visits[signature],
                "visitCauses": list(causes[signature]),
            }, step["timestamp"], step=destination_step))
        action = step["action"]
        if (cause == "MODEL_NAVIGATION" and action["actionType"] == "CLICK"
                and action["result"] == "NAVIGATED"
                and not any(path[:len(route)] == route for path in allowed)):
            selected = next((c for c in step["candidateSet"]["candidates"]
                             if c["optionId"] == step["decision"]["selectedOptionId"]), None)
            if selected is None:
                raise ValueError("선택한 클릭 후보가 Step에 없습니다.")
            found.append(create_issue(run, "REFERENCE_PATH_DEVIATION", {
                "fromPageKey": step["page"]["pageKey"], "toPageKey": key,
                "selectedSemanticKey": selected["semanticKey"], "selectedProbability": selected["probability"],
            }, step["timestamp"], step=step))

    reached = summary["goalReached"]
    if reached and summary["extraSteps"] is not None and summary["extraSteps"] > 0:
        found.append(create_issue(run, "EXTRA_STEPS", {
            "successfulNavigationClickCount": summary["successfulNavigationClickCount"],
            "shortestAllowedSteps": run["referencePathConfig"]["shortestAllowedSteps"],
            "extraSteps": summary["extraSteps"],
        }, run["finishedAt"]))
    if not reached and run["status"] in ("MAX_STEPS", "BLOCKED"):
        found.append(create_issue(run, "GOAL_NOT_REACHED", {
            "status": run["status"], "terminationReason": run["terminationReason"], "stepCount": summary["stepCount"],
        }, run["finishedAt"]))
    return merge_issues(found)


def compare(before: dict, after: dict) -> dict:
    """각 {run, steps, issues} 묶음으로 Before/After 결과를 계산한다."""
    from uxm.comparison import compare as compare_bundles
    return compare_bundles(before, after)
