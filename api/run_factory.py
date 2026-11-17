"""READY 상태의 Run 만들기와 종료 시 유효성 결정 (계약 6.1~6.6).

Before/After 비교 조건(15.2)에 쓰이는 값은 모두 여기서 한 번에 채운다.
문구·성공 조건은 preset에서 복사하거나 직접 입력값을 정리해 저장한다. 루프·분석은 Run 값만 읽는다.
같은 experimentKey의 Run끼리 문구·성공 조건이 같은지는 `input_mismatch`로 확인한다 (runner.create).
"""
import logging
import os
import secrets

from api.presets import normalize_verification
from api.timeutil import now_iso, now_stamp
from uxm import config as uxm_config
from uxm import sitemap
from uxm.prompts import PROMPT_VERSION
from uxm.status import STATUS_BY_REASON  # terminationReason → 고정 status (계약 4.14). 루프와 같은 표

log = logging.getLogger(__name__)

SCHEMA_VERSION = "1.3.5"

# 비교 조건 계산용 버전 값 (계약 6.1 예시 값). 바꾸면 이전 Run과 비교 조건이 달라진다.
SITE_VERSION = "shop-v1"
LAYOUT_VERSION = "shop-layout-v1"
INITIAL_BROWSER_STATE_ID = "clean-context-v1"
CANDIDATE_POLICY_VERSION = "candidate-v2"
ANALYSIS_VERSION = "analysis-v1"
THRESHOLD_CONFIG = {
    "version": "threshold-v1",
    "entropyThreshold": 0.8,
    "minCandidateCount": 2,
    "configuredAt": "2026-11-15T09:00:00+09:00",
    "changeReason": "초기 사전 지정 기준",
}

DEFAULT_LIMITS = {
    "modelTimeoutMs": 30000,
    "pageChangeTimeoutMs": 10000,
    "runTimeoutMs": 180000,
    "maxModelBackActions": 2,
    "maxSystemBackActions": 1,
    "maxSamePageVisits": 3,
}


def model_settings() -> dict:
    """`.env` 기반 모델 설정. endpoint는 비밀 URL 대신 식별자만 남긴다 (계약 18.2, 19장)."""
    cfg = uxm_config.load()
    backend = cfg["backend"]
    return {
        "config": cfg,
        "modelVersion": os.getenv("ZORO_MODEL_VERSION", "trillionlabs/Zoro-1"),
        "modelEndpointId": os.getenv("ZORO_ENDPOINT_ID", backend),
        # API는 항상 설정된 실제 모델로 실행한다. MOCK은 테스트용 실행기(scripts)에서만 만든다.
        "resultOrigin": "LIVE",
        "temperature": cfg["temperature"],
        "goalJudgmentMode": cfg["goal_judgment_mode"],
    }


SITE_MAP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), sitemap.SHOP.out)
# 지도를 만든 조건이 Run과 같아야 지도의 간선이 루프가 보는 후보와 같다 (설계 v2.1 2.1)
_SITE_MAP_CONDITIONS = {"siteVersion": SITE_VERSION, "layoutVersion": LAYOUT_VERSION,
                        "candidatePolicyVersion": CANDIDATE_POLICY_VERSION,
                        "initialBrowserStateId": INITIAL_BROWSER_STATE_ID}


def site_map_reference(start_path: str, verification: dict, viewport: dict,
                       path: str | None = None) -> dict | None:
    """직접 입력한 목표의 SITE_MAP 경로 기준 (설계 v2.1 4.1). `distances`를 Run에 복사해 지도가 바뀌어도 재현된다.

    지도 오류(파일 없음·검증 실패·조건 불일치·도착 화면이 지도에 없음)면 None: 실행은 하고 경로 지표는 null (3.2).
    지도는 정상인데 시작 화면에서 도착 화면으로 가는 길이 없으면 ValueError → 실행 전 422 (5장 3번).
    """
    try:
        site_map = sitemap.load_site_map(path or SITE_MAP_PATH)
    except sitemap.SiteMapError as e:
        log.warning("사이트 지도를 쓸 수 없어 경로 기준 없이 실행합니다: %s", e)
        return None
    mismatched = [k for k, v in _SITE_MAP_CONDITIONS.items() if site_map.get(k) != v]
    if site_map.get("viewport") != viewport:
        mismatched.append("viewport")
    start, goal = sitemap.SHOP.page_key(start_path), sitemap.SHOP.page_key(verification["path"])
    if start != site_map["startPageKey"]:
        mismatched.append("startPageKey")
    if goal not in site_map["pages"]:
        mismatched.append("goalPageKey")
    if mismatched:
        log.warning("사이트 지도 조건이 Run과 달라 경로 기준 없이 실행합니다: %s", ", ".join(mismatched))
        return None
    dist = sitemap.distances(site_map["pages"], goal)
    if dist[start] is None:
        raise ValueError("도착 화면으로 가는 경로가 없습니다.")
    return {"basis": "SITE_MAP", "siteMapVersion": site_map["siteMapVersion"],
            "siteMapHash": site_map["siteMapHash"], "startPageKey": start, "goalPageKey": goal,
            "shortestSteps": dist[start], "distances": dist}


def new_run_id(variant: str) -> str:
    return f"run_{variant}_{now_stamp()}_{secrets.token_hex(2)}"


def build_run(req, goal: dict, persona: dict, settings: dict) -> dict:
    viewport = req.viewport.model_dump() if req.viewport else {"width": 1440, "height": 900}
    limits = {"maxSteps": req.maxSteps, **DEFAULT_LIMITS}
    return {
        "schemaVersion": SCHEMA_VERSION,
        "runId": new_run_id(req.variant),
        "experimentKey": req.experimentKey,
        "variant": req.variant,
        "resultOrigin": settings["resultOrigin"],
        "siteVersion": SITE_VERSION,
        "modelVersion": settings["modelVersion"],
        "modelEndpointId": settings["modelEndpointId"],
        "promptVersion": PROMPT_VERSION,
        "temperature": settings["temperature"],
        "decisionMode": "DIRECT",
        "goalJudgmentMode": settings["goalJudgmentMode"],
        "goalId": goal["goalId"],
        "goal": goal["goal"],
        "personaId": persona["personaId"],
        "persona": persona["persona"],
        "startPath": req.startPath.rstrip("/"),
        "viewport": viewport,
        "limits": limits,
        "thresholdConfig": dict(THRESHOLD_CONFIG),
        "analysisVersion": ANALYSIS_VERSION,
        "referencePathConfig": goal["referencePathConfig"],   # preset: ALLOWED_PATHS, 직접 입력: SITE_MAP 또는 None
        "verification": normalize_verification(goal["verification"]),
        "comparisonContext": {
            "leakCheckPassed": None,
            "initialBrowserStateId": INITIAL_BROWSER_STATE_ID,
            "layoutVersion": LAYOUT_VERSION,
            "candidatePolicyVersion": CANDIDATE_POLICY_VERSION,
        },
        "status": "READY",
        "createdAt": now_iso(),
        "startedAt": None,
        "finishedAt": None,
        "terminationReason": None,
        "progress": {
            "stepRecordCount": 0,
            "stepCount": 0,
            "maxSteps": req.maxSteps,
            "currentPageKey": None,
            "lastUpdatedAt": None,
        },
        "summary": None,
        "error": None,
        "validity": None,
        "invalidReason": None,
        "replacementRunId": None,
    }


INPUT_FIELDS = ("goalId", "goal", "personaId", "persona", "verification")


def input_mismatch(run: dict, other: dict) -> list[str]:
    """같은 experimentKey의 Run끼리 다른 입력 필드. verification이 없는 이전 Run은 그 필드를 비교하지 않는다."""
    return [k for k in INPUT_FIELDS
            if not (k == "verification" and "verification" not in other) and run.get(k) != other.get(k)]


# ---- 종료 상태 → 유효성 (계약 6.1 유효성 표)

TERMINAL_STATUSES = ("SUCCESS", "BLOCKED", "MAX_STEPS", "TIMEOUT", "ERROR")

INVALID_BY_REASON = {
    "BROWSER_ERROR": "BROWSER_CRASH",
    "PAGE_LOAD_FAILED": "BROWSER_CRASH",
    "STORAGE_FAILED": "STORAGE_FAILURE",
    "LEAK_CHECK_FAILED": "LEAK_CHECK_FAILED",
}
INVALID_BY_MODEL_ERROR_CODE = {
    "MODEL_UNAVAILABLE": "INFRASTRUCTURE_FAILURE",
    "MODEL_RESPONSE_INVALID": "CORRUPTED_RESPONSE",
    "INVALID_CHOICE": "CORRUPTED_RESPONSE",
}


def decide_validity(status: str, reason: str | None, error: dict | None) -> tuple[str, str | None]:
    """(validity, invalidReason). 결과가 기대와 다를 뿐인 정상 종료는 항상 VALID다."""
    if status != "ERROR":
        return "VALID", None
    if reason == "OUT_OF_SCOPE_NAVIGATION":
        return "VALID", None
    if reason == "MODEL_ERROR":
        code = (error or {}).get("code")
        return "INVALID", INVALID_BY_MODEL_ERROR_CODE.get(code, "INFRASTRUCTURE_FAILURE")
    return "INVALID", INVALID_BY_REASON.get(reason, "BROWSER_CRASH")


def finalize(run: dict) -> dict:
    """종료 사유에 맞춰 status·validity·invalidReason을 확정한다.

    루프가 돌려주거나 run.json에 써 둔 validity는 믿지 않고 항상 terminationReason·error.code로 다시 정한다.
    (예: LEAK_CHECK_FAILED인데 VALID가 넘어와도 INVALID가 된다)
    """
    reason = run.get("terminationReason")
    if reason in STATUS_BY_REASON:
        run["status"] = STATUS_BY_REASON[reason]
    run["validity"], run["invalidReason"] = decide_validity(run["status"], reason, run.get("error"))
    run["finishedAt"] = run.get("finishedAt") or now_iso()
    return run
