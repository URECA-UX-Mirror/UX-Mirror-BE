"""C 시나리오 실행기: 실행 API 없이 명령줄에서 에이전트 루프를 돌린다 (#7).

쇼핑몰 서버(Before 8001 / After 8002)가 켜져 있어야 한다. Run은 실행 API와 같은 방식으로 만든다
(api/run_factory.build_run → RunStore.create → run_agent → finalize). 결과: results/runs/<runId>/

    가짜 모델 (C0-MOCK):
      .venv\\Scripts\\python scripts\\run_c.py --variant before --mock "상단 메뉴>/shop/orders#1" "본문>/shop/refund#1"
    실제 모델 (.env 설정, C1·C2):
      .venv\\Scripts\\python scripts\\run_c.py --variant after
    C3 (mypage 시작):
      .venv\\Scripts\\python scripts\\run_c.py --variant before --goal refund-from-mypage-test
    testMode (C4·C6):
      .venv\\Scripts\\python scripts\\run_c.py --variant before --test-mode nochange --mock ...
"""
import argparse
import json
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from api import run_factory  # noqa: E402  (fastapi 없이 import 됨)
from api.presets import Presets  # noqa: E402
from uxm.agent import agent_settings, browser_module, run_agent  # noqa: E402
from uxm.mock import MockBackend  # noqa: E402
from uxm.storage import RunStore  # noqa: E402

# api/runner.py와 같은 값 (runner는 fastapi를 불러와서 여기서 import하지 않는다)
SHOP_URLS = {
    "before": os.getenv("SHOP_BEFORE_URL", "http://127.0.0.1:8001"),
    "after": os.getenv("SHOP_AFTER_URL", "http://127.0.0.1:8002"),
}
AGENT_RESULT_FIELDS = ("status", "terminationReason", "progress", "summary", "error", "finishedAt")

_browser = browser_module()   # 1번 browser.py, 없을 때만 임시 함수
open_browser, close_browser = _browser.open_browser, _browser.close_browser


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="에이전트 루프 실행 (C 시나리오)")
    ap.add_argument("--variant", choices=("before", "after"), required=True)
    ap.add_argument("--goal", default="refund", help="config/presets.json의 goalId")
    ap.add_argument("--persona", default="novice-shopper")
    ap.add_argument("--max-steps", type=int, default=8)
    ap.add_argument("--experiment-key", default=None, help="기본: c_<variant>_<시각>")
    ap.add_argument("--mock", nargs="*", default=None, help="가짜 모델 대본 (semanticKey 또는 GO_BACK)")
    ap.add_argument("--mock-delay", type=float, default=0.0, help="가짜 모델 응답 지연(초). C10")
    ap.add_argument("--model-timeout-ms", type=int, default=None, help="C10처럼 시험할 때만")
    ap.add_argument("--test-mode", choices=("deadend", "nochange"), default=None, help="C4·C6")
    ap.add_argument("--headed", action="store_true", help="브라우저 창 띄우기")
    a = ap.parse_args(argv)

    presets = Presets()
    goal, persona = presets.goal(a.goal), presets.persona(a.persona)
    if goal is None or persona is None:
        print(f"없는 goalId/personaId: {a.goal} / {a.persona}", file=sys.stderr)
        return 2

    model = run_factory.model_settings()
    if a.mock is not None:
        backend = MockBackend(a.mock, delay=a.mock_delay)
        model = {**model, "resultOrigin": "MOCK", "modelVersion": None, "modelEndpointId": "mock"}
    else:
        from uxm.backends import make_backend
        backend = make_backend(model["config"])

    key = a.experiment_key or f"c_{a.variant}_{run_factory.now_stamp()}"
    req = SimpleNamespace(viewport=None, maxSteps=a.max_steps, variant=a.variant, experimentKey=key,
                          startPath=goal["startPath"])
    run = run_factory.build_run(req, goal, persona, model)
    if a.model_timeout_ms:
        run["limits"]["modelTimeoutMs"] = a.model_timeout_ms
    store = RunStore(run["runId"])
    store.create(run)

    start_url = SHOP_URLS[a.variant] + run["startPath"] + (f"?testMode={a.test_mode}" if a.test_mode else "")
    settings = agent_settings(run, goal, store.dir, start_url, SHOP_URLS[a.variant])

    run["status"], run["startedAt"] = "RUNNING", run_factory.now_iso()
    store.write_run(run)
    print(f"[{run['runId']}] {a.variant} {start_url} ({run['resultOrigin']})")
    try:
        session = open_browser(start_url, headless=not a.headed)
    except Exception as e:   # 쇼핑몰 서버가 꺼져 있는 등 (API와 같은 처리)
        run = store.read_run()
        run.update(status="ERROR", terminationReason="BROWSER_ERROR",
                   error={"code": "BROWSER_ERROR", "message": str(e), "retryable": False, "details": None,
                          "occurredAt": run_factory.now_iso()})
        store.write_run(run_factory.finalize(run))
        print(f"브라우저를 열지 못함: {e}", file=sys.stderr)
        return 1
    try:
        result = run_agent(settings, backend, session["page"])
    finally:
        close_browser(session)

    run = store.read_run()   # 루프가 실행 중에 갱신했을 수 있음 (API runner와 같은 순서)
    for k in AGENT_RESULT_FIELDS:
        if k in result:
            run[k] = result[k]
    run["comparisonContext"]["leakCheckPassed"] = result["comparisonContext"]["leakCheckPassed"]
    store.write_run(run_factory.finalize(run))

    sm = run["summary"] or {}
    print(json.dumps({k: run[k] for k in ("status", "terminationReason", "validity", "invalidReason")},
                     ensure_ascii=False))
    print(f"  stepCount={sm.get('stepCount')} path={' → '.join(v['pageKey'] for v in sm.get('path', []))}")
    if run["error"]:
        print(f"  error={run['error']['code']}: {run['error']['message']}")
    print(f"  결과: {store.dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
