"""B3 + B5 (+B6 미리보기): 실제 쇼핑몰 화면의 후보로 Zoro-1 확률 실측.

    python scripts/run_b5.py                 # home, Before/After
    python scripts/run_b5.py --pages home orders
    python scripts/run_b5.py --headless --pause 0

순서: 화면 열기 → 후보 추출 → 요청 생성 → 누출 검사 → Before/After 요청 비교(B3, 실패 시 중단)
      → Zoro-1 판단 → 확률 배지를 화면에 표시 → 저장.
쇼핑몰 서버 두 개(8001, 8002)가 켜져 있어야 한다.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from uxm import config, prompts  # noqa: E402
from uxm.candidates import VIEWPORT, draw_probabilities, extract  # noqa: E402
from uxm.leak import assert_no_leak  # noqa: E402
from uxm.metrics import decision_metrics, probability_by_destination, validate_probabilities  # noqa: E402
from uxm.screen_request import build_screen_request, payload_diff  # noqa: E402

SHOPS = {"before": "http://127.0.0.1:8001", "after": "http://127.0.0.1:8002"}
LABELS = {"before": "이용정보", "after": "주문·환불"}
PAGES = ("home", "mypage", "support", "orders", "refund")


def check_shops():
    """브라우저를 띄우기 전에 쇼핑몰 서버 두 개가 켜져 있는지 확인."""
    import urllib.request
    down = []
    for variant, base in SHOPS.items():
        try:
            urllib.request.urlopen(base + "/shop/home", timeout=3)
        except Exception:
            folder = "moro-before" if variant == "before" else "moro-after"
            down.append(f"  {variant}: {base} 응답 없음 → 새 터미널에서 cd C:\\workspace\\uxmirror\\shop\\{folder} 후 py -3.11 server.py")
    if down:
        sys.exit("쇼핑몰 서버가 꺼져 있습니다.\n" + "\n".join(down))


def fmt(x):
    return "N/A" if x is None else f"{x:.3f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", nargs="*", default=["home"], choices=PAGES)
    ap.add_argument("--pause", type=float, default=8.0)
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args()
    check_shops()

    from playwright.sync_api import sync_playwright
    cfg = config.load()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run = f"b5_{stamp}"
    shot_dir = os.path.join(ROOT, "results", "screenshots", "b5", run)
    json_path = os.path.join(ROOT, "results", "json", "b5", f"{run}.jsonl")
    os.makedirs(shot_dir, exist_ok=True)
    os.makedirs(os.path.dirname(json_path), exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=a.headless)
        page = browser.new_page(viewport=VIEWPORT)

        # 1) 화면 → 후보 → 요청 (모델 호출 전에 B3 검사까지 끝낸다)
        prepared = {}
        for key in a.pages:
            for variant, base in SHOPS.items():
                page.goto(f"{base}/shop/{key}", wait_until="networkidle")
                ext = extract(page)
                req, meta = build_screen_request(ext)
                assert_no_leak(req)
                prepared[(key, variant)] = (ext, req, meta)
            d = payload_diff(prepared[(key, "before")][1], prepared[(key, "after")][1])
            label_only = all(LABELS["before"] in str(x) and LABELS["after"] in str(y) and
                             str(x).replace(LABELS["before"], LABELS["after"]) == str(y) for _, x, y in d)
            bm, am = prepared[(key, "before")][2], prepared[(key, "after")][2]
            same_keys = bm["optionMap"] == am["optionMap"] and bm["destination"] == am["destination"]
            same_bbox = bm["bbox"] == am["bbox"]
            print(f"[B3] {key}: 요청 차이 {len(d)}곳 {[(x, y) for _, x, y in d]} "
                  f"| 라벨만 다름={label_only} semanticKey·목적지 동일={same_keys} bbox 동일={same_bbox}", flush=True)
            if not (label_only and same_keys and len(d) <= 1):
                sys.exit(f"[B3] {key}: Before/After 요청이 라벨 하나 이상 다름 → 모델 호출 중단")

        # 2) 모델
        from uxm.backends import make_backend
        print(f"[load] backend={cfg['backend']} device={cfg['device']} ...", flush=True)
        backend = make_backend(cfg)
        print(f"[load] {backend.info()}", flush=True)

        rows = []
        with open(json_path, "w", encoding="utf-8") as f:
            for key in a.pages:
                for variant, base in SHOPS.items():
                    ext, req, meta = prepared[(key, variant)]
                    t0 = time.perf_counter()
                    resp = backend.decide(req, cfg["temperature"])
                    wall = round((time.perf_counter() - t0) * 1000)
                    ans = resp["answers"]["next_click"]
                    probs = ans["probabilities"]
                    validate_probabilities(probs, list(meta["optionMap"]))
                    m = decision_metrics(probs, meta["backOptionId"])
                    by_dest = probability_by_destination(probs, meta["destination"])

                    print(f"\n[B5] {variant.upper()} / {key}  {wall}ms  선택={ans['choice']}", flush=True)
                    for c in ext["candidates"]:
                        mark = " ✓" if c["optionId"] == ans["choice"] else ""
                        print(f"   {probs[c['optionId']]:.3f}  [{c['region']}] {c['label']}{mark}", flush=True)
                    print(f"   목적지별: " + ", ".join(f"{d}={v:.3f}" for d, v in by_dest.items()), flush=True)
                    print(f"   H_click={fmt(m['clickOnlyNormalizedEntropy'])} top2={fmt(m['clickOnlyTop2Margin'])}",
                          flush=True)

                    page.goto(f"{base}/shop/{key}", wait_until="networkidle")
                    draw_probabilities(page, ext["candidates"], probs, ans["choice"])
                    shot = os.path.join(shot_dir, f"{variant}_{key}.png")
                    page.screenshot(path=shot)
                    if not a.headless:
                        page.wait_for_timeout(a.pause * 1000)

                    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "scenario": "B5", "pageKey": key,
                           "variant": variant, "promptVersion": prompts.PROMPT_VERSION,
                           "modelConfig": backend.info(), "temperature": cfg["temperature"],
                           "goalJudgmentMode": cfg["goal_judgment_mode"], "viewport": VIEWPORT, "wallMs": wall,
                           "request": req, "meta": meta, "excluded": ext["excluded"], "response": resp,
                           "metrics": m, "probabilityByDestination": by_dest, "resultOrigin": "LIVE",
                           "screenshot": os.path.relpath(shot, os.path.join(ROOT, "results")).replace("\\", "/")}
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    top = ", ".join(f"{c['label'][:12]}={probs[c['optionId']]:.3f}" for c in ext["candidates"])
                    rows.append((f"B5 {variant}/{key}", f"{top} / H_click={fmt(m['clickOnlyNormalizedEntropy'])}"))
        browser.close()

    log = os.path.join(ROOT, "results", "run_log.md")
    with open(log, "a", encoding="utf-8") as f:
        for tag, line in rows:
            f.write(f"| {stamp} | {tag} | {prompts.PROMPT_VERSION} | {cfg['backend']}/{cfg['device']} "
                    f"| {line.replace('|', '/')} | 기록 |\n")
    print(f"\n데이터: {json_path}\n스크린샷: {shot_dir}\n기록: {log}")


if __name__ == "__main__":
    main()
