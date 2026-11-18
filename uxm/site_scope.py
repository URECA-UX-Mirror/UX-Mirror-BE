"""사이트별 탐색 범위 (#67, 외부 사이트 시나리오 테스트).

후보 추출(`uxm/candidates.py`)·화면 구분과 클릭 범위(`uxm/browser.py`)가 "어느 링크가 이동 후보인지, 화면 이름이 무엇인지"를
정할 때 쓰는 규칙을 한곳에 모았다.

- **사이트를 넘기지 않으면(`scope=None`) MORO MARKET 기존 규칙 그대로**다 (`/shop/` 링크, 시작 화면 `/shop/home`).
  candidates·browser는 `scope`가 None일 때 지금까지의 코드를 그대로 실행하므로 버전 비교·E 결과가 바뀌지 않는다.
- `SiteScope(config)`는 사이트 설정(`config/sites/*.json`)에 **등록된 주소로 가는 링크만** 허용한다 (리뷰 1).

허용 판정은 안전 검사 모듈(#70 `uxm/scenario_safety.ScenarioSafety`)에 맡긴다. 규칙은 그 모듈 한 곳에만 있다.
  - 같은 출처(scheme·host·port)만. 사이트 주소는 설정 `variants`의 주소
  - 등록 주소 = 화면마다 `open`(경로+쿼리, 없으면 `path`). **경로와 쿼리가 모두 같아야** 허용 (쿼리 추가·누락·변경 거부)
  - 모호한 경로(`//`, `\\`, `.`·`..`, 인코딩한 `/`, 제어 문자)와 위험 경로(`/exec/`, 주문서, 로그아웃)는 거부
  - `onclick`·`download`·새 창(`target`)·`role=button`이 있는 링크, 위험 라벨(구매·주문·장바구니 담기·로그아웃 등)은 거부
  - 목적지가 현재 화면과 같으면 이동이 아니다(`NOT_NAVIGATION`) → 링크 검사에는 **실제 현재 주소**를 넘긴다

화면 이름(pageKey)은 **경로 기준**이다. 성공 조건·지도·경로 기준(`referencePathConfig`)이 경로로 화면을 부르기 때문이다.
등록 밖 쿼리의 화면은 위 허용 검사에서 막혀 탐색 중에 들어가지 않는다.

상단 메뉴를 후보로 넣을 화면은 사이트별 정책(`globalNavPolicy`, 리뷰 2).
후보의 `href`는 외부 사이트에서 **도메인을 뺀 경로 + 쿼리**(`/board/review/list.html?board_no=4`)로 저장한다.
"""
import json
import os
from urllib.parse import urljoin, urlsplit

from uxm.scenario_safety import ScenarioSafety
from uxm.scenario_safety import _origin, _url_parts   # 같은 정규화를 쓰기 위해 (#70 모듈의 규칙 그대로)
from uxm.sitemap import SITES_DIR, SiteConfig, SiteMapError, site_config_from_dict, url_path


def _canonical(parts: tuple[str, tuple]) -> str:
    """후보 추출 JS와 비교할 문자열: 경로 + 디코딩한 쿼리 쌍 (순서 유지)."""
    path, query = parts
    return path + ("?" + "&".join(f"{k}={v}" for k, v in query) if query else "")


class SiteScope:
    """등록된 주소만 허용하는 탐색 범위 (외부 사이트용). 허용 판정은 `ScenarioSafety`(#70)."""

    def __init__(self, config: SiteConfig):
        self.config = config
        self.site_id = config.name
        self.base_url = next(iter(config.variants.values()))
        opens = tuple(config.open_path(key) for key in config.pages)
        self.safety = ScenarioSafety(self.base_url, opens)   # 사이트 주소·등록 주소가 잘못되면 ValueError
        self.origin = _origin(self.base_url)
        self._registered = sorted(_canonical(_url_parts(urljoin(self.base_url, o))) for o in opens)
        self._by_path = {url_path(p) or "/": key for key, p in config.pages.items()}
        self.start_path = url_path(config.pages[config.start_page]) or "/"

    # ---- 허용 판정: ScenarioSafety에 맡긴다
    def url_rejection(self, current_url: str, href: str | None) -> str | None:
        """링크가 등록 주소면 None, 아니면 사유. current_url은 **실제 현재 주소** (목적지=현재 화면이면 NOT_NAVIGATION)."""
        return self.safety.url_rejection(current_url, href)

    def arrival_rejection(self, url: str) -> str | None:
        """이동 후 실제 주소(리다이렉트 포함)가 등록 주소인지."""
        return self.safety.arrival_rejection(url)

    def candidate_rejection(self, current_url: str, el: dict) -> str | None:
        """후보 추출·클릭 직전 재추출 때 실제 DOM 값(태그·주소·라벨·링크 속성)으로 검사."""
        attrs = el.get("attrs") or {}
        return self.safety.candidate_rejection(current_url, {"tag": el.get("tag"), "href": el.get("href"),
                                                             "label": el.get("label"), **attrs})

    # ---- 브라우저·후보 추출에서 쓰는 이름
    def link_allowed(self, href: str | None, current_url: str | None = None) -> bool:
        """후보 href가 등록 주소로 가는지. 클릭 직전 확인용으로 **실제 현재 주소**를 넘긴다 (없으면 사이트 첫 주소)."""
        return self.url_rejection(current_url or self.base_url, href) is None

    def in_scope(self, url: str, origin: str | None = None) -> bool:
        """도착한 주소가 이 사이트의 등록 주소인지 (클릭·뒤로가기 뒤 리다이렉트 포함 확인).

        출처는 **사이트 설정의 출처**로 판단하고 호출자가 넘긴 `origin`은 쓰지 않는다 (#71 리뷰 2: 외부 사이트로
        리다이렉트된 뒤 뒤로 가기를 하면 "이탈한 주소의 출처"가 넘어와 정상 복귀도 범위 밖으로 판정됐다).
        """
        return self.arrival_rejection(url) is None

    def nav_key(self, url: str) -> str:
        """클릭 뒤 화면이 바뀌었는지 비교할 값: 출처 + 경로 + 쿼리 (모호한 주소면 원문)."""
        try:
            return f"{_origin(url)}{_canonical(_url_parts(url))}"
        except (TypeError, ValueError):
            return url

    def global_nav_allowed(self, path: str) -> bool:
        """현재 화면에서 상단 메뉴를 후보로 넣는지 (리뷰 2)."""
        return self.config.global_nav_policy == "ALL_PAGES" or (url_path(path) or "/") == self.start_path

    def js_args(self) -> dict:
        """후보 추출 JS에 넘길 규칙: 사이트 출처와 등록 주소(경로+쿼리) 목록."""
        scheme, host, port = self.origin
        return {"mode": "pages", "origin": f"{scheme}://{host}:{port}", "urls": list(self._registered)}

    # ---- 화면 이름 (경로 기준)
    def registered_key(self, url: str | None) -> str | None:
        """주소 → 등록된 화면 pageKey (경로 기준, 쿼리 무시). 다른 출처이거나 등록되지 않은 경로면 None."""
        if not url:
            return None
        if urlsplit(url).netloc and _origin(url) != self.origin:
            return None
        return self._by_path.get(url_path(url) or "/")

    def page_key(self, url: str) -> str:
        """화면 이름표. 등록된 화면이면 그 pageKey, 아니면 경로 (등록 밖 화면은 탐색하지 않으므로 기록용)."""
        return self.registered_key(url) or (url_path(url) or "/")


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
