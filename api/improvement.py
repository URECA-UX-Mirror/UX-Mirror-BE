"""자동 개선 최종안 검증 Run (#101, 「자동 개선 에이전트 설계 v2.1」 §4.6 (a), 역할 배분 6).

자동 개선 루프가 고른 최종안 사이트로 실제 Run을 돌려, 확률 추정(⑤′)과 실제 탐색 결과를 비교할 수 있게 한다.

- `POST /api/runs`는 고정 Before/After와 등록된 시나리오 사이트만 받고, Optimizer 검증 경로(`start_verification()`)는
  평가가 끝난 Optimization에서만 돌아서 쓰지 않는다. 대신 같은 구조(동결 조건 → `run_factory.build_run` →
  `Runner.create()`)를 그대로 따른다.
- 목표마다 v1(C0 등록 사이트, variant=before)과 최종안(variant=after) Run 1쌍을 순서대로 만든다. 한 번에 Run 하나
  (단일 활성 Run). 같은 목표의 두 Run은 같은 experimentKey라 기존 비교·재생 화면을 그대로 쓸 수 있다.
- 최종안 주소는 내부 값이라 Run·결과에 남기지 않는다. Run에는 `improvementContext`(siteInstanceId·이름)만 남는다.
- 스크립트(`scripts/improve.py` 등)에서 부르는 함수다. 같은 결과 폴더를 쓰는 API 서버에서 Run이 진행 중이면 두 Run이
  겹칠 수 있으므로, 실행 중에는 API로 Run을 만들지 않는다.
"""
import copy
import os
import re
from types import SimpleNamespace
from urllib.parse import urlsplit

from api import run_factory
from api.presets import Presets
from api.site_instances import SiteRegistry

FINAL_SITE_INSTANCE_ID = "improve-final"
BASELINE_SITE_INSTANCE_ID = "site-c0"
VERIFY_RUN_WAIT_S = 600
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
MODEL_FIELDS = ("modelVersion", "modelEndpointId", "temperature", "promptVersion", "goalJudgmentMode")


def run_verification(frozen: dict, site_url: str, label: str, goals: list, *, runner=None, results_dir=None,
                     presets=None, registry=None, wait_s: float = VERIFY_RUN_WAIT_S) -> list[dict]:
    """목표마다 v1·최종안 Run 1쌍을 실행하고 Run 목록을 돌려준다.

    frozen: 루프 시작 때 동결한 조건. `improvementId`, `personaId`, `persona`, `startPath`, `viewport`,
            `limits`(`maxSteps` 필수, 나머지는 기본값), `modelMetadata`(있으면 지금 모델 설정과 같아야 실행).
    site_url: 최종안 사이트 주소 (`http(s)://host:port`). 기록하지 않는다.
    label: 최종안 버튼 이름.
    goals: 목표 목록. preset goalId 문자열 또는 {goalId, goal, verification, referencePathConfig}.
    반환: 실행 순서대로 [{runId, goalId, experimentKey, variant, executionRole, label, status, validity}].
    입력이 잘못되면 Run을 하나도 만들지 않고 ValueError. 다른 Run이 진행 중이면 ApiError(RUN_ALREADY_ACTIVE).
    Run이 wait_s 안에 끝나지 않으면 TimeoutError (그 뒤 Run은 만들지 않는다).
    """
    presets = presets or Presets()
    registry = registry or SiteRegistry()
    base = _check_frozen(frozen)
    final_url = _check_site_url(site_url)
    if not isinstance(label, str) or not label.strip():
        raise ValueError("최종안 이름(label)이 필요합니다.")
    goal_list = _resolve_goals(goals, presets)
    v1 = registry.instances.get(BASELINE_SITE_INSTANCE_ID)
    if v1 is None:
        raise ValueError(f"v1 사이트({BASELINE_SITE_INSTANCE_ID})가 config/site_instances.json에 없습니다.")
    _check_model(frozen)
    if runner is None:
        runner = _default_runner(results_dir)

    sites = {BASELINE_SITE_INSTANCE_ID: v1["baseUrl"], FINAL_SITE_INSTANCE_ID: final_url}
    previous = runner.site_base_url
    runner.site_base_url = lambda sid: sites.get(sid) or (previous(sid) if previous else None)
    out = []
    try:
        runner.ensure_ready()
        for goal in goal_list:
            key = f"{base['improvementId']}-{goal['goalId']}"
            for role, variant, sid, name in (("BASELINE", "before", BASELINE_SITE_INSTANCE_ID, v1["label"]),
                                             ("CANDIDATE", "after", FINAL_SITE_INSTANCE_ID, label.strip())):
                run = _build_run(base, goal, key, variant)
                run["improvementContext"] = {"improvementId": base["improvementId"], "goalId": goal["goalId"],
                                             "label": name, "siteInstanceId": sid, "executionRole": role}
                runner.create(run, goal)
                runner.wait(wait_s)
                if runner.active_run_id == run["runId"]:
                    raise TimeoutError(f"{goal['goalId']} {role} 검증 Run이 제한 시간 안에 끝나지 않았습니다: {run['runId']}")
                done = runner.store.load_run(run["runId"])
                out.append({"runId": run["runId"], "goalId": goal["goalId"], "experimentKey": key, "variant": variant,
                            "executionRole": role, "label": name, "status": done.get("status"),
                            "validity": done.get("validity")})
    finally:
        runner.site_base_url = previous
    return out


def _build_run(base: dict, goal: dict, key: str, variant: str) -> dict:
    req = SimpleNamespace(viewport=None, maxSteps=base["limits"]["maxSteps"], variant=variant, experimentKey=key,
                          startPath=base["startPath"])
    persona = {"personaId": base["personaId"], "persona": base["persona"]}
    run = run_factory.build_run(req, goal, persona, run_factory.model_settings())
    run.update(viewport=dict(base["viewport"]), limits=dict(base["limits"]))
    return run


def _check_frozen(frozen: dict) -> dict:
    if not isinstance(frozen, dict):
        raise ValueError("frozen은 dict여야 합니다.")
    imp = frozen.get("improvementId")
    if not isinstance(imp, str) or not _ID_RE.match(imp):
        raise ValueError("improvementId는 영문·숫자·_.- 로 된 문자열이어야 합니다.")
    for k in ("personaId", "persona"):
        if not isinstance(frozen.get(k), str) or not frozen[k].strip():
            raise ValueError(f"frozen.{k}가 필요합니다.")
    start = frozen.get("startPath")
    if not isinstance(start, str) or not start.startswith("/"):
        raise ValueError("frozen.startPath는 /로 시작하는 경로여야 합니다.")
    limits = frozen.get("limits") or {}
    if not isinstance(limits.get("maxSteps"), int) or limits["maxSteps"] < 1:
        raise ValueError("frozen.limits.maxSteps(1 이상 정수)가 필요합니다.")
    viewport = frozen.get("viewport") or {"width": 1440, "height": 900}
    return {"improvementId": imp, "personaId": frozen["personaId"], "persona": frozen["persona"], "startPath": start,
            "viewport": dict(viewport), "limits": {**run_factory.DEFAULT_LIMITS, **limits}}


def _check_site_url(site_url) -> str:
    try:
        u = urlsplit(site_url) if isinstance(site_url, str) else None
        port = u.port if u else None   # 잘못된 포트면 ValueError
    except ValueError:
        u = None
    if u is None or u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        raise ValueError("site_url은 인증 정보 없는 http(s) 주소여야 합니다.")
    host = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
    return f"{u.scheme}://{host}" + (f":{port}" if port else "")


def _resolve_goals(goals, presets) -> list[dict]:
    if not isinstance(goals, (list, tuple)) or not goals:
        raise ValueError("목표가 하나 이상 필요합니다.")
    out, seen = [], set()
    for g in goals:
        goal = presets.goal(g) if isinstance(g, str) else copy.deepcopy(g) if isinstance(g, dict) else None
        if goal is None:
            raise ValueError(f"알 수 없는 목표입니다: {g}")
        missing = [k for k in ("goalId", "goal", "verification") if not goal.get(k)]
        if missing or not _ID_RE.match(str(goal["goalId"])):
            raise ValueError(f"목표 형식이 올바르지 않습니다: {', '.join(missing) or 'goalId'}")
        if goal["goalId"] in seen:
            raise ValueError(f"같은 목표가 두 번 있습니다: {goal['goalId']}")
        seen.add(goal["goalId"])
        out.append({k: goal.get(k) for k in ("goalId", "goal", "verification", "referencePathConfig")})
    return out


def _check_model(frozen: dict) -> None:
    meta = frozen.get("modelMetadata")
    if not meta:
        return
    s = run_factory.model_settings()
    now = {**{k: s[k] for k in MODEL_FIELDS if k in s}, "promptVersion": run_factory.PROMPT_VERSION}
    diff = [k for k in MODEL_FIELDS if k in meta and meta[k] != now.get(k)]
    if diff:
        raise ValueError(f"동결한 모델 설정과 지금 설정이 다릅니다: {', '.join(diff)}")


def _default_runner(results_dir):
    from api.integrations import Integrations
    from api.runner import Runner
    from api.store import DEFAULT_RESULTS_DIR, RunStore
    # API 서버와 같은 결과 폴더 → 검증 Run을 기존 조회·비교·재생 화면에서 그대로 연다
    results_dir = results_dir or os.getenv("UXM_RESULTS_DIR") or DEFAULT_RESULTS_DIR
    return Runner(RunStore(results_dir), Integrations())
