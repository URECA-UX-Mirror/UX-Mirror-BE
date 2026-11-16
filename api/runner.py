"""Run 실행기: 동시에 1개, 별도 스레드 (계약 17.2, 기획안 10.1.2).

Playwright Sync API는 asyncio 루프 안에서 부를 수 없어서(#2 참고) 스레드에서 돌린다.
흐름: READY 저장 → 스레드 시작 → RUNNING → 브라우저·모델 준비 → run_agent → 종료 상태·유효성 확정 → Issue 분석.
"""
import logging
import os
import threading

from api import run_factory
from api.errors import ApiError, error_detail
from api.integrations import Integrations
from api.presets import success_condition
from api.store import RunStore
from api.timeutil import now_iso

log = logging.getLogger("uxm.api.runner")

SHOP_URLS = {
    "before": os.getenv("SHOP_BEFORE_URL", "http://127.0.0.1:8001"),
    "after": os.getenv("SHOP_AFTER_URL", "http://127.0.0.1:8002"),
}

# run_agent 결과에서 Run에 반영하는 필드. 나머지(설정·버전 값)는 생성 때 값을 그대로 둔다.
AGENT_RESULT_FIELDS = ("status", "terminationReason", "progress", "summary", "error",
                       "validity", "invalidReason", "finishedAt")

ISSUE_ORDER = ["GOAL_NOT_REACHED", "REFERENCE_PATH_DEVIATION", "REPEATED_VISIT", "EXTRA_STEPS", "CHOICE_AMBIGUITY"]


class Runner:
    def __init__(self, store: RunStore, integrations: Integrations):
        self.store = store
        self.integrations = integrations
        self._lock = threading.Lock()
        self._active: str | None = None
        self._thread: threading.Thread | None = None

    # ---- 상태
    @property
    def active_run_id(self) -> str | None:
        return self._active

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
            same = [r for r in self.store.list_runs()
                    if r.get("experimentKey") == run["experimentKey"] and r.get("variant") == run["variant"]]
            if any(r.get("status") in ("READY", "RUNNING") or r.get("validity") == "VALID" for r in same):
                raise ApiError(409, "EXPERIMENT_RUN_EXISTS",
                               "같은 experimentKey와 variant에 유효하거나 진행 중인 실행이 이미 있습니다.")
            self.store.save_run(run)
            self._link_replacement(same, run["runId"])
            self._active = run["runId"]
            self._thread = threading.Thread(target=self._execute, args=(run["runId"], goal),
                                            name=f"run-{run['runId']}", daemon=True)
            self._thread.start()
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
        # 루프가 실패 전에 VALID를 저장했어도 실패 사유로 다시 정한다 (finalize가 재계산)
        run["validity"] = None
        run["invalidReason"] = None

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

        base_url = SHOP_URLS[run["variant"]]
        start_url = base_url + run["startPath"]
        try:
            session = open_browser(start_url)
        except Exception as e:
            raise _RunFailure("BROWSER_ERROR", "BROWSER_ERROR", "브라우저 또는 쇼핑몰을 시작하지 못했습니다.") from e
        try:
            return run_agent(self._agent_settings(run, goal, base_url, start_url), backend, session["page"]) or {}
        finally:
            try:
                close_browser(session)
            except Exception:
                log.exception("close_browser failed")

    def _agent_settings(self, run: dict, goal: dict, base_url: str, start_url: str) -> dict:
        """run_agent에 넘기는 설정. 성공 조건·허용 경로는 채점용이며 모델 요청에 넣으면 안 된다 (9.3)."""
        return {
            "run": run,
            "runId": run["runId"],
            "runDir": self.store.run_dir(run["runId"]),
            "shopBaseUrl": base_url,
            "startUrl": start_url,
            "startPath": run["startPath"],
            "goalId": run["goalId"],
            "goal": run["goal"],
            "persona": run["persona"],
            "successCondition": success_condition(goal),
            "referencePathConfig": run["referencePathConfig"],
            "limits": run["limits"],
            "viewport": run["viewport"],
            "goalJudgmentMode": run["goalJudgmentMode"],
            "temperature": run["temperature"],
            "promptVersion": run["promptVersion"],
            "resultOrigin": run["resultOrigin"],
        }

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
        """서버가 꺼지며 READY·RUNNING에 멈춘 Run을 무효 처리한다. 그대로 두면 같은 키로 다시 실행할 수 없다."""
        fixed = []
        for run in self.store.list_runs():
            if run.get("status") in ("READY", "RUNNING") and run["runId"] != self._active:
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
