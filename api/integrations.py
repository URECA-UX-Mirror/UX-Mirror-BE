"""다른 담당 모듈과의 연결 지점. API는 이 파일을 통해서만 부른다.

| 모듈 | 담당 | 쓰는 함수 |
|---|---|---|
| `uxm/agent.py` | C 루프 | `run_agent(settings, backend, page) -> dict` |
| `uxm/browser.py` | 1번 (#2) | `open_browser(start_url) -> session`, `close_browser(session)` |
| `uxm/analysis.py` | 4번 | `detect_issues(run, steps, events) -> list[Issue]`, `compare(before, after) -> ComparisonResult` |
| `uxm/backends.py` | 기존 | `make_backend(cfg)` |
| `uxm/optimizer_policy.py` | 4번 | `compute_evaluation_metrics(evaluation)`, `select_candidate(evaluations)`, `judge_verification(baseline_run, candidate_run)` |

모듈이 아직 없으면 `None`을 돌려주고, API는 계약에 맞는 응답(503, PENDING, 500)으로 처리한다.
함수 모양이 실제와 다르면 이 파일만 고친다.
"""
import importlib
import threading


def _load(module: str, *names: str):
    try:
        mod = importlib.import_module(module)
    except ImportError:
        return None
    if not all(hasattr(mod, n) and not getattr(getattr(mod, n), "__uxm_pending__", False) for n in names):
        return None
    return tuple(getattr(mod, n) for n in names)


class Integrations:
    def __init__(self):
        self._backend = None
        self._backend_key = None
        self._backend_lock = threading.Lock()

    def agent(self):
        """`run_agent` 또는 None."""
        found = _load("uxm.agent", "run_agent")
        return found[0] if found else None

    def browser(self):
        """`(open_browser, close_browser)` 또는 None."""
        return _load("uxm.browser", "open_browser", "close_browser")

    def goal_preflight(self):
        """직접 입력 성공 조건의 실행 전 검사 `check(verification, base_urls, scope=None)` (api/preflight.py).
        scope가 있으면 외부 사이트 시나리오 1곳만 확인한다 (#74). 브라우저가 없으면 None."""
        browser = self.browser()
        if browser is None:
            return None
        from api.preflight import check_goal_screens
        return lambda verification, base_urls, scope=None: check_goal_screens(verification, base_urls, browser, scope=scope)

    def detect_issues(self):
        found = _load("uxm.analysis", "detect_issues")
        return found[0] if found else None

    def compare(self):
        found = _load("uxm.analysis", "compare")
        return found[0] if found else None

    def optimizer_policy(self):
        """UX Optimizer 정책 함수 `(compute_evaluation_metrics, select_candidate, judge_verification)` 또는 None.
        모델 호출·저장이 없는 순수 함수 (고도화 v1.3 13.5, 4번)."""
        return _load("uxm.optimizer_policy", "compute_evaluation_metrics", "select_candidate", "judge_verification")

    def backend(self, cfg: dict):
        """모델 백엔드. local은 모델 로딩이 무거워 같은 설정이면 재사용한다."""
        key = (cfg["backend"], cfg["base_url"], cfg["checkpoint"], cfg["device"], cfg["temperature"], cfg["timeout_s"])
        with self._backend_lock:
            if self._backend is None or self._backend_key != key:
                from uxm.backends import make_backend
                self._backend = make_backend(cfg)
                self._backend_key = key
            return self._backend
