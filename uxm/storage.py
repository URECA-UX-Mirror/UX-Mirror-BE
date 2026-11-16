"""실행 기록 저장 (2번 소유). 계약 v1.3.5 18장 저장 계약.

results/runs/<runId>/
├ run.json        Run 1개. 임시 파일에 쓴 뒤 원자적으로 교체
├ steps.jsonl     Step 1개 = 1줄. 단계마다 추가 (실행 전에 먼저 저장)
├ events.jsonl    Event 1개 = 1줄
├ raw/            원본 모델 요청·응답 (디버깅용, Frontend에 내보내지 않음)
└ screenshots/    1번 browser.capture가 저장

- 줄바꿈까지 끝난 줄만 완전한 레코드로 본다. 쓰다가 끊긴 마지막 줄은 읽기에서 뺀다.
- 브라우저 런타임 전용 값(domIndex 등)과 인증 정보는 저장 직전에 지운다 (계약 19장).
- 디스크 오류는 StorageError로 올린다. 루프는 mark_storage_failed로 Run을 STORAGE_FAILED 처리한다.
"""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

SCHEMA_VERSION = "1.3.5"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(ROOT, "results", "runs")

# 저장하지 않는 key. 어느 깊이에 있어도 지운다.
RUNTIME_ONLY_KEYS = {"domIndex", "locatorKey", "elementHandle"}      # 브라우저 런타임 전용
SECRET_KEYS = {"authorization", "apiKey", "api_key", "accessToken", "token"}   # 인증 정보
DROP_KEYS = RUNTIME_ONLY_KEYS | SECRET_KEYS

_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")   # api/store.py SAFE_ID와 같은 규칙 (PR #5)
_KST = timezone(timedelta(hours=9))


class StorageError(RuntimeError):
    """저장 실패. 루프는 terminationReason=STORAGE_FAILED로 종료한다."""


def now_iso() -> str:
    return datetime.now(_KST).isoformat(timespec="milliseconds")


def scrub(obj):
    """DROP_KEYS를 재귀로 지운 사본. 원본은 그대로 둔다 (browser.click은 메모리의 후보를 계속 쓴다)."""
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if k not in DROP_KEYS}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj


def _dumps(obj, indent=None) -> str:
    return json.dumps(scrub(obj), ensure_ascii=False, indent=indent, allow_nan=False)


def _write_atomic(path: str, text: str) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    # Windows는 다른 쪽(API)이 파일을 읽는 순간 교체가 실패할 수 있어 잠깐 다시 시도한다
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)


def _read_jsonl(path: str) -> list[dict]:
    """줄바꿈까지 끝난 줄만 돌려준다. 파일이 없으면 빈 목록."""
    try:
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
    except FileNotFoundError:
        return []
    lines = text.split("\n")[:-1]   # 마지막 조각은 줄바꿈 전이면 쓰는 중, 끝났으면 빈 문자열
    return [json.loads(line) for line in lines if line.strip()]


class RunStore:
    """Run 하나의 저장소. 한 Run은 한 스레드에서만 쓴다 (1·2번 협의)."""

    def __init__(self, run_id: str, runs_dir: str = RUNS_DIR):
        if not _RUN_ID.match(run_id):
            raise ValueError(f"runId는 영문·숫자·_·- 1~64자: {run_id!r}")
        self.run_id = run_id
        self.dir = os.path.join(runs_dir, run_id)

    @classmethod
    def open_dir(cls, run_dir: str) -> "RunStore":
        """3번 API가 이미 만든 Run 폴더(run.json 있음)를 루프가 이어 쓸 때. settings["runDir"]를 넘긴다 (PR #5)."""
        run_dir = os.path.normpath(run_dir)
        store = cls(os.path.basename(run_dir), runs_dir=os.path.dirname(run_dir))
        try:
            os.makedirs(store.screenshots_dir, exist_ok=True)
            os.makedirs(store.path("raw"), exist_ok=True)
        except OSError as e:
            raise StorageError(f"Run 폴더를 준비하지 못함: {e}") from e
        return store

    # ------------------------------------------------------------ 경로

    def path(self, *parts: str) -> str:
        return os.path.join(self.dir, *parts)

    @property
    def screenshots_dir(self) -> str:
        return self.path("screenshots")

    def exists(self) -> bool:
        return os.path.exists(self.path("run.json"))

    # ------------------------------------------------------------ 쓰기

    def create(self, run: dict) -> None:
        """폴더를 만들고 첫 run.json을 쓴다. 같은 runId가 이미 있으면 덮어쓰지 않는다."""
        if run.get("runId") != self.run_id:
            raise ValueError(f"run.runId {run.get('runId')!r} != {self.run_id!r}")
        if self.exists():
            raise StorageError(f"이미 있는 Run: {self.run_id}")
        try:
            os.makedirs(self.screenshots_dir, exist_ok=True)
            os.makedirs(self.path("raw"), exist_ok=True)
        except OSError as e:
            raise StorageError(f"Run 폴더를 만들지 못함: {e}") from e
        self.write_run(run)

    def write_run(self, run: dict) -> None:
        try:
            _write_atomic(self.path("run.json"), _dumps(run, indent=2) + "\n")
        except (OSError, ValueError) as e:
            raise StorageError(f"run.json 저장 실패: {e}") from e

    def append_step(self, step: dict) -> None:
        self._append("steps.jsonl", step)

    def append_event(self, event: dict) -> None:
        self._append("events.jsonl", event)

    def _append(self, name: str, record: dict) -> None:
        try:
            line = _dumps(record) + "\n"   # 직렬화를 먼저 끝내서 실패해도 반쪽 줄이 남지 않게
            with open(self.path(name), "a", encoding="utf-8", newline="\n") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        except (OSError, ValueError) as e:
            raise StorageError(f"{name} 저장 실패: {e}") from e

    def save_raw(self, step_id: int, kind: str, payload: dict) -> str:
        """원본 모델 요청(kind='request')·응답(kind='response'). 실행 폴더 기준 상대 경로를 돌려준다."""
        if kind not in ("request", "response"):
            raise ValueError(f"kind는 request 또는 response: {kind!r}")
        ref = f"raw/model_{kind}_step_{step_id:03d}.json"
        try:
            _write_atomic(self.path(*ref.split("/")), _dumps(payload, indent=2) + "\n")
        except (OSError, ValueError) as e:
            raise StorageError(f"{ref} 저장 실패: {e}") from e
        return ref

    # ------------------------------------------------------------ 읽기 (3번 API, 4번 분석)

    def read_run(self) -> dict:
        with open(self.path("run.json"), encoding="utf-8") as f:
            return json.load(f)

    def read_steps(self) -> list[dict]:
        return _read_jsonl(self.path("steps.jsonl"))

    def read_events(self) -> list[dict]:
        return _read_jsonl(self.path("events.jsonl"))


def list_run_ids(runs_dir: str = RUNS_DIR) -> list[str]:
    if not os.path.isdir(runs_dir):
        return []
    return sorted(d for d in os.listdir(runs_dir) if os.path.isfile(os.path.join(runs_dir, d, "run.json")))


def mark_storage_failed(run: dict, exc: Exception) -> dict:
    """저장 실패로 Run을 끝낸다 (계약 6.1 유효성 표). run.json 쓰기도 실패할 수 있어 쓰기는 호출한 쪽이 시도한다."""
    t = now_iso()
    run.update({
        "status": "ERROR",
        "terminationReason": "STORAGE_FAILED",
        "finishedAt": t,
        "error": {"code": "STORAGE_ERROR", "message": "실행 기록을 저장하지 못했습니다.",
                  "retryable": False, "details": {"exception": repr(exc)}, "occurredAt": t},
        "validity": "INVALID",
        "invalidReason": "STORAGE_FAILURE",
    })
    return run
