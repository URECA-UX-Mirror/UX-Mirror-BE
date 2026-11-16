"""READY 상태의 Run 만들기와 종료 시 유효성 결정 (계약 6.1~6.6).

Before/After 비교 조건(15.2)에 쓰이는 값은 모두 여기서 한 번에 채운다.
문구는 요청이 아니라 preset에서 복사해 두 variant가 같은 문구를 쓰게 한다.
"""
import os
import secrets

from api.timeutil import now_iso, now_stamp
from uxm import config as uxm_config
from uxm.prompts import PROMPT_VERSION

SCHEMA_VERSION = "1.3.2"

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
        "referencePathConfig": goal["referencePathConfig"],
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


# ---- 종료 상태 → 유효성 (계약 6.1 유효성 표)

TERMINAL_STATUSES = ("SUCCESS", "BLOCKED", "MAX_STEPS", "TIMEOUT", "ERROR")

# terminationReason → 고정 status. PAGE_LOAD_FAILED는 계약 v1.3.2(BLOCKED)가 아니라
# 최종 기획안·P0 문서(계약 v1.3.3 표기) 기준으로 ERROR + INVALID(BROWSER_CRASH).
STATUS_BY_REASON = {
    "GOAL_VERIFIED": "SUCCESS",
    "MAX_STEPS_REACHED": "MAX_STEPS",
    "NO_VALID_CANDIDATES": "BLOCKED",
    "REVISIT_LIMIT_REACHED": "BLOCKED",
    "BACK_RECOVERY_FAILED": "BLOCKED",
    "MODEL_TIMEOUT": "TIMEOUT",
    "RUN_TIMEOUT": "TIMEOUT",
    "MODEL_ERROR": "ERROR",
    "BROWSER_ERROR": "ERROR",
    "OUT_OF_SCOPE_NAVIGATION": "ERROR",
    "PAGE_LOAD_FAILED": "ERROR",
    "LEAK_CHECK_FAILED": "ERROR",
    "STORAGE_FAILED": "ERROR",
}

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
    """종료 사유에 맞춰 status·validity를 확정한다. 이미 확정된 유효성은 덮어쓰지 않는다."""
    reason = run.get("terminationReason")
    if reason in STATUS_BY_REASON:
        run["status"] = STATUS_BY_REASON[reason]
    if run.get("validity") is None:
        run["validity"], run["invalidReason"] = decide_validity(run["status"], reason, run.get("error"))
    if run["validity"] == "VALID":
        run["invalidReason"] = None
    run["finishedAt"] = run.get("finishedAt") or now_iso()
    return run
