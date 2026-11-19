"""자동 개선 실행·리포트 API (#130).

- `POST /api/improvements`: 등록된 동결 계획으로 자동 개선 1회를 시작한다. 클라이언트는 아무 값도 넘기지 않는다
  (목표·사용자 유형·채택 규칙은 서버의 동결 계획 그대로). 실행은 별도 프로세스(`scripts/improve_live.py`)가 하고,
  처음부터 끝까지 Run 잠금(#122)을 잡으므로 그동안 다른 Run·Optimization·배포는 막힌다.
- `GET /api/improvements/{id}`: 상태·단계·오류 + 리포트(`results/improvements/<id>/improvement.json`, FE 형식 그대로).
- `GET /api/improvements`: 목록. 서버에서 시작한 실행과, 결과 폴더에 이미 있는 결과(예: imp-1119b)를 같이 보여 준다.
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
from api.run_lock import RunLock
from api.store import is_safe_id
from api.timeutil import now_iso, now_stamp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVER = [sys.executable, os.path.join(ROOT, "scripts", "improve_live.py")]
JOBS_DIR = "improvement_jobs"
ACTIVE = ("QUEUED", "RUNNING")
START_TIMEOUT_S = 20   # 실행 프로세스가 Run 잠금을 잡을 때까지 기다리는 시간
PUBLIC_STATUS_FIELDS = ("improvementId", "status", "phase", "phases", "createdAt", "startedAt", "finishedAt", "error")


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

    def _active_id(self) -> str | None:
        with self._lock:
            for imp_id, proc in list(self._procs.items()):
                if proc.poll() is None:
                    return imp_id
                self._procs.pop(imp_id, None)
        return None

    def start(self) -> dict:
        with self._lock, self.runner.creation_lock:
            if any(p.poll() is None for p in self._procs.values()):
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
        if status is None:   # 서버 밖에서 돌려 결과만 넣은 것 (예: imp-1119b)
            status = {"improvementId": imp_id, "status": "COMPLETED" if result.get("status") == "COMPLETED" else
                      result.get("status"), "phase": "DONE", "phases": [], "createdAt": result.get("createdAt"),
                      "startedAt": result.get("createdAt"), "finishedAt": result.get("finishedAt"), "error": None}
        elif status.get("status") in ACTIVE and not self._alive(imp_id, status):
            status.update(status="INTERRUPTED", finishedAt=status.get("finishedAt") or now_iso(),
                          error={"code": "IMPROVEMENT_INTERRUPTED", "message": "실행 프로세스가 끝까지 가지 못하고 멈췄습니다."})
            _write(os.path.join(self._job_dir(imp_id), "status.json"), status)
        out = {k: status.get(k) for k in PUBLIC_STATUS_FIELDS}
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
