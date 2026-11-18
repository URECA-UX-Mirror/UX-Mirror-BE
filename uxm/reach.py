"""사이트 지도 기반 목표 도달 확률 엔진 (「남은 일과 분업」 19번, 고도화 v1.3 P1·18장, 팀 아이디어 G6).

실행 한 번이 우연히 탄 경로가 아니라, 사이트 구조 전체 기준으로 "이 목표에 도달할 확률"을 계산한다.

    지도(uxm/sitemap.py 파일)의 간선  +  화면마다 모델이 준 후보별 확률(semanticKey 기준)
    → 화면 사이 이동 확률 → maxSteps 안에 도착 화면에 닿을 확률·단계별 분포·도달 시 기대 단계

- 모델 호출은 화면당 1번. 지도 간선에 없는 후보(지도 밖 화면, 같은 화면 앵커 등)로 가는 확률은 '이탈'로 본다.
- 도착 화면은 흡수 상태. 이전 화면 기록·되돌아가기·재방문 제한은 넣지 않는다(구조 기준 추정치).
  실제 루프는 지나온 경로를 요청에 넣고 되돌아가기 선택지도 주므로 실측과 다를 수 있다.
- 모델 확률 합계는 검증에서 ±0.02까지 허용되므로 화면마다 합 1로 정규화한다. 반올림 오차가 이탈이나 1 초과 확률이 되지 않는다.
  이탈(`lostProbability`)은 지도 간선이 없는 후보로 간 확률뿐이다.
- 확률이 0인 이동은 따라가지 않는다. 실제로 확률이 닿는 화면만 `missingPages`에 들어간다.
- 확률이 없는 화면에 양의 확률로 닿으면 그 화면에서 멈춘 것으로 두고 status=INCOMPLETE.
  이때 `reachProbability`만 하한이고, 기대 단계·두 추정치의 차이는 하한이 아니다.
- 실제 화면의 간선이 지도와 다르면 status=MAP_MISMATCH (`mapMismatches`). 지도 기준 추정이 성립하지 않으므로 정상 결과로 쓰지 않는다.
- 순수 함수(`estimate`)와 실제 화면에서 확률을 모으는 함수(`collect_page_probabilities`)를 나눴다.
"""
from uxm.leak import assert_no_leak
from uxm.metrics import validate_probabilities
from uxm.screen_request import build_screen_request
from uxm.sitemap import SHOP, SiteConfig, distances, edges_from_candidates

ENGINE_VERSION = "reach-v1"


def _page_transitions(page: dict, probs: dict[str, float]) -> tuple[dict[str, float], float]:
    """한 화면의 후보 확률(semanticKey → p) → ({다음 화면: p}, 지도 밖으로 가는 확률). 지도 간선 기준.

    확률은 합 1로 정규화한 뒤 나눈다. 이탈 = 지도 간선이 없는 후보의 확률 합 (1 − 합이 아니다).
    """
    total = sum(probs.values())
    if total <= 0:
        raise ValueError("화면의 후보 확률 합이 0입니다.")
    to_of = {e["semanticKey"]: e["to"] for e in page["edges"]}
    nxt: dict[str, float] = {}
    out = 0.0
    for key, p in probs.items():
        if p <= 0:
            continue
        q = p / total
        if key in to_of:
            nxt[to_of[key]] = nxt.get(to_of[key], 0.0) + q
        else:
            out += q
    return nxt, out


def estimate(site_map: dict, page_probs: dict[str, dict[str, float]], start: str, goal: str,
             max_steps: int, map_mismatches: dict | None = None) -> dict:
    """maxSteps 안 목표 도달 추정치.

    page_probs: {pageKey: {semanticKey: 확률}}. 도착 화면은 없어도 된다.
    반환: reachProbability(합), reachByStep[k] = 정확히 k+1번째 이동에 처음 닿을 확률, lostProbability(지도 밖),
    unfinishedProbability(maxSteps 안에 못 닿음), expectedStepsGivenReach, mostLikelyPath, status, missingPages.
    map_mismatches: `collect_page_probabilities`가 찾은 {pageKey: {added, removed}}. 있으면 status=MAP_MISMATCH.
    """
    pages = site_map["pages"]
    for key in (start, goal):
        if key not in pages:
            raise ValueError(f"지도에 없는 화면입니다: {key}")
    if max_steps < 1:
        raise ValueError("max_steps는 1 이상이어야 합니다.")
    trans = {k: _page_transitions(pages[k], p) for k, p in page_probs.items() if k in pages and k != goal}

    dist = {start: 1.0} if start != goal else {}
    reach_by_step, lost, missing = [], 0.0, set()
    stuck = 0.0
    for _ in range(max_steps):
        nxt: dict[str, float] = {}
        arrived = 0.0
        for page, mass in dist.items():
            if mass <= 0:
                continue
            if page not in trans:          # 확률을 모르는 화면: 더 진행하지 않음
                missing.add(page)
                stuck += mass
                continue
            moves, out = trans[page]
            lost += mass * out
            for to, p in moves.items():
                if to == goal:
                    arrived += mass * p
                else:
                    nxt[to] = nxt.get(to, 0.0) + mass * p   # p > 0만 들어 있다 (_page_transitions)
        reach_by_step.append(arrived)
        dist = nxt
    reach = min(1.0, sum(reach_by_step)) if start != goal else 1.0
    unfinished = sum(dist.values()) + stuck
    # 이동 수가 넉넉하면 두 사이트 모두 도달 확률이 1에 가까워진다. 지도상 최단 이동 수 안에 닿을 확률로 차이를 본다.
    shortest = distances(pages, goal).get(start)
    # reachByStep은 maxSteps까지만 계산하므로, maxSteps가 최단 거리보다 작으면 최단 거리 안 확률을 낼 수 없다
    null_reasons = {}
    if shortest is None:
        within = None
        null_reasons["reachWithinShortestProbability"] = "GOAL_UNREACHABLE_ON_MAP"
    elif max_steps < shortest:
        within = None
        null_reasons["reachWithinShortestProbability"] = "MAX_STEPS_BELOW_SHORTEST"
    else:
        within = 1.0 if shortest == 0 else sum(reach_by_step[:shortest])
    expected = (sum((i + 1) * p for i, p in enumerate(reach_by_step)) / reach) if reach > 0 and start != goal else (
        0.0 if start == goal else None)
    return {
        "engineVersion": ENGINE_VERSION,
        "basis": "SITE_MAP",
        "siteMapVersion": site_map.get("siteMapVersion"),
        "siteMapHash": site_map.get("siteMapHash"),
        "startPageKey": start,
        "goalPageKey": goal,
        "maxSteps": max_steps,
        "status": "MAP_MISMATCH" if map_mismatches else "INCOMPLETE" if missing else "COMPLETE",
        "missingPages": sorted(missing),
        "mapMismatches": dict(sorted((map_mismatches or {}).items())),
        "reachProbability": _r(reach),
        "shortestSteps": shortest,
        "reachWithinShortestProbability": _r(within),
        "reachByStep": [_r(p) for p in reach_by_step],
        "lostProbability": _r(lost),
        "unfinishedProbability": _r(unfinished),
        "expectedStepsGivenReach": _r(expected),
        "nullReasons": null_reasons,
        "mostLikelyPath": most_likely_path(trans, start, goal, max_steps),
    }


def most_likely_path(trans: dict, start: str, goal: str, max_steps: int) -> dict | None:
    """maxSteps 안에서 도착 화면까지 확률이 가장 큰 화면 순서 (재방문 없음). 같으면 짧은 경로. 없으면 None."""
    best: dict | None = None

    def walk(page, path, prob):
        nonlocal best
        if len(path) - 1 >= max_steps or page not in trans:
            return
        for to, p in sorted(trans[page][0].items()):
            q = prob * p
            if q <= 0 or to in path or (best is not None and q < best["probability"]):
                continue
            if to == goal:
                if best is None or q > best["probability"] or len(path) + 1 < len(best["path"]):
                    best = {"path": path + [to], "probability": q}
            else:
                walk(to, path + [to], q)

    walk(start, [start], 1.0)
    return None if best is None else {"path": best["path"], "probability": _r(best["probability"])}


def compare(before: dict, after: dict) -> dict:
    """두 추정치의 차이 (After − Before). 지도·시작·도착·maxSteps가 다르면 비교하지 않는다."""
    keys = ("siteMapHash", "startPageKey", "goalPageKey", "maxSteps")
    mismatched = [k for k in keys if before.get(k) != after.get(k)]
    if mismatched:
        return {"comparable": False, "mismatched": mismatched}
    if "MAP_MISMATCH" in (before["status"], after["status"]):
        return {"comparable": False, "mismatched": ["siteStructure"]}   # 지도와 다른 사이트는 지도 기준으로 비교 못 함
    d = lambda k: None if before[k] is None or after[k] is None else _r(after[k] - before[k])  # noqa: E731
    both = before["status"] == after["status"] == "COMPLETE"
    out = {"comparable": True, "deltaReachProbability": d("reachProbability"),
           "deltaReachWithinShortestProbability": d("reachWithinShortestProbability"),
           "deltaExpectedStepsGivenReach": d("expectedStepsGivenReach"),
           "bothComplete": both}
    if not both:
        out["note"] = "INCOMPLETE 추정치가 있어 차이는 하한이 아니며 개선 판단 근거로 쓰지 않는다."
    return out


def map_mismatch(config: SiteConfig, page: dict, candidates: list[dict]) -> dict | None:
    """실제 화면 후보의 지원 화면 간선이 지도와 같은지. 다르면 {added, removed} (semanticKey·to 쌍).

    지도 밖(지원하지 않는 화면·사이트 밖)으로 가는 후보는 간선이 아니라 원래 이탈이라 비교하지 않는다.
    목적지가 바뀐 링크는 removed(옛 목적지)와 added(새 목적지)에 같이 나온다. 간선 추출은 지도 생성과 같은 함수.
    """
    actual = {(e["semanticKey"], e["to"]) for e in edges_from_candidates(config, candidates)}
    expected = {(e["semanticKey"], e["to"]) for e in page["edges"]}
    if actual == expected:
        return None
    pair = lambda items: [{"semanticKey": k, "to": t} for k, t in sorted(items)]  # noqa: E731
    return {"added": pair(actual - expected), "removed": pair(expected - actual)}


def collect_page_probabilities(page, base_url: str, site_map: dict, backend, *, goal: str, persona: str,
                               temperature=None, extract=None, skip=(),
                               config: SiteConfig = SHOP) -> tuple[dict, list[dict], dict]:
    """지도 화면마다 열어서 후보 추출 → 지도 간선 대조 → 모델 요청(목표·사용자 유형, 이력 없음) → 누출 검사 → 모델 1회.

    page: Playwright Page (열린 브라우저 하나를 돌려 쓴다).
    반환: ({pageKey: {semanticKey: p}}, 화면별 기록, {pageKey: 간선 차이}). 간선 차이가 있으면 `estimate`에 넘긴다.
    skip: 확률이 필요 없는 화면(보통 도착 화면). 간선 대조는 skip 화면도 한다.
    """
    if extract is None:
        from uxm.candidates import extract
    out, records, mismatches = {}, [], {}
    for key, info in sorted(site_map["pages"].items()):
        page.goto(base_url.rstrip("/") + info["path"], wait_until="networkidle")
        extracted = extract(page)
        diff = map_mismatch(config, info, extracted["candidates"])
        if diff:
            mismatches[key] = diff
        if key in skip:
            continue
        if not extracted["candidates"]:
            records.append({"pageKey": key, "status": "NO_CANDIDATES"})
            continue
        request, meta = build_screen_request(extracted, goal=goal, persona=persona)
        assert_no_leak(request)
        response = backend.decide(request, temperature)
        probs = dict(response["answers"]["next_click"]["probabilities"])
        validate_probabilities(probs, list(request["questions"]["next_click"]["criteria"]))
        out[key] = {meta["optionMap"][oid]: p for oid, p in probs.items()}
        records.append({"pageKey": key, "status": "COMPLETED", "candidateCount": len(extracted["candidates"]),
                        "request": request, "response": response})
    return out, records, mismatches


def _r(x):
    return None if x is None else round(x, 6)
