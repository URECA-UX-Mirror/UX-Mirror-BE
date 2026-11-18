"""사이트 지도 생성 (이슈 #46, 설계 「사이트 지도 기반 경로 지표 설계」 v2.1 2장).

사이트 설정 파일(`config/sites/*.json`: 화면 목록·시작 화면·경로 범위·허용 도메인·사이트 주소)에 적힌 화면을
사이트 주소(variant)마다 **새 브라우저**(clean-context-v1, viewport 1440×900, 스크롤 0)로 열고
`candidates.extract`(후보 정책 candidate-v2)로 후보를 뽑아 지도를 만든다. 검증(화면 누락·variant 간 간선 일치·
도달 가능·preset 허용 경로 대조) 중 하나라도 실패하면 파일을 만들지 않고 이유를 출력한다(종료 코드 1).

테스트 쇼핑몰은 서버(Before 8001 / After 8002)가 켜져 있어야 한다.

    .venv\\Scripts\\python.exe scripts\\build_sitemap.py                       # 기본: config/sites/moro-market.json
    .venv\\Scripts\\python.exe scripts\\build_sitemap.py --check-runs          # 저장된 실제 Run 후보와 지도 대조(2단계)
    .venv\\Scripts\\python.exe scripts\\build_sitemap.py --dry-run             # 파일을 쓰지 않고 결과만 출력
    .venv\\Scripts\\python.exe scripts\\build_sitemap.py --site config\\sites\\other.json --url main=https://example.com

다른 사이트: 설정 파일을 새로 만들고 `--site`로 넘긴다. 후보는 그 설정의 등록된 화면으로 가는 링크만 뽑는다
(`uxm/site_scope.py`, #67). 쿼리가 있어야 열리는 화면은 설정 `pages.<key>.open`으로 연다.

    .venv\\Scripts\\python.exe scripts\\build_sitemap.py --site config\\sites\\cafe24-ons.json --dry-run
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from uxm import sitemap  # noqa: E402

KST = timezone(timedelta(hours=9))
DEFAULT_SITE = os.path.join(sitemap.SITES_DIR, "moro-market.json")


def open_page(url: str) -> tuple[dict, int | None]:
    """새 브라우저로 화면을 열고 (세션, HTTP 상태)를 돌려준다. 조건은 `browser.open_browser`와 같다.

    실행 경로(`uxm/browser.py`)는 바꾸지 않고, 응답 상태를 보려고 여기서 직접 연다 (#48 리뷰).
    """
    from playwright.sync_api import sync_playwright
    from uxm import browser
    pw = sync_playwright().start()
    try:
        b = pw.chromium.launch(headless=True)
        context = b.new_context(viewport=browser.VIEWPORT)
        page = context.new_page()
        response = page.goto(url, wait_until="networkidle")
    except Exception:
        pw.stop()
        raise
    return {"playwright": pw, "browser": b, "context": context, "page": page}, (response.status if response else None)


def site_scope(config: sitemap.SiteConfig):
    """MORO MARKET은 None(기존 후보 규칙 그대로), 다른 사이트는 등록된 화면 기준 범위 (#67)."""
    if config.name == sitemap.SHOP.name:
        return None
    from uxm.site_scope import SiteScope
    return SiteScope(config)


def extract_site(base_url: str, config: sitemap.SiteConfig) -> dict[str, list[dict] | None]:
    """화면마다 새 브라우저로 열어 후보를 뽑는다. 열기 실패·오류 응답·다른 화면 도착이면 None (지도 생성 실패로 이어짐)."""
    from uxm import browser, candidates   # playwright는 여기서만 필요
    scope = site_scope(config)
    out: dict[str, list[dict] | None] = {}
    for key, path in config.pages.items():
        try:
            session, status = open_page(base_url.rstrip("/") + config.open_path(key))
        except Exception as e:   # 사이트 꺼짐·로딩 실패
            print(f"  {key}: 열기 실패 ({e})", file=sys.stderr)
            out[key] = None
            continue
        try:
            problem = sitemap.page_load_problem(path, status, session["page"].url)
            if problem:
                print(f"  {key}: {problem}", file=sys.stderr)
                out[key] = None
            else:
                out[key] = candidates.extract(session["page"], scope=scope)["candidates"]
        except Exception as e:
            print(f"  {key}: 후보 추출 실패 ({e})", file=sys.stderr)
            out[key] = None
        finally:
            browser.close_browser(session)
    return out


def preset_checks(config: sitemap.SiteConfig) -> list[dict]:
    """config/presets.json에서 시작 화면이 지도 시작 화면인 목표의 허용 경로 (검증 4). presetCheck=false면 생략."""
    if not config.preset_check:
        return []
    with open(os.path.join(ROOT, "config", "presets.json"), encoding="utf-8") as f:
        goals = json.load(f)["goals"]
    start = config.pages[config.start_page]
    checks = []
    for g in goals:
        if g.get("startPath", start).rstrip("/") != start or not g.get("referencePathConfig"):
            continue
        goal_page = config.page_key(g["verification"]["path"])
        ref = g["referencePathConfig"]
        checks.append({"goalId": g["goalId"], "goalPage": goal_page, "allowedPaths": ref["allowedPaths"],
                       "shortestAllowedSteps": ref["shortestAllowedSteps"]})
    return checks


def variant_urls(config: sitemap.SiteConfig, overrides: list[str]) -> dict[str, str]:
    """설정의 사이트 주소에 `--url 이름=주소`를 덮어쓴다. 설정에 없는 이름은 추가."""
    urls = dict(config.variants)
    for item in overrides:
        name, sep, url = item.partition("=")
        if not sep or not name or not url:
            raise SystemExit(f"--url은 이름=주소 형식이어야 합니다: {item}")
        urls[name] = url
    return urls


def main() -> int:
    ap = argparse.ArgumentParser(description="사이트 지도 생성 (SITE_MAP)")
    ap.add_argument("--site", default=DEFAULT_SITE, help="사이트 설정 파일 (기본: config/sites/moro-market.json)")
    ap.add_argument("--url", action="append", default=[], metavar="이름=주소",
                    help="사이트 주소 덮어쓰기 (예: before=http://127.0.0.1:8001). 여러 번 가능")
    ap.add_argument("--version", default=None, help="siteMapVersion (기본: 설정 값)")
    ap.add_argument("--out", default=None, help="지도 파일 경로 (기본: 설정 값)")
    ap.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않음")
    ap.add_argument("--check-runs", action="store_true", help="저장된 LIVE Run의 Step 후보와 지도를 대조")
    args = ap.parse_args()

    try:
        config = sitemap.load_site_config(args.site)
    except sitemap.SiteMapError as e:
        print("사이트 설정 오류:", *(f"  - {r}" for r in e.reasons), sep="\n", file=sys.stderr)
        return 1
    urls = variant_urls(config, args.url)
    version = args.version or config.site_map_version
    out = args.out or os.path.join(ROOT, config.out)

    print(f"사이트 '{config.name}' 후보 추출: " + ", ".join(f"{k} {v}" for k, v in urls.items()))
    extracted = {name: extract_site(url, config) for name, url in urls.items()}
    try:
        site_map = sitemap.build_site_map(config, extracted, version=version,
                                          generated_at=datetime.now(KST).isoformat(timespec="seconds"),
                                          preset_checks=preset_checks(config))
    except sitemap.SiteMapError as e:
        print("지도 생성 실패:", *(f"  - {r}" for r in e.reasons), sep="\n", file=sys.stderr)
        return 1

    print(f"\n{site_map['siteMapVersion']}  {site_map['siteMapHash']}")
    if site_map.get("unreachablePages"):
        print("시작 화면에서 닿지 않는 화면: " + ", ".join(site_map["unreachablePages"]))
    for key, page in site_map["pages"].items():
        print(f"  {key:8} → {', '.join(sorted({e['to'] for e in page['edges']})) or '(없음)'}")
    print("\n도착 화면별 최단 거리 (시작 화면 기준):")
    for goal in config.pages:
        d = sitemap.distances(site_map["pages"], goal)
        print(f"  {goal:8} " + "  ".join(f"{k}={v}" for k, v in d.items()))

    if args.check_runs:
        run_dirs = sorted(glob.glob(os.path.join(ROOT, "results", "runs", "*")))
        steps, read_problems = sitemap.collect_run_steps(run_dirs, config.meta.get("siteVersion"))
        cmp = sitemap.compare_with_steps(site_map, config, steps)
        print(f"\n실제 Run 대조: 읽은 Step {len(steps)}개 = 대조 {cmp.compared}개 + 건너뜀 {cmp.skipped}개 "
              f"(testMode·재판단 후보), 읽기 문제 {len(read_problems)}개, 불일치 {len(cmp.problems)}개")
        for p in read_problems + cmp.problems:
            print(f"  - {p}")
        # 대조를 요청했는데 실제로 비교하지 못했거나 어긋나면 지도를 저장하지 않는다
        # (읽은 Step이 전부 건너뛰는 대상이어도 "불일치 0"으로 통과하지 않게)
        if read_problems or not cmp.ok:
            if cmp.compared == 0:
                reason = "실제로 대조한 Step이 없음"
            else:
                reason = "읽기 문제 또는 불일치가 있음"
            print(f"실제 Run 대조 실패: {reason}. 지도를 저장하지 않습니다.", file=sys.stderr)
            return 1

    if not args.dry_run:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(site_map, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"\n저장: {os.path.relpath(out, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
