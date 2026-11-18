"""사이트 지도 (SITE_MAP, 이슈 #46). 설계: 「사이트 지도 기반 경로 지표 설계」 v2.1 2장.

에이전트가 실제로 고를 수 있는 링크(`candidates.extract` 결과)로 "화면 → 갈 수 있는 화면" 표를 만들고,
도착 화면까지의 최단 거리로 경로 지표를 계산할 수 있게 한다. HTML 링크 전체를 쓰지 않는 이유:
상단 메뉴·하단 링크 때문에 모든 화면이 서로 연결돼 최단 거리가 전부 1이 된다.

이 모듈은 브라우저를 열지 않는다. 후보 추출은 `scripts/build_sitemap.py`가 하고 여기에 넘긴다.

지도 파일 (`config/sitemaps/*.json`, 예: `sitemap-shop-v1.json`. 파일 이름이 아니라 안의 `siteMapVersion`으로 찾는다)
    {siteMapVersion, siteMapHash, siteVersion, layoutVersion, candidatePolicyVersion, initialBrowserStateId,
     viewport, startPageKey, generatedAt, pages: {pageKey: {path, edges: [{semanticKey, to}]}}}

해시 정규화 규칙 (#46)
    1. 대상은 `pages`만 (버전·generatedAt 등 메타데이터 제외)
    2. pages는 pageKey 순, 각 화면은 {"path", "edges"}만
    3. edges는 {"semanticKey", "to"}만, 중복 제거 후 (semanticKey, to) 순 (라벨·bbox·확률 제외 → Before·After 공통)
    4. json.dumps(ensure_ascii=False, sort_keys=True, separators=(",", ":")) → UTF-8 → SHA-256
    5. "sha256:" + 소문자 hex
"""
import hashlib
import json
import os
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class SiteMapError(Exception):
    """지도를 만들거나 읽을 수 없음. `reasons`에 실패 이유 목록."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


GLOBAL_NAV_POLICIES = ("START_ONLY", "ALL_PAGES")


def url_path(href: str) -> str:
    """주소의 경로 부분. 경로 매개변수(`;jsessionid=…`)와 끝 `/`는 뗀다 (루트는 빈 문자열)."""
    return urlsplit(href).path.split(";", 1)[0].rstrip("/")


@dataclass(frozen=True)
class SiteConfig:
    """지도를 만들 사이트 설정. 실제 사이트를 추가할 때는 설정 파일(`config/sites/*.json`)만 새로 만든다.

    화면 목록은 사람이 적는다(링크 따라가기 수집은 범위 밖). 후보 추출·화면 구분은 사이트를 넘기지 않으면
    쇼핑몰 규칙(`/shop/`, 시작 화면 `/shop/home`)이고, 다른 사이트는 `uxm/site_scope.py`가 이 설정으로 정한다 (#67).

    `pages` 값은 경로 문자열(기존 형식) 또는 `{"path", "label", "text"}` 객체(화면 이름·도착 확인 글자, #67).
    어느 형식이든 `pages`에는 경로만 남기고 이름·글자는 `page_labels`·`page_texts`에 둔다 (지도 해시는 경로만 씀).
    """
    name: str
    pages: dict[str, str]                 # pageKey → path (지원 화면 목록)
    start_page: str                       # 시작 화면 pageKey (상단 메뉴는 이 화면에서만 후보)
    path_prefix: str                      # 이 접두어 아래 경로만 지도에 넣는다 (허용 범위)
    meta: dict = field(default_factory=dict)   # siteVersion 등 지도 파일에 남길 고정 조건
    allowed_hosts: tuple[str, ...] = ()   # 허용 도메인(host[:port]). 비어 있으면 상대 경로(`/shop/...`)만 허용
    variants: dict[str, str] = field(default_factory=dict)   # 지도를 만들 사이트 주소 {이름: 기본 URL}. 1개 이상
    site_map_version: str = "sitemap-v1"
    out: str = ""                         # 지도 파일 경로 (저장소 루트 기준)
    preset_check: bool = False            # config/presets.json 허용 경로와 대조할지 (그 사이트용 preset이 있을 때만)
    display_name: str = ""                # 화면에 보일 사이트 이름 (없으면 name)
    page_labels: dict[str, str] = field(default_factory=dict)   # pageKey → 화면 이름
    page_texts: dict[str, str] = field(default_factory=dict)    # pageKey → 도착 확인 글자 (도착 화면 후보)
    page_open: dict[str, str] = field(default_factory=dict)     # pageKey → 열 때 쓸 주소(경로+쿼리). 없으면 path
    global_nav_policy: str = "START_ONLY"   # 상단 메뉴를 후보로 넣을 화면: 시작 화면만 / 모든 화면 (리뷰 2)
    require_all_reachable: bool = True      # 지도 검증 3: 모든 화면이 시작 화면에서 닿아야 하는지

    def open_path(self, key: str) -> str:
        """지도 생성·사전 검사에서 그 화면을 열 주소 (게시판 목록처럼 쿼리가 있어야 열리는 화면용)."""
        return self.page_open.get(key, self.pages[key])

    def page_key(self, href: str | None) -> str | None:
        """후보 href → 지원 화면 pageKey. 쿼리·경로 매개변수·끝 `/`는 무시. 지원 화면이 아니면 None."""
        if not href:
            return None
        parts = urlsplit(href)
        if parts.netloc and parts.netloc not in self.allowed_hosts:   # 바깥 도메인 제외
            return None
        path = url_path(href)
        if not (path + "/").startswith(self.path_prefix):
            return None
        for key, page_path in self.pages.items():
            if page_path.rstrip("/") == path:
                return key
        return None


SITES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "sites")
SITE_FIELDS = ("name", "pages", "startPage", "pathPrefix", "variants", "siteMapVersion", "out")


def _split_pages(raw: dict) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str], list[str]]:
    """`pages` 값(문자열 또는 객체) → (경로, 이름, 확인 글자, 여는 주소, 문제 목록)."""
    paths, labels, texts, opens, reasons = {}, {}, {}, {}, []
    for key, value in raw.items():
        if isinstance(value, str):
            path = value
        elif isinstance(value, dict):
            path = value.get("path")
            if "open" in value:
                o = value["open"]
                if not (isinstance(o, str) and o.startswith("/") and isinstance(path, str)
                        and url_path(o) == path.rstrip("/")):
                    reasons.append(f"'{key}' 화면의 open은 path와 같은 경로(+쿼리)여야 함")
                else:
                    opens[key] = o
            for f, out in (("label", labels), ("text", texts)):
                if f in value:
                    if isinstance(value[f], str) and value[f].strip():
                        out[key] = value[f].strip()
                    else:
                        reasons.append(f"'{key}' 화면의 {f}는 빈 값이 아닌 문자열이어야 함")
        else:
            path = None
        if not isinstance(path, str) or not path.startswith("/"):
            reasons.append(f"'{key}' 화면의 path가 '/'로 시작하는 문자열이 아님")
            continue
        paths[key] = path
    return paths, labels, texts, opens, reasons


def site_config_from_dict(data: dict) -> SiteConfig:
    """사이트 설정 파일 내용 → SiteConfig. 빠진 필드·잘못된 값이면 SiteMapError."""
    reasons = [f"사이트 설정 필드 '{f}' 없음" for f in SITE_FIELDS if f not in data]
    paths, labels, texts, opens = {}, {}, {}, {}
    if not reasons:
        if not isinstance(data["pages"], dict) or not data["pages"]:
            reasons.append("pages가 비어 있음")
        else:
            paths, labels, texts, opens, page_reasons = _split_pages(data["pages"])
            reasons += page_reasons
            if data["startPage"] not in data["pages"]:
                reasons.append(f"시작 화면 '{data['startPage']}'이 pages에 없음")
        if not isinstance(data["variants"], dict) or not data["variants"]:
            reasons.append("variants(사이트 주소)가 비어 있음")
        if not str(data["pathPrefix"]).endswith("/"):
            reasons.append("pathPrefix는 '/'로 끝나야 함 (예: /shop/)")
        elif any(not (p.rstrip("/") + "/").startswith(data["pathPrefix"]) for p in paths.values()):
            reasons.append(f"pathPrefix '{data['pathPrefix']}' 밖의 화면이 pages에 있음")
        if data.get("globalNavPolicy", "START_ONLY") not in GLOBAL_NAV_POLICIES:
            reasons.append(f"globalNavPolicy는 {GLOBAL_NAV_POLICIES} 중 하나여야 함")
    if reasons:
        raise SiteMapError(reasons)
    return SiteConfig(name=data["name"], pages=paths, start_page=data["startPage"],
                      path_prefix=data["pathPrefix"], meta=dict(data.get("meta", {})),
                      allowed_hosts=tuple(data.get("allowedHosts", ())), variants=dict(data["variants"]),
                      site_map_version=data["siteMapVersion"], out=data["out"],
                      preset_check=bool(data.get("presetCheck", False)),
                      display_name=data.get("displayName") or data["name"], page_labels=labels, page_texts=texts,
                      page_open=opens, global_nav_policy=data.get("globalNavPolicy", "START_ONLY"),
                      require_all_reachable=bool(data.get("requireAllReachable", True)))


def load_site_config(path: str) -> SiteConfig:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise SiteMapError([f"사이트 설정을 읽을 수 없음: {path} ({e})"]) from e
    return site_config_from_dict(data)


# 테스트 쇼핑몰 (MORO MARKET). 설정 원본은 config/sites/moro-market.json
SHOP = load_site_config(os.path.join(SITES_DIR, "moro-market.json"))


# ---------------------------------------------------------------- 간선·정규화·해시

def edges_from_candidates(config: SiteConfig, candidates: list[dict]) -> list[dict]:
    """한 화면의 후보 → 간선 [{semanticKey, to}]. 지원 화면으로 가는 후보만, 중복 제거·정렬."""
    edges = set()
    for c in candidates:
        to = config.page_key(c.get("href"))
        if to is not None:
            edges.add((c["semanticKey"], to))
    return [{"semanticKey": k, "to": to} for k, to in sorted(edges)]


def normalize_pages(pages: dict) -> dict:
    """해시 규칙 2·3: pageKey 순, {path, edges}만, edges는 중복 제거 후 (semanticKey, to) 순."""
    out = {}
    for key in sorted(pages):
        edges = {(e["semanticKey"], e["to"]) for e in pages[key].get("edges", [])}
        out[key] = {"path": pages[key]["path"],
                    "edges": [{"semanticKey": k, "to": to} for k, to in sorted(edges)]}
    return out


def site_map_hash(pages: dict) -> str:
    """해시 규칙 1~5."""
    text = json.dumps(normalize_pages(pages), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 거리

def distances(pages: dict, goal_page: str) -> dict[str, int | None]:
    """각 화면에서 도착 화면까지 최단 이동 수 (간선 역방향 BFS). 갈 수 없는 화면은 None.

    도착 화면이 지도에 없으면 SiteMapError (지도 오류와 도달 불가를 구분: 설계 3.2).
    """
    if goal_page not in pages:
        raise SiteMapError([f"도착 화면 '{goal_page}'이 지도에 없습니다"])
    incoming: dict[str, set[str]] = {key: set() for key in pages}
    for key, page in pages.items():
        for e in page.get("edges", []):
            if e["to"] in incoming:
                incoming[e["to"]].add(key)
    dist: dict[str, int | None] = {key: None for key in pages}
    dist[goal_page] = 0
    queue = deque([goal_page])
    while queue:
        cur = queue.popleft()
        for prev in incoming[cur]:
            if dist[prev] is None:
                dist[prev] = dist[cur] + 1
                queue.append(prev)
    return dist


def is_path_on_map(pages: dict, route: list[str]) -> bool:
    """route(pageKey 순서)의 연속한 두 화면마다 지도에 간선이 있는지."""
    return all(any(e["to"] == b for e in pages.get(a, {}).get("edges", [])) for a, b in zip(route, route[1:]))


# ---------------------------------------------------------------- 생성·검증

def build_site_map(config: SiteConfig, extracted: dict[str, dict[str, list[dict] | None]], *,
                   version: str, generated_at: str, preset_checks: list[dict] | None = None) -> dict:
    """variant별 후보 추출 결과로 지도를 만든다. 검증 4가지 중 하나라도 실패하면 SiteMapError.

    extracted: {"before": {pageKey: 후보 목록 또는 None(추출 실패)}, "after": {...}}
    preset_checks: [{"goalPage", "allowedPaths", "shortestAllowedSteps"}] — 기존 preset 허용 경로 대조
    """
    reasons: list[str] = []
    by_variant: dict[str, dict] = {}
    for variant, per_page in extracted.items():
        pages = {}
        for key, path in config.pages.items():
            cands = per_page.get(key)
            if cands is None:   # 검증 1: 화면을 열고 후보를 뽑았는가
                reasons.append(f"{variant}: '{key}' 화면 후보 추출 실패")
                continue
            pages[key] = {"path": path, "edges": edges_from_candidates(config, cands)}
        by_variant[variant] = normalize_pages(pages)
    if reasons:
        raise SiteMapError(reasons)

    # 검증 2: Before·After 간선 구조가 같은가 (라벨은 간선에 없음)
    variants = list(by_variant)
    base = by_variant[variants[0]]
    for other in variants[1:]:
        for key in config.pages:
            a = {(e["semanticKey"], e["to"]) for e in base[key]["edges"]}
            b = {(e["semanticKey"], e["to"]) for e in by_variant[other][key]["edges"]}
            if a != b:
                reasons.append(f"'{key}' 간선이 {variants[0]}·{other}에서 다름: "
                               f"{variants[0]}만 {sorted(a - b)}, {other}만 {sorted(b - a)}")
    if reasons:
        raise SiteMapError(reasons)
    pages = base

    # 검증 3: 모든 지원 화면에 시작 화면에서 가는 길이 있는가
    # 외부 사이트(requireAllReachable=false)는 실패 대신 지도에 기록한다: 첫 화면에 링크가 없어 닿지 않는 화면 자체가
    # 그 사이트의 발견 사항이고, 그 화면을 도착 화면으로 고르면 실행 API가 422로 알린다 (#67)
    unreachable = [key for key in config.pages
                   if key != config.start_page and distances(pages, key)[config.start_page] is None]
    if config.require_all_reachable:
        reasons += [f"시작 화면 '{config.start_page}'에서 '{key}'로 가는 길이 없음" for key in unreachable]

    # 검증 4: 기존 preset 허용 경로와 맞는가
    for check in preset_checks or []:
        goal = check["goalPage"]
        shortest = distances(pages, goal)[config.start_page]
        if shortest != check["shortestAllowedSteps"]:
            reasons.append(f"'{goal}' 최단 거리 {shortest} ≠ preset shortestAllowedSteps {check['shortestAllowedSteps']}")
        for route in check["allowedPaths"]:
            if not is_path_on_map(pages, route):
                reasons.append(f"preset 허용 경로 {' → '.join(route)}가 지도에 없음")
    if reasons:
        raise SiteMapError(reasons)

    out = {"siteMapVersion": version, "siteMapHash": site_map_hash(pages), **config.meta,
           "startPageKey": config.start_page, "generatedAt": generated_at, "pages": pages}
    if not config.require_all_reachable:
        out["unreachablePages"] = unreachable   # 시작 화면에서 닿지 않는 화면 (해시 대상 아님)
    return out


def site_map_reference(site_map: dict, goal_page: str, start_page: str | None = None) -> dict:
    """Run에 저장할 SITE_MAP `referencePathConfig` (설계 4.1). 실행 API·`scripts/run_c.py`가 같이 쓴다.

    `distances`는 지원 화면 전부(갈 수 없는 화면은 None)를 그대로 넣는다 (#47 재검증이 사전 전체 비교).
    시작 화면에서 도착 화면으로 가는 길이 없으면 SiteMapError (API는 실행 전 422).
    """
    start = start_page or site_map["startPageKey"]
    if start != site_map["startPageKey"]:
        raise SiteMapError([f"시작 화면 '{start}'이 지도 시작 화면 '{site_map['startPageKey']}'과 다름"])
    dist = distances(site_map["pages"], goal_page)
    if dist.get(start) is None:
        raise SiteMapError([f"시작 화면 '{start}'에서 도착 화면 '{goal_page}'으로 가는 길이 없음"])
    return {"basis": "SITE_MAP", "siteMapVersion": site_map["siteMapVersion"], "siteMapHash": site_map["siteMapHash"],
            "startPageKey": start, "goalPageKey": goal_page, "shortestSteps": dist[start], "distances": dist}


# ---------------------------------------------------------------- 읽기

REQUIRED_FIELDS = ("siteMapVersion", "siteMapHash", "startPageKey", "pages")


def _structure_problems(data) -> list[str]:
    """타입·필수 값 검사. 잘못된 구조가 TypeError·KeyError가 아니라 SiteMapError가 되도록 먼저 확인한다 (#48 리뷰)."""
    if not isinstance(data, dict):
        return ["지도 내용이 객체가 아님"]
    reasons = [f"필드 '{f}' 없음" for f in REQUIRED_FIELDS if f not in data]
    if reasons:
        return reasons
    for f in ("siteMapVersion", "siteMapHash", "startPageKey"):
        if not isinstance(data[f], str) or not data[f].strip():
            reasons.append(f"'{f}'는 빈 값이 아닌 문자열이어야 함")
    pages = data["pages"]
    if not isinstance(pages, dict) or not pages:
        return reasons + ["'pages'는 비어 있지 않은 객체여야 함"]
    for key, page in pages.items():
        if not isinstance(key, str) or not key:
            reasons.append(f"화면 이름 {key!r}이 올바르지 않음")
            continue
        if not isinstance(page, dict):
            reasons.append(f"'{key}' 화면이 객체가 아님")
            continue
        if not isinstance(page.get("path"), str) or not page["path"]:
            reasons.append(f"'{key}' 화면의 path가 없음")
        edges = page.get("edges")
        if not isinstance(edges, list):
            reasons.append(f"'{key}' 화면의 edges가 목록이 아님")
            continue
        for i, e in enumerate(edges):
            if not isinstance(e, dict):
                reasons.append(f"'{key}' 간선 {i}가 객체가 아님")
            elif not isinstance(e.get("semanticKey"), str) or not e["semanticKey"]:
                reasons.append(f"'{key}' 간선 {i}에 semanticKey가 없음")
            elif not isinstance(e.get("to"), str) or e["to"] not in pages:
                reasons.append(f"'{key}' 간선 목적지 {e.get('to')!r}이 pages에 없음")
    if isinstance(data["startPageKey"], str) and data["startPageKey"] not in pages:
        reasons.append(f"시작 화면 '{data['startPageKey']}'이 pages에 없음")
    return reasons


def validate_site_map(data: dict) -> dict:
    """지도 파일 내용 검사: 구조(타입·필수 값), 간선 목적지, 해시 일치. 문제가 있으면 항상 SiteMapError."""
    reasons = _structure_problems(data)
    if reasons:
        raise SiteMapError(reasons)
    actual = site_map_hash(data["pages"])
    if actual != data["siteMapHash"]:
        raise SiteMapError([f"해시 불일치: 파일 {data['siteMapHash']} ≠ 계산 {actual}"])
    return data


def load_site_map(path: str) -> dict:
    """지도 파일 읽기 + 검사. 파일이 없거나 깨졌으면 SiteMapError (지도 오류: 설계 3.2)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise SiteMapError([f"지도 파일을 읽을 수 없음: {path} ({e})"]) from e
    return validate_site_map(data)


# ---------------------------------------------------------------- 화면 열기 결과 검사

def page_load_problem(expected_path: str, status: int | None, final_url: str) -> str | None:
    """지원 화면을 열었을 때 정상 응답이고 기대한 화면에 도착했는지. 문제가 있으면 이유 (#48 리뷰).

    오류 화면(404 등)이나 다른 화면으로 넘어간 경우를 후보 추출 성공으로 처리하면 잘못된 지도가 저장될 수 있다.
    """
    if status is None:
        return "응답이 없음"
    if not 200 <= status < 300:
        return f"HTTP {status}"
    arrived = urlsplit(final_url).path.rstrip("/")
    if arrived != expected_path.rstrip("/"):
        return f"다른 화면에 도착: {arrived or '/'} (기대 {expected_path})"
    return None


# ---------------------------------------------------------------- 실제 Run과 대조 (2단계)

@dataclass
class StepComparison:
    """실제 Run 대조 결과. 대조한 Step과 건너뛴 Step을 구분한다 (#48 리뷰: 대조 0개를 통과로 보이지 않게)."""
    compared: int = 0              # 지도와 실제로 비교한 Step 수 (불일치 포함)
    skipped: int = 0               # testMode 화면·재판단 후보라 비교하지 않은 Step 수
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """실제로 1개 이상 비교했고 불일치가 없을 때만 통과."""
        return self.compared > 0 and not self.problems


def compare_with_steps(site_map: dict, config: SiteConfig, steps: list[dict]) -> StepComparison:
    """저장된 Step 후보(목적지)가 지도 간선과 맞는지.

    testMode 등 기본 상태가 아닌 화면(pageSignature가 `_default`가 아님)과 재판단 후보 집합(`_retry_`)은
    비교하지 않고 `skipped`로 센다. 지도에 없는 화면은 비교한 것으로 세고 불일치로 남긴다.
    """
    result = StepComparison()
    pages = site_map["pages"]
    for step in steps:
        page = step.get("page") or {}
        key = page.get("pageKey")
        cs = step.get("candidateSet") or {}
        if not str(page.get("pageSignature", "")).endswith("_default") or "_retry_" in str(cs.get("candidateSetId", "")):
            result.skipped += 1
            continue
        result.compared += 1
        if key not in pages:
            result.problems.append(f"{step.get('runId')} step {step.get('stepId')}: 화면 '{key}'이 지도에 없음")
            continue
        expected = {(e["semanticKey"], e["to"]) for e in pages[key]["edges"]}
        actual = {(e["semanticKey"], e["to"]) for e in edges_from_candidates(config, cs.get("candidates", []))}
        if actual != expected:
            result.problems.append(f"{step.get('runId')} step {step.get('stepId')} '{key}': "
                                   f"Step에만 {sorted(actual - expected)}, 지도에만 {sorted(expected - actual)}")
    return result


def collect_run_steps(run_dirs: list[str], site_version: str | None) -> tuple[list[dict], list[str]]:
    """저장된 LIVE Run의 Step과 읽기 문제 목록. 같은 사이트(`siteVersion`) Run만.

    쓰는 중인 마지막 줄은 `storage.read_jsonl` 규칙대로 빼고 앞의 정상 Step은 쓴다.
    읽을 수 없는 Run은 조용히 건너뛰지 않고 문제 목록에 남긴다 (#48 리뷰).
    """
    from uxm.storage import read_jsonl
    steps, problems = [], []
    for run_dir in run_dirs:
        name = os.path.basename(run_dir.rstrip("/\\"))
        run_path = os.path.join(run_dir, "run.json")
        if not os.path.isfile(run_path):
            continue   # Run 폴더가 아님 (예: 샘플 폴더의 다른 파일)
        try:
            with open(run_path, encoding="utf-8") as f:
                run = json.load(f)
        except (OSError, ValueError) as e:
            problems.append(f"{name}: run.json을 읽을 수 없음 ({e})")
            continue
        if run.get("resultOrigin") != "LIVE" or run.get("siteVersion") != site_version:
            continue
        try:
            steps += read_jsonl(os.path.join(run_dir, "steps.jsonl"))
        except (OSError, ValueError) as e:
            problems.append(f"{name}: steps.jsonl을 읽을 수 없음 ({e})")
    return steps, problems
