"""UX MIRROR 실행 API (계약 17장).

    uvicorn api.main:app --port 8000

`create_app()`은 테스트에서 결과 폴더·연결 모듈을 바꿔 끼우기 위한 것이다.
"""
import os
import socket
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api import run_factory
from api import runner as runner_module
from api.comparison import build_comparison
from api.errors import (AnalysisError, ApiError, analysis_error_handler, api_error_handler,
                        http_error_handler, unexpected_error_handler, validation_error_handler)
from api.integrations import Integrations
from api.presets import Presets, _check_verification, normalize_verification
from api.runner import Runner, with_detected_types
from api.schemas import P0_VIEWPORT, SHOP_PAGES, CreateRunRequest
from api.store import DEFAULT_RESULTS_DIR, RunStore, is_safe_id
from api.timeutil import now_iso
from uxm.leak import FORBIDDEN
from uxm.prompts import PROMPT_VERSION

DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"


def create_app(results_dir: str | None = None, integrations: Integrations | None = None) -> FastAPI:
    store = RunStore(results_dir or os.getenv("UXM_RESULTS_DIR") or DEFAULT_RESULTS_DIR)
    integrations = integrations or Integrations()
    presets = Presets()
    runner = Runner(store, integrations)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        runner.recover_interrupted()
        yield

    app = FastAPI(title="UX MIRROR API", version=run_factory.SCHEMA_VERSION, lifespan=lifespan)
    app.state.store, app.state.runner, app.state.presets = store, runner, presets

    origins = [o.strip() for o in os.getenv("UXM_CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["*"])

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(AnalysisError, analysis_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)

    def load_run_or_404(run_id: str) -> dict:
        if not store.exists(run_id):
            raise ApiError(404, "RUN_NOT_FOUND", "실행을 찾을 수 없습니다.")
        return store.load_run(run_id)

    # ---- 17.9
    @app.get("/api/presets")
    def get_presets(includeTest: bool = False):
        return presets.public_view(include_test=includeTest)

    # ---- 17.2
    @app.post("/api/runs", status_code=201)
    def create_run(req: CreateRunRequest):
        goal, persona = _resolve_goal(req, presets), _resolve_persona(req, presets)
        if req.promptVersion is not None and req.promptVersion != PROMPT_VERSION:
            raise ApiError(422, "CONTRACT_VALIDATION_ERROR",
                           f"서버의 promptVersion은 {PROMPT_VERSION}입니다. 다른 버전은 실행할 수 없습니다.")
        # preset 목표는 시작 화면이 정해져 있다 (refund는 /shop/home, C3 테스트 전용 목표는 /shop/mypage).
        # 허용 경로가 시작 화면 기준이므로 다른 화면에서는 실행하지 않는다. 직접 입력한 목표는 요청 값(기본 /shop/home).
        if goal["startPath"] and "startPath" not in req.model_fields_set:
            req.startPath = goal["startPath"]
        elif goal["startPath"] and req.startPath.rstrip("/") != goal["startPath"]:
            raise ApiError(422, "CONTRACT_VALIDATION_ERROR",
                           f"goalId={req.goalId}는 startPath={goal['startPath']}로만 실행할 수 있습니다.")
        page = req.startPath.rstrip("/").removeprefix("/shop/")
        if not req.startPath.startswith("/shop/") or page not in SHOP_PAGES:
            raise ApiError(422, "CONTRACT_VALIDATION_ERROR",
                           f"startPath는 /shop/{{{','.join(SHOP_PAGES)}}} 중 하나여야 합니다.")
        if req.viewport is not None and req.viewport.model_dump() != P0_VIEWPORT:
            raise ApiError(422, "CONTRACT_VALIDATION_ERROR", "P0 viewport는 1440×900으로 고정입니다.")
        if goal["goalId"] is None:   # 직접 입력한 목표는 허용 경로 대신 사이트 지도 기준 (preset은 ALLOWED_PATHS 유지)
            try:
                goal["referencePathConfig"] = run_factory.site_map_reference(
                    req.startPath, goal["verification"], P0_VIEWPORT)
            except ValueError as e:
                raise _invalid(str(e)) from e
        runner.ensure_ready()
        if goal["goalId"] is None:   # 직접 입력한 성공 조건은 실제 화면에서 먼저 확인한다 (preset은 로드 때 검사)
            if runner.active_run_id is not None:   # 브라우저를 띄우기 전에 바로 알려 준다 (create에서도 다시 확인)
                raise ApiError(409, "RUN_ALREADY_ACTIVE", "다른 실행이 진행 중입니다.", retryable=True)
            integrations.goal_preflight()(goal["verification"], runner_module.SHOP_URLS)
        run = run_factory.build_run(req, goal, persona, run_factory.model_settings())
        runner.create(run, goal)
        return {k: run[k] for k in ("runId", "experimentKey", "variant", "status", "createdAt")} | {
            "maxSteps": run["limits"]["maxSteps"]}

    # ---- 계약 밖 추가 (미결정 OI-2): Run 목록
    @app.get("/api/runs")
    def list_runs(experimentKey: str | None = None, variant: Literal["before", "after"] | None = None):
        runs = [r for r in store.list_runs()
                if (experimentKey is None or r.get("experimentKey") == experimentKey)
                and (variant is None or r.get("variant") == variant)]
        runs.sort(key=lambda r: r.get("createdAt") or "", reverse=True)
        items = [{
            "runId": r["runId"], "experimentKey": r.get("experimentKey"), "variant": r.get("variant"),
            "status": r.get("status"), "terminationReason": r.get("terminationReason"),
            "validity": r.get("validity"), "replacementRunId": r.get("replacementRunId"),
            "resultOrigin": r.get("resultOrigin"),
            "goalReached": (r.get("summary") or {}).get("goalReached"),
            "createdAt": r.get("createdAt"), "finishedAt": r.get("finishedAt"),
        } for r in runs]
        return {"count": len(items), "items": items}

    # ---- 17.3
    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        return with_detected_types(load_run_or_404(run_id), store.load_issues(run_id))

    # ---- 17.4
    @app.get("/api/runs/{run_id}/steps")
    def get_steps(run_id: str):
        load_run_or_404(run_id)
        items = store.load_steps(run_id)
        return {"runId": run_id, "count": len(items), "items": items}

    # ---- 17.5
    @app.get("/api/runs/{run_id}/issues")
    def get_issues(run_id: str):
        run = load_run_or_404(run_id)
        if run.get("status") not in run_factory.TERMINAL_STATUSES:
            return {"runId": run_id, "analysisStatus": "PENDING", "items": []}
        doc = store.load_issues(run_id)   # 조회는 파일을 쓰지 않는다 (E-5 원본 Run·Git 샘플 보호)
        if doc is None:
            return {"runId": run_id, "analysisStatus": "PENDING", "items": []}
        return {"runId": run_id, "analysisStatus": doc.get("analysisStatus", "COMPLETED"), "items": doc.get("items", [])}

    # ---- 17.7
    @app.get("/api/runs/{run_id}/screenshots/{step_id}")
    def get_screenshot(run_id: str, step_id: int, kind: Literal["original", "overlay"] = Query("original")):
        load_run_or_404(run_id)
        step = next((s for s in store.load_steps(run_id) if s.get("stepId") == step_id), None)
        page = (step or {}).get("page") or {}
        ref = page.get("screenshotRef") if kind == "original" else page.get("overlayScreenshotRef")
        path = store.screenshot_path(run_id, ref)
        if path is None:
            raise ApiError(404, "RUN_NOT_FOUND", "스크린샷을 찾을 수 없습니다.")
        return FileResponse(path, media_type="image/png")

    # ---- 17.6
    @app.get("/api/comparisons/{experiment_key}")
    def get_comparison(experiment_key: str):
        if not is_safe_id(experiment_key):
            raise ApiError(404, "RUN_NOT_FOUND", "해당 experimentKey의 실행이 없습니다.")
        return build_comparison(store, integrations, experiment_key)

    # ---- 17.8
    @app.get("/api/health")
    def health():
        settings = run_factory.model_settings()
        model_ready = _model_reachable(settings["config"])
        browser_ready = integrations.browser() is not None and _importable("playwright")
        agent_ready = integrations.agent() is not None
        return {
            "status": "READY" if (model_ready and browser_ready and agent_ready) else "NOT_READY",
            "backend": {"ready": True},
            "model": {"ready": model_ready, "modelVersion": settings["modelVersion"],
                      "endpointId": settings["modelEndpointId"]},
            "browser": {"ready": browser_ready},
            "agent": {"ready": agent_ready},
            "activeRun": runner.active_run_id,
            "checkedAt": now_iso(),
        }

    return app


def _invalid(message: str) -> ApiError:
    return ApiError(422, "CONTRACT_VALIDATION_ERROR", message)


def _sentence(text: str, field: str) -> str:
    """직접 입력한 문장 정리. 빈 값과 모델 입력 누출 검사(T-LEAK)에 걸릴 글자는 실행 전에 거부한다.

    그대로 받으면 실행 중 LEAK_CHECK_FAILED로 Run이 무효가 된다. 검사 기준은 루프와 같은 `FORBIDDEN`.
    """
    text = text.strip()
    if not text:
        raise _invalid(f"{field}가 비어 있습니다.")
    words = [w for w in FORBIDDEN if w in text]
    if words:
        raise _invalid(f"{field}에 쓸 수 없는 글자가 있습니다: {', '.join(words)}")
    return text


def _resolve_goal(req: CreateRunRequest, presets: Presets) -> dict:
    """{goalId, goal, startPath, verification, referencePathConfig}. preset ID 또는 직접 입력 중 하나."""
    if (req.goalId is None) == (req.goal is None):
        raise _invalid("goalId와 goal 중 하나만 보내야 합니다.")
    if req.goalId is not None:
        if req.verification is not None:
            raise _invalid("goalId로 실행할 때는 verification을 보내지 않습니다 (preset 값을 씁니다).")
        preset = presets.goal(req.goalId)
        if preset is None:
            raise _invalid(f"등록되지 않은 goalId입니다: {req.goalId}")
        return {"goalId": preset["goalId"], "goal": preset["goal"], "startPath": preset["startPath"],
                "verification": preset["verification"],
                "referencePathConfig": preset["referencePathConfig"]}
    if req.verification is None:
        raise _invalid("goal을 직접 입력할 때는 성공 조건(verification)이 필요합니다.")
    try:
        goal = {"goalId": None, "goal": _sentence(req.goal, "goal"), "startPath": None,
                "verification": normalize_verification(req.verification.model_dump()),
                "referencePathConfig": None}
        _check_verification(goal)   # preset 로드 때와 같은 검사 (#35)
    except ValueError as e:
        raise _invalid(str(e)) from e
    return goal


def _resolve_persona(req: CreateRunRequest, presets: Presets) -> dict:
    if (req.personaId is None) == (req.persona is None):
        raise _invalid("personaId와 persona 중 하나만 보내야 합니다.")
    if req.personaId is not None:
        persona = presets.persona(req.personaId)
        if persona is None:
            raise _invalid(f"등록되지 않은 personaId입니다: {req.personaId}")
        return {"personaId": persona["personaId"], "persona": persona["persona"]}
    return {"personaId": None, "persona": _sentence(req.persona, "persona")}


def _importable(module: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(module) is not None


def _model_reachable(cfg: dict) -> bool:
    """http: 서버 포트에 연결되는지만 본다(추론 성공은 보장하지 않음, 17.8). local: 체크포인트 폴더가 있는지."""
    if cfg["backend"] == "http":
        u = urlsplit(cfg["base_url"])
        port = u.port or (443 if u.scheme == "https" else 80)
        try:
            with socket.create_connection((u.hostname, port), timeout=1.0):
                return True
        except OSError:
            return False
    return os.path.isdir(cfg["checkpoint"])


app = create_app()
