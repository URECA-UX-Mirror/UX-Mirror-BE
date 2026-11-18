"""UX Optimizer — Optimization 엔진 (고도화 기획안 v1.3, 3번 담당 13.4).

기존 Explorer(P0 `POST /api/runs`)는 그대로 두고 별도 흐름으로 돈다.

    CREATED → EVALUATING → EVALUATED → VERIFYING → COMPLETED
                 └→ FAILED        └→ (검증 요청 전까지 대기)    └→ FAILED

1. 생성: 후보 풀(`config/optimization.json`)·모델·프롬프트·목표·사이트 조건을 동결한다 (11.1).
2. 평가(비동기): 후보별 Site Instance 화면을 실제로 열어 스냅샷 → 기준(C0) 대비 유효성 검사 → Zoro-1 Direct 1회.
   원본 요청·응답은 `raw/`에 보존하고 Request·Snapshot Hash를 남긴다 (6장, 11.3).
   지표 검증과 후보 선정은 4번 정책 함수(`compute_evaluation_metrics`, `select_candidate`)에 맡긴다.
3. 검증(비동기): 기준과 검증 후보 사이트를 기존 Runner로 차례로 실행한다 (5.5, 7장). Run은 기존 구조 그대로 저장되고
   `optimizationContext`로 연결된다. 최종 판단은 4번 `judge_verification`에 맡긴다 (8장).
4. 보고서: 완료된 Optimization의 근거를 한곳에 모은다 (11.4).

다른 담당 모듈은 `api/integrations.py`를 거쳐 부른다. 모듈이 없으면 조용히 대체하지 않고 503으로 알린다.
"""
import concurrent.futures as cf
import copy
import json
import logging
import os
import secrets
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

from api import run_factory
from api.errors import ApiError, error_detail
from api.schemas import P0_VIEWPORT
from api.presets import normalize_verification
from api.site_instances import (SiteRegistry, canonical_hash, capture_snapshot, public_site_instance,
                                validate_against_baseline, validate_baseline, validate_snapshot_keys)
from api.store import read_json, write_json_atomic
from api.timeutil import now_iso, now_stamp
from uxm.leak import assert_no_leak
from uxm.metrics import validate_probabilities
from uxm.screen_request import build_screen_request

log = logging.getLogger("uxm.api.optimizer")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPTIMIZATION_CONFIG_PATH = os.path.join(ROOT, "config", "optimization.json")
SCHEMA_VERSION = "1.0"
MAX_STEPS = 8
VERIFY_RUN_WAIT_S = 600
ACTIVE_STATUSES = ("CREATED", "EVALUATING", "VERIFYING")
REPORT_LIMITATIONS = ("기준과 검증 후보를 각각 1회만 실행한 단일 관찰값이다. 실행 간 변동성은 검증하지 않았다. "
                      "IMPROVEMENT_OBSERVED는 사전 정의한 개선 조건이 관찰됐다는 뜻이며 UX 개선을 입증하지 않는다.")


def page_of(href_or_path: str) -> str:
    return urlsplit(href_or_path).path.rstrip("/").removeprefix("/shop/")


class OptimizationStore:
    """`results/optimizations/<optimizationId>/optimization.json` + `raw/` (원본 모델 요청·응답)."""

    def __init__(self, results_dir: str):
        self.dir = os.path.join(results_dir, "optimizations")

    def path(self, opt_id: str, *parts: str) -> str:
        return os.path.join(self.dir, opt_id, *parts)

    def exists(self, opt_id: str) -> bool:
        return os.path.isfile(self.path(opt_id, "optimization.json"))

    def load(self, opt_id: str) -> dict:
        return read_json(self.path(opt_id, "optimization.json"))

    def save(self, opt: dict) -> None:
        opt["updatedAt"] = now_iso()
        write_json_atomic(self.path(opt["optimizationId"], "optimization.json"), opt)

    def save_raw(self, opt_id: str, name: str, payload: dict) -> str:
        ref = f"raw/{name}.json"
        write_json_atomic(self.path(opt_id, *ref.split("/")), payload)
        return ref

    def list(self) -> list[dict]:
        if not os.path.isdir(self.dir):
            return []
        return [self.load(d) for d in sorted(os.listdir(self.dir)) if self.exists(d)]


class Optimizer:
    def __init__(self, results_dir: str, run_store, runner, integrations, presets,
                 site_registry: SiteRegistry | None = None, config_path: str = OPTIMIZATION_CONFIG_PATH):
        self.store = OptimizationStore(results_dir)
        self.run_store, self.runner, self.integrations, self.presets = run_store, runner, integrations, presets
        self.sites = site_registry or SiteRegistry()
        self.config_path = config_path
        self._lock = threading.Lock()
        self._job: tuple[str, str] | None = None     # (optimizationId, "EVALUATING"|"VERIFYING")
        self._thread: threading.Thread | None = None
        # 제한 시간을 넘기고도 끝나지 않은 모델 호출 (optimizationId, future). 끝날 때까지 모델을 쓰는 새 작업을 막는다
        self._stuck_model_call: tuple[str, cf.Future] | None = None
        # 후보 화면 스냅샷. 1번 모듈이 제공하면 api/integrations.py에서 교체한다.
        self.snapshot = self._capture_snapshot

    # ------------------------------------------------------------------ 상태

    def busy_reason(self, run: dict | None = None) -> str | None:
        """기존 Runner가 Run을 만들기 전에 묻는다. 평가·검증 중에는 다른 Run이 모델·Runner를 쓰지 않게 막는다.

        검증 중에는 기준 Run과 후보 Run 사이의 빈 구간에도 막아야 한다. 이 Optimization의 검증 Run만 통과시킨다.
        """
        stuck = self._pending_model_call()
        if stuck is not None:   # 끝나지 않은 모델 호출과 겹치지 않게 (시간 제한 없이 끝날 때까지)
            return f"Optimization {stuck} 의 이전 모델 호출이 아직 끝나지 않았습니다."
        job = self._job
        if job is None:
            return None
        opt_id, kind = job
        if kind == "VERIFYING" and ((run or {}).get("optimizationContext") or {}).get("optimizationId") == opt_id:
            return None
        what = "후보 평가가" if kind == "EVALUATING" else "후보 검증 실행이"
        return f"Optimization {opt_id} {what} 진행 중입니다."

    def active_job(self) -> dict | None:
        """`/api/health`용: 진행 중인 Optimization (없으면 None). 끝나지 않은 모델 호출만 남았으면 MODEL_CALL_PENDING."""
        job = self._job
        if job:
            return {"optimizationId": job[0], "status": job[1]}
        stuck = self._pending_model_call()
        return {"optimizationId": stuck, "status": "MODEL_CALL_PENDING"} if stuck else None

    def _pending_model_call(self) -> str | None:
        stuck = self._stuck_model_call
        if stuck is None:
            return None
        if stuck[1].done():
            self._stuck_model_call = None
            return None
        return stuck[0]

    def wait(self, timeout: float | None = None) -> None:
        """테스트용: 진행 중인 평가·검증이 끝날 때까지 기다린다."""
        if self._thread is not None:
            self._thread.join(timeout)

    def policy(self):
        found = self.integrations.optimizer_policy()
        if found is None:
            raise ApiError(503, "OPTIMIZER_POLICY_UNAVAILABLE",
                           "후보 선정·최종 판단 정책 모듈(uxm/optimizer_policy.py)이 준비되지 않았습니다.")
        return found

    def load_or_404(self, opt_id: str) -> dict:
        if not self.store.exists(opt_id):
            raise ApiError(404, "OPTIMIZATION_NOT_FOUND", "Optimization을 찾을 수 없습니다.")
        return self.store.load(opt_id)

    # ------------------------------------------------------------------ 생성 (10.2)

    def create(self, source_run_id: str | None) -> dict:
        self.policy()
        cfg = read_json(self.config_path)
        goal, persona = self.presets.goal(cfg["goalId"]), self.presets.persona(cfg["personaId"])
        if goal is None or persona is None:
            raise ApiError(422, "EXPERIMENT_CONDITION_MISMATCH", "Optimization 설정의 목표·사용자 유형이 preset에 없습니다.")
        if source_run_id is not None and not self.run_store.exists(source_run_id):
            raise ApiError(404, "RUN_NOT_FOUND", "기준 진단 Run을 찾을 수 없습니다.")
        settings = run_factory.model_settings()
        target = cfg["target"]
        opt_id = f"opt_{now_stamp()}_{secrets.token_hex(2)}"
        conditions = {"siteVersion": run_factory.SITE_VERSION, "layoutVersion": run_factory.LAYOUT_VERSION,
                      "candidatePolicyVersion": run_factory.CANDIDATE_POLICY_VERSION,
                      "initialBrowserStateId": run_factory.INITIAL_BROWSER_STATE_ID}
        opt = {
            "schemaVersion": SCHEMA_VERSION,
            "optimizationId": opt_id,
            "status": "CREATED",
            "verificationPhase": None,
            "sourceRunId": source_run_id,
            "targetPageKey": target["pageKey"],
            "targetSemanticKey": target["semanticKey"],
            "originalLabel": target["originalLabel"],
            "target": dict(target),
            "candidateLabels": [dict(c, candidateOrder=i) for i, c in enumerate(cfg["candidates"])],
            "siteInstances": [],
            "evaluations": [],
            "progress": {"evaluatedCount": 0, "totalCount": len(cfg["candidates"])},
            "selection": None,
            "recommendedCandidateId": None,
            "verificationCandidateId": None,
            "recommendationStatus": None,
            "baselineRunId": None,
            "validationRunId": None,
            "selectionPolicyVersion": cfg["selectionPolicyVersion"],
            "verificationPolicyVersion": cfg["verificationPolicyVersion"],
            "verification": None,
            "siteRecheck": None,
            "finalDecision": None,
            "resultOrigin": settings["resultOrigin"],
            "frozenConditions": {
                "goalId": goal["goalId"], "goal": goal["goal"], "verification": goal["verification"],
                "referencePathConfig": goal["referencePathConfig"],
                "personaId": persona["personaId"], "persona": persona["persona"],
                "startPath": target["startPath"], "viewport": dict(P0_VIEWPORT),
                "limits": {"maxSteps": MAX_STEPS, **run_factory.DEFAULT_LIMITS},
                **conditions,
            },
            "modelMetadata": {"modelVersion": settings["modelVersion"], "modelEndpointId": settings["modelEndpointId"],
                              "temperature": settings["temperature"], "promptVersion": run_factory.PROMPT_VERSION,
                              "decisionMode": "DIRECT", "goalJudgmentMode": settings["goalJudgmentMode"]},
            "executionMetadata": {"modelCallCount": 0, "evaluationTimeMs": None, "verificationTimeMs": None},
            "error": None,
            "createdAt": now_iso(),
            "updatedAt": None,
        }
        # Runner의 생성 잠금도 같이 잡아, 확인과 시작 사이에 일반 Run이 끼어들지 못하게 한다
        with self._lock, self.runner.creation_lock:
            if self.runner.active_run_id is not None or self._job is not None or self._pending_model_call():
                raise ApiError(409, "RUN_ALREADY_ACTIVE", "다른 실행이나 후보 평가가 진행 중입니다.", retryable=True)
            self.store.save(opt)
            self._start(opt_id, "EVALUATING", self._evaluate)
        return opt

    def _start(self, opt_id: str, kind: str, target) -> None:
        self._job = (opt_id, kind)
        self._thread = threading.Thread(target=self._run_job, args=(opt_id, target), daemon=True,
                                        name=f"uxm-opt-{opt_id}")
        self._thread.start()

    def _run_job(self, opt_id: str, target) -> None:
        try:
            target(opt_id)
        finally:
            # 끝나지 않은 모델 호출이 있으면 _stuck_model_call에 남아 있어, 작업이 끝나도 그 호출이 끝날 때까지 busy_reason이 막는다
            with self._lock:
                self._job = None

    # ------------------------------------------------------------------ 후보 평가 (4장, 6장)

    def _evaluate(self, opt_id: str) -> None:
        opt = self.store.load(opt_id)
        started = time.monotonic()
        try:
            compute_metrics, select_candidate, _ = self.policy()
            opt["status"] = "EVALUATING"
            self.store.save(opt)
            frozen = opt["frozenConditions"]
            settings = run_factory.model_settings()
            backend = self.integrations.backend(settings["config"])
            # 목표 경로 확률은 고정 환불 preset의 허용 경로 기준 (ALLOWED_PATHS). 기준은 평가마다 기록한다
            allowed_next = {p[1] for p in (frozen["referencePathConfig"] or {}).get("allowedPaths", []) if len(p) > 1}

            baseline_snap = None
            for cand in opt["candidateLabels"]:
                ev, snap = self._evaluate_one(opt, cand, baseline_snap, backend, settings, allowed_next)
                if cand["role"] == "BASELINE":
                    if ev["candidateValidity"] == "INVALID_CANDIDATE":
                        opt["evaluations"].append(ev)   # 근거는 남기고 이후 후보는 평가하지 않는다
                        raise _JobFailure("BASELINE_INVALID", "기준(C0) 화면이 Optimization 설정과 다릅니다: "
                                          + ", ".join(ev["invalidReasons"]))
                    if snap is None or ev["evaluationStatus"] != "COMPLETED":
                        opt["evaluations"].append(ev)
                        raise _JobFailure("BASELINE_NOT_EVALUATED", "기준(C0) 후보를 평가하지 못했습니다.")
                    baseline_snap = snap
                if ev["evaluationStatus"] == "COMPLETED":
                    ev.update(compute_metrics(dict(ev)) or {})
                opt["evaluations"].append(ev)
                opt["progress"]["evaluatedCount"] += 1
                self.store.save(opt)

            selection = select_candidate(copy.deepcopy(opt["evaluations"])) or {}
            opt["selection"] = selection
            for key in ("recommendedCandidateId", "verificationCandidateId", "recommendationStatus"):
                opt[key] = selection.get(key)
            opt["selectionPolicyVersion"] = selection.get("selectionPolicyVersion", opt["selectionPolicyVersion"])
            opt["status"] = "EVALUATED"
        except _JobFailure as e:
            self._fail(opt, "EVALUATING", e.code, e.message)
        except Exception as e:   # 예상 못 한 오류도 EVALUATING에 남기지 않는다
            log.exception("optimization %s evaluation failed", opt_id)
            self._fail(opt, "EVALUATING", "OPTIMIZATION_FAILED", f"후보 평가 중 오류가 발생했습니다: {type(e).__name__}")
        finally:
            opt["executionMetadata"]["evaluationTimeMs"] = round((time.monotonic() - started) * 1000)
            self.store.save(opt)

    def _evaluate_one(self, opt, cand, baseline_snap, backend, settings, allowed_next) -> tuple[dict, dict | None]:
        frozen = opt["frozenConditions"]
        ev = {"candidateId": cand["candidateId"], "candidateOrder": cand["candidateOrder"], "label": cand["label"],
              "role": cand["role"], "semanticClarityRank": cand.get("semanticClarityRank"),
              "semanticKey": opt["targetSemanticKey"], "siteInstanceId": None,
              "snapshotHash": None, "requestHash": None, "candidateValidity": None, "invalidReasons": [],
              "evaluationStatus": None, "probabilities": None, "targetOptionProbability": None,
              "goalPathProbability": None, "goalPathBasis": None, "metricNullReasons": {},
              "modelVersion": settings["modelVersion"],
              "modelEndpointId": settings["modelEndpointId"], "promptVersion": run_factory.PROMPT_VERSION,
              "temperature": settings["temperature"], "modelLatencyMs": None, "rawRequestRef": None,
              "rawResponseRef": None, "error": None, "evaluatedAt": None}
        inst = self.sites.for_candidate(cand["candidateId"], cand["label"])
        if inst is None:
            ev.update(candidateValidity="INVALID_CANDIDATE", invalidReasons=["SITE_INSTANCE_MISSING"],
                      evaluationStatus="SKIPPED")
            return ev, None
        opt["siteInstances"].append(public_site_instance(inst, opt["optimizationId"], frozen, frozen["startPath"]))
        ev["siteInstanceId"] = inst["siteInstanceId"]
        try:
            snap = self.snapshot(inst, frozen)
        except Exception:
            # 예외 문구에는 내부 사이트 주소가 들어 있을 수 있어 서버 로그에만 남긴다
            log.exception("snapshot failed for %s", cand["candidateId"])
            ev.update(evaluationStatus="FAILED", error=error_detail("BROWSER_ERROR", "후보 화면을 열지 못했습니다."))
            return ev, None
        ev["snapshotHash"] = snap["snapshotHash"]
        self.store.save_raw(opt["optimizationId"], f"snapshot_{cand['candidateId']}", snap)
        if cand["role"] == "BASELINE":
            problems = validate_baseline(snap, opt["target"])
        elif baseline_snap is None:
            problems = validate_snapshot_keys(snap, opt["targetSemanticKey"])
        else:
            problems = validate_against_baseline(snap, baseline_snap, opt["targetSemanticKey"], cand["label"])
        if problems:
            ev.update(candidateValidity="INVALID_CANDIDATE", invalidReasons=problems, evaluationStatus="SKIPPED")
            return ev, snap

        extracted = {"title": snap["pageTitle"], "url": snap["urlPath"], "candidates": snap["candidates"]}
        request, meta = build_screen_request(extracted, goal=frozen["goal"], persona=frozen["persona"])
        ev["requestHash"] = canonical_hash(request)
        try:
            assert_no_leak(request)
        except RuntimeError as e:
            ev.update(candidateValidity="INVALID_CANDIDATE", invalidReasons=["LEAK_DETECTED"], evaluationStatus="SKIPPED",
                      error=error_detail("LEAK_CHECK_FAILED", str(e)))
            return ev, snap
        ev["candidateValidity"] = "VALID"
        ev["rawRequestRef"] = self.store.save_raw(opt["optimizationId"], f"model_request_{cand['candidateId']}", request)
        response, latency, err = self._call_model(backend, request, settings, frozen["limits"]["modelTimeoutMs"],
                                                  opt["optimizationId"])
        opt["executionMetadata"]["modelCallCount"] += 1
        ev["modelLatencyMs"], ev["evaluatedAt"] = latency, now_iso()
        if err:
            ev.update(evaluationStatus="FAILED", error=err)
            return ev, snap
        ev["rawResponseRef"] = self.store.save_raw(opt["optimizationId"], f"model_response_{cand['candidateId']}", response)
        option_ids = list(request["questions"]["next_click"]["criteria"])
        try:
            probs = dict(response["answers"]["next_click"]["probabilities"])
            validate_probabilities(probs, option_ids)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            ev.update(evaluationStatus="FAILED", error=error_detail("MODEL_RESPONSE_INVALID", repr(e)))
            return ev, snap
        # 스냅샷에서 semanticKey 누락·중복을 이미 걸렀으므로 option → semanticKey 변환에서 값이 덮어써지지 않는다
        by_key = {meta["optionMap"][oid]: p for oid, p in probs.items()}
        ev.update(evaluationStatus="COMPLETED", probabilities=by_key,
                  targetOptionProbability=by_key[opt["targetSemanticKey"]])
        if allowed_next:
            ev.update(goalPathBasis=(frozen["referencePathConfig"] or {}).get("basis", "ALLOWED_PATHS"),
                      goalPathProbability=sum(p for oid, p in probs.items()
                                              if page_of(meta["destination"][oid]) in allowed_next))
        else:   # 계산할 수 없으면 0으로 바꾸지 않고 null과 사유를 남긴다
            ev["metricNullReasons"]["goalPathProbability"] = "NO_REFERENCE_PATH"
        return ev, snap

    def _call_model(self, backend, request, settings, timeout_ms, opt_id: str) -> tuple[dict | None, int, dict | None]:
        """모델 1회 호출. 제한 시간을 넘기면 그 후보는 MODEL_TIMEOUT.

        스레드는 강제로 멈출 수 없으므로, 늦은 호출이 실제로 끝날 때까지(같은 시간만큼 더) 기다린 뒤 다음 후보로 간다.
        그래도 안 끝나면 호출이 겹치지 않도록 평가를 중단한다. 그 호출이 실제로 끝날 때까지는 시간 제한 없이
        일반 Run·다른 Optimization 시작을 막는다 (`busy_reason`, `_pending_model_call`).
        """
        started = time.perf_counter()
        pool = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="uxm-opt-model")
        future = pool.submit(backend.decide, request, settings["temperature"])
        try:
            response = future.result(timeout=timeout_ms / 1000)
            return response, round((time.perf_counter() - started) * 1000), None
        except cf.TimeoutError:
            latency = round((time.perf_counter() - started) * 1000)
            try:
                future.result(timeout=timeout_ms / 1000)
            except cf.TimeoutError:
                self._stuck_model_call = (opt_id, future)
                raise _JobFailure("MODEL_TIMEOUT", "모델 호출이 제한 시간 뒤에도 끝나지 않아 후보 평가를 중단했습니다.")
            except Exception:
                pass
            return None, latency, error_detail("MODEL_TIMEOUT", "모델이 제한 시간 안에 응답하지 않았습니다.")
        except Exception as e:
            return None, round((time.perf_counter() - started) * 1000), error_detail(
                "MODEL_UNAVAILABLE", f"모델 호출에 실패했습니다: {type(e).__name__}")
        finally:
            pool.shutdown(wait=False)

    def _capture_snapshot(self, inst: dict, frozen: dict) -> dict:
        browser = self.integrations.browser()
        if browser is None:
            raise RuntimeError("브라우저 모듈(uxm/browser.py)이 준비되지 않았습니다.")
        from uxm.candidates import extract
        return capture_snapshot(browser, inst["baseUrl"], frozen["startPath"], extract, frozen["viewport"],
                                frozen["candidatePolicyVersion"])

    # ------------------------------------------------------------------ 실제 검증 (7장)

    def start_verification(self, opt_id: str) -> dict:
        _, _, judge = self.policy()
        with self._lock, self.runner.creation_lock:
            opt = self.load_or_404(opt_id)
            if opt["status"] in ("VERIFYING", "COMPLETED"):
                raise ApiError(409, "OPTIMIZATION_ALREADY_PROCESSED", "이미 검증 중이거나 완료된 Optimization입니다.")
            if opt["status"] != "EVALUATED":
                raise ApiError(409, "OPTIMIZATION_NOT_READY", "후보 평가가 끝난 뒤에만 검증할 수 있습니다.")
            if not opt["verificationCandidateId"]:
                raise ApiError(409, "OPTIMIZATION_NOT_READY", "검증할 후보가 선정되지 않았습니다.")
            if self.runner.active_run_id is not None or self._job is not None or self._pending_model_call():
                raise ApiError(409, "RUN_ALREADY_ACTIVE", "다른 실행이 진행 중입니다.", retryable=True)
            for cid in self._baseline_id(opt), opt["verificationCandidateId"]:
                if self._site_for(opt, cid) is None:
                    raise ApiError(422, "INVALID_SITE_INSTANCE", f"후보 {cid}의 사이트 인스턴스가 등록되어 있지 않습니다.")
            self._check_conditions(opt)
            self.runner.ensure_ready()
            opt.update(status="VERIFYING", verificationPhase="BASELINE")
            self.store.save(opt)
            self._start(opt_id, "VERIFYING", self._verify)
        return opt

    def _baseline_id(self, opt: dict) -> str:
        return next(c["candidateId"] for c in opt["candidateLabels"] if c["role"] == "BASELINE")

    def _site_for(self, opt: dict, cid: str) -> dict | None:
        label = next(c["label"] for c in opt["candidateLabels"] if c["candidateId"] == cid)
        return self.sites.for_candidate(cid, label)

    def _check_conditions(self, opt: dict) -> None:
        """생성 때 동결한 모델 설정·목표·사용자 유형과 지금 설정이 같아야 검증한다 (7.3, 11.2).

        검증 Run은 동결 값으로 만들지만, preset이 바뀌었다면 평가 때와 다른 실험이 되므로 시작하지 않는다.
        """
        s, m, f = run_factory.model_settings(), opt["modelMetadata"], opt["frozenConditions"]
        diff = [k for k, now in (("modelVersion", s["modelVersion"]), ("modelEndpointId", s["modelEndpointId"]),
                                 ("temperature", s["temperature"]), ("promptVersion", run_factory.PROMPT_VERSION),
                                 ("goalJudgmentMode", s["goalJudgmentMode"]))
                if m[k] != now]
        goal, persona = self.presets.goal(f["goalId"]), self.presets.persona(f["personaId"])
        if goal is None:
            diff.append("goalId")
        else:
            diff += [k for k in ("goal", "referencePathConfig") if goal[k] != f[k]]
            if normalize_verification(goal["verification"]) != normalize_verification(f["verification"]):
                diff.append("verification")
        if persona is None or persona["persona"] != f["persona"]:
            diff.append("persona")
        if diff:
            raise ApiError(422, "EXPERIMENT_CONDITION_MISMATCH",
                           f"Optimization 생성 때와 실행 조건이 다릅니다: {', '.join(diff)}")

    def _verify(self, opt_id: str) -> None:
        opt = self.store.load(opt_id)
        started = time.monotonic()
        try:
            _, _, judge = self.policy()
            plan = (("BASELINE", self._baseline_id(opt), "before", "baselineRunId"),
                    ("CANDIDATE", opt["verificationCandidateId"], "after", "validationRunId"))
            # 평가 뒤 사이트가 바뀌었으면 평가한 화면과 다른 화면을 검증하게 된다. Run을 만들기 전에 다시 확인한다
            opt["siteRecheck"] = {cid: self._recheck_site(opt, cid) for _, cid, _, _ in plan}
            self.store.save(opt)
            runs = {}
            for phase, cid, variant, field in plan:
                opt["verificationPhase"] = phase
                self.store.save(opt)
                run, goal = self._build_run(opt, cid, variant, phase)
                try:
                    self.runner.create(run, goal)
                except ApiError as e:
                    raise _JobFailure(e.code, e.message) from e
                opt[field] = run["runId"]
                self.store.save(opt)
                self.runner.wait(VERIFY_RUN_WAIT_S)
                if self.runner.active_run_id == run["runId"]:
                    raise _JobFailure("RUN_TIMEOUT", f"{phase} 검증 실행이 제한 시간 안에 끝나지 않았습니다.")
                runs[phase] = self.run_store.load_run(run["runId"])
            result = judge(copy.deepcopy(runs["BASELINE"]), copy.deepcopy(runs["CANDIDATE"])) or {}
            opt["verification"] = result
            opt["finalDecision"] = result.get("finalDecision", result.get("decision"))
            opt["verificationPolicyVersion"] = result.get("verificationPolicyVersion", opt["verificationPolicyVersion"])
            opt.update(status="COMPLETED", verificationPhase=None)
        except _JobFailure as e:
            self._fail(opt, "VERIFYING", e.code, e.message)
        except Exception as e:
            log.exception("optimization %s verification failed", opt_id)
            self._fail(opt, "VERIFYING", "OPTIMIZATION_FAILED", f"검증 중 오류가 발생했습니다: {type(e).__name__}")
        finally:
            opt["executionMetadata"]["verificationTimeMs"] = round((time.monotonic() - started) * 1000)
            self.store.save(opt)

    def _recheck_site(self, opt: dict, cid: str) -> dict:
        """검증 직전, 후보 사이트의 현재 화면이 평가 때 스냅샷과 같은지 (같은 Site Instance·같은 snapshotHash).

        다르거나 확인할 수 없으면 검증을 중단한다. 예외 문구는 내부 주소가 있을 수 있어 로그에만 남긴다.
        """
        ev = next(e for e in opt["evaluations"] if e["candidateId"] == cid)
        site = self._site_for(opt, cid)
        if site is None or site["siteInstanceId"] != ev["siteInstanceId"] or not ev["snapshotHash"]:
            raise _JobFailure("SITE_INSTANCE_CHANGED", f"후보 {cid}의 사이트가 평가 때와 다릅니다.")
        try:
            snap = self.snapshot(site, opt["frozenConditions"])
        except Exception:
            log.exception("recheck snapshot failed for %s", cid)
            raise _JobFailure("SITE_RECHECK_FAILED", f"후보 {cid}의 화면을 다시 확인하지 못했습니다.") from None
        if snap["snapshotHash"] != ev["snapshotHash"]:
            self.store.save_raw(opt["optimizationId"], f"recheck_snapshot_{cid}", snap)
            raise _JobFailure("SITE_INSTANCE_CHANGED", f"후보 {cid}의 화면이 평가 때와 다릅니다.")
        return {"siteInstanceId": site["siteInstanceId"], "snapshotHash": snap["snapshotHash"], "checkedAt": now_iso()}

    def _build_run(self, opt: dict, cid: str, variant: str, phase: str) -> tuple[dict, dict]:
        """검증 Run과 그 목표. 목표·사용자 유형·성공 조건·경로 기준·제한은 생성 때 동결한 값을 쓴다 (현재 preset 아님)."""
        frozen = copy.deepcopy(opt["frozenConditions"])
        req = SimpleNamespace(viewport=None, maxSteps=frozen["limits"]["maxSteps"], variant=variant,
                              experimentKey=opt["optimizationId"], startPath=frozen["startPath"])
        goal = {k: frozen[k] for k in ("goalId", "goal", "verification", "referencePathConfig")}
        persona = {k: frozen[k] for k in ("personaId", "persona")}
        run = run_factory.build_run(req, goal, persona, run_factory.model_settings())
        run.update(viewport=dict(frozen["viewport"]), limits=dict(frozen["limits"]))
        site = self._site_for(opt, cid)
        label = next(c["label"] for c in opt["candidateLabels"] if c["candidateId"] == cid)
        # variant는 기존 비교 API를 그대로 쓰기 위한 자리(기준=before, 후보=after)이고, 역할은 executionRole로 구분한다 (5.5)
        run["optimizationContext"] = {"optimizationId": opt["optimizationId"], "candidateId": cid, "label": label,
                                      "siteInstanceId": site["siteInstanceId"], "executionRole": phase}
        return run, goal

    # ------------------------------------------------------------------ 실패·복구

    def _fail(self, opt: dict, phase: str, code: str, message: str) -> None:
        opt["error"] = {**error_detail(code, message), "failedPhase": phase, "lastStatus": opt["status"]}
        opt.update(status="FAILED", verificationPhase=None)

    def recover_interrupted(self) -> list[str]:
        """서버가 꺼지며 진행 중에 멈춘 Optimization을 FAILED로 정리한다 (이미 만든 Run ID는 그대로 둔다)."""
        fixed = []
        for opt in self.store.list():
            if opt.get("status") in ACTIVE_STATUSES:
                self._fail(opt, opt["status"], "OPTIMIZATION_INTERRUPTED", "서버 재시작으로 중단되었습니다.")
                self.store.save(opt)
                fixed.append(opt["optimizationId"])
        return fixed

    # ------------------------------------------------------------------ 보고서 (10.6, 11.4)

    def report(self, opt_id: str) -> dict:
        opt = self.load_or_404(opt_id)
        if opt["status"] != "COMPLETED":
            raise ApiError(409, "OPTIMIZATION_NOT_READY", "완료된 Optimization만 보고서를 볼 수 있습니다.")
        runs = {k: self.run_store.load_run(opt[k]) for k in ("baselineRunId", "validationRunId")}
        return {
            "schemaVersion": SCHEMA_VERSION,
            "optimizationId": opt_id,
            "status": opt["status"],
            "finalDecision": opt["finalDecision"],
            "resultOrigin": opt["resultOrigin"],
            "target": {"pageKey": opt["targetPageKey"], "semanticKey": opt["targetSemanticKey"],
                       "originalLabel": opt["originalLabel"]},
            "selection": opt["selection"],
            "recommendationStatus": opt["recommendationStatus"],
            "recommendedCandidateId": opt["recommendedCandidateId"],
            "verificationCandidateId": opt["verificationCandidateId"],
            "evaluations": opt["evaluations"],
            "verification": opt["verification"],
            "runs": {"baseline": _run_outcome(runs["baselineRunId"]), "candidate": _run_outcome(runs["validationRunId"])},
            "policyVersions": {"selection": opt["selectionPolicyVersion"], "verification": opt["verificationPolicyVersion"]},
            "modelMetadata": opt["modelMetadata"],
            "frozenConditions": opt["frozenConditions"],
            "executionMetadata": opt["executionMetadata"],
            "limitations": {"baselineRunCount": 1, "candidateRunCount": 1, "repeated": False,
                            "note": REPORT_LIMITATIONS},
            "trace": {"sourceRunId": opt["sourceRunId"],
                      "siteInstances": opt["siteInstances"],
                      "rawRefs": {e["candidateId"]: {"request": e["rawRequestRef"], "response": e["rawResponseRef"]}
                                  for e in opt["evaluations"]}},
            "createdAt": opt["createdAt"],
            "updatedAt": opt["updatedAt"],
        }


def _run_outcome(run: dict) -> dict:
    s = run.get("summary") or {}
    return {"runId": run["runId"], "variant": run["variant"],
            "executionRole": (run.get("optimizationContext") or {}).get("executionRole"),
            "label": (run.get("optimizationContext") or {}).get("label"),
            "status": run["status"], "terminationReason": run["terminationReason"], "validity": run["validity"],
            "resultOrigin": run["resultOrigin"], "goalReached": s.get("goalReached"), "stepCount": s.get("stepCount"),
            "navigationCost": s.get("navigationCost"), "backCount": s.get("backCount"),
            "path": [v.get("pageKey") for v in s.get("path") or []], "executionTimeMs": s.get("executionTimeMs")}


class _JobFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def public_view(opt: dict) -> dict:
    """GET 응답 (10.5). 저장 형식 그대로이며 내부 주소(baseUrl)는 애초에 저장하지 않는다."""
    return json.loads(json.dumps(opt, ensure_ascii=False))
