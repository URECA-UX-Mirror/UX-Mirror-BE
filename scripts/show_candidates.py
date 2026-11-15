"""브라우저 창을 띄워 Before/After 각 화면의 클릭 후보를 번호 박스로 보여준다 (B1 눈으로 확인용).

    python scripts/show_candidates.py              # Before → After, 5페이지씩
    python scripts/show_candidates.py --pause 6    # 화면당 6초
    python scripts/show_candidates.py --headless   # 창 없이 스크린샷만

쇼핑몰 서버 두 개(8001, 8002)가 켜져 있어야 한다.
결과: results/json/candidates/candidates_<stamp>/*.json (후보 목록·도착 판정)
      results/screenshots/candidates/candidates_<stamp>/*.png (후보 번호 박스)
"""
import argparse
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from uxm.candidates import VIEWPORT, draw_overlay, extract  # noqa: E402
from uxm.goal import verify_goal  # noqa: E402

SHOPS = {"before": "http://127.0.0.1:8001", "after": "http://127.0.0.1:8002"}
PAGES = ("home", "mypage", "support", "orders", "refund")


def banner(page, text):
    page.evaluate("""t => { const d = document.createElement('div'); d.className = 'uxm-overlay'; d.textContent = t;
      Object.assign(d.style, {position:'fixed', right:'16px', bottom:'16px', background:'rgba(17,24,39,.9)',
        color:'#fff', font:'bold 16px sans-serif', padding:'10px 16px', borderRadius:'10px', zIndex:2147483647,
        pointerEvents:'none'}); document.body.append(d); }""", text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pause", type=float, default=4.0)
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args()

    from playwright.sync_api import sync_playwright
    run = "candidates_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    json_dir = os.path.join(ROOT, "results", "json", "candidates", run)
    shot_dir = os.path.join(ROOT, "results", "screenshots", "candidates", run)
    os.makedirs(json_dir, exist_ok=True)
    os.makedirs(shot_dir, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=a.headless)
        page = browser.new_page(viewport=VIEWPORT)
        for variant, base in SHOPS.items():
            for key in PAGES:
                page.goto(f"{base}/shop/{key}", wait_until="networkidle")
                res = extract(page)
                reached, why = verify_goal(page)   # 오버레이 그리기 전에 판정
                res["goal"] = {"reached": reached, **why}
                draw_overlay(page, res["candidates"])
                banner(page, f"{variant.upper()} · {key} · 후보 {len(res['candidates'])}개"
                             f"{' · 목표 도착' if reached else ''}")
                shot = os.path.join(shot_dir, f"{variant}_{key}.png")
                page.screenshot(path=shot)
                res["screenshot"] = os.path.relpath(shot, os.path.join(ROOT, "results")).replace("\\", "/")
                with open(os.path.join(json_dir, f"{variant}_{key}.json"), "w", encoding="utf-8") as f:
                    json.dump(res, f, ensure_ascii=False, indent=2)
                print(f"\n[{variant} / {key}] 후보 {len(res['candidates'])}개, 제외 {len(res['excluded'])}개"
                      f" | 도착 판정={'예' if reached else '아니오'} (주소 일치={why['pathMatched']},"
                      f" 글자 보임={why['textVisible']})", flush=True)
                for i, c in enumerate(res["candidates"], 1):
                    ctx = f" · {c['context']}" if c.get("context") else ""
                    print(f"  {i}. [{c['region']}{ctx}] {c['label'][:40]}  → {c['href']}", flush=True)
                danger = [e["label"] for e in res["excluded"] if e["reason"] == "DANGEROUS_ACTION"]
                if danger:
                    print(f"  위험 동작 제외: {danger}", flush=True)
                if not a.headless:
                    page.wait_for_timeout(a.pause * 1000)
        browser.close()
    print(f"\n데이터: {json_dir}\n스크린샷: {shot_dir}")


if __name__ == "__main__":
    main()
