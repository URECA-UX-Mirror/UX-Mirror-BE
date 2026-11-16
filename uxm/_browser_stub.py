"""임시 브라우저 함수 (1·2번 협의 6절). 1번 uxm/browser.py(PR #2)가 develop에 들어오면 쓰지 않는다.

루프(uxm/agent.py)는 uxm.browser가 있으면 그것을, 없으면 이 파일을 쓴다. 함수 이름·입력·결과 모양은 협의 2절과 같다.
간단히 만든 것이라 1번 구현과 다른 점:
- click: 후보를 다시 찾지 않고 bbox 가운데를 누른다 (ELEMENT_NOT_FOUND/UNIQUE 판정 없음)
- page_signature: 계약 7.1 `{pageKey}_{상태}`. 상태는 testMode가 적용되는 화면(deadend→support, nochange→mypage)만
"""
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from uxm.candidates import DANGEROUS_WORDS, VIEWPORT, draw_probabilities

SCOPE_PREFIX = "/shop/"
TESTMODE_PAGE = {"deadend": "support", "nochange": "mypage"}   # 테스트 시나리오 C4·C6
_KST = timezone(timedelta(hours=9))


def _now() -> str:
    return datetime.now(_KST).isoformat(timespec="milliseconds")


def _path(url: str) -> str:
    return urlsplit(url).path.rstrip("/") or "/"


def _result(result, started, from_path, *, reason=None, executed=False, error=None, to_path=None) -> dict:
    return {"result": result, "reason": reason, "executed": executed, "error": error,
            "fromPath": from_path, "toPath": to_path, "startedAt": started, "finishedAt": _now()}


def page_key(url: str) -> str:
    path = _path(url)
    return path.rsplit("/", 1)[-1] if path.startswith(SCOPE_PREFIX) else path


def page_signature(page) -> str:
    key = page_key(page.url)
    mode = (parse_qs(urlsplit(page.url).query).get("testMode") or [None])[0]
    if mode is None:
        try:
            mode = page.evaluate("() => sessionStorage.getItem('testMode')")
        except Exception:
            mode = None
    return f"{key}_{mode}" if mode and TESTMODE_PAGE.get(mode) == key else f"{key}_default"


def open_browser(start_url: str, headless: bool = True) -> dict:
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        page.goto(start_url, wait_until="networkidle")
    except Exception as e:
        pw.stop()
        raise RuntimeError(f"브라우저를 시작하지 못함: {start_url} ({e})") from e
    return {"playwright": pw, "browser": browser, "context": context, "page": page}


def close_browser(session: dict) -> None:
    for key in ("context", "browser"):
        try:
            session[key].close()
        except Exception:
            pass
    try:
        session["playwright"].stop()
    except Exception:
        pass


def click(page, candidate: dict, timeout_ms: int = 10000) -> dict:
    started, from_path = _now(), _path(page.url)
    href, label, bbox = candidate.get("href") or "", candidate.get("label") or "", candidate.get("bbox")
    if not href.startswith(SCOPE_PREFIX):
        return _result("CLICK_FAILED", started, from_path, reason="OUT_OF_SCOPE", error=f"사이트 밖 링크: {href}")
    if any(w in label for w in DANGEROUS_WORDS):
        return _result("CLICK_FAILED", started, from_path, reason="REJECTED_UNSAFE", error=f"위험 동작: {label}")
    if not bbox:
        return _result("CLICK_FAILED", started, from_path, reason="ELEMENT_NOT_FOUND", error="bbox 없음")
    try:
        page.mouse.click(bbox["x"] + bbox["width"] / 2, bbox["y"] + bbox["height"] / 2)
    except Exception as e:
        return _result("CLICK_FAILED", started, from_path, reason="ACTION_ERROR", error=f"클릭 실패: {e}")
    try:
        page.wait_for_url(lambda u: _path(u) != from_path, timeout=timeout_ms)
    except PlaywrightTimeout:
        return _result("NO_CHANGE", started, from_path, executed=True, to_path=_path(page.url))
    except Exception as e:
        return _result("CLICK_FAILED", started, from_path, reason="ACTION_ERROR", executed=True, error=str(e))
    if not _path(page.url).startswith(SCOPE_PREFIX.rstrip("/")):
        return _result("CLICK_FAILED", started, from_path, reason="OUT_OF_SCOPE", executed=True,
                       error=f"사이트 밖으로 나감: {page.url}", to_path=_path(page.url))
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PlaywrightTimeout:
        return _result("CLICK_FAILED", started, from_path, reason="PAGE_TIMEOUT", executed=True,
                       error="화면 로딩 시간 초과", to_path=_path(page.url))
    return _result("NAVIGATED", started, from_path, executed=True, to_path=_path(page.url))


def go_back(page, expected_path: str, timeout_ms: int = 10000) -> dict:
    started, from_path = _now(), _path(page.url)
    try:
        resp = page.go_back(wait_until="networkidle", timeout=timeout_ms)
    except Exception as e:
        reason = "PAGE_TIMEOUT" if isinstance(e, PlaywrightTimeout) else "ACTION_ERROR"
        return _result("BACK_FAILED", started, from_path, reason=reason, executed=True, error=str(e),
                       to_path=_path(page.url))
    to_path = _path(page.url)
    if (resp is None and to_path == from_path) or not to_path.startswith(SCOPE_PREFIX.rstrip("/")):
        if to_path != from_path:
            try:
                page.go_forward(wait_until="networkidle", timeout=timeout_ms)
            except Exception:
                pass
        return _result("BACK_FAILED", started, from_path, reason="NO_HISTORY", executed=True,
                       error="돌아갈 화면이 없음", to_path=_path(page.url))
    if to_path != _path(expected_path):
        return _result("BACK_FAILED", started, from_path, reason="WRONG_DESTINATION", executed=True,
                       error=f"{expected_path}가 아니라 {to_path}", to_path=to_path)
    return _result("NAVIGATED", started, from_path, executed=True, to_path=to_path)


def reload(page, timeout_ms: int = 10000) -> dict:
    started, from_path = _now(), _path(page.url)
    try:
        page.reload(wait_until="networkidle", timeout=timeout_ms)
    except Exception as e:
        reason = "PAGE_TIMEOUT" if isinstance(e, PlaywrightTimeout) else "ACTION_ERROR"
        return _result("CLICK_FAILED", started, from_path, reason=reason, executed=True, error=str(e),
                       to_path=_path(page.url))
    return _result("NAVIGATED", started, from_path, executed=True, to_path=_path(page.url))


def capture(page, run_dir: str, step_id: int, candidates: list, probs: dict | None = None,
            chosen: str | None = None) -> dict:
    raw_ref, overlay_ref = f"screenshots/{step_id}.png", f"screenshots/{step_id}_overlay.png"
    out = {"screenshotRef": None, "overlayRef": None, "viewport": page.viewport_size,
           "scroll": {"x": 0, "y": 0}, "screenshotSize": None, "capturedAt": _now(), "error": None}
    try:
        os.makedirs(os.path.join(run_dir, "screenshots"), exist_ok=True)
        out["scroll"] = page.evaluate("() => ({x: scrollX, y: scrollY})")
        png = page.screenshot(path=os.path.join(run_dir, *raw_ref.split("/")))
        out["screenshotRef"] = raw_ref
        out["screenshotSize"] = {"width": int.from_bytes(png[16:20], "big"), "height": int.from_bytes(png[20:24], "big")}
        if probs is not None:
            try:
                draw_probabilities(page, [c for c in candidates if c.get("bbox")], probs, chosen)
                page.screenshot(path=os.path.join(run_dir, *overlay_ref.split("/")))
                out["overlayRef"] = overlay_ref
            finally:
                page.evaluate("() => document.querySelectorAll('.uxm-overlay').forEach(e => e.remove())")
    except Exception as e:
        out["error"] = f"화면 저장 실패: {e}"
    return out
