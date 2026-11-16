"""가짜 실행 샘플용 원본 스크린샷(배지 없음) + 같은 순간의 후보. Before 쇼핑몰(8001)이 켜져 있어야 한다.

    .venv\\Scripts\\python scripts\\capture_sample_screens.py

저장: results/screenshots/sample_raw/
  before_<page>.png   원본 스크린샷 (1440x900, 스크롤 없음)
  before_<page>.json  같은 화면 상태에서 뽑은 후보 (extract 결과). 사진과 bbox가 어긋나지 않게 함께 저장
그다음 scripts/make_sample_run.py를 다시 실행하면 이 사진과 후보로 샘플을 만든다.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from playwright.sync_api import sync_playwright  # noqa: E402

from uxm.candidates import VIEWPORT, extract  # noqa: E402

PAGES = ("home", "mypage", "orders", "refund")
OUT = os.path.join(ROOT, "results", "screenshots", "sample_raw")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8001")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_context(viewport=VIEWPORT).new_page()
        for name in PAGES:
            page.goto(f"{args.base}/shop/{name}", wait_until="networkidle")
            page.evaluate("() => document.fonts.ready")   # 글꼴이 다 적용된 뒤 (글자 폭이 바뀌지 않게)
            extracted = extract(page)                      # 화면을 바꾸지 않는다
            page.screenshot(path=os.path.join(OUT, f"before_{name}.png"))
            with open(os.path.join(OUT, f"before_{name}.json"), "w", encoding="utf-8") as f:
                json.dump(extracted, f, ensure_ascii=False, indent=2)
            print(f"saved before_{name}.png / .json  (후보 {len(extracted['candidates'])}개)")
        browser.close()


if __name__ == "__main__":
    main()
