"""시나리오 테스트 사이트 (#74, 팀 문서 「외부 사이트 시나리오 테스트」 4.3).

버전 비교(MORO MARKET v1/v2)와 별도로, 등록한 외부 사이트 1곳에서 Run 1개를 돌린다.
사이트 설정은 `config/sites/<siteId>.json`이고 탐색 범위는 `uxm/site_scope.py`(#71)가 정한다.

- 요청은 `siteId`만 받는다. 임의 주소는 받지 않는다 (등록된 설정만 실행).
- MORO MARKET(`config/sites/moro-market.json`)은 버전 비교용이라 시나리오 사이트 목록에 넣지 않는다.
  `siteId`가 없으면 지금까지처럼 MORO MARKET 버전 비교 Run이다.
- 내부 버전 값(`meta`)은 응답에 넣지 않는다. 사이트 주소는 FE가 사용자가 입력한 링크로 등록 사이트를 찾을 수
  있게 `siteUrl`·`allowedHosts`로 내려 준다(#95). 공개 주소일 때만 내려 주고(`public_site_url`), 실행은 여전히
  등록된 `siteId`로만 한다. Optimizer Site Instance의 내부 주소와 환경변수 값은 여기서 다루지 않는다.
"""
import ipaddress
import logging
import os
from urllib.parse import urlsplit

from api.errors import ApiError
from uxm import sitemap
from uxm.site_scope import SiteScope, load_scope

log = logging.getLogger("uxm.api.sites")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIO_MODE = "SCENARIO"


def _invalid(message: str) -> ApiError:
    return ApiError(422, "CONTRACT_VALIDATION_ERROR", message)


def resolve_site(site_id: str | None) -> SiteScope | None:
    """요청의 siteId → 탐색 범위. 없으면 None (MORO MARKET 버전 비교). 등록되지 않은 사이트면 422."""
    if site_id is None:
        return None
    if site_id == sitemap.SHOP.name:
        raise _invalid(f"{site_id}는 버전 비교용 사이트입니다. siteId 없이 실행해 주세요.")
    try:
        return load_scope(site_id)
    except ValueError as e:
        log.warning("siteId=%s: %s", site_id, e)
        raise _invalid(f"등록되지 않은 사이트입니다: {site_id}") from e


def scenario_site_ids() -> list[str]:
    """`config/sites/`에서 시나리오에 쓸 수 있는 사이트 (MORO MARKET 제외, 설정이 올바른 것만)."""
    ids = []
    for name in sorted(os.listdir(sitemap.SITES_DIR)):
        site_id, ext = os.path.splitext(name)
        if ext != ".json" or site_id == sitemap.SHOP.name:
            continue
        try:
            load_scope(site_id)
        except ValueError as e:
            log.warning("시나리오 사이트 설정을 건너뜁니다: %s", e)
            continue
        ids.append(site_id)
    return ids


def site_map_of(scope: SiteScope) -> dict | None:
    """그 사이트의 지도 (없거나 검증 실패면 None)."""
    if not scope.config.out:
        return None
    try:
        return sitemap.load_site_map(os.path.join(ROOT, scope.config.out))
    except sitemap.SiteMapError:
        return None


GOAL_UNAVAILABLE_REASONS = ("START_PAGE", "NO_GOAL_TEXT", "UNREACHABLE_ON_SITE_MAP")


def unreachable_pages(scope: SiteScope, site_map: dict | None) -> list[str] | None:
    """지도상 시작 화면에서 닿지 않는 화면. 지도를 만들 때 기록한 `unreachablePages`(#71)를 그대로 쓴다.

    그 값이 없는 지도(모든 화면이 닿아야 하는 사이트)는 빈 목록. 지도가 없으면 None.
    """
    if site_map is None:
        return None
    recorded = set(site_map.get("unreachablePages") or [])
    return [k for k in scope.config.pages if k in recorded or (k != scope.config.start_page and k not in site_map["pages"])]


DEFAULT_PORTS = {"http": 80, "https": 443}


def _is_public_host(host: str) -> bool:
    """응답에 내보내도 되는 호스트인지: 내 컴퓨터(`localhost`·루프백)·사설망·링크 로컬 등 공인 주소가 아닌 IP는 제외.

    `siteUrl`과 `allowedHosts`가 같은 판정을 쓴다 (#97 리뷰).
    """
    host = (host or "").strip().strip("[]").lower()
    if not host or host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True   # 도메인 이름


def public_allowed_hosts(scope: SiteScope) -> list[str]:
    """응답에 내려 줄 허용 도메인. 공개 호스트만 (내부 IP가 이 필드로 새지 않게)."""
    out = []
    for h in scope.config.allowed_hosts:
        h = h.strip().strip("[]").lower()
        if _is_public_host(h) and h not in out:
            out.append(h)
    return out


def public_site_url(scope: SiteScope) -> str | None:
    """응답에 내려 줄 사이트 주소. 공개 주소만: http(s), 호스트가 `allowedHosts`에 있고, 공개 호스트여야 한다.

    조건이 안 맞으면 None (예: 내부 서버를 사이트로 등록한 경우에도 그 주소가 응답에 나가지 않게).
    경로·쿼리·인증 정보는 떼고 `scheme://host[:port]`만 돌려준다. IPv6는 대괄호로 감싸고 기본 포트(80·443)는 뺀다.
    """
    try:
        u = urlsplit(scope.base_url)
        host = (u.hostname or "").lower()
        port = u.port
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not host or u.username or u.password:
        return None
    if host not in public_allowed_hosts(scope) or not _is_public_host(host):
        return None
    shown = f"[{host}]" if ":" in host else host
    return f"{u.scheme}://{shown}" + (f":{port}" if port and port != DEFAULT_PORTS[u.scheme] else "")


def public_site(scope: SiteScope) -> dict:
    """`GET /api/sites` 항목.

    `goalSelectable`: 도착 화면으로 고를 수 있는지 = 확인 글자(`goalText`)가 있고, 시작 화면이 아니고, 지도가 있으면
    시작 화면에서 닿는 화면. 지도상 닿지 않는 화면을 도착 화면으로 보내면 실행 전에 422가 난다
    (`run_factory.site_map_reference`). 지도가 없으면 닿는지 판단하지 않는다.
    `goalUnavailableReason`: 고를 수 없는 이유 (`START_PAGE` / `NO_GOAL_TEXT` / `UNREACHABLE_ON_SITE_MAP`), 고를 수 있으면 null.
    `UNREACHABLE_ON_SITE_MAP`은 "시작 화면에 보이는 링크로는 닿지 않는 화면"이라는 사이트 지도 진단 결과다.
    `siteMap.unreachablePages`: 지도가 기록한 닿지 않는 화면 목록 (지도가 없으면 null).
    `siteUrl`·`allowedHosts`: 사용자가 입력한 링크로 이 사이트를 찾는 용도(#95). 둘 다 공개 호스트만 (siteUrl은 아니면 null).
    """
    config = scope.config
    site_map = site_map_of(scope)
    unreachable = unreachable_pages(scope, site_map)
    pages = []
    for key, path in config.pages.items():
        goal_text = config.page_texts.get(key)
        reason = ("START_PAGE" if key == config.start_page else "NO_GOAL_TEXT" if not goal_text
                  else "UNREACHABLE_ON_SITE_MAP" if key in (unreachable or ()) else None)
        pages.append({"pageKey": key, "path": path, "label": config.page_labels.get(key, key), "goalText": goal_text,
                      "goalSelectable": reason is None, "goalUnavailableReason": reason})
    return {"siteId": config.name, "name": config.display_name, "mode": SCENARIO_MODE,
            "siteUrl": public_site_url(scope), "allowedHosts": public_allowed_hosts(scope),
            "startPage": config.start_page, "pages": pages,
            "siteMap": {"available": site_map is not None,
                        "siteMapVersion": site_map["siteMapVersion"] if site_map else None,
                        "unreachablePages": unreachable}}


def check_page(scope: SiteScope, path: str, what: str) -> str:
    """경로가 그 사이트에 등록된 화면의 경로와 **정확히 같으면** pageKey, 아니면 422.

    쿼리가 붙은 경로(`/board/review/list.html?board_no=5`)는 화면 구분(쿼리 무시)으로는 같은 화면이지만,
    Run 기록에 다른 주소가 남지 않도록 받지 않는다. 화면은 실행할 때 설정의 여는 주소(`open`)로 연다.
    """
    key = scope.registered_key(path) if isinstance(path, str) and path.startswith("/") else None
    if key is not None and path != scope.config.pages[key]:
        raise _invalid(f"{what}({path})은 쿼리 없는 등록 경로로 보내야 합니다: {scope.config.pages[key]}")
    if key is None:
        labels = ", ".join(f"{k}({p})" for k, p in scope.config.pages.items())
        raise _invalid(f"{what}({path})은 이 사이트에 등록된 화면이 아닙니다. 등록된 화면: {labels}")
    return key


def page_url(scope: SiteScope, key: str) -> str:
    """그 화면을 열 주소 (게시판처럼 쿼리가 있어야 열리는 화면은 설정의 `open`)."""
    return scope.base_url.rstrip("/") + scope.config.open_path(key)
