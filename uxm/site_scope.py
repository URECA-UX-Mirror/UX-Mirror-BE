"""사이트별 탐색 범위 (#67, 외부 사이트 시나리오 테스트).

후보 추출(`uxm/candidates.py`)·화면 구분과 클릭 범위(`uxm/browser.py`)가 "어느 링크가 이동 후보인지, 화면 이름이 무엇인지"를
정할 때 쓰는 규칙을 한곳에 모았다.

- **사이트를 넘기지 않으면(`scope=None`) MORO MARKET 기존 규칙 그대로**다 (`/shop/` 링크, 시작 화면 `/shop/home`).
  candidates·browser는 `scope`가 None일 때 지금까지의 코드를 그대로 실행하므로 버전 비교·E 결과가 바뀌지 않는다.
- `SiteScope(config)`는 사이트 설정(`config/sites/*.json`)의 **등록된 화면(`pages`)으로 가는 링크만** 허용한다 (리뷰 1).
  `pathPrefix`·`allowedHosts`만으로는 범위가 넓어서, 등록 목록이 실제 경계다. 클릭 뒤 도착한 주소(리다이렉트 포함)도
  같은 규칙으로 확인해 등록 밖이면 `browser.click`이 `OUT_OF_SCOPE`를 돌려주고 루프가 되돌아간다.
- 상단 메뉴를 후보로 넣을 화면은 사이트별 정책(`globalNavPolicy`, 리뷰 2): MORO MARKET은 `START_ONLY`(기존 실험 조건),
  상단 메뉴가 모든 화면에 있는 외부 사이트는 `ALL_PAGES`.

후보의 `href`는 외부 사이트에서 **도메인을 뺀 정규화 경로 + 쿼리**(`/board/review/list.html?board_no=4`)로 저장한다.
상대 주소(`about.htm`)·세션 꼬리(`;jsessionid=…`)가 섞여도 semanticKey와 화면 구분이 같은 값이 되게 하기 위해서다.
"""
import json
import os
from urllib.parse import urlsplit

from uxm.sitemap import SITES_DIR, SiteConfig, SiteMapError, site_config_from_dict, url_path


def _norm(path: str) -> str:
    """경로 정규화: 경로 매개변수·끝 `/` 제거, 루트는 `/`."""
    return url_path(path) or "/"


class SiteScope:
    """등록된 화면만 허용하는 탐색 범위 (외부 사이트용)."""

    def __init__(self, config: SiteConfig):
        self.config = config
        self.site_id = config.name
        self.hosts = tuple(config.allowed_hosts)
        self._by_path = {_norm(p): key for key, p in config.pages.items()}
        self.start_path = _norm(config.pages[config.start_page])

    # ---- 화면 구분
    def registered_key(self, url: str | None) -> str | None:
        """주소 → 등록된 화면 pageKey. 다른 도메인이거나 등록되지 않은 화면이면 None. 쿼리는 무시."""
        if not url:
            return None
        u = urlsplit(url)
        if u.netloc and u.netloc not in self.hosts:
            return None
        return self._by_path.get(_norm(u.path))

    def page_key(self, url: str) -> str:
        """화면 이름표. 등록된 화면이면 그 pageKey, 아니면 정규화 경로 (등록 밖 화면은 탐색하지 않으므로 기록용)."""
        return self.registered_key(url) or _norm(urlsplit(url).path)

    # ---- 범위
    def link_allowed(self, href: str | None) -> bool:
        """후보 href(정규화 경로 또는 주소)가 등록된 화면으로 가는지. 클릭 직전 확인용."""
        return self.registered_key(href) is not None

    def in_scope(self, url: str, origin: str) -> bool:
        """도착한 주소가 같은 사이트의 등록된 화면인지 (클릭·뒤로가기 뒤 리다이렉트 포함 확인)."""
        u = urlsplit(url)
        return f"{u.scheme}://{u.netloc}" == origin and self.registered_key(url) is not None

    def global_nav_allowed(self, path: str) -> bool:
        """현재 화면에서 상단 메뉴를 후보로 넣는지 (리뷰 2)."""
        return self.config.global_nav_policy == "ALL_PAGES" or _norm(path) == self.start_path

    def js_args(self) -> dict:
        """후보 추출 JS에 넘길 규칙. 등록된 경로 목록과 허용 도메인."""
        return {"mode": "pages", "hosts": list(self.hosts), "paths": sorted(self._by_path)}


def site_config_path(site_id: str) -> str:
    return os.path.join(SITES_DIR, f"{site_id}.json")


def load_scope(site_id: str | None) -> SiteScope | None:
    """`siteId` → SiteScope. 없으면 None(= MORO MARKET 기존 규칙). 등록되지 않은 사이트·잘못된 설정이면 ValueError.

    사이트 설정 파일 이름과 안의 `name`이 같아야 한다 (요청의 siteId로 다른 사이트 설정을 읽지 않게).
    """
    if not site_id:
        return None
    if not isinstance(site_id, str) or not site_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"siteId 형식이 올바르지 않습니다: {site_id!r}")
    path = site_config_path(site_id)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise ValueError(f"등록되지 않은 사이트입니다: {site_id}") from e
    except ValueError as e:
        raise ValueError(f"사이트 설정을 읽을 수 없습니다: {site_id} ({e})") from e
    try:
        config = site_config_from_dict(data)
    except SiteMapError as e:
        raise ValueError(f"사이트 설정이 올바르지 않습니다: {site_id} ({e})") from e
    if config.name != site_id:
        raise ValueError(f"사이트 설정 이름({config.name})이 siteId({site_id})와 다릅니다")
    return SiteScope(config)
