"""C 시나리오 실행기: 실행 API 없이 명령줄에서 에이전트 루프를 돌린다 (#7).

쇼핑몰 서버(v1 8001 / 최종안 8010)가 켜져 있어야 한다. Run은 실행 API와 같은 방식으로 만든다
(api/run_factory.build_run → RunStore.create → run_agent → finalize). 결과: results/runs/<runId>/

    가짜 모델 (C0-MOCK):
      .venv\\Scripts\\python scripts\\run_c.py --variant before --mock "상단 메뉴>/shop/orders#1" "본문>/shop/refund#1"
    실제 모델 (.env 설정, C1·C2):
      .venv\\Scripts\\python scripts\\run_c.py --variant after
    C3 (mypage 시작):
      .venv\\Scripts\\python scripts\\run_c.py --variant before --goal refund-from-mypage-test
    testMode (C4·C6):
      .venv\\Scripts\\python scripts\\run_c.py --variant before --test-mode nochange --mock ...
    사이트 지도 기준 경로 지표 (#49, 실행 API가 SITE_MAP을 저장하기 전 확인용):
      .venv\\Scripts\\python scripts\\run_c.py --variant before --site-map --mock "상단 메뉴>/shop/mypage#1" ...
    눈으로 보기 (브라우저 창, 행동마다 3초 멈춤, 끝나면 Enter로 닫기):
      .venv\\Scripts\\python scripts\\run_c.py --variant after --watch 3
"""
import argparse
import dataclasses
import json
import os
import sys
import time
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from api import run_factory  # noqa: E402  (fastapi 없이 import 됨)
from api.presets import Presets  # noqa: E402
from uxm.agent import agent_settings, browser_module, default_env, run_agent  # noqa: E402
from uxm.candidates import draw_probabilities  # noqa: E402
from uxm.mock import MockBackend  # noqa: E402
from uxm.storage import RunStore  # noqa: E402

# api/runner.py와 같은 값 (runner는 fastapi를 불러와서 여기서 import하지 않는다)
SHOP_URLS = {
    "before": os.getenv("SHOP_BEFORE_URL", "http://127.0.0.1:8001"),
    "after": os.getenv("SHOP_AFTER_URL", "http://127.0.0.1:8010"),   # 자동 개선 최종안 (api/runner.py는 기본값 없음)
}
AGENT_RESULT_FIELDS = ("status", "terminationReason", "progress", "summary", "error", "finishedAt")

_browser = browser_module()   # 1번 uxm/browser.py
open_browser, close_browser = _browser.open_browser, _browser.close_browser


def _watch_env(env, seconds: float):
    """눈으로 보기용. 저장되는 사진·기록은 그대로이고, 화면에 배지를 오래 띄우고 멈추기만 한다."""
    def show_probabilities(capture):
        def wrapped(page, run_dir, step_id, candidates, probs=None, chosen=None):
            out = capture(page, run_dir, step_id, candidates, probs, chosen)   # 배지 사진은 원래대로 저장
            if probs is not None:
                labels = {c["optionId"]: c["label"] for c in candidates}
                print(f"\n  [{step_id}단계] {page.url.split('?')[0]}")
                for oid, p in sorted(probs.items(), key=lambda kv: -kv[1])[:6]:
                    mark = "  ← 선택" if oid == chosen else ""
                    print(f"    {p:6.1%}  {labels.get(oid, '이전 화면으로 돌아가기 (GO_BACK)')}{mark}")
                draw_probabilities(page, [c for c in candidates if c.get("bbox")], probs, chosen)
                time.sleep(seconds)
                page.evaluate("() => document.querySelectorAll('.uxm-overlay').forEach(e => e.remove())")
            return out
        return wrapped

    def pause_after(fn):
        def wrapped(*args, **kwargs):
            result = fn(*args, **kwargs)
            time.sleep(seconds)
            return result
        return wrapped

    return dataclasses.replace(env, capture=show_probabilities(env.capture),
                               click=pause_after(env.click), go_back=pause_after(env.go_back))


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
    ap.add_argument("--site-map", action="store_true",
                    help="경로 지표를 사이트 지도(SITE_MAP) 기준으로 계산 (#49). config/sitemaps의 지도로 Run에 설정을 넣음")
    ap.add_argument("--headed", action="store_true", help="브라우저 창 띄우기")
    ap.add_argument("--watch", type=float, default=0.0, metavar="초",
                    help="눈으로 보기: 창을 띄우고, 단계마다 확률 배지를 이만큼 보여 준 뒤 클릭하고, 이동한 화면도 "
                         "이만큼 보여 줌. 끝나면 Enter를 누를 때까지 창 유지. "
                         "멈춘 시간이 executionTimeMs에 들어가므로 측정용 실행에는 쓰지 않는다")
    a = ap.parse_args(argv)
    if a.watch:
        a.headed = True

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
    if a.site_map:   # 실행 API가 SITE_MAP을 저장하기 전, 루프 확인용으로 설정을 직접 넣는다
        from uxm import sitemap
        site_map = sitemap.load_site_map(os.path.join(ROOT, sitemap.SHOP.out))
        goal_page = sitemap.SHOP.page_key(run["verification"]["path"])
        run["referencePathConfig"] = sitemap.site_map_reference(site_map, goal_page,
                                                                sitemap.SHOP.page_key(run["startPath"]))
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
    env = default_env()
    if a.watch:   # 루프는 그대로 두고 화면 함수만 감싼다
        env = _watch_env(env, a.watch)
    try:
        result = run_agent(settings, backend, session["page"], env)
        if a.watch:
            try:   # 창을 닫으려고 Ctrl+C를 눌러도 아래 결과 반영(finalize)은 하도록 (#17 리뷰)
                input("끝났어요. 브라우저를 닫으려면 Enter를 누르세요...")
            except (EOFError, KeyboardInterrupt):
                pass
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
