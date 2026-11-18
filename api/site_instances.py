"""Site Instance 조회와 후보 스냅샷 (고도화 기획안 v1.3 4장·5장).

- Site Instance는 서버에 등록된 테스트 사이트만 쓴다. 클라이언트가 주소를 지정할 수 없다 (5.3).
  등록 목록은 `config/site_instances.json`이고, 라벨 변형 사이트(#37)를 띄우면 여기에 한 줄씩 추가한다.
  `baseUrl`은 서버 내부 값이라 API 응답과 모델 요청에 넣지 않는다.
- 후보 스냅샷은 실제 렌더링 화면에서 `candidates.extract()`로 뽑는다 (4.3). 라벨 문자열만 바꾸는 간소화 방식은
  공식 평가 경로로 쓰지 않는다.
- 유효성 검사(4.5)는 기준(C0) 화면이 설정(대상 메뉴·라벨·목적지)과 맞는지 먼저 보고, 다른 후보는 C0 스냅샷과 비교한다. 1번 모듈이 같은 기능을 제공하면 `api/integrations.py`에서 교체한다.
"""
import hashlib
import json
import os
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_INSTANCES_PATH = os.path.join(ROOT, "config", "site_instances.json")
BBOX_TOLERANCE_PX = 2
SNAPSHOT_FIELDS = ("optionId", "semanticKey", "label", "region", "href", "bbox")


def canonical_hash(obj) -> str:
    """정렬한 JSON의 sha256. Request Hash·Snapshot Hash에 쓴다."""
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class SiteRegistry:
    """`config/site_instances.json`의 등록 사이트. `baseUrlEnv` 환경변수가 있으면 그 주소를 쓴다."""

    def __init__(self, path: str = SITE_INSTANCES_PATH):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.instances = {}
        for inst in data["instances"]:
            env = inst.get("baseUrlEnv")
            base = (os.getenv(env) if env else None) or inst["baseUrl"]
            self.instances[inst["siteInstanceId"]] = {**inst, "baseUrl": base.rstrip("/")}

    def for_candidate(self, candidate_id: str, label: str) -> dict | None:
        """후보에 연결된 사이트. 라벨까지 같아야 한다 (다른 라벨 사이트로 평가하지 않게)."""
        return next((i for i in self.instances.values()
                     if i["candidateId"] == candidate_id and i["label"] == label), None)

    def base_url(self, site_instance_id: str) -> str | None:
        inst = self.instances.get(site_instance_id)
        return inst["baseUrl"] if inst else None


def public_site_instance(inst: dict, optimization_id: str, conditions: dict, start_path: str) -> dict:
    """Optimization에 저장·응답하는 Site Instance (5.2). `baseUrl`은 내부 값이라 뺀다."""
    return {"siteInstanceId": inst["siteInstanceId"], "optimizationId": optimization_id,
            "candidateId": inst["candidateId"], "label": inst["label"],
            "siteVersion": conditions["siteVersion"], "layoutVersion": conditions["layoutVersion"],
            "candidatePolicyVersion": conditions["candidatePolicyVersion"], "startPath": start_path,
            "status": "READY"}


def capture_snapshot(browser, base_url: str, start_path: str, extract, viewport: dict,
                     candidate_policy_version: str) -> dict:
    """실제 화면을 열어 후보를 뽑는다. `browser` = (open_browser, close_browser)."""
    open_browser, close_browser = browser
    session = open_browser(base_url + start_path)
    try:
        extracted = extract(session["page"])
    finally:
        close_browser(session)
    return snapshot_from_extracted(extracted, start_path, viewport, candidate_policy_version)


def snapshot_from_extracted(extracted: dict, start_path: str, viewport: dict, candidate_policy_version: str) -> dict:
    """후보 스냅샷 (4.6). 런타임 전용 값(domIndex 등)은 남기지 않는다."""
    cands = [{k: c.get(k) for k in SNAPSHOT_FIELDS} for c in extracted["candidates"]]
    reasons: dict[str, int] = {}
    for ex in extracted.get("excluded") or []:
        reasons[ex.get("reason") or "UNKNOWN"] = reasons.get(ex.get("reason") or "UNKNOWN", 0) + 1
    snap = {
        "pageKey": start_path.rstrip("/").removeprefix("/shop/"),
        "urlPath": start_path,
        "pageTitle": extracted["title"],
        "viewport": viewport,
        "candidateCount": len(cands),
        "candidateOrder": [c["semanticKey"] for c in cands],
        "candidates": cands,
        "exclusionReasons": reasons,
        "candidatePolicyVersion": candidate_policy_version,
    }
    snap["snapshotHash"] = canonical_hash(snap)
    return snap


def _path(href: str | None) -> str:
    return urlsplit(href or "").path.rstrip("/")


def validate_snapshot_keys(snap: dict, target_key: str) -> list[str]:
    """semanticKey로 확률을 묶기 전에 확인한다. 빠지거나 겹치면 dict로 바꿀 때 값이 덮어써진다 (정윤희 11-18)."""
    keys = [c.get("semanticKey") for c in snap["candidates"]]
    present = [k for k in keys if k]
    problems = []
    if len(present) != len(keys):
        problems.append("SEMANTIC_KEY_MISSING")
    if len(set(present)) != len(present):
        problems.append("SEMANTIC_KEY_DUPLICATE")
    if target_key not in present:
        problems.append("TARGET_NOT_FOUND")
    return problems


def validate_baseline(snap: dict, target: dict) -> list[str]:
    """기준(C0) 화면 자체가 Optimization 설정과 맞는지 (4.5). 다른 후보는 이 스냅샷과 비교하므로 먼저 확인한다.

    target: `config/optimization.json`의 `target` (`pageKey`, `semanticKey`, `href`, `originalLabel`).
    """
    problems = validate_snapshot_keys(snap, target["semanticKey"])
    if snap["pageKey"] != target["pageKey"]:
        problems.append("PAGE_MISMATCH")
    found = next((c for c in snap["candidates"] if c.get("semanticKey") == target["semanticKey"]), None)
    if found is not None:
        if found["label"] != target["originalLabel"]:
            problems.append("TARGET_LABEL_MISMATCH")
        if _path(found["href"]) != _path(target["href"]):
            problems.append("DESTINATION_MISMATCH")
    return sorted(set(problems))


def validate_against_baseline(snap: dict, base: dict, target_key: str, label: str) -> list[str]:
    """기준 스냅샷과 비교한 위반 사유 목록 (4.5). 비어 있으면 유효.

    라벨 말고 모델 요청에 들어가는 값(화면 제목·주소·후보 영역)도 같아야 한다.
    """
    problems = validate_snapshot_keys(snap, target_key)
    if snap["candidateCount"] != base["candidateCount"]:
        problems.append("CANDIDATE_COUNT_MISMATCH")
    if snap["candidateOrder"] != base["candidateOrder"]:
        problems.append("CANDIDATE_ORDER_MISMATCH")
    if snap["pageTitle"] != base["pageTitle"] or snap["urlPath"] != base["urlPath"]:
        problems.append("PAGE_MISMATCH")
    by_key = {c["semanticKey"]: c for c in snap["candidates"] if c.get("semanticKey")}
    target = by_key.get(target_key)
    if target is not None and target["label"] != label:
        problems.append("TARGET_LABEL_MISMATCH")
    for b in base["candidates"]:
        c = by_key.get(b["semanticKey"])
        if c is None:
            continue
        if c["href"] != b["href"]:
            problems.append("DESTINATION_MISMATCH")
        if c.get("region") != b.get("region"):
            problems.append("REGION_MISMATCH")
        if b["semanticKey"] != target_key and c["label"] != b["label"]:
            problems.append("OTHER_LABEL_CHANGED")
        if any(abs((c["bbox"] or {}).get(k, 0) - (b["bbox"] or {}).get(k, 0)) > BBOX_TOLERANCE_PX
               for k in ("x", "y", "width", "height")):
            problems.append("BBOX_MISMATCH")
    return sorted(set(problems))
