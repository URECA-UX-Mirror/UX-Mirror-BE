"""Run 실행기: 동시에 1개, 별도 스레드 (계약 17.2, 기획안 10.1.2).

Playwright Sync API는 asyncio 루프 안에서 부를 수 없어서(#2 참고) 스레드에서 돌린다.
흐름: READY 저장 → 스레드 시작 → RUNNING → 브라우저·모델 준비 → run_agent → 종료 상태·유효성 확정 → Issue 분석.
"""
import logging
import os
import threading

from api.run_lock import RunLock
from api import run_factory
from api.errors import ApiError, error_detail
from api.integrations import Integrations
from api.store import RunStore
from uxm.agent import agent_settings
from api.timeutil import now_iso

log = logging.getLogger("uxm.api.runner")

SHOP_URLS = {
    "before": os.getenv("SHOP_BEFORE_URL", "http://127.0.0.1:8001"),
    "after": os.getenv("SHOP_AFTER_URL", "http://127.0.0.1:8002"),
}

# run_agent 결과에서 Run에 반영하는 필드. 나머지(설정·버전 값)는 생성 때 값을 그대로 둔다.
# validity·invalidReason은 받지 않는다. 종료 시 finalize()가 terminationReason·error.code로 정한다.
AGENT_RESULT_FIELDS = ("status", "terminationReason", "progress", "summary", "error", "finishedAt")

ISSUE_ORDER = ["GOAL_NOT_REACHED", "REFERENCE_PATH_DEVIATION", "REPEATED_VISIT", "EXTRA_STEPS", "CHOICE_AMBIGUITY"]


class Runner:
    def __init__(self, store: RunStore, integrations: Integrations):
        self.store = store
        self.integrations = integrations
        self._lock = threading.Lock()
        self._active: str | None = None
        self._thread: threading.Thread | None = None
        # UX Optimizer 연결 (고도화 v1.3 5.5). Optimizer가 없으면 기존 P0 동작 그대로.
        # 프로세스 사이 한 번에 Run 하나 (#122): API·검증 스크립트·Optimization이 결과 폴더의 같은 잠금을 쓴다
        self.run_lock = RunLock.for_results(store.results_dir)
        self.site_base_url = None    # siteInstanceId → 서버에 등록된 사이트 주소 (없으면 None)
        self.external_busy = None    # external_busy(run) → Run을 만들면 안 되는 이유 (후보 평가·검증 중 등) 또는 None

    # ---- 상태
    @property
    def active_run_id(self) -> str | None:
        return self._active

    def external_run(self) -> dict | None:
        """다른 프로세스(서버에서 직접 돌리는 검증 스크립트 등)가 잡고 있는 Run 잠금. 이 프로세스가 잡고 있으면 None."""
        if self.run_lock.held_here:
            return None
        return self.run_lock.holder()

    @property
    def creation_lock(self) -> threading.Lock:
        """Run 생성 확인과 시작을 묶는 잠금. Optimizer가 평가·검증을 시작할 때도 같이 잡는다 (경쟁 방지)."""
        return self._lock

    def ensure_ready(self) -> None:
        """실행에 필요한 모듈이 없으면 Run을 만들지 않고 503 (17.2)."""
        missing = []
        if self.integrations.agent() is None:
            missing.append("에이전트 루프(uxm/agent.py)")
        if self.integrations.browser() is None:
            missing.append("브라우저(uxm/browser.py)")
        if missing:
            raise ApiError(503, "BROWSER_ERROR", f"실행 환경이 준비되지 않았습니다: {', '.join(missing)}")

    # ---- 생성
    def create(self, run: dict, goal: dict) -> dict:
        """중복 검사 → READY 저장 → 이전 INVALID Run 연결 → 실행 시작. 검사와 시작은 한 번에 (경쟁 방지)."""
        with self._lock:
            if self._active is not None:
                raise ApiError(409, "RUN_ALREADY_ACTIVE", "다른 실행이 진행 중입니다.", retryable=True)
            busy = self.external_busy(run) if self.external_busy else None
            if busy:
                raise ApiError(409, "RUN_ALREADY_ACTIVE", busy, retryable=True)
            runs = [r for r in self.store.list_runs() if r.get("experimentKey") == run["experimentKey"]]
            # Before/After는 같은 문구·성공 조건이어야 비교된다. 첫 Run과 다르면 만들지 않는다.
            first = min(runs, key=lambda r: r.get("createdAt") or "", default=None)
            diff = run_factory.input_mismatch(run, first) if first else []
            if diff:
                raise ApiError(422, "CONTRACT_VALIDATION_ERROR",
                               f"같은 experimentKey의 첫 실행과 입력이 다릅니다: {', '.join(diff)}")
            same = [r for r in runs if r.get("variant") == run["variant"]]
            if any(r.get("status") in ("READY", "RUNNING") or r.get("validity") == "VALID" for r in same):
                raise ApiError(409, "EXPERIMENT_RUN_EXISTS",
                               "같은 experimentKey와 variant에 유효하거나 진행 중인 실행이 이미 있습니다.")
            if not self.run_lock.acquire({"kind": "RUN", "runId": run["runId"]}):
                raise ApiError(409, "RUN_ALREADY_ACTIVE",
                               "다른 실행이 진행 중입니다 (서버의 다른 작업이 Run을 실행하고 있습니다).", retryable=True)
            try:
                self.store.save_run(run)
                self._link_replacement(same, run["runId"])
                self._active = run["runId"]
                self._thread = threading.Thread(target=self._execute, args=(run["runId"], goal),
                                                name=f"run-{run['runId']}", daemon=True)
                self._thread.start()
            except BaseException:
                self._active = None
                self.run_lock.release()
                raise
        return run

    def _link_replacement(self, same: list[dict], new_run_id: str) -> None:
        """기존 Run이 모두 INVALID면 체인 끝(대체 Run이 없는 가장 최근 INVALID)에 새 Run을 연결한다 (5.1)."""
        tails = [r for r in same if r.get("validity") == "INVALID" and not r.get("replacementRunId")]
        if not tails:
            return
        tail = max(tails, key=lambda r: r.get("createdAt") or "")
        tail["replacementRunId"] = new_run_id
        self.store.save_run(tail)

    def wait(self, timeout: float | None = None) -> None:
        """테스트용: 진행 중인 실행이 끝날 때까지 기다린다."""
        if self._thread is not None:
            self._thread.join(timeout)

    # ---- 실행 (스레드)
    def _execute(self, run_id: str, goal: dict) -> None:
        run = self.store.load_run(run_id)
        try:
            run["status"] = "RUNNING"
            run["startedAt"] = now_iso()
            run["progress"]["lastUpdatedAt"] = run["startedAt"]
            self.store.save_run(run)
            result = self._run_agent(run, goal)
            run = self.store.load_run(run_id)   # 루프가 실행 중에 run.json을 갱신했을 수 있음
            for key in AGENT_RESULT_FIELDS:
                if key in result:
                    run[key] = result[key]
            context = result.get("comparisonContext") or {}
            if "leakCheckPassed" in context:
                run["comparisonContext"]["leakCheckPassed"] = context["leakCheckPassed"]
            if run["status"] not in run_factory.TERMINAL_STATUSES:
                self._fail(run, "BROWSER_ERROR", "BROWSER_ERROR", "에이전트가 종료 상태 없이 끝났습니다.")
        except _RunFailure as f:
            run = self._reload_or(run_id, run)
            self._fail(run, f.reason, f.code, f.message)
        except Exception:   # 예상 못 한 예외도 Run을 RUNNING에 남기지 않는다
            log.exception("run %s failed", run_id)
            run = self._reload_or(run_id, run)
            self._fail(run, "BROWSER_ERROR", "BROWSER_ERROR", "실행 중 예기치 않은 오류가 발생했습니다.")
        finally:
            try:
                run_factory.finalize(run)
                self.store.save_run(run)
                self.analyze(run)
            except Exception:
                log.exception("run %s finalize failed", run_id)
            with self._lock:
                self._active = None
                self.run_lock.release()

    def _reload_or(self, run_id: str, fallback: dict) -> dict:
        try:
            return self.store.load_run(run_id)
        except Exception:
            return fallback

    @staticmethod
    def _fail(run: dict, reason: str, code: str, message: str) -> None:
        run["status"] = "ERROR"
        run["terminationReason"] = reason
        run["error"] = error_detail(code, message)

    def _run_agent(self, run: dict, goal: dict) -> dict:
        run_agent = self.integrations.agent()
        browser = self.integrations.browser()
        if run_agent is None or browser is None:
            raise _RunFailure("BROWSER_ERROR", "BROWSER_ERROR", "실행 환경이 준비되지 않았습니다.")
        open_browser, close_browser = browser

        settings = run_factory.model_settings()
        try:
            backend = self.integrations.backend(settings["config"])
        except Exception as e:
            log.exception("backend init failed")
            raise _RunFailure("MODEL_ERROR", "MODEL_UNAVAILABLE", "모델 서버에 연결하지 못했습니다.") from e

        base_url = self._base_url(run)
        start_url = self._start_url(run, base_url)
        try:
            session = open_browser(start_url)
        except Exception as e:
            raise _RunFailure("BROWSER_ERROR", "BROWSER_ERROR", "브라우저 또는 쇼핑몰을 시작하지 못했습니다.") from e
        try:
            # 루프 설정은 루프 쪽 함수로 만든다 (scripts/run_c.py와 같은 설정, #7)
            settings = agent_settings(run, goal, self.store.run_dir(run["runId"]), start_url, base_url)
            return run_agent(settings, backend, session["page"]) or {}
        finally:
            try:
                close_browser(session)
            except Exception:
                log.exception("close_browser failed")

    @staticmethod
    def _scenario_scope(run: dict):
        from uxm.site_scope import load_scope
        try:
            return load_scope(run["siteId"])
        except ValueError as e:
            raise _RunFailure("BROWSER_ERROR", "BROWSER_ERROR", "등록되지 않은 사이트입니다.") from e

    def _start_url(self, run: dict, base_url: str) -> str:
        """시작 주소. 시나리오 Run은 등록된 화면의 여는 주소(쿼리가 필요한 게시판 등, #74)."""
        if not run.get("siteId"):
            return base_url + run["startPath"]
        from api.sites import page_url
        scope = self._scenario_scope(run)
        key = scope.registered_key(run["startPath"])
        if key is None:
            raise _RunFailure("BROWSER_ERROR", "BROWSER_ERROR", "시작 화면이 사이트에 등록되어 있지 않습니다.")
        return page_url(scope, key)

    def _base_url(self, run: dict) -> str:
        """P0 Run은 variant별 고정 쇼핑몰. Optimization·자동 개선 검증 Run은 등록된 Site Instance 주소 (클라이언트 지정 불가).
        시나리오 Run은 사이트 설정의 주소 (#74)."""
        if run.get("siteId"):
            return self._scenario_scope(run).base_url.rstrip("/")
        # 자동 개선 검증 Run(#101)도 Optimization 검증 Run처럼 등록 주소를 siteInstanceId로 찾는다
        ctx = run.get("optimizationContext") or run.get("improvementContext")
        if not ctx:
            return SHOP_URLS[run["variant"]]
        base = self.site_base_url(ctx["siteInstanceId"]) if self.site_base_url else None
        if not base:
            raise _RunFailure("BROWSER_ERROR", "BROWSER_ERROR", "등록되지 않은 사이트 인스턴스입니다.")
        return base

    # ---- 분석
    def analyze(self, run: dict, *, update_run: bool = True) -> None:
        """종료 후 Issue 분석 (14장). 실행이 끝날 때 한 번만 부른다 (조회 API는 파일을 쓰지 않는다).

        무효 Run은 Issue를 만들지 않는다. 분석 모듈이 없으면 issues.json을 만들지 않아 PENDING으로 남는다.
        update_run=False면 issues.json만 쓰고 run.json은 건드리지 않는다 (scripts/analyze_runs.py).
        """
        run_id = run["runId"]
        if run["validity"] == "INVALID":
            self.store.save_issues(run_id, _issues_doc(run_id, "COMPLETED", []))
            return
        detect = self.integrations.detect_issues()
        if detect is None:
            return
        try:
            items = list(detect(run, self.store.load_steps(run_id), self.store.load_events(run_id)) or [])
        except Exception:
            log.exception("detect_issues failed for %s", run_id)
            self.store.save_issues(run_id, _issues_doc(run_id, "FAILED", []))
            return
        items.sort(key=lambda i: ISSUE_ORDER.index(i["issueType"]) if i.get("issueType") in ISSUE_ORDER else 99)
        # run.json 요약을 먼저 저장하고 issues.json(COMPLETED)을 나중에 쓴다.
        # 비교는 COMPLETED를 본 뒤에만 하므로 그 사이에 비교 요청이 와도 요약이 어긋나지 않는다.
        if update_run and run.get("summary") is not None:
            run["summary"]["detectedIssueTypes"] = issue_types(items)
            self.store.save_run(run)
        self.store.save_issues(run_id, _issues_doc(run_id, "COMPLETED", items))

    # ---- 서버 시작 시 정리
    def recover_interrupted(self) -> list[str]:
        """서버가 꺼지며 READY·RUNNING에 멈춘 Run을 무효 처리한다. 그대로 두면 같은 키로 다시 실행할 수 없다.

        다른 프로세스가 지금 Run 잠금을 잡고 있으면(서버에서 직접 돌리는 검증 스크립트 등) 그 Run은 살아 있으므로 건드리지 않는다.
        """
        fixed = []
        other = self.external_run()
        live = {other.get("runId")} if other else set()
        for run in self.store.list_runs():
            if run.get("status") in ("READY", "RUNNING") and run["runId"] != self._active and run["runId"] not in live:
                self._fail(run, "BROWSER_ERROR", "BROWSER_ERROR", "서버 재시작으로 실행이 중단되었습니다.")
                run_factory.finalize(run)
                self.store.save_run(run)
                self.store.save_issues(run["runId"], _issues_doc(run["runId"], "COMPLETED", []))
                fixed.append(run["runId"])
        return fixed


class _RunFailure(Exception):
    def __init__(self, reason: str, code: str, message: str):
        super().__init__(message)
        self.reason, self.code, self.message = reason, code, message


def issue_types(items: list) -> list[str]:
    """Issue 목록 → summary.detectedIssueTypes (14.4 정렬 순서)."""
    return [t for t in ISSUE_ORDER if any(i.get("issueType") == t for i in items)]


def with_detected_types(run: dict, issues_doc: dict | None) -> dict:
    """응답·비교용 Run. 분석이 COMPLETED면 detectedIssueTypes를 issues.json에서 다시 계산한 값으로 쓴다.

    scripts/analyze_runs.py로 나중에 분석한 Run은 run.json을 고치지 않으므로, 저장된 요약 대신
    완료된 Issue 목록을 기준으로 맞춘다. 파일은 바꾸지 않는다.
    """
    if not issues_doc or issues_doc.get("analysisStatus") != "COMPLETED" or run.get("summary") is None:
        return run
    out = dict(run)
    out["summary"] = dict(run["summary"], detectedIssueTypes=issue_types(issues_doc.get("items", [])))
    return out


def _issues_doc(run_id: str, status: str, items: list) -> dict:
    return {"schemaVersion": run_factory.SCHEMA_VERSION, "runId": run_id, "analysisStatus": status, "items": items}
