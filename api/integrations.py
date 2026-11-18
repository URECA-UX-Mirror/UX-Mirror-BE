"""다른 담당 모듈과의 연결 지점. API는 이 파일을 통해서만 부른다.

| 모듈 | 담당 | 쓰는 함수 |
|---|---|---|
| `uxm/agent.py` | C 루프 | `run_agent(settings, backend, page) -> dict` |
| `uxm/browser.py` | 1번 (#2) | `open_browser(start_url) -> session`, `close_browser(session)` |
| `uxm/analysis.py` | 4번 | `detect_issues(run, steps, events) -> list[Issue]`, `compare(before, after) -> ComparisonResult` |
| `uxm/backends.py` | 기존 | `make_backend(cfg)` |
| `api/mock_model.py` | 3번 | `ZORO_BACKEND=mock`일 때 가짜 모델 (`resultOrigin=MOCK`) |

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

    def detect_issues(self):
        found = _load("uxm.analysis", "detect_issues")
        return found[0] if found else None

    def compare(self):
        found = _load("uxm.analysis", "compare")
        return found[0] if found else None

    def backend(self, cfg: dict):
        """모델 백엔드. local은 모델 로딩이 무거워 같은 설정이면 재사용한다."""
        key = (cfg["backend"], cfg["base_url"], cfg["checkpoint"], cfg["device"], cfg["temperature"], cfg["timeout_s"])
        with self._backend_lock:
            if self._backend is None or self._backend_key != key:
                if cfg["backend"] == "mock":
                    from api.mock_model import HeuristicMockBackend
                    self._backend = HeuristicMockBackend()
                else:
                    from uxm.backends import make_backend
                    self._backend = make_backend(cfg)
                self._backend_key = key
            return self._backend
