"""UX 분석·비교의 공개 인터페이스.

규칙과 비교 계산은 후속 작업에서 구현한다. 미구현 함수를 호출하면 빈 결과를
정상적인 분석 결과로 오인하지 않도록 명시적으로 실패한다.
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
    """종료된 Run과 모델·시스템 이벤트에서 Issue를 탐지한다 (구현 예정)."""
    if run.get("validity") == "INVALID":
        return []
    raise NotImplementedError("UX Issue 탐지는 아직 구현되지 않았습니다.")


def compare(before: dict, after: dict) -> dict:
    """각 {run, steps, issues} 묶음을 비교한다 (구현 예정)."""
    raise NotImplementedError("Before/After 비교는 아직 구현되지 않았습니다.")


# API가 골격을 실제 분석기로 오인하지 않도록 각 함수의 준비 상태를 따로 표시한다.
# 후속 작업에서 해당 함수의 구현과 테스트가 끝나면 그 함수의 표시만 제거한다.
detect_issues.__uxm_pending__ = True
compare.__uxm_pending__ = True
