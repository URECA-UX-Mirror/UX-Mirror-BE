"""직접 입력 목표의 실행 전 검사 (「남은 일과 분업」 7번, 사이트 지도 설계 v2.1 5장, FE OI-9).

직접 입력한 성공 조건이 실제 화면과 맞지 않으면 Run은 도착하고도 실패로 끝난다.
그래서 Run을 만들기 전에 Before·After 두 쇼핑몰에서 도착 화면을 직접 열어 확인한다.

- 도착 화면이 등록된 화면이 아니거나, 열었더니 다른 주소로 바뀌면 → 422
- 화면은 열었는데 성공 조건 글자가 보이지 않으면 → 422 (어느 쪽인지 메시지에 표시)
- 화면을 열지 못하면(쇼핑몰 꺼짐·응답 없음·브라우저 오류) → 503 BROWSER_ERROR, 재시도 가능
  응답이 없을 때 요청이 오래 멈추지 않도록 화면마다 PAGE_TIMEOUT_MS까지만 기다린다 (Before·After 합쳐 최대 약 20초).
글자 판정은 루프와 같은 `uxm.goal.verify_goal`(exact·scope 포함)을 쓴다.
"""
import logging

from api.errors import ApiError
from api.schemas import SHOP_PAGES
from uxm.goal import path_matches, verify_goal

log = logging.getLogger("uxm.api.preflight")

LABELS = {"before": "Before", "after": "After"}
PAGE_TIMEOUT_MS = 10_000   # 루프의 화면 로딩 제한(browser.click 등)과 같은 10초


def _invalid(message: str) -> ApiError:
    return ApiError(422, "CONTRACT_VALIDATION_ERROR", message)


def check_goal_screens(verification: dict, base_urls: dict, browser) -> None:
    """두 쇼핑몰(`base_urls` = {"before": url, "after": url})에서 성공 조건을 확인한다. 문제가 있으면 ApiError.

    browser: `(open_browser, close_browser)` (`Integrations.browser()`).
    """
    path = verification["path"]
    page_key = path.rstrip("/").removeprefix("/shop/")
    if page_key not in SHOP_PAGES:
        raise _invalid(f"도착 화면({path})은 쇼핑몰에 없는 화면입니다. "
                       f"/shop/{{{','.join(SHOP_PAGES)}}} 중 하나여야 합니다.")
    open_browser, close_browser = browser
    for variant in ("before", "after"):
        label = LABELS[variant]
        try:
            session = open_browser(base_urls[variant].rstrip("/") + path, timeout_ms=PAGE_TIMEOUT_MS)
        except Exception as e:   # 쇼핑몰이 꺼져 있거나 응답이 없거나 브라우저를 띄우지 못함
            log.warning("preflight: %s 도착 화면을 열지 못함: %s", variant, e)
            raise ApiError(503, "BROWSER_ERROR", f"{label} 도착 화면을 읽지 못했습니다", retryable=True) from e
        try:
            page = session["page"]
            if not path_matches(page.url, path):
                raise _invalid(f"{label} 도착 화면({path})을 열면 다른 화면으로 이동합니다. 실제로 열리는 화면이 아닙니다.")
            reached, evidence = verify_goal(page, verification)
        except ApiError:
            raise
        except Exception as e:   # 화면은 열렸지만 글자를 읽는 중 브라우저 오류
            log.warning("preflight: %s 도착 화면을 읽지 못함: %s", variant, e)
            raise ApiError(503, "BROWSER_ERROR", f"{label} 도착 화면을 읽지 못했습니다", retryable=True) from e
        finally:
            close_browser(session)
        if not reached:
            exact = " (정확 일치)" if verification.get("exact") else ""
            where = " <main> 안" if verification.get("scope") == "main" else ""
            raise _invalid(f"{label} 도착 화면({path}){where}에 '{verification['text']}' 글자가 없습니다{exact}.")
        log.info("preflight: %s %s OK (%s)", variant, path, evidence)
