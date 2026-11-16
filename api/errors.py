"""공통 오류 응답 `{"error": ErrorDetail}` (계약 16장, 4.9)."""
import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.timeutil import now_iso

log = logging.getLogger("uxm.api")


def error_detail(code: str, message: str, *, retryable: bool = False, details: dict | None = None) -> dict:
    return {"code": code, "message": message, "retryable": retryable, "details": details, "occurredAt": now_iso()}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable


def _response(status: int, code: str, message: str, retryable: bool = False) -> JSONResponse:
    # details는 내부 진단 정보라 사용자 응답에는 넣지 않는다 (16.1)
    return JSONResponse(status_code=status, content={"error": error_detail(code, message, retryable=retryable)})


async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return _response(exc.status, exc.code, exc.message, exc.retryable)


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
    message = f"요청 형식이 올바르지 않습니다: {where} {first.get('msg', '')}".strip()
    return _response(422, "CONTRACT_VALIDATION_ERROR", message)


async def http_error_handler(_: Request, exc) -> JSONResponse:
    """라우팅에 걸리지 않은 404·405 등도 공통 형식으로."""
    code = "RUN_NOT_FOUND" if exc.status_code == 404 else "CONTRACT_VALIDATION_ERROR"
    message = "요청한 주소를 찾을 수 없습니다." if exc.status_code == 404 else "지원하지 않는 요청입니다."
    return _response(exc.status_code, code, message)


class AnalysisError(Exception):
    """분석·비교 계산 실패. HTTP 500 + ANALYSIS_ERROR (17.6)."""


async def analysis_error_handler(_: Request, exc: AnalysisError) -> JSONResponse:
    if exc.__cause__ is not None:
        log.error("analysis error: %s", exc, exc_info=exc.__cause__)
    return _response(500, "ANALYSIS_ERROR", str(exc) or "분석 결과를 계산하지 못했습니다.")


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # 저장 파일 손상 등 예상하지 못한 실패. 원인은 서버 로그에만 남기고 응답에는 넣지 않는다 (16.1).
    log.error("unexpected error on %s %s", request.method, request.url.path, exc_info=exc)
    return _response(500, "STORAGE_ERROR", "저장된 실행 결과를 읽지 못했습니다.")
