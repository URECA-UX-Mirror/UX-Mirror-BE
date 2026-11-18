"""비교 대상 Run 고르기 (계약 15.1, 17.6). 비교 계산 자체는 4번 `uxm/analysis.py`의 `compare`가 한다.

두 최종 Run의 Issue 분석이 모두 COMPLETED일 때만 비교한다 (v1.3.4).
분석 전 빈 Issue 목록을 "Issue 없음"으로 쓰지 않기 위해서다.
"""
from api.errors import AnalysisError, ApiError
from api.integrations import Integrations
from api.runner import with_detected_types
from api.store import RunStore


def final_run(runs: list[dict]) -> dict | None:
    """한 variant의 Run 중 replacementRunId 체인을 끝까지 따라간 최종 Run.

    체인 시작(다른 Run이 대체 대상으로 가리키지 않는 Run)이 여럿이면 가장 최근에 만든 체인을 쓴다.
    """
    if not runs:
        return None
    by_id = {r["runId"]: r for r in runs}
    pointed = {r.get("replacementRunId") for r in runs if r.get("replacementRunId")}
    heads = [r for r in runs if r["runId"] not in pointed] or runs
    tails = []
    for head in heads:
        cur, seen = head, set()
        while cur.get("replacementRunId") in by_id and cur["runId"] not in seen:   # 순환 방지
            seen.add(cur["runId"])
            cur = by_id[cur["replacementRunId"]]
        tails.append(cur)
    return max(tails, key=lambda r: r.get("createdAt") or "")


def select_pair(store: RunStore, experiment_key: str) -> tuple[dict, dict]:
    runs = [r for r in store.list_runs() if r.get("experimentKey") == experiment_key]
    if not runs:
        raise ApiError(404, "RUN_NOT_FOUND", "해당 experimentKey의 실행이 없습니다.")
    runs = [r for r in runs if r.get("mode") != "SCENARIO"]   # 시나리오 Run은 비교하지 않는다 (#74, 리뷰 3)
    if not runs:
        raise ApiError(404, "RUN_NOT_FOUND", "시나리오 실행은 비교하지 않습니다.")
    finals = {v: final_run([r for r in runs if r.get("variant") == v]) for v in ("before", "after")}
    if any(r is not None and r.get("status") in ("READY", "RUNNING") for r in finals.values()):
        raise ApiError(409, "COMPARISON_NOT_READY", "비교할 실행이 아직 끝나지 않았습니다.", retryable=True)
    if any(r is None or r.get("validity") != "VALID" for r in finals.values()):
        raise ApiError(409, "COMPARISON_RUN_INVALID", "Before 또는 After에 유효한 최종 실행이 없습니다.")
    return finals["before"], finals["after"]


def analysis_status(store: RunStore, run_id: str) -> str:
    """issues.json이 아직 없으면 PENDING."""
    doc = store.load_issues(run_id)
    return (doc or {}).get("analysisStatus", "PENDING")


def require_analyzed(store: RunStore, before: dict, after: dict) -> None:
    statuses = [analysis_status(store, r["runId"]) for r in (before, after)]
    if "FAILED" in statuses:
        raise AnalysisError("비교할 실행의 Issue 분석이 실패했습니다.")
    if any(s != "COMPLETED" for s in statuses):
        raise ApiError(409, "COMPARISON_NOT_READY", "비교할 실행의 Issue 분석이 아직 끝나지 않았습니다.", retryable=True)


def _bundle(store: RunStore, run: dict) -> dict:
    issues = store.load_issues(run["runId"]) or {}
    return {"run": with_detected_types(run, issues), "steps": store.load_steps(run["runId"]),
            "issues": issues.get("items", [])}


def build_comparison(store: RunStore, integrations: Integrations, experiment_key: str) -> dict:
    before, after = select_pair(store, experiment_key)
    require_analyzed(store, before, after)
    compare = integrations.compare()
    if compare is None:
        raise AnalysisError("비교 모듈(uxm/analysis.py)이 아직 준비되지 않았습니다.")
    try:
        result = compare(_bundle(store, before), _bundle(store, after))
    except Exception as e:
        raise AnalysisError("비교 결과를 계산하지 못했습니다.") from e
    if not isinstance(result, dict):
        raise AnalysisError("비교 결과 형식이 올바르지 않습니다.")
    store.save_comparison(experiment_key, result)   # 고른 Run이 바뀔 수 있어 매번 다시 계산해 덮어쓴다 (18.1)
    return result
