"""사이트 지도 기반 목표 도달 확률 (19번): 실행 없이 화면당 모델 1번으로 Before/After를 비교한다.

쇼핑몰 서버(Before 8001 / After 8002)가 켜져 있어야 한다. 모델은 .env 설정(실제 Zoro-1).
결과: results/reach/<이름>.json (화면별 원본 요청·응답 포함)

    .venv\\Scripts\\python scripts\\estimate_reach.py
    .venv\\Scripts\\python scripts\\estimate_reach.py --goal-page orders --max-steps 5
    .venv\\Scripts\\python scripts\\estimate_reach.py --goal "환불 신청 화면 찾기" --persona "70대, 인터넷 쇼핑이 서툰 사용자"
    .venv\\Scripts\\python scripts\\estimate_reach.py --url C1=http://127.0.0.1:8003   # 다른 사이트(라벨 변형 등) 추가
    .venv\\Scripts\\python scripts\\estimate_reach.py --site cafe24-ons --goal-page review --goal "써 본 사람들의 후기가 궁금하다"

--site: 외부 사이트 시나리오(#76). 사이트 설정(config/sites/<siteId>.json)의 주소·지도·탐색 규칙으로 1곳만 추정한다.

값은 구조 기준 추정치다(이전 화면 기록·되돌아가기 없음). 실제 Run 결과를 대신하지 않는다.
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from uxm import config, prompts, reach  # noqa: E402
from uxm.sitemap import load_site_map  # noqa: E402

SITE_MAP = os.path.join(ROOT, "config", "sitemaps", "sitemap-shop-v1.json")
SHOP_URLS = {"before": os.getenv("SHOP_BEFORE_URL", "http://127.0.0.1:8001"),
             "after": os.getenv("SHOP_AFTER_URL", "http://127.0.0.1:8002")}


def main(argv=None, backend=None, browser=None) -> dict:
    """backend·browser: 테스트용 주입 (없으면 .env 모델, 실제 브라우저)."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--goal-page", default="refund")
    ap.add_argument("--start-page", default=None, help="기본: 지도의 시작 화면")
    ap.add_argument("--goal", default=prompts.GOAL)
    ap.add_argument("--persona", default=prompts.PERSONA)
    ap.add_argument("--max-steps", type=int, default=8)
    ap.add_argument("--site-map", default=SITE_MAP)
    ap.add_argument("--url", action="append", default=[], metavar="NAME=URL", help="비교할 사이트 (기본 before·after)")
    ap.add_argument("--site", default=None, help="시나리오 사이트 siteId (config/sites/<siteId>.json). 주면 그 사이트 1곳만")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    scope = None
    if args.site:
        from uxm.site_scope import load_scope
        try:
            scope = load_scope(args.site)
        except ValueError as e:
            ap.error(str(e))
        if args.url:
            ap.error("--site와 --url은 같이 쓰지 않습니다 (사이트 주소는 사이트 설정에서 온다)")
        if args.site_map == SITE_MAP:
            args.site_map = os.path.join(ROOT, scope.config.out)
    site_map = load_site_map(args.site_map)
    start = args.start_page or site_map["startPageKey"]
    # 지도가 기록한, 시작 화면에서 닿지 않는 화면은 도달 확률에 영향이 없어 모델을 부르지 않는다 (행사 GPU 시간 절약).
    # 시작 화면을 바꾸면 이 기록과 맞지 않으므로 지도 기본 시작 화면일 때만 쓴다. 간선 대조는 그대로 한다.
    unreachable = set(site_map.get("unreachablePages") or []) if start == site_map["startPageKey"] else set()
    if args.goal_page in unreachable:
        print(f"[주의] 도착 화면 '{args.goal_page}'은 지도상 시작 화면에서 닿지 않습니다. 도달 확률은 0입니다.")
    if scope is not None:
        sites = {args.site: scope.base_url}
    else:
        sites = dict(SHOP_URLS) if not args.url else dict(u.split("=", 1) for u in args.url)
    cfg = config.load()
    if backend is None:
        from uxm.backends import make_backend
        backend = make_backend(cfg)
    if browser is None:
        from uxm.browser import close_browser, open_browser
    else:
        open_browser, close_browser = browser

    results = {}
    for name, url in sites.items():
        session = open_browser(url.rstrip("/") + site_map["pages"][start]["path"])
        try:
            probs, records, mismatches = reach.collect_page_probabilities(
                session["page"], url, site_map, backend, goal=args.goal, persona=args.persona,
                temperature=cfg["temperature"], skip={args.goal_page} | unreachable, scope=scope)
        finally:
            close_browser(session)
        est = reach.estimate(site_map, probs, start, args.goal_page, args.max_steps, map_mismatches=mismatches)
        results[name] = {"estimate": est, "pageProbabilities": probs, "pages": records}
        print(f"[{name}] 도달 {est['reachProbability']}  최단 {est['shortestSteps']}번 안 도달 "
              f"{est['reachWithinShortestProbability']}  도달 시 기대 단계 {est['expectedStepsGivenReach']}  "
              f"이탈 {est['lostProbability']}  미도달 {est['unfinishedProbability']}  "
              f"경로 {est['mostLikelyPath']}  {est['status']}")
    names = list(results)
    comparison = (reach.compare(results[names[0]]["estimate"], results[names[1]]["estimate"])
                  if len(names) == 2 else None)
    if comparison:
        print(f"[{names[1]} − {names[0]}] {comparison}")

    out = {"engineVersion": reach.ENGINE_VERSION, "siteId": args.site, "goal": args.goal, "persona": args.persona,
           "goalPageKey": args.goal_page, "startPageKey": start, "maxSteps": args.max_steps,
           "model": {k: cfg[k] for k in ("backend", "base_url", "temperature")}, "sites": sites,
           "results": results, "comparison": comparison, "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    path = args.out or os.path.join(ROOT, "results", "reach", f"reach_{args.goal_page}_{time.strftime('%Y%m%d_%H%M%S')}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("저장:", path)
    return out


if __name__ == "__main__":
    main()
