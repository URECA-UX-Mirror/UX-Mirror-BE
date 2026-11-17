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

다른 사이트: 설정 파일을 새로 만들고 `--site`로 넘긴다. 단, 후보 추출 규칙(`uxm/candidates.py`)이 아직
쇼핑몰(`/shop/`, 시작 화면 `/shop/home`)에 묶여 있어 그 규칙을 사이트별로 바꾸기 전에는 후보가 나오지 않는다.
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


def extract_site(base_url: str, config: sitemap.SiteConfig) -> dict[str, list[dict] | None]:
    """화면마다 새 브라우저로 열어 후보를 뽑는다. 실패한 화면은 None (지도 생성 실패로 이어짐)."""
    from uxm import browser, candidates   # playwright는 여기서만 필요
    out: dict[str, list[dict] | None] = {}
    for key, path in config.pages.items():
        try:
            session = browser.open_browser(base_url.rstrip("/") + path)
        except Exception as e:   # 사이트 꺼짐·로딩 실패
            print(f"  {key}: 열기 실패 ({e})", file=sys.stderr)
            out[key] = None
            continue
        try:
            out[key] = candidates.extract(session["page"])["candidates"]
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


def saved_steps(config: sitemap.SiteConfig) -> list[dict]:
    """results/runs에서 이 사이트(siteVersion)의 LIVE Run Step (2단계 대조용)."""
    steps = []
    for run_dir in sorted(glob.glob(os.path.join(ROOT, "results", "runs", "*"))):
        try:
            with open(os.path.join(run_dir, "run.json"), encoding="utf-8") as f:
                run = json.load(f)
            if run.get("resultOrigin") != "LIVE" or run.get("siteVersion") != config.meta.get("siteVersion"):
                continue
            with open(os.path.join(run_dir, "steps.jsonl"), encoding="utf-8") as f:
                steps += [json.loads(line) for line in f if line.strip()]
        except (OSError, ValueError):
            continue
    return steps


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
    for key, page in site_map["pages"].items():
        print(f"  {key:8} → {', '.join(sorted({e['to'] for e in page['edges']})) or '(없음)'}")
    print("\n도착 화면별 최단 거리 (시작 화면 기준):")
    for goal in config.pages:
        d = sitemap.distances(site_map["pages"], goal)
        print(f"  {goal:8} " + "  ".join(f"{k}={v}" for k, v in d.items()))

    if args.check_runs:
        steps = saved_steps(config)
        problems = sitemap.compare_with_steps(site_map, config, steps)
        print(f"\n실제 Run 대조: Step {len(steps)}개, 불일치 {len(problems)}개")
        for p in problems:
            print(f"  - {p}")

    if not args.dry_run:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(site_map, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"\n저장: {os.path.relpath(out, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
