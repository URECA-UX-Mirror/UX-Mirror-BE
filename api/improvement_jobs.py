"""자동 개선 실행·리포트 API (#130).

- `POST /api/improvements`: 등록된 동결 계획으로 자동 개선 1회를 시작한다. 클라이언트는 아무 값도 넘기지 않는다
  (목표·사용자 유형·채택 규칙은 서버의 동결 계획 그대로). 실행은 별도 프로세스(`scripts/improve_live.py`)가 하고,
  처음부터 끝까지 Run 잠금(#122)을 잡으므로 그동안 다른 Run·Optimization·배포는 막힌다.
- `GET /api/improvements/{id}`: 상태·단계·오류 + 리포트(`results/improvements/<id>/improvement.json`, FE 형식 그대로).
- `GET /api/improvements`: 목록. 서버에서 시작한 실행과, 결과 폴더에 이미 있는 결과(예: imp-1119b)를 같이 보여 준다.
- `kind`: `LIVE`(이 서버가 시작한 실행) / `OFFICIAL`(동결된 공식 계획 `results/improvement_plan*.json`의 improvementId와
  같은 결과) / `UNKNOWN`(그 밖에 결과 파일만 있는 것: 연습·별도 스크립트 실행 등 출처를 확인할 수 없음).
- 응답에는 pid·로그 경로·사이트 주소를 넣지 않는다.
"""
import json
import os
import secrets
import subprocess
import sys
import threading
import time

from api.errors import ApiError
from api import run_factory
from api.input_rules import sentence_problem
from api.run_lock import RunLock
from api.store import is_safe_id
from api.timeutil import now_iso, now_stamp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PLAN = os.path.join(ROOT, "results", "improvement_plan_1119b.json")
PLANS_DIR = os.path.join(ROOT, "results")   # 사전 등록(동결)된 공식 계획 `improvement_plan*.json`이 있는 곳
DRIVER = [sys.executable, os.path.join(ROOT, "scripts", "improve_live.py")]
JOBS_DIR = "improvement_jobs"
ACTIVE = ("QUEUED", "RUNNING")
START_TIMEOUT_S = 20   # 실행 프로세스가 Run 잠금을 잡을 때까지 기다리는 시간
PUBLIC_STATUS_FIELDS = ("improvementId", "status", "phase", "phases", "createdAt", "startedAt", "finishedAt", "error")
ENABLE_ENV = "UXM_IMPROVEMENTS_ENABLED"   # "0"이면 새 AI 개선 시작을 막는다 (심사 기간 뒤 끄기용). 조회는 그대로


class ImprovementJobs:
    def __init__(self, results_dir: str, runner, optimizer=None, driver: list[str] | None = None):
        self.results_dir = results_dir
        self.runner, self.optimizer = runner, optimizer
        self.driver = driver or DRIVER
        self.run_lock = RunLock.for_results(results_dir)
        self._lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen] = {}

    # ---- 경로
    def _job_dir(self, imp_id: str) -> str:
        return os.path.join(self.results_dir, JOBS_DIR, imp_id)

    def _result_path(self, imp_id: str) -> str:
        return os.path.join(self.results_dir, "improvements", imp_id, "improvement.json")

    # ---- 시작
    def busy_reason(self, run: dict | None = None) -> str | None:
        """시작 직후 실행 프로세스가 Run 잠금을 잡기 전까지 다른 Run이 끼어들지 못하게 (Runner.external_busy에 연결)."""
        if self._active_id():
            return "자동 개선이 진행 중입니다."
        return None

    def _running(self, imp_id: str, proc) -> bool:
        """진행 중: 프로세스가 살아 있고 상태가 아직 끝나지 않음 (끝남을 쓴 뒤 프로세스가 닫히는 잠깐은 진행 중이 아님)."""
        if proc.poll() is not None:
            return False
        status = _read(os.path.join(self._job_dir(imp_id), "status.json")) or {}
        return status.get("status", "QUEUED") in ACTIVE

    def _active_id(self) -> str | None:
        with self._lock:
            for imp_id, proc in list(self._procs.items()):
                if self._running(imp_id, proc):
                    return imp_id
                if proc.poll() is not None:
                    self._procs.pop(imp_id, None)
        return None

    def goal_pages(self) -> dict:
        """도착 화면으로 고를 수 있는 화면: 동결 계획의 기본 목표 4개 (pageKey → {name, path})."""
        plan = _read(TEMPLATE_PLAN) or {}
        pool = [plan.get("mainGoal") or {}, *(plan.get("guardGoals") or [])]
        return {g["goalPage"]: {"name": g.get("name"), "path": g["verification"]["path"]}
                for g in pool if g.get("goalPage") and g.get("verification")}

    def resolve_input(self, body: dict | None) -> dict | None:
        """요청 → 실행 입력 {goal, persona, goalPage[, verification, sourceRunId]} (없으면 None = 동결 계획 그대로).

        `sourceRunId`: 그 진단 Run의 고객이 하려는 일·고객 유형·도착 화면·성공 조건을 그대로 쓴다
        (진단 결과의 "AI에게 고쳐 달라고 하기"). 끝난 유효한 MORO MARKET before 진단만 받는다.
        직접 입력: goal·persona·goalPage 셋 다. 문장은 진단과 같은 규칙으로 검사한다 (`api/input_rules`).
        """
        body = body or {}
        pages = self.goal_pages()
        if body.get("sourceRunId"):
            if any(body.get(k) for k in ("goal", "persona", "goalPage")):
                raise _invalid("sourceRunId와 직접 입력(goal·persona·goalPage)은 같이 보낼 수 없습니다.")
            run_id = body["sourceRunId"]
            if not isinstance(run_id, str) or not self.runner.store.exists(run_id):
                raise ApiError(404, "RUN_NOT_FOUND", "진단 실행을 찾을 수 없습니다.")
            run = self.runner.store.load_run(run_id)
            source_problem(run)
            verification = source_verification(run)
            page = next((k for k, v in pages.items() if v["path"] == verification["path"]), None)
            if page is None:
                raise _invalid("이 진단의 도착 화면으로는 AI 개선을 할 수 없습니다.")
            # 진단의 성공 조건(확인 글자·정확 일치·범위)을 그대로 넘긴다 → 개선 검증도 같은 조건으로 판정 (#131 리뷰)
            user_input = {"goal": run.get("goal"), "persona": run.get("persona"), "goalPage": page,
                          "verification": verification, "sourceRunId": run_id}
        elif any(body.get(k) for k in ("goal", "persona", "goalPage")):
            user_input = {"goal": body.get("goal"), "persona": body.get("persona"), "goalPage": body.get("goalPage")}
            if user_input["goalPage"] not in pages:
                raise _invalid(f"도착 화면은 {', '.join(pages)} 중 하나여야 합니다.")
        else:
            return None
        for key, field in (("goal", "고객이 하려는 일"), ("persona", "고객 유형")):
            text = user_input[key]
            problem = sentence_problem(text, field)
            if problem:
                raise _invalid(problem)
            user_input[key] = text.strip()
        return user_input

    def start(self, body: dict | None = None) -> dict:
        if os.getenv(ENABLE_ENV, "1").strip() == "0":
            raise ApiError(503, "IMPROVEMENTS_DISABLED", "지금은 AI 개선을 새로 시작할 수 없어요. 지난 결과는 볼 수 있어요.")
        user_input = self.resolve_input(body)
        with self._lock, self.runner.creation_lock:
            if any(self._running(i, p) for i, p in self._procs.items()):
                raise ApiError(409, "RUN_ALREADY_ACTIVE", "자동 개선이 이미 진행 중입니다.", retryable=True)
            if self.runner.active_run_id is not None or self.run_lock.holder() is not None:
                raise ApiError(409, "RUN_ALREADY_ACTIVE", "다른 실행이 진행 중입니다.", retryable=True)
            busy = self.optimizer.busy_reason() if self.optimizer else None
            if busy:
                raise ApiError(409, "RUN_ALREADY_ACTIVE", busy, retryable=True)
            imp_id = f"imp-live-{now_stamp()}-{secrets.token_hex(2)}"
            folder = self._job_dir(imp_id)
            os.makedirs(folder)
            status = {"improvementId": imp_id, "status": "QUEUED", "phase": "QUEUED", "phases": [],
                      "createdAt": now_iso(), "startedAt": None, "finishedAt": None, "error": None}
            _write(os.path.join(folder, "status.json"), status)
            if user_input:
                _write(os.path.join(folder, "input.json"), user_input)
            env = {**os.environ, "UXM_RESULTS_DIR": self.results_dir}
            with open(os.path.join(folder, "log.txt"), "w", encoding="utf-8") as log:
                proc = subprocess.Popen(
                    [*self.driver, "--improvement-id", imp_id, "--results-dir", self.results_dir],
                    cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            self._procs[imp_id] = proc
            # 실행 프로세스가 Run 잠금을 잡을 때까지 생성 잠금을 쥐고 기다린다 → 그 사이 다른 Run·Optimization이 끼어들지 못함
            end = time.monotonic() + START_TIMEOUT_S
            while time.monotonic() < end and proc.poll() is None:
                if (self.run_lock.holder() or {}).get("improvementId") == imp_id:
                    break
                time.sleep(0.1)
            else:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(5)
        return self.view(imp_id)

    # ---- 조회
    def view(self, imp_id: str) -> dict:
        if not is_safe_id(imp_id):
            raise ApiError(404, "IMPROVEMENT_NOT_FOUND", "자동 개선 결과를 찾을 수 없습니다.")
        status = _read(os.path.join(self._job_dir(imp_id), "status.json"))
        result = _read(self._result_path(imp_id))
        if status is None and result is None:
            raise ApiError(404, "IMPROVEMENT_NOT_FOUND", "자동 개선 결과를 찾을 수 없습니다.")
        kind = "LIVE" if status is not None else "OFFICIAL" if imp_id in official_ids() else "UNKNOWN"   # (#131 리뷰)
        if status is None:   # 서버 밖에서 돌려 결과만 넣은 것 (예: imp-1119b)
            status = {"improvementId": imp_id, "status": "COMPLETED" if result.get("status") == "COMPLETED" else
                      result.get("status"), "phase": "DONE", "phases": [], "createdAt": result.get("createdAt"),
                      "startedAt": result.get("createdAt"), "finishedAt": result.get("finishedAt"), "error": None}
        elif status.get("status") in ACTIVE and not self._alive(imp_id, status):
            status.update(status="INTERRUPTED", finishedAt=status.get("finishedAt") or now_iso(),
                          error={"code": "IMPROVEMENT_INTERRUPTED", "message": "실행 프로세스가 끝까지 가지 못하고 멈췄습니다."})
            _write(os.path.join(self._job_dir(imp_id), "status.json"), status)
        out = {k: status.get(k) for k in PUBLIC_STATUS_FIELDS}
        out["kind"] = kind
        out["input"] = _read(os.path.join(self._job_dir(imp_id), "input.json"))   # 어떤 입력으로 돌렸는지 (없으면 기본 계획)
        # 루프가 끝나면 리포트가 생긴다. 공식 점수·검증 중(RUNNING)에는 루프 결과까지만 들어 있다
        out["improvement"] = result
        return out

    def _alive(self, imp_id: str, status: dict) -> bool:
        """실행 중인지: 이 서버가 띄운 프로세스가 살아 있거나, Run 잠금을 이 실행이 잡고 있으면."""
        proc = self._procs.get(imp_id)
        if proc is not None and proc.poll() is None:
            return True
        holder = self.run_lock.holder() or {}
        return holder.get("improvementId") == imp_id

    def list(self) -> list[dict]:
        ids = set()
        for sub in (JOBS_DIR, "improvements"):
            base = os.path.join(self.results_dir, sub)
            if os.path.isdir(base):
                ids |= {n for n in os.listdir(base) if is_safe_id(n) and os.path.isdir(os.path.join(base, n))}
        items = []
        for imp_id in ids:
            try:
                v = self.view(imp_id)
            except ApiError:
                continue
            imp = v.pop("improvement") or {}
            s = imp.get("summary") or {}
            v["summary"] = {k: s.get(k) for k in ("result", "beforeLabel", "afterLabel", "main")} if s else None
            items.append(v)
        return sorted(items, key=lambda v: v.get("createdAt") or "", reverse=True)


def official_ids() -> set[str]:
    """공식 결과 id: 동결(FROZEN)된 사전 등록 계획 `results/improvement_plan*.json`의 improvementId."""
    ids = set()
    try:
        names = os.listdir(PLANS_DIR)
    except OSError:
        return ids
    for name in names:
        if name.startswith("improvement_plan") and name.endswith(".json"):
            plan = _read(os.path.join(PLANS_DIR, name)) or {}
            if plan.get("status") == "FROZEN" and isinstance(plan.get("improvementId"), str):
                ids.add(plan["improvementId"])
    return ids


def source_problem(run: dict) -> None:
    """AI 개선의 원본으로 쓸 수 있는 진단인지. 끝난 유효한 MORO MARKET 지금 화면(before) 진단만 (#131 리뷰).

    목표에 못 간 진단(BLOCKED·MAX_STEPS 등)도 유효하면 받는다 (고칠 대상이 바로 그런 진단).
    """
    if run.get("siteId"):
        raise _invalid("AI 개선은 MORO MARKET 진단에서만 할 수 있어요 (다른 사이트는 메뉴를 바꾼 화면을 만들 수 없습니다).")
    if run.get("optimizationContext") or run.get("improvementContext"):
        raise _invalid("후보 비교·AI 개선이 만든 확인용 진단으로는 AI 개선을 시작할 수 없어요. 직접 한 진단에서 시작해 주세요.")
    if run.get("variant") != "before":
        raise _invalid("지금 화면(개선 전) 진단에서만 AI 개선을 시작할 수 있어요.")
    if run.get("status") not in run_factory.TERMINAL_STATUSES:
        raise _invalid("진단이 아직 끝나지 않았어요. 끝난 뒤에 AI 개선을 시작해 주세요.")
    if run.get("validity") != "VALID":
        raise _invalid("결과로 쓸 수 없는 진단이에요. 진단을 다시 한 뒤에 AI 개선을 시작해 주세요.")


def source_verification(run: dict) -> dict:
    """원본 진단의 성공 조건 (빠진 값은 진단과 같은 기본값: 부분 일치·화면 전체)."""
    v = run.get("verification") or {}
    if not isinstance(v.get("path"), str) or not isinstance(v.get("text"), str) or not v["text"].strip():
        raise _invalid("이 진단에는 성공 조건이 없어 AI 개선을 시작할 수 없어요.")
    return {"type": "PATH_AND_VISIBLE_TEXT", "path": v["path"], "text": v["text"],
            "exact": v.get("exact") is True, "scope": v.get("scope") if v.get("scope") in ("page", "main") else "page"}


def _invalid(message: str) -> ApiError:
    return ApiError(422, "CONTRACT_VALIDATION_ERROR", message)


def _read(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
