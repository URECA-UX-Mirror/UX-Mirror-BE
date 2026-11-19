"""Optimizer 후보 사이트(C0~C4)를 모델 없이 실제 렌더링으로 점검한다 (#85).

    python scripts/check_site_instances.py           # 이미 떠 있는 서버(등록 주소, baseUrlEnv 우선)를 검사
    python scripts/check_site_instances.py --serve   # 후보 서버 5개(C0 8001, C1~C4 8003~8006)를 직접 띄우고 끝나면 끈다

검사 (후보마다):
1. `/shop/home` 접속
2. Optimizer와 같은 스냅샷·유효성 검사 (`api/site_instances.py`: C0는 설정과, 나머지는 C0와 비교)
3. 대상 메뉴가 헤더의 다른 링크·버튼과 겹치거나 글자가 잘리지 않는지
4. C0 대비 화면 픽셀 차이가 대상 메뉴 영역 안에만 있는지 (애니메이션은 끄고 찍는다)

결과: results/site_checks/<stamp>/ (result.json, 후보별 home.png). 하나라도 실패하면 종료 코드 1.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from api.schemas import P0_VIEWPORT  # noqa: E402
from api.site_instances import (SiteRegistry, capture_snapshot, validate_against_baseline,  # noqa: E402
                                validate_baseline)
from uxm.browser import close_browser, open_browser  # noqa: E402
from uxm.candidates import extract  # noqa: E402

SITE_FOLDERS = {"C0": "moro-before", "C1": "moro-c1", "C2": "moro-c2", "C3": "moro-c3", "C4": "moro-c4"}
CANDIDATE_POLICY_VERSION = "candidate-v2"
NO_ANIMATION_CSS = "*, *::before, *::after { animation: none !important; transition: none !important; }"

# 대상 메뉴(href가 같은 헤더 링크)의 위치, 다른 헤더 요소와의 겹침, 글자 넘침
LAYOUT_JS = """(href) => {
  const norm = h => (h || '').replace(/\\/$/, '');
  const a = [...document.querySelectorAll('header a[href], nav a[href]')].find(e => norm(e.getAttribute('href')) === norm(href));
  if (!a) return null;
  const r = a.getBoundingClientRect();
  const others = [...a.closest('header, nav').querySelectorAll('a, button')].filter(e => e !== a && !e.contains(a) && !a.contains(e));
  const overlaps = others.filter(e => {
    const b = e.getBoundingClientRect();
    return b.width && b.height && b.left < r.right - 0.5 && r.left < b.right - 0.5 && b.top < r.bottom - 0.5 && r.top < b.bottom - 0.5;
  }).map(e => e.innerText.trim());
  return {rect: {x: r.x, y: r.y, width: r.width, height: r.height}, overlaps,
          clipped: a.scrollWidth > a.clientWidth + 1 || a.scrollHeight > a.clientHeight + 1,
          inViewport: r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight};
}"""


def port_in_use(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def start_servers(targets, procs: list, ready_timeout_s: float = 5.0) -> dict:
    """targets: [(candidateId, server.py 경로, host, port)]. 띄운 프로세스는 `procs`에 바로 넣는다
    (중간에 예외가 나도 호출한 쪽 finally에서 정리되게). 반환: 기동하지 못한 후보 → 사유.

    이미 다른 프로세스가 쓰는 포트는 띄우지 않고 실패로 남긴다. 그대로 두면 기존 서버를 검사하게 된다.
    """
    failed, started = {}, []
    for cid, server_py, host, port in targets:
        if port_in_use(host, port):
            failed[cid] = f"PORT_IN_USE: {port}"
            continue
        p = subprocess.Popen([sys.executable, server_py, "--port", str(port)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        procs.append(p)
        started.append((cid, p, host, port))
    deadline = time.monotonic() + ready_timeout_s
    for cid, p, host, port in started:
        while p.poll() is None and not port_in_use(host, port) and time.monotonic() < deadline:
            time.sleep(0.1)
        if p.poll() is not None:
            err = (p.stderr.read() or b"").decode(errors="replace").strip().splitlines()
            failed[cid] = f"SERVER_START_FAILED: exit {p.returncode}" + (f" ({err[-1]})" if err else "")
        elif not port_in_use(host, port):
            failed[cid] = f"SERVER_NOT_READY: {ready_timeout_s}s"
    return failed


def stop_servers(procs: list) -> None:
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


def layout_and_screenshot(base_url: str, start_path: str, href: str, png_path: str) -> dict | None:
    session = open_browser(base_url + start_path)
    try:
        page = session["page"]
        page.add_style_tag(content=NO_ANIMATION_CSS)
        layout = page.evaluate(LAYOUT_JS, href)
        page.screenshot(path=png_path)
        return layout
    finally:
        close_browser(session)


def pixel_diff_box(a_path: str, b_path: str):
    from PIL import Image, ImageChops
    a, b = Image.open(a_path).convert("RGB"), Image.open(b_path).convert("RGB")
    return ImageChops.difference(a, b).getbbox()


def inside(box, rect, margin=2) -> bool:
    """getbbox (x0, y0, x1, y1)가 메뉴 rect 안에 있는지."""
    x0, y0, x1, y1 = box
    return (x0 >= rect["x"] - margin and y0 >= rect["y"] - margin
            and x1 <= rect["x"] + rect["width"] + margin and y1 <= rect["y"] + rect["height"] + margin)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--serve", action="store_true", help="서버 5개를 직접 띄우고 끝나면 끈다")
    args = ap.parse_args()

    with open(os.path.join(ROOT, "config", "optimization.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    target = cfg["target"]
    registry = SiteRegistry()
    out_dir = os.path.join(ROOT, "results", "site_checks", datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)

    procs, start_failed = [], {}
    results, base_snap, base_png = {}, None, None
    try:
        if args.serve:
            targets = []
            for cand in cfg["candidates"]:
                inst = registry.for_candidate(cand["candidateId"], cand["label"])
                if inst is None or cand["candidateId"] not in SITE_FOLDERS:
                    continue  # 아래 후보 검사에서 SITE_INSTANCE_MISSING으로 남긴다
                url = urlsplit(inst["baseUrl"])
                targets.append((cand["candidateId"], os.path.join(ROOT, "shop", SITE_FOLDERS[cand["candidateId"]], "server.py"),
                                url.hostname, url.port))
            start_failed = start_servers(targets, procs)
        for cand in cfg["candidates"]:
            cid, label = cand["candidateId"], cand["label"]
            inst = registry.for_candidate(cid, label)
            row = {"label": label, "siteInstanceId": inst and inst["siteInstanceId"], "problems": []}
            results[cid] = row
            if inst is None:
                row["problems"].append("SITE_INSTANCE_MISSING")
                continue
            if cid in start_failed:
                row["problems"].append(start_failed[cid])
                continue
            try:
                urllib.request.urlopen(inst["baseUrl"] + target["startPath"], timeout=5).read()
            except Exception as e:  # noqa: BLE001  서버가 꺼져 있으면 그 후보만 실패로 남긴다
                row["problems"].append(f"SERVER_UNREACHABLE: {type(e).__name__}")
                continue

            snap = capture_snapshot((open_browser, close_browser), inst["baseUrl"], target["startPath"], extract,
                                    dict(P0_VIEWPORT), CANDIDATE_POLICY_VERSION)
            row.update(snapshotHash=snap["snapshotHash"], candidateCount=snap["candidateCount"])
            if cid == "C0":
                row["problems"] += validate_baseline(snap, target)
                base_snap = snap
            elif base_snap is None:
                row["problems"].append("BASELINE_NOT_CHECKED")
            else:
                row["problems"] += validate_against_baseline(snap, base_snap, target["semanticKey"], label)

            png = os.path.join(out_dir, f"{cid}_home.png")
            layout = layout_and_screenshot(inst["baseUrl"], target["startPath"], target["href"], png)
            row["layout"] = layout
            if layout is None:
                row["problems"].append("TARGET_MENU_NOT_FOUND")
            else:
                if layout["overlaps"]:
                    row["problems"].append("TARGET_MENU_OVERLAP")
                if layout["clipped"] or not layout["inViewport"]:
                    row["problems"].append("TARGET_MENU_CLIPPED")
            if cid == "C0":
                base_png = png
            elif base_png and layout:
                box = pixel_diff_box(base_png, png)
                row["pixelDiffBox"] = box
                if box and not inside(box, layout["rect"]):
                    row["problems"].append("CHANGED_OUTSIDE_TARGET_MENU")
    finally:
        stop_servers(procs)

    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    for cid, row in results.items():
        print(f"{cid} {row['label']:<6} {'OK  ' if not row['problems'] else 'FAIL'} {', '.join(row['problems'])}")
    print(f"결과: {os.path.relpath(out_dir, ROOT)}")
    return 1 if any(r["problems"] for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
