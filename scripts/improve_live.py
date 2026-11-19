"""자동 개선 1회를 처음부터 끝까지 (API `POST /api/improvements`가 별도 프로세스로 실행, #130).

    python scripts/improve_live.py --improvement-id imp-live-20261120031500-ab12

순서 (`scripts/improve.py`의 loop·finalize를 그대로 쓴다)
  1. Run 잠금(#122)을 잡는다 — 끝날 때까지 놓지 않아 그동안 다른 Run·Optimization·배포가 멈춘다
  2. 기준 계획(동결본)을 복사해 이번 실행 id로 바꾼 계획을 만든다 (목표·점수·채택 규칙·모델 조건은 그대로)
  3. 루프: v1에서 진단 → 이름 제안 → 글자 바꾸기(A)·화면 검사 → 점수 → 채택
  4. 채택 이름이 있으면 그 이름으로 최종안 사이트(B)를 만들어 이 서버 안(127.0.0.1, 빈 포트)에서 띄운다
  5. B 공식 점수 + 실제 Run 검증 → 리포트 갱신. B 사이트를 내린다
  상태는 `results/improvement_jobs/<id>/status.json`에 단계마다 적는다 (API가 읽는다).
  결과·상태에 모델 서버 주소·사이트 주소·포트는 남기지 않는다.
"""
import argparse
import copy
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from api.run_lock import RunLock  # noqa: E402
from api.timeutil import now_iso  # noqa: E402

TEMPLATE_PLAN = os.path.join(ROOT, "results", "improvement_plan_1119b.json")
JOBS_DIR = "improvement_jobs"
SITE_START_TIMEOUT_S = 20
_URL_RE = re.compile(r"https?://\S+|127\.0\.0\.1:\d+|localhost:\d+|(?:/home|/Users|/private|/tmp|/var)/\S+|[A-Za-z]:\\\S+")


def job_dir(results_dir: str, improvement_id: str) -> str:
    return os.path.join(results_dir, JOBS_DIR, improvement_id)


def write_status(folder: str, status: dict) -> None:
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, "status.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    os.replace(tmp, os.path.join(folder, "status.json"))


def read_status(folder: str) -> dict | None:
    try:
        with open(os.path.join(folder, "status.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def scrub(text: str) -> str:
    """오류 문구에서 주소·포트를 지운다 (모델 서버·최종안 사이트 주소가 응답에 나가지 않게)."""
    return _URL_RE.sub("<주소>", str(text))[:300]


MAIN_KEY = "MAIN"
GUARD_VERIFY_KEY = "S4"   # 실제로 다시 확인하는 다른 고객 목표 (문의). 입력 화면이 문의면 남은 첫 번째


def goal_pool(template: dict) -> list[dict]:
    """동결 계획의 기본 고객 목표 4개 (S1 환불·S2 배송·S3 쿠폰·S4 문의)."""
    return [template["mainGoal"], *template["guardGoals"]]


def goal_pages(template: dict) -> dict:
    """도착 화면 pageKey → 그 화면의 기본 목표 (확인 글자·이름을 여기서 가져온다)."""
    return {g["goalPage"]: g for g in goal_pool(template)}


def make_plan(template: dict, improvement_id: str, user_input: dict | None = None) -> dict:
    """동결 계획을 이번 실행 id로 바꾼 복사본. 입력이 있으면 주 목표·고객 유형만 그 값으로 바꾼다.

    user_input = {goal, persona, goalPage[, verification]}. 주 목표 = 입력 문장 + 성공 조건(원본 진단 것, 없으면 그 화면 기본), 지킬 목표 = 도착 화면이 다른
    기본 목표 3개. 점수 기준·채택 규칙·회차 한도·모델 조건은 그대로 둔다 (측정 기준).
    """
    plan = copy.deepcopy(template)
    if plan.get("status") != "FROZEN":
        raise ValueError("기준 계획이 동결(FROZEN)되지 않았습니다.")
    based_on = plan.get("improvementId")
    plan["improvementId"] = improvement_id
    plan["improvementPlanId"] = f"{plan.get('improvementPlanId') or based_on}-live"   # 공식 계획과 구분 (#131 리뷰)
    plan.pop("secondRun", None)   # "2차 1회" 같은 공식 실행 기록은 복사본에 남기지 않는다
    frozen = plan.get("verification", {}).get("frozen") if isinstance(plan.get("verification"), dict) else None
    if isinstance(frozen, dict):
        frozen["improvementId"] = improvement_id
    if user_input:
        pages = goal_pages(template)
        base = pages.get(user_input["goalPage"])
        if base is None:
            raise ValueError(f"도착 화면은 {', '.join(pages)} 중 하나여야 합니다.")
        # 원본 진단의 성공 조건이 있으면 그대로 (같은 조건으로 개선을 판정), 없으면 그 화면의 기본 조건 (#131 리뷰)
        verification = copy.deepcopy(user_input.get("verification") or base["verification"])
        if verification.get("path") != base["verification"]["path"]:
            raise ValueError("성공 조건의 경로가 도착 화면과 다릅니다.")
        plan["mainGoal"] = {"goalKey": MAIN_KEY, "goal": user_input["goal"], "goalPage": base["goalPage"],
                            "name": base["name"], "verification": verification}
        plan["guardGoals"] = [copy.deepcopy(g) for g in goal_pool(template) if g["goalPage"] != base["goalPage"]]
        plan["persona"] = user_input["persona"]
        guard_keys = [g["goalKey"] for g in plan["guardGoals"]]
        check = GUARD_VERIFY_KEY if GUARD_VERIFY_KEY in guard_keys else guard_keys[0]
        plan["verification"]["goals"] = [MAIN_KEY, check]
        plan["verification"]["optionalGoals"] = [k for k in guard_keys if k != check]
        if isinstance(frozen, dict):
            frozen["persona"], frozen["personaId"] = user_input["persona"], None
    plan["live"] = {"basedOnPlan": based_on, "startedBy": "API", "startedAt": now_iso(), "input": user_input,
                    "note": "심사·시연용 실행. 점수 기준·채택 규칙·모델 조건은 동결본과 같다."}
    return plan


def load_improve():
    """`scripts/improve.py` (loop·finalize 정본)를 모듈로 불러온다."""
    spec = importlib.util.spec_from_file_location("uxm_improve_script", os.path.join(ROOT, "scripts", "improve.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _die_with_parent():   # pragma: no cover - 자식 프로세스에서 실행
    """실행 프로세스가 죽으면(kill 포함) 최종안 사이트 서버도 같이 끝나게 (Linux). 다른 OS에서는 아무것도 하지 않는다."""
    if sys.platform.startswith("linux"):
        import ctypes
        import signal
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)   # PR_SET_PDEATHSIG


def build_and_serve(label: str, folder: str):
    """채택 이름으로 최종안 사이트(B)를 만들고 빈 포트에서 띄운다 → (주소, 프로세스)."""
    site = os.path.join(folder, "site")
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "build_shop.py"), "--label", label, "--out", site,
                    "--force"], check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
    port = free_port()
    proc = subprocess.Popen([sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(port)], cwd=site,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=_die_with_parent)
    end = time.time() + SITE_START_TIMEOUT_S
    while time.time() < end:
        if proc.poll() is not None:
            raise RuntimeError("최종안 사이트 서버가 시작되지 않았습니다.")
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return f"http://127.0.0.1:{port}", proc
        time.sleep(0.2)
    proc.terminate()
    raise RuntimeError("최종안 사이트 서버가 제한 시간 안에 뜨지 않았습니다.")


def run_job(improvement_id: str, results_dir: str, *, template_path: str = TEMPLATE_PLAN, v1_url: str | None = None,
            improve=None, serve=build_and_serve) -> dict:
    """한 번 실행. 마지막 상태를 돌려준다. improve·serve는 테스트에서 바꿔 끼운다."""
    folder = job_dir(results_dir, improvement_id)
    status = read_status(folder) or {"improvementId": improvement_id, "createdAt": now_iso()}
    status.update(status="RUNNING", phase="STARTING", pid=os.getpid(), startedAt=now_iso(), finishedAt=None,
                  error=None, phases=[])

    def phase(name: str) -> None:
        status["phase"] = name
        status["phases"].append({"phase": name, "at": now_iso()})
        write_status(folder, status)

    lock = RunLock.for_results(results_dir)
    if not lock.acquire({"kind": "IMPROVEMENT", "runId": None, "improvementId": improvement_id}):
        status.update(status="FAILED", phase="STARTING", finishedAt=now_iso(),
                      error={"code": "RUN_ALREADY_ACTIVE", "message": "다른 실행이 진행 중이라 시작하지 못했습니다."})
        write_status(folder, status)
        return status
    server = None
    try:
        write_status(folder, status)
        user_input = None
        input_path = os.path.join(folder, "input.json")
        if os.path.exists(input_path):
            with open(input_path, encoding="utf-8") as f:
                user_input = json.load(f)
        with open(template_path, encoding="utf-8") as f:
            plan = make_plan(json.load(f), improvement_id, user_input)
        plan_path = os.path.join(folder, "plan.json")
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        improve = improve or load_improve()
        common = {"plan": plan_path, "results_dir": results_dir, "allow_draft": False,
                  "v1_url": v1_url or os.getenv("SHOP_BEFORE_URL", "http://127.0.0.1:8001")}

        phase("LOOP")
        improve.cmd_loop(SimpleNamespace(**common, force=False))
        imp_path = os.path.join(results_dir, "improvements", improvement_id, "improvement.json")
        with open(imp_path, encoding="utf-8") as f:
            label = json.load(f)["summary"].get("afterLabel")
        if label:
            phase("BUILD_FINAL_SITE")
            url, server = serve(label, folder)
            phase("OFFICIAL_AND_VERIFY")
            improve.cmd_finalize(SimpleNamespace(**common, final_url=url, skip_verify=False))
        phase("DONE")
        status.update(status="COMPLETED", finishedAt=now_iso())
    except BaseException as e:   # SystemExit(조건 불일치 등)도 실패로 기록
        code = "CONDITION_MISMATCH" if isinstance(e, SystemExit) else type(e).__name__
        status.update(status="FAILED", finishedAt=now_iso(), error={"code": code, "message": scrub(e)})
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(10)
            except subprocess.TimeoutExpired:
                server.kill()
        lock.release()            # 잠금을 먼저 푼다: '끝남'을 본 사용자가 바로 다음 진단을 시작해도 409가 나지 않게
        write_status(folder, status)
    return status


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--improvement-id", required=True)
    ap.add_argument("--results-dir", default=os.getenv("UXM_RESULTS_DIR") or os.path.join(ROOT, "results"))
    ap.add_argument("--template", default=TEMPLATE_PLAN)
    a = ap.parse_args(argv)
    os.environ["UXM_RESULTS_DIR"] = a.results_dir   # 검증 Run도 같은 결과 폴더 (improve.verify가 이 값을 쓴다)
    status = run_job(a.improvement_id, a.results_dir, template_path=a.template)
    return 0 if status["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())
