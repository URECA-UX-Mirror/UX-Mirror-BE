"""사이트별 탐색 범위 (#67, 외부 사이트 시나리오 테스트).

후보 추출(`uxm/candidates.py`)·화면 구분과 클릭 범위(`uxm/browser.py`)가 "어느 링크가 이동 후보인지, 화면 이름이 무엇인지"를
정할 때 쓰는 규칙을 한곳에 모았다.

- **사이트를 넘기지 않으면(`scope=None`) MORO MARKET 기존 규칙 그대로**다 (`/shop/` 링크, 시작 화면 `/shop/home`).
  candidates·browser는 `scope`가 None일 때 지금까지의 코드를 그대로 실행하므로 버전 비교·E 결과가 바뀌지 않는다.
- `SiteScope(config)`는 사이트 설정(`config/sites/*.json`)에 **등록된 주소로 가는 링크만** 허용한다 (리뷰 1).

허용 규칙은 안전 검사 모듈(#70 `uxm/scenario_safety.ScenarioSafety`)과 같다. #70이 머지되면 아래 판정 함수
(`url_rejection`·`arrival_rejection`·`link_action_rejection`·`label_rejection`)를 그 모듈 호출로 바꾼다.
  - 같은 출처(scheme·host·port)만. 사이트 주소는 설정 `variants`의 주소
  - 등록 주소 = 화면마다 `open`(경로+쿼리, 없으면 `path`). **경로와 쿼리가 모두 같아야** 허용 (쿼리 추가·누락·변경 거부)
  - 모호한 경로(`//`, `\\`, `.`·`..`, 인코딩한 `/`, 제어 문자)와 위험 경로(`/exec/`, 주문서, 로그아웃)는 거부
  - `onclick`·`download`·새 창(`target`)·`role=button`이 있는 링크, 위험 라벨(구매·주문·장바구니 담기·로그아웃 등)은 거부

화면 이름(pageKey)은 **경로 기준**이다. 성공 조건·지도·경로 기준(`referencePathConfig`)이 경로로 화면을 부르기 때문이다.
등록 밖 쿼리의 화면은 위 허용 검사에서 막혀 탐색 중에 들어가지 않는다.

상단 메뉴를 후보로 넣을 화면은 사이트별 정책(`globalNavPolicy`, 리뷰 2).
후보의 `href`는 외부 사이트에서 **도메인을 뺀 경로 + 쿼리**(`/board/review/list.html?board_no=4`)로 저장한다.
"""
import json
import os
import re
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

from uxm.sitemap import SITES_DIR, SiteConfig, SiteMapError, site_config_from_dict, url_path

# ---- #70 ScenarioSafety와 같은 규칙 (머지 후 그 모듈로 교체)
DANGEROUS_LABELS = (
    "구매하기", "바로구매", "주문하기", "장바구니담기", "로그아웃", "비우기", "탈퇴", "삭제", "결제",
    "buy now", "checkout", "place order", "log out", "logout", "sign out", "delete", "remove",
)
DANGEROUS_PATHS = ("/exec/", "/order/orderform.html", "/member/logout", "/logout")


def _label_text(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _origin(url: str) -> tuple[str, str, int] | None:
    try:
        u = urlsplit(url)
        scheme = u.scheme.lower()
        if scheme not in ("http", "https") or not u.hostname or u.username or u.password:
            return None
        return scheme, u.hostname.lower(), u.port or (443 if scheme == "https" else 80)
    except ValueError:
        return None


def _url_parts(url: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    """(경로, 쿼리 쌍). 모호한 경로는 ValueError (서버마다 해석이 달라 허용하지 않는다)."""
    u = urlsplit(url)
    decoded = unquote(u.path)
    if ("\\" in decoded or "//" in decoded or any(s in (".", "..") for s in decoded.split("/"))
            or any(ord(c) < 32 or ord(c) == 127 for c in decoded)):
        raise ValueError("모호한 URL 경로")
    return decoded.rstrip("/") or "/", tuple(parse_qsl(u.query, keep_blank_values=True))


def _canonical(parts: tuple[str, tuple]) -> str:
    """후보 추출 JS와 비교할 문자열: 경로 + 디코딩한 쿼리 쌍 (순서 유지)."""
    path, query = parts
    return path + ("?" + "&".join(f"{k}={v}" for k, v in query) if query else "")


def _dangerous_path(path: str) -> bool:
    folded = path.casefold()
    return any(folded == p.rstrip("/") or folded.startswith(p) for p in DANGEROUS_PATHS)


class SiteScope:
    """등록된 주소만 허용하는 탐색 범위 (외부 사이트용)."""

    def __init__(self, config: SiteConfig):
        self.config = config
        self.site_id = config.name
        self.base_url = next(iter(config.variants.values()))
        self.origin = _origin(self.base_url)
        if self.origin is None:
            raise ValueError(f"사이트 주소가 올바르지 않습니다: {self.base_url}")
        self._allowed = {}   # (경로, 쿼리) → pageKey
        for key in config.pages:
            full = urljoin(self.base_url, config.open_path(key))
            if _origin(full) != self.origin:
                raise ValueError(f"'{key}' 화면 주소가 사이트 밖입니다: {full}")
            self._allowed[_url_parts(full)] = key
        self._by_path = {url_path(p) or "/": key for key, p in config.pages.items()}
        self.start_path = url_path(config.pages[config.start_page]) or "/"

    # ---- 허용 판정 (#70과 같은 사유 코드)
    def url_rejection(self, current_url: str, href: str | None) -> str | None:
        """링크가 등록 주소면 None, 아니면 사유. 현재 화면도 같은 출처여야 한다."""
        if not isinstance(href, str) or not href.strip() or href.strip().startswith("#"):
            return "NOT_NAVIGATION"
        if _origin(current_url) != self.origin:
            return "OUT_OF_SCOPE"
        try:
            target = urljoin(current_url, href.strip())
            if _origin(target) != self.origin:
                return "OUT_OF_SCOPE"
            parts = _url_parts(target)
            if _dangerous_path(parts[0]):
                return "DANGEROUS_PATH"
            if parts not in self._allowed:
                return "OUT_OF_SCOPE"
            return None
        except (TypeError, ValueError):
            return "OUT_OF_SCOPE"

    def arrival_rejection(self, url: str) -> str | None:
        """이동 후 실제 주소(리다이렉트 포함)가 등록 주소인지."""
        try:
            if _origin(url) != self.origin:
                return "OUT_OF_SCOPE"
            parts = _url_parts(url)
            if _dangerous_path(parts[0]):
                return "DANGEROUS_PATH"
            return None if parts in self._allowed else "OUT_OF_SCOPE"
        except (TypeError, ValueError):
            return "OUT_OF_SCOPE"

    @staticmethod
    def link_action_rejection(attrs: dict) -> str | None:
        """링크의 HTML 속성: 스크립트 동작·다운로드·새 창·버튼 역할이면 거부. 속성 정보가 없으면 거부(fail-closed)."""
        if any(k not in attrs for k in ("onclick", "download", "target", "role")):
            return "DOM_ATTRIBUTES_MISSING"
        if (attrs["onclick"] is not None or attrs["download"] is not None
                or (attrs["target"] or "").lower() not in ("", "_self") or (attrs["role"] or "").lower() == "button"):
            return "UNSUPPORTED_LINK_ACTION"
        return None

    @staticmethod
    def label_rejection(label: str) -> str | None:
        normalized = _label_text(label or "")
        return "DANGEROUS_ACTION" if any(_label_text(w) in normalized for w in DANGEROUS_LABELS) else None

    # ---- 브라우저·후보 추출에서 쓰는 이름
    def link_allowed(self, href: str | None) -> bool:
        """후보 href(경로+쿼리 또는 주소)가 등록 주소로 가는지. 클릭 직전 확인용."""
        return self.url_rejection(self.base_url, href) is None

    def in_scope(self, url: str, origin: str) -> bool:
        """도착한 주소가 같은 사이트의 등록 주소인지 (클릭·뒤로가기 뒤 리다이렉트 포함 확인)."""
        return _origin(origin) == self.origin and self.arrival_rejection(url) is None

    def global_nav_allowed(self, path: str) -> bool:
        """현재 화면에서 상단 메뉴를 후보로 넣는지 (리뷰 2)."""
        return self.config.global_nav_policy == "ALL_PAGES" or (url_path(path) or "/") == self.start_path

    def js_args(self) -> dict:
        """후보 추출 JS에 넘길 규칙: 사이트 출처와 등록 주소(경로+쿼리) 목록."""
        scheme, host, port = self.origin
        return {"mode": "pages", "origin": f"{scheme}://{host}:{port}",
                "urls": sorted(_canonical(p) for p in self._allowed)}

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
