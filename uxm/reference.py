"""Run에 기록된 경로 기준을 확인한다. 입력과 지도 파일은 수정하지 않는다."""
from collections import deque
import hashlib
import json
from pathlib import Path


SITE_MAP_DIRECTORY = Path(__file__).resolve().parents[1] / "config" / "sitemaps"
ALLOWED_FIELDS = ("version", "allowedPaths", "shortestAllowedSteps")
SITE_MAP_FIELDS = ("siteMapVersion", "siteMapHash", "startPageKey", "goalPageKey", "shortestSteps", "distances")


def _normalized_pages(pages):
    """간선을 semanticKey/to 순서로 정렬한 compact UTF-8 JSON을 해시한다."""
    if not isinstance(pages, dict) or not pages:
        raise ValueError("지도 pages가 비어 있습니다.")
    normalized = {}
    for key, page in pages.items():
        if (not isinstance(key, str) or not key or not isinstance(page, dict)
                or not isinstance(page.get("path"), str) or not page["path"]
                or not isinstance(page.get("edges"), list)):
            raise ValueError("지도 화면 형식이 올바르지 않습니다.")
        edges = page["edges"]
        if any(not isinstance(edge, dict) or not isinstance(edge.get("semanticKey"), str)
               or not edge["semanticKey"] or not isinstance(edge.get("to"), str)
               or edge["to"] not in pages for edge in edges):
            raise ValueError("지도 간선 형식이 올바르지 않습니다.")
        unique_edges = {(edge["semanticKey"], edge["to"]) for edge in edges}
        normalized[key] = {"path": page["path"], "edges": [
            {"semanticKey": semantic_key, "to": destination}
            for semantic_key, destination in sorted(unique_edges)
        ]}
    return normalized


def _verify_map_file(value):
    """같은 버전의 파일이 없으면 None, 실패하면 오류 메시지를 반환한다."""
    found = []
    unreadable = False
    for path in sorted(SITE_MAP_DIRECTORY.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            unreadable = True
            continue
        if not isinstance(data, dict):
            unreadable = True
            continue
        if data.get("siteMapVersion") == value["siteMapVersion"]:
            found.append(data)
    if not found:
        if unreadable:
            return "읽을 수 없는 지도 파일이 있어 같은 버전의 사이트 지도를 확인할 수 없습니다."
        return None
    if len(found) != 1:
        return "동일한 버전의 사이트 지도 파일이 여러 개입니다."
    data = found[0]
    try:
        pages = _normalized_pages(data.get("pages"))
        encoded = json.dumps(pages, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
        if digest != data.get("siteMapHash"):
            return "사이트 지도 파일의 해시가 내용과 일치하지 않습니다."
        if digest != value["siteMapHash"]:
            return "사이트 지도 해시가 저장된 Run과 일치하지 않습니다."
        goal = value["goalPageKey"]
        if goal not in pages:
            return "사이트 지도에 목표 화면이 없습니다."
        reverse = {key: [] for key in pages}
        for source, page in pages.items():
            for edge in page["edges"]:
                reverse[edge["to"]].append(source)
        distances = {key: None for key in pages}
        distances[goal] = 0
        queue = deque([goal])
        while queue:
            dest = queue.popleft()
            for source in reverse[dest]:
                if distances[source] is None:
                    distances[source] = distances[dest] + 1
                    queue.append(source)
        if distances != value["distances"]:
            return "저장된 사이트 지도 거리가 지도와 맞지 않습니다."
    except (TypeError, ValueError, KeyError):
        return "사이트 지도 형식 또는 거리 계산을 확인할 수 없습니다."
    return ""


def reference(run):
    """(검증 성공, 비교할 정규화 설정, 검증 메시지)."""
    if "referencePathConfig" not in run:
        return False, None, "경로 기준 설정이 누락됐습니다."
    value = run["referencePathConfig"]
    if value is None:
        return True, None, None
    if not isinstance(value, dict):
        return False, None, "경로 기준 설정을 확인할 수 없습니다."
    basis = value.get("basis", "ALLOWED_PATHS")
    if basis == "SITE_MAP":
        if any(k not in value for k in SITE_MAP_FIELDS):
            return False, None, "사이트 지도 설정 필드가 누락됐습니다."
        if (any(not isinstance(value[k], str) or not value[k].strip() for k in SITE_MAP_FIELDS[:4])
                or type(value["shortestSteps"]) is not int or value["shortestSteps"] < 0
                or not isinstance(value["distances"], dict) or not value["distances"]
                or any(not isinstance(k, str) or not k or (v is not None and (type(v) is not int or v < 0))
                       for k, v in value["distances"].items())):
            return False, None, "사이트 지도 설정 값이 올바르지 않습니다."
        distances = value["distances"]
        if (distances.get(value["goalPageKey"]) != 0
                or distances.get(value["startPageKey"]) != value["shortestSteps"]):
            return False, None, "사이트 지도 시작·목표 거리의 정합성이 맞지 않습니다."
        verification = _verify_map_file(value)
        if verification:
            return False, None, verification
        note = "지도 파일로 재검증하지 않았습니다." if verification is None else None
        return True, (basis, *(value[k] for k in SITE_MAP_FIELDS)), note
    if basis != "ALLOWED_PATHS":
        return False, None, "알 수 없는 경로 기준입니다."
    if any(k not in value for k in ALLOWED_FIELDS):
        return False, None, "저장된 허용 경로 설정을 확인할 수 없습니다."
    if (not isinstance(value["version"], str) or not value["version"]
            or not isinstance(value["allowedPaths"], list) or not value["allowedPaths"]
            or not all(isinstance(path, list) and path and all(isinstance(key, str) and key for key in path)
                       for path in value["allowedPaths"])
            or type(value["shortestAllowedSteps"]) is not int or value["shortestAllowedSteps"] < 0):
        return False, None, "저장된 허용 경로 설정을 확인할 수 없습니다."
    return True, (basis, *(value[k] for k in ALLOWED_FIELDS)), None
