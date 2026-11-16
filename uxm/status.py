"""종료 사유(terminationReason) → Run 상태(status) (계약 4.14).

루프(uxm/agent.py)와 실행 API(api/run_factory.py)가 같이 쓴다. 계약이 바뀌면 여기 한 곳만 고친다.
"""

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
    "PAGE_LOAD_FAILED": "ERROR",         # v1.3.3부터 ERROR + INVALID(BROWSER_CRASH)
    "LEAK_CHECK_FAILED": "ERROR",
    "STORAGE_FAILED": "ERROR",
}
