"""등록된 외부 사이트 시나리오의 링크 안전 검사.

호출자는 후보 추출 및 클릭 직전 각각 현재 DOM의 값으로 검사해야 한다.
이 모듈은 클릭이나 이동을 수행하지 않으며, MORO MARKET 기본 동작을 바꾸지 않는다.
"""
from dataclasses import dataclass, field
import re
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit


SCENARIO_LINK_SELECTOR = "a[href]"
DANGEROUS_LABELS = (
    "구매하기", "바로구매", "주문하기", "장바구니담기", "로그아웃", "비우기", "탈퇴", "삭제", "결제",
    "buy now", "checkout", "place order", "log out", "logout", "sign out", "delete", "remove",
)
DANGEROUS_PATHS = ("/exec/", "/order/orderform.html", "/member/logout", "/logout")


def _label_text(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _origin(url: str) -> tuple[str, str, int] | None:
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            return None
        return parts.scheme.lower(), parts.hostname.lower(), parts.port or (443 if parts.scheme.lower() == "https" else 80)
    except ValueError:
        return None


def _url_parts(url: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    parts = urlsplit(url)
    # 인코딩한 경로 구분자, 역슬래시, dot segment는 서버별 해석이 다르므로 허용하지 않는다.
    decoded = unquote(parts.path)
    if ("\\" in decoded or "//" in decoded or any(s in (".", "..") for s in decoded.split("/"))
            or any(ord(c) < 32 or ord(c) == 127 for c in decoded)):
        raise ValueError("모호한 URL 경로")
    path = decoded.rstrip("/") or "/"
    return path, tuple(parse_qsl(parts.query, keep_blank_values=True))


@dataclass(frozen=True)
class ScenarioSafety:
    """한 실행에 고정된 시작 URL과 허용 화면. 각 URL의 쿼리까지 등록한 것만 허용한다.

    예: `base_url=https://example.com`, `pages=("/", "/list?category=1")`.
    허용하지 않은 쿼리나 다른 출처의 같은 경로는 허용하지 않는다.
    """
    base_url: str
    pages: tuple[str, ...]
    _site_origin: tuple[str, str, int] = field(init=False, repr=False)
    _allowed: frozenset = field(init=False, repr=False)

    def __post_init__(self):
        origin = _origin(self.base_url)
        if origin is None or not self.pages:
            raise ValueError("유효한 사이트 주소와 등록 화면이 필요합니다.")
        allowed = set()
        for page in self.pages:
            if not isinstance(page, str) or not page:
                raise ValueError("등록 화면 주소가 비어 있습니다.")
            full = urljoin(self.base_url, page)
            if _origin(full) != origin or urlsplit(full).fragment:
                raise ValueError("등록 화면은 같은 출처여야 합니다.")
            allowed.add(_url_parts(full))
        object.__setattr__(self, "pages", tuple(self.pages))
        object.__setattr__(self, "_site_origin", origin)
        object.__setattr__(self, "_allowed", frozenset(allowed))

    def url_rejection(self, current_url: str, href: str) -> str | None:
        """허용 화면이면 None, 아니면 사유. 현재 주소도 고정된 출처여야 한다."""
        if not isinstance(href, str) or not href.strip() or href.strip().startswith("#"):
            return "NOT_NAVIGATION"
        if _origin(current_url) != self._site_origin:
            return "OUT_OF_SCOPE"
        try:
            target = urljoin(current_url, href.strip())
            if _origin(target) != self._site_origin:
                return "OUT_OF_SCOPE"
            path, query = _url_parts(target)
            folded = unquote(path).casefold()
            if any(folded == p.rstrip("/") or folded.startswith(p) for p in DANGEROUS_PATHS):
                return "DANGEROUS_PATH"
            if (path, query) not in self._allowed:
                return "OUT_OF_SCOPE"
            if (path, query) == _url_parts(current_url):
                return "NOT_NAVIGATION"
            return None
        except (TypeError, ValueError):
            return "OUT_OF_SCOPE"

    def candidate_rejection(self, current_url: str, candidate: dict) -> str | None:
        """후보 생성과 클릭 직전에 실제 DOM의 태그·주소·라벨과 선택 속성을 검사한다.

        호출자는 `onclick`, `download`, `target`, `role`을 DOM에서 읽어야 한다. 검사만으로
        JS 이벤트 리스너·서버 측 부작용을 증명할 수 없으며 허용 URL도 별도 검토가 필요하다.
        """
        tag = candidate.get("tag")
        if not isinstance(tag, str) or tag.lower() != "a":
            return "NOT_LINK"
        if any(key not in candidate for key in ("onclick", "download", "target", "role")):
            return "DOM_ATTRIBUTES_MISSING"
        if (candidate.get("onclick") is not None or candidate.get("download") is not None
                or (candidate.get("target") or "").lower() not in ("", "_self")
                or (candidate.get("role") or "").lower() == "button"):
            return "UNSUPPORTED_LINK_ACTION"
        label = candidate.get("label")
        if not isinstance(label, str) or not label.strip():
            return "NO_VISIBLE_LABEL"
        normalized = _label_text(label)
        if any(_label_text(word) in normalized for word in DANGEROUS_LABELS):
            return "DANGEROUS_ACTION"
        return self.url_rejection(current_url, candidate.get("href"))

    def arrival_rejection(self, url: str) -> str | None:
        """이동 후 실제 주소가 등록 화면인지 확인한다. 이동 자체를 되돌리지는 않는다."""
        try:
            if _origin(url) != self._site_origin:
                return "OUT_OF_SCOPE"
            path, query = _url_parts(url)
            folded = unquote(path).casefold()
            if any(folded == p.rstrip("/") or folded.startswith(p) for p in DANGEROUS_PATHS):
                return "DANGEROUS_PATH"
            return None if (path, query) in self._allowed else "OUT_OF_SCOPE"
        except (TypeError, ValueError):
            return "OUT_OF_SCOPE"
