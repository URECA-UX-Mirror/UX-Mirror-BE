"""`results/runs/<runId>/` 파일 읽기·쓰기 (계약 18장).

API가 쓰는 파일은 `run.json`(생성·상태·종료)과 `issues.json`(분석 결과)뿐이다.
`steps.jsonl`, `events.jsonl`, 스크린샷은 에이전트 루프(`uxm/storage.py`, #3)가 쓰고 여기서는 읽기만 한다.
"""
import json
import os
import re
import tempfile

from uxm.storage import read_jsonl   # 줄바꿈까지 끝난 줄만 읽는 규칙(18.2)은 저장 모듈과 같은 함수를 쓴다

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RESULTS_DIR = os.path.join(ROOT, "results")

# 경로 조작 방지: 파일 이름으로 쓰는 ID는 이 형식만 허용
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def is_safe_id(value: str) -> bool:
    return bool(SAFE_ID.match(value or ""))


def write_json_atomic(path: str, data) -> None:
    """임시 파일에 쓴 뒤 교체한다. 읽는 쪽이 반쯤 쓴 파일을 보지 않게 (18.2)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def read_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class RunStore:
    def __init__(self, results_dir: str = DEFAULT_RESULTS_DIR):
        self.results_dir = results_dir
        self.runs_dir = os.path.join(results_dir, "runs")
        self.comparisons_dir = os.path.join(results_dir, "comparisons")

    # ---- 경로
    def run_dir(self, run_id: str) -> str:
        return os.path.join(self.runs_dir, run_id)

    def exists(self, run_id: str) -> bool:
        return is_safe_id(run_id) and os.path.isfile(os.path.join(self.run_dir(run_id), "run.json"))

    # ---- Run
    def load_run(self, run_id: str) -> dict:
        return read_json(os.path.join(self.run_dir(run_id), "run.json"))

    def save_run(self, run: dict) -> None:
        run_dir = self.run_dir(run["runId"])
        os.makedirs(os.path.join(run_dir, "screenshots"), exist_ok=True)
        os.makedirs(os.path.join(run_dir, "raw"), exist_ok=True)
        write_json_atomic(os.path.join(run_dir, "run.json"), run)

    def list_runs(self) -> list[dict]:
        if not os.path.isdir(self.runs_dir):
            return []
        runs = []
        for name in sorted(os.listdir(self.runs_dir)):
            path = os.path.join(self.runs_dir, name, "run.json")
            if is_safe_id(name) and os.path.isfile(path):
                try:
                    runs.append(read_json(path))
                except (OSError, ValueError):
                    continue   # 손상된 Run은 목록·비교에서 제외
        return runs

    # ---- Step·Event
    def load_steps(self, run_id: str) -> list[dict]:
        return read_jsonl(os.path.join(self.run_dir(run_id), "steps.jsonl"))

    def load_events(self, run_id: str) -> list[dict]:
        return read_jsonl(os.path.join(self.run_dir(run_id), "events.jsonl"))

    # ---- Issue
    def load_issues(self, run_id: str) -> dict | None:
        path = os.path.join(self.run_dir(run_id), "issues.json")
        return read_json(path) if os.path.isfile(path) else None

    def save_issues(self, run_id: str, data: dict) -> None:
        write_json_atomic(os.path.join(self.run_dir(run_id), "issues.json"), data)

    # ---- 스크린샷
    def screenshot_path(self, run_id: str, ref: str | None) -> str | None:
        """Step에 적힌 상대 경로를 실행 폴더 안의 실제 파일로. 폴더 밖을 가리키면 None."""
        if not ref:
            return None
        base = os.path.realpath(self.run_dir(run_id))
        path = os.path.realpath(os.path.join(base, ref))
        if os.path.commonpath([base, path]) != base or not os.path.isfile(path):
            return None
        return path

    # ---- 비교
    def save_comparison(self, experiment_key: str, data: dict) -> None:
        write_json_atomic(os.path.join(self.comparisons_dir, f"{experiment_key}.json"), data)
