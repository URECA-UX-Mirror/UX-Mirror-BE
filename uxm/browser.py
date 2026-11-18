"""브라우저 조작 (1번 소유). 2번(agent)은 이 함수들만 불러 쓴다.

합의: ../docs/12_1번_2번_협의_최종.md (2026-11-16 v3)

- 브라우저를 만지는 건 전부 여기서, 판단·흐름·기록은 2번
- click / go_back / reload는 예외를 던지지 않고 ActionResult(dict)로 돌려준다
- click / go_back / reload는 다음 화면이 다 뜬 뒤(networkidle) 돌아온다
- sync Playwright라서 한 Run은 같은 스레드에서 open_browser ~ close_browser까지 진행한다

ActionResult
    result    NAVIGATED | NO_CHANGE | CLICK_FAILED | BACK_FAILED
    reason    None | ELEMENT_NOT_FOUND | ELEMENT_NOT_UNIQUE | REJECTED_UNSAFE | OUT_OF_SCOPE
              | PAGE_TIMEOUT | ACTION_ERROR | NO_HISTORY | WRONG_DESTINATION
    executed  실제로 클릭/뒤로가기/새로고침을 했는지
    error     사람이 읽는 문구 (성공이면 None)
    fromPath, toPath, startedAt, finishedAt

외부 사이트 (#67): 함수마다 `scope`(`uxm/site_scope.SiteScope`)를 받는다. 넘기면 클릭 범위·도착 주소 확인·화면 이름표가
사이트 설정의 등록된 화면 기준이고, None이면 지금까지의 `/shop/` 규칙 그대로다 (MORO MARKET).
"""
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from uxm.candidates import CLICKABLE_SELECTOR, DANGEROUS_WORDS, VIEWPORT, draw_probabilities, extract

SCOPE_PREFIX = "/shop/"   # 이 밖으로는 누르지 않는다 (기획안 7.8)
BBOX_TOLERANCE_PX = 2     # 후보 추출 때와 클릭 직전의 위치 차이 허용치
_KST = timezone(timedelta(hours=9))


def _now() -> str:
    return datetime.now(_KST).isoformat(timespec="milliseconds")


def _path(url: str) -> str:
    return urlsplit(url).path.rstrip("/") or "/"


def _in_scope(url: str, origin: str, scope=None) -> bool:
    if scope is not None:
        return scope.in_scope(url, origin)
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}" == origin and (_path(url) + "/").startswith(SCOPE_PREFIX)


def _link_allowed(href: str, scope=None, current_url: str | None = None) -> bool:
    """클릭해도 되는 링크인지. 외부 사이트는 실제 현재 주소 기준으로 #70 안전 검사."""
    return scope.link_allowed(href, current_url) if scope is not None else href.startswith(SCOPE_PREFIX)


def _nav_key(url: str, scope=None) -> str:
    """클릭 뒤 "화면이 바뀌었는지" 비교할 값. MORO는 경로, 외부 사이트는 경로+쿼리 (#71 리뷰 1:
    같은 경로·다른 쿼리로 리다이렉트돼도 바뀐 것으로 보고 범위를 검사한다)."""
    return scope.nav_key(url) if scope is not None else _path(url)


def _origin(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}"


def _result(result, started, from_path, *, reason=None, executed=False, error=None, to_path=None) -> dict:
    return {"result": result, "reason": reason, "executed": executed, "error": error,
            "fromPath": from_path, "toPath": to_path, "startedAt": started, "finishedAt": _now()}


# ---------------------------------------------------------------- 화면 이름표

def page_key(url: str, scope=None) -> str:
    """Before/After 공통 논리 페이지. /shop/orders?testMode=x → orders. 외부 사이트는 사이트 설정의 pageKey."""
    if scope is not None:
        return scope.page_key(url)
    path = _path(url)
    return path.rsplit("/", 1)[-1] if path.startswith(SCOPE_PREFIX) else path


TESTMODE_PAGE = {"deadend": "support", "nochange": "mypage"}   # testMode가 화면을 바꾸는 곳 (테스트 시나리오 C4·C6)


def _test_mode(page) -> str | None:
    """첫 URL의 ?testMode= 또는 쇼핑몰이 sessionStorage에 유지한 값."""
    mode = (parse_qs(urlsplit(page.url).query).get("testMode") or [None])[0]
    if mode is None:
        try:
            mode = page.evaluate("() => sessionStorage.getItem('testMode')")
        except Exception:
            mode = None
    return mode


def page_signature(page, scope=None) -> str:
    """Run 내부 재방문 판단용 `{pageKey}_{상태}` (계약 7.1).

    상태는 testMode가 실제로 적용되는 화면이면 그 값(deadend, nochange), 아니면 default.
    예: home_default, support_deadend. Backend 전용 값이라 모델 입력에는 들어가지 않는다.
    """
    key = page_key(page.url, scope)
    mode = _test_mode(page)
    return f"{key}_{mode}" if mode and TESTMODE_PAGE.get(mode) == key else f"{key}_default"


# ---------------------------------------------------------------- 열기 / 닫기

def open_browser(start_url: str, headless: bool = True, timeout_ms: int | None = None) -> dict:
    """Run마다 새 브라우저. 쇼핑몰 서버가 꺼져 있는 등 시작 자체가 안 되면 RuntimeError (Run을 시작할 수 없음).

    timeout_ms: 첫 화면 로딩 제한. None이면 Playwright 기본값(30초). 실행 전 검사는 짧게 준다.
    """
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        page.goto(start_url, wait_until="networkidle", timeout=timeout_ms)
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


# ---------------------------------------------------------------- 클릭

def _same_bbox(a: dict, b: dict) -> bool:
    return all(abs(a[k] - b[k]) <= BBOX_TOLERANCE_PX for k in ("x", "y", "width", "height"))


def _find_target(page, candidate: dict, scope=None) -> list[dict]:
    """클릭 직전에 후보를 다시 뽑아 같은 목적지·글자·위치인 요소를 찾는다 (extract와 같은 규칙)."""
    fresh = extract(page, keep_dom_index=True, scope=scope)["candidates"]
    return [c for c in fresh if c["href"] == candidate["href"] and c["label"] == candidate["label"]
            and _same_bbox(c["bbox"], candidate["bbox"])]


def click(page, candidate: dict, timeout_ms: int = 10000, scope=None) -> dict:
    started, from_url = _now(), page.url
    from_path = _path(from_url)
    href = candidate.get("href") or ""
    label = candidate.get("label") or ""

    if not _link_allowed(href, scope, from_url):
        return _result("CLICK_FAILED", started, from_path, reason="OUT_OF_SCOPE",
                       error=f"테스트 사이트 밖 링크라 누르지 않음: {href or '(없음)'}")
    if any(w in label for w in DANGEROUS_WORDS):
        return _result("CLICK_FAILED", started, from_path, reason="REJECTED_UNSAFE",
                       error=f"위험 동작이라 누르지 않음: {label}")
    if not candidate.get("bbox"):
        return _result("CLICK_FAILED", started, from_path, reason="ELEMENT_NOT_FOUND",
                       error="위치 정보가 없는 후보는 클릭할 수 없음 (GO_BACK은 go_back 사용)")

    try:
        matches = _find_target(page, candidate, scope)
    except Exception as e:
        return _result("CLICK_FAILED", started, from_path, reason="ACTION_ERROR", error=f"후보 재확인 실패: {e}")
    if not matches:
        return _result("CLICK_FAILED", started, from_path, reason="ELEMENT_NOT_FOUND",
                       error=f"같은 위치·글자의 요소를 찾지 못함: {label}")
    if len(matches) > 1:
        return _result("CLICK_FAILED", started, from_path, reason="ELEMENT_NOT_UNIQUE",
                       error=f"같은 위치·글자의 요소가 {len(matches)}개라 누르지 않음: {label}")

    try:
        page.locator(CLICKABLE_SELECTOR).nth(matches[0]["domIndex"]).click(timeout=timeout_ms)
    except Exception as e:
        return _result("CLICK_FAILED", started, from_path, reason="ACTION_ERROR", error=f"클릭 실패: {e}")

    # 주소가 바뀌는지 기다린다. 안 바뀌면 NO_CHANGE (외부 사이트는 경로+쿼리 기준)
    origin = _origin(from_url)
    from_key = _nav_key(from_url, scope)
    try:
        page.wait_for_url(lambda u: _nav_key(u, scope) != from_key, timeout=timeout_ms)
    except PlaywrightTimeout:
        if scope is not None and not _in_scope(page.url, origin, scope):   # NO_CHANGE 전에도 실제 도착 주소 확인
            return _result("CLICK_FAILED", started, from_path, reason="OUT_OF_SCOPE", executed=True,
                           error=f"누른 뒤 등록 밖 주소에 있음 ({scope.arrival_rejection(page.url)}): {page.url}",
                           to_path=_path(page.url))
        return _result("NO_CHANGE", started, from_path, executed=True, to_path=_path(page.url))
    except Exception as e:   # 브라우저가 죽는 등 시간 초과가 아닌 오류를 NO_CHANGE로 숨기지 않는다
        return _result("CLICK_FAILED", started, from_path, reason="ACTION_ERROR", executed=True,
                       error=f"클릭 후 화면 확인 실패: {e}")

    if not _in_scope(page.url, origin, scope):   # 리다이렉트로 등록 밖 화면에 가면 OUT_OF_SCOPE → 루프가 되돌아감
        why = f" ({scope.arrival_rejection(page.url)})" if scope is not None else ""
        return _result("CLICK_FAILED", started, from_path, reason="OUT_OF_SCOPE", executed=True,
                       error=f"누른 뒤 테스트 사이트 밖으로 나감{why}: {page.url}", to_path=_path(page.url))
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PlaywrightTimeout:
        return _result("CLICK_FAILED", started, from_path, reason="PAGE_TIMEOUT", executed=True,
                       error=f"{timeout_ms}ms 안에 화면 로딩이 끝나지 않음", to_path=_path(page.url))
    except Exception as e:
        return _result("CLICK_FAILED", started, from_path, reason="ACTION_ERROR", executed=True,
                       error=f"화면 로딩 확인 실패: {e}", to_path=_path(page.url))
    return _result("NAVIGATED", started, from_path, executed=True, to_path=_path(page.url))


# ---------------------------------------------------------------- 뒤로 가기 / 새로고침

def go_back(page, expected_path: str, timeout_ms: int = 10000, scope=None) -> dict:
    """돌아갈 주소(expected_path)는 2번이 넘긴다. 실제로 그 주소에 도착했을 때만 NAVIGATED."""
    started, from_url = _now(), page.url
    from_path = _path(from_url)
    try:
        resp = page.go_back(wait_until="networkidle", timeout=timeout_ms)
    except Exception as e:
        reason = "PAGE_TIMEOUT" if isinstance(e, PlaywrightTimeout) else "ACTION_ERROR"
        return _result("BACK_FAILED", started, from_path, reason=reason, executed=True,
                       error=f"뒤로가기 실패: {e}", to_path=_path(page.url))
    to_path = _path(page.url)
    if resp is None and to_path == from_path:
        return _result("BACK_FAILED", started, from_path, reason="NO_HISTORY", executed=True,
                       error="브라우저 기록이 없어 돌아갈 화면이 없음", to_path=to_path)
    if not _in_scope(page.url, _origin(from_url), scope):
        # 새 탭의 about:blank 등 Run 이전 기록으로 빠졌다 → 원래 화면으로 되돌리고 기록 없음으로 처리 (기획안 7.4.1)
        try:
            page.go_forward(wait_until="networkidle", timeout=timeout_ms)
        except Exception:
            pass
        return _result("BACK_FAILED", started, from_path, reason="NO_HISTORY", executed=True,
                       error="Run 안에서 돌아갈 화면이 없음 (실행 전 기록으로는 돌아가지 않음)",
                       to_path=_path(page.url))
    if to_path != _path(expected_path):
        return _result("BACK_FAILED", started, from_path, reason="WRONG_DESTINATION", executed=True,
                       error=f"{expected_path}로 돌아가야 하는데 {to_path}에 도착", to_path=to_path)
    return _result("NAVIGATED", started, from_path, executed=True, to_path=to_path)


def reload(page, timeout_ms: int = 10000) -> dict:
    """PAGE_TIMEOUT 복구용. 현재 화면을 다시 불러와 로딩 완료를 확인한다."""
    started, from_path = _now(), _path(page.url)
    try:
        page.reload(wait_until="networkidle", timeout=timeout_ms)
    except Exception as e:
        reason = "PAGE_TIMEOUT" if isinstance(e, PlaywrightTimeout) else "ACTION_ERROR"
        return _result("CLICK_FAILED", started, from_path, reason=reason, executed=True,
                       error=f"새로고침 실패: {e}", to_path=_path(page.url))
    return _result("NAVIGATED", started, from_path, executed=True, to_path=_path(page.url))


# ---------------------------------------------------------------- 화면 저장

def _png_size(png: bytes) -> dict:
    return {"width": int.from_bytes(png[16:20], "big"), "height": int.from_bytes(png[20:24], "big")}


def capture(page, run_dir: str, step_id: int, candidates: list, probs: dict | None = None,
            chosen: str | None = None) -> dict:
    """원본 스크린샷 + (probs가 있으면) 확률 배지 스크린샷. 경로는 run_dir 기준 상대 경로.

    - bbox 없는 후보(GO_BACK)는 그리지 않는다
    - probs=None이면 원본만 (도착 화면용)
    - 배지는 찍은 뒤 바로 지운다 (다음 후보 추출에 섞이지 않게)
    """
    raw_ref, overlay_ref = f"screenshots/{step_id}.png", f"screenshots/{step_id}_overlay.png"
    out = {"screenshotRef": None, "overlayRef": None, "viewport": None,   # 경로는 실제로 저장된 것만 채운다
           "scroll": None, "screenshotSize": None, "capturedAt": _now(), "error": None}
    try:
        os.makedirs(os.path.join(run_dir, "screenshots"), exist_ok=True)
        out["viewport"] = page.viewport_size
        out["scroll"] = page.evaluate("() => ({x: scrollX, y: scrollY})")
        png = page.screenshot(path=os.path.join(run_dir, raw_ref))
        out["screenshotRef"], out["screenshotSize"] = raw_ref, _png_size(png)
        if probs is not None:
            try:
                draw_probabilities(page, [c for c in candidates if c.get("bbox")], probs, chosen)
                page.screenshot(path=os.path.join(run_dir, overlay_ref))
                out["overlayRef"] = overlay_ref
            finally:
                _clear_overlay(page)
    except Exception as e:
        out["error"] = f"화면 저장 실패: {e}"
    return out


def _clear_overlay(page) -> None:
    try:
        page.evaluate("() => document.querySelectorAll('.uxm-overlay').forEach(e => e.remove())")
    except Exception:
        pass
