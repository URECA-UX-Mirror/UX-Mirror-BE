"""에이전트 루프 (2번 흐름·저장, 이슈 #7).

Zoro-1이 고른 행동을 그대로 실행하고, 실패를 처리하고, 언제 멈출지 정하고, 전부 기록한다.
고르는 것은 모델(backend.decide)이고, 루프는 서버의 choice를 바꾸지 않는다 (계약 10.1).

    run_agent(settings, backend, page) -> dict

- 실행 API(api/runner.py)가 Run 폴더·run.json을 만들고 브라우저를 연 뒤 `page`만 넘긴다. 루프는 브라우저를 열고 닫지 않는다.
- 돌려주는 값: status, terminationReason, progress, summary, error, finishedAt, comparisonContext.leakCheckPassed.
  validity는 API가 terminationReason·error.code로 정한다 (계약 6.1, PR #5 3794897).
- 기준: 인터페이스·데이터 계약 v1.3.5 6장·8장·11~13장, 1번 2번 협의 사항 3·5절, 테스트 시나리오 C.

한 단계:
  도착 판정 → 같은 화면 반복·최대 단계 검사 → 후보 추출(실패한 후보 제외, + 이전 화면이 있으면 GO_BACK)
  → 요청 생성 → 누출 검사 → 원본 요청 저장 → 모델 호출 → 응답 검증 → 원본 응답·스크린샷 저장
  → click / go_back → 결과 처리 → Step·Event 저장 → run.json progress 갱신

"Step 저장(실행 전에 먼저)"(역할 분담)은 원본 모델 요청·응답과 스크린샷을 행동 전에 남기는 것으로 지킨다.
steps.jsonl은 한 줄씩 추가만 하므로 Step 한 줄은 행동 결과까지 채운 뒤 쓴다 (계약 18.2 완전한 줄만).
"""
import concurrent.futures as cf
import importlib
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

from uxm import goal as goal_mod
from uxm.candidates import extract as extract_candidates
from uxm.leak import assert_no_leak
from uxm.metrics import decision_metrics, validate_probabilities
from uxm.screen_request import build_screen_request
from uxm.status import STATUS_BY_REASON
from uxm.storage import SCHEMA_VERSION, RunStore, StorageError, now_iso

PROBABILITY_EPSILON = 1e-12
CANDIDATE_SCOPE = ["상단 메뉴", "본문", "사이드 메뉴", "경로 표시", "하단"]
FAILED_ACTION_REASON = "FAILED_ACTION"   # 다시 판단할 때 뺀 후보의 exclusionReasons 키

# 누르지 않았거나 화면이 그대로라 그 후보만 빼고 다시 판단하는 경우 (협의 5절)
RETRY_REASONS = {"ELEMENT_NOT_FOUND", "ELEMENT_NOT_UNIQUE", "REJECTED_UNSAFE"}

ERROR_MESSAGES = {
    "MODEL_TIMEOUT": "모델이 제한 시간 안에 응답하지 않았습니다.",
    "MODEL_UNAVAILABLE": "모델 서버에 연결하지 못했습니다.",
    "MODEL_RESPONSE_INVALID": "AI 판단 결과를 정상적으로 처리할 수 없습니다.",
    "INVALID_CHOICE": "AI가 후보에 없는 선택지를 골랐습니다.",
    "BROWSER_ERROR": "브라우저 조작 중 오류가 발생했습니다.",
    "PAGE_TIMEOUT": "화면을 다시 불러와도 로딩이 끝나지 않았습니다.",
    "LEAK_CHECK_FAILED": "모델 요청에 실험 정보가 섞여 호출하지 않았습니다.",
    "STORAGE_ERROR": "실행 기록을 저장하지 못했습니다.",
    "NO_CANDIDATES": "누를 수 있는 후보가 없고 이전 화면으로도 돌아갈 수 없습니다.",
}


@dataclass
class Env:
    """루프가 쓰는 화면 쪽 함수들. 테스트에서는 가짜로 바꿔 끼운다."""
    extract: Callable
    verify_goal: Callable
    click: Callable
    go_back: Callable
    reload: Callable
    capture: Callable
    page_key: Callable
    page_signature: Callable


def browser_module():
    """1번 uxm/browser.py (PR #2). 그 파일이 아직 없을 때만 임시 함수(_browser_stub, 협의 6절)를 쓴다.

    browser.py가 있는데 그 안에서 import가 실패하면 오류를 그대로 낸다 (임시 함수로 조용히 넘어가지 않게, #8 리뷰).
    `from uxm import browser`는 파일이 없을 때도 일반 ImportError(name='uxm')를 내서 구분이 안 되므로
    import_module로 불러와 ModuleNotFoundError.name을 본다.
    """
    try:
        return importlib.import_module("uxm.browser")
    except ModuleNotFoundError as e:
        if e.name != "uxm.browser":
            raise
    return importlib.import_module("uxm._browser_stub")


def default_env() -> Env:
    b = browser_module()
    return Env(extract=extract_candidates, verify_goal=goal_mod.verify_goal, click=b.click, go_back=b.go_back,
               reload=b.reload, capture=b.capture, page_key=b.page_key, page_signature=b.page_signature)


def run_agent(settings: dict, backend, page, env: Env | None = None) -> dict:
    return _Loop(settings, backend, page, env or default_env()).run()


def agent_settings(run: dict, goal: dict, run_dir: str, start_url: str, shop_base_url: str) -> dict:
    """run_agent에 넘기는 설정. 실행 API(api/runner.py)와 scripts/run_c.py가 같이 쓴다.

    run: READY Run(run.json), goal: config/presets.json의 목표 preset.
    성공 조건·허용 경로는 채점용이라 모델 요청에 넣지 않는다 (계약 9.3).
    """
    v = goal["verification"]
    return {
        "run": run, "runId": run["runId"], "runDir": run_dir, "shopBaseUrl": shop_base_url,
        "startUrl": start_url, "startPath": run["startPath"], "goalId": run["goalId"], "goal": run["goal"],
        "persona": run["persona"], "successCondition": {"path": v["path"], "text": v["text"]},
        "referencePathConfig": run["referencePathConfig"], "limits": run["limits"], "viewport": run["viewport"],
        "goalJudgmentMode": run["goalJudgmentMode"], "temperature": run["temperature"],
        "promptVersion": run["promptVersion"], "resultOrigin": run["resultOrigin"],
    }


def _norm(url_or_path: str) -> str:
    return urlsplit(url_or_path).path.rstrip("/") or "/"


def _error(code: str, message: str | None = None, details: dict | None = None) -> dict:
    return {"code": code, "message": message or ERROR_MESSAGES.get(code, code), "retryable": False,
            "details": details, "occurredAt": now_iso()}


class _Stop(Exception):
    """루프 종료. reason=terminationReason, error=ErrorDetail."""

    def __init__(self, reason: str, error: dict | None = None):
        super().__init__(reason)
        self.reason, self.error = reason, error


class _Loop:
    def __init__(self, settings, backend, page, env: Env):
        self.s, self.backend, self.page, self.env = settings, backend, page, env
        self.limits = settings["limits"]
        self.store = RunStore.open_dir(settings["runDir"])
        self.origin = settings["resultOrigin"]
        self.success = settings["successCondition"]
        self.allowed = settings["referencePathConfig"]["allowedPaths"]
        self.shortest = settings["referencePathConfig"]["shortestAllowedSteps"]
        self.t0 = time.monotonic()
        self.pool = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="uxm-model")

        self.step_id = 0            # 저장한 Step 수 = 마지막 stepId
        self.step_count = 0         # 계약 6.4 stepCount
        self.n = {"model_calls": 0, "clicks": 0, "nav_clicks": 0, "model_back": 0, "system_back": 0,
                  "deviation": 0}
        self.events = 0
        self.visits: list[dict] = []
        self.visit_count: dict[str, int] = {}
        self.stack: list[dict] = []          # 지금까지 들어온 화면 (뒤로가기로 빠짐). 마지막이 현재 화면
        self.history: list[dict] = []        # recentActions
        self.leak_passed: bool | None = None
        self._reset_screen()

    # ------------------------------------------------------------ 실행

    def run(self) -> dict:
        try:
            self._event("RUN_STARTED", None, None, startPath=self.s["startPath"])
            self._enter("RUN_START", None, None)
            while True:
                self._tick()
        except _Stop as stop:
            return self._finish(stop.reason, stop.error)
        except StorageError as e:
            return self._finish("STORAGE_FAILED", _error("STORAGE_ERROR", details={"exception": repr(e)}),
                                best_effort=True)
        finally:
            self.pool.shutdown(wait=False)

    def _tick(self) -> None:
        if (time.monotonic() - self.t0) * 1000 > self.limits["runTimeoutMs"]:
            raise _Stop("RUN_TIMEOUT")
        reached, evidence = self.env.verify_goal(self.page, self.success)
        if reached:
            self._arrive(evidence)
            raise _Stop("GOAL_VERIFIED")
        here = self.stack[-1]
        if self.visit_count[here["signature"]] >= self.limits["maxSamePageVisits"]:
            raise _Stop("REVISIT_LIMIT_REACHED")
        if self.step_count >= self.limits["maxSteps"]:
            raise _Stop("MAX_STEPS_REACHED")
        self._decide_and_act(here)

    # ------------------------------------------------------------ 한 번의 판단과 행동

    def _decide_and_act(self, here: dict) -> None:
        extracted = self.env.extract(self.page)
        kept = [c for c in extracted["candidates"] if c["semanticKey"] not in self.failed_keys]
        cands = [{**c, "optionId": f"option_{i:02d}"} for i, c in enumerate(kept, 1)]   # 다시 판단해도 01부터
        if not cands:
            self._system_back(here, "후보 없음")
            return

        back_ok = (len(self.stack) > 1 and not self.back_failed
                   and self.n["model_back"] < self.limits["maxModelBackActions"])
        screen = {**extracted, "candidates": cands}
        request, meta = build_screen_request(screen, history=self.history, failed=self.failed, go_back=back_ok)
        try:
            assert_no_leak(request)
        except RuntimeError as e:
            self.leak_passed = False
            raise _Stop("LEAK_CHECK_FAILED", _error("LEAK_CHECK_FAILED", details={"reason": str(e)}))
        self.leak_passed = True   # 실패하면 바로 끝나므로 여기 오면 지금까지 모든 요청이 통과 (계약 6.5)

        step_id = self.step_id + 1
        retry_n = self.retry_n   # 행동 결과 처리에서 바뀌기 전 값 (candidateSetId용)
        self.store.save_raw(step_id, "request", request)
        response, latency = self._call_model(request, meta)
        self.store.save_raw(step_id, "response", response)
        option_ids = list(request["questions"]["next_click"]["criteria"])
        choice, probs = self._validate(response, option_ids)
        back_id = meta["backOptionId"]
        is_back = choice == back_id
        chosen = None if is_back else next(c for c in cands if c["optionId"] == choice)

        shot = self.env.capture(self.page, self.store.dir, step_id, cands, probs, choice)
        decision = self._decision(choice, is_back, probs, back_id, cands, latency)

        timeout = self.limits["pageChangeTimeoutMs"]
        if is_back:
            expected = self.stack[-2]
            res = self.env.go_back(self.page, expected["path"], timeout)
        else:
            res = self.env.click(self.page, chosen, timeout)

        outcome = self._handle(res, here, chosen, is_back)   # 카운터·방문·스택 갱신, 종료면 _Stop을 미룸
        step = self._step(step_id, here, extracted, cands, back_id, probs, choice, shot, decision, res,
                          is_back, outcome, retry_n)
        self.store.append_step(step)
        self.step_id = step_id
        self._after_step(step, res, here, is_back, outcome)
        if outcome.get("stop"):
            raise outcome["stop"]

    def _call_model(self, request: dict, meta: dict) -> tuple[dict, int]:
        kwargs = {"meta": meta} if getattr(self.backend, "wants_meta", False) else {}
        self.n["model_calls"] += 1
        started = time.perf_counter()
        future = self.pool.submit(self.backend.decide, request, self.s.get("temperature"), **kwargs)
        try:
            response = future.result(timeout=self.limits["modelTimeoutMs"] / 1000)
        except cf.TimeoutError:
            raise _Stop("MODEL_TIMEOUT", _error("MODEL_TIMEOUT"))
        except Exception as e:   # requests.ReadTimeout = 연결은 됐고 응답이 늦음. 그 외는 서버에 닿지 못함
            code = "MODEL_TIMEOUT" if type(e).__name__ == "ReadTimeout" else "MODEL_UNAVAILABLE"
            reason = "MODEL_TIMEOUT" if code == "MODEL_TIMEOUT" else "MODEL_ERROR"
            raise _Stop(reason, _error(code, details={"exception": repr(e)}))
        return response, round((time.perf_counter() - started) * 1000)

    def _validate(self, response: dict, option_ids: list[str]) -> tuple[str, dict]:
        try:
            answer = response["answers"]["next_click"]
            choice, probs = answer["choice"], dict(answer["probabilities"])
            validate_probabilities(probs, option_ids)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise _Stop("MODEL_ERROR", _error("MODEL_RESPONSE_INVALID", details={"reason": repr(e)}))
        if choice not in option_ids:   # 서버 choice를 그대로 쓰되, 요청에 없는 ID면 실행하지 않는다
            raise _Stop("MODEL_ERROR", _error("INVALID_CHOICE", details={"choice": choice}))
        return choice, probs

    # ------------------------------------------------------------ 행동 결과 처리 (협의 5절)

    def _handle(self, res: dict, here: dict, chosen: dict | None, is_back: bool) -> dict:
        result, reason, executed = res["result"], res.get("reason"), res.get("executed", False)
        out: dict = {"navigated": False}

        if is_back:
            if result == "NAVIGATED":
                out.update(navigated=True, entered=self._back_succeeded(here))
            elif reason == "PAGE_TIMEOUT" and self._reload_ok():
                if _norm(self.page.url) == self.stack[-2]["path"]:   # 늦게라도 이전 화면에 도착
                    out.update(navigated=True, entered=self._back_succeeded(here))
                else:
                    out["stop"] = _Stop("BACK_RECOVERY_FAILED")
            elif reason == "PAGE_TIMEOUT":
                out["stop"] = _Stop("PAGE_LOAD_FAILED", _error("PAGE_TIMEOUT"))
            elif reason == "ACTION_ERROR":
                out["stop"] = _Stop("BROWSER_ERROR", _error("BROWSER_ERROR", details={"error": res.get("error")}))
            elif _norm(self.page.url) != here["path"]:   # 엉뚱한 화면으로 가 버림 → 어디인지 확실하지 않아 멈춤
                out["stop"] = _Stop("BACK_RECOVERY_FAILED")
            else:                                        # NO_HISTORY 등: GO_BACK을 빼고 다시 판단
                self.back_failed = True
                self.failed.append({"steps": ["이전 화면으로 돌아가기 선택", "돌아갈 수 없음"]})
                self.retry_n += 1
            return out

        label = chosen["label"]
        if executed:
            self.step_count += 1
            self.n["clicks"] += 1
        if result == "NAVIGATED":
            self.n["nav_clicks"] += 1
            out.update(navigated=True, entered=self._push_new_screen(here, label))
        elif result == "NO_CHANGE":
            self._fail_candidate(chosen, "화면 변화 없음")
        elif reason in RETRY_REASONS or (reason == "OUT_OF_SCOPE" and not executed):
            self._fail_candidate(chosen, "누를 수 없음")
        elif reason == "OUT_OF_SCOPE":   # 누른 뒤 사이트 밖으로 나감 → 시스템 BACK
            out["recover_out_of_scope"] = True
            self._fail_candidate(chosen, "사이트 밖으로 나감")
        elif reason == "PAGE_TIMEOUT":   # 주소는 이미 바뀜 → 새로고침해서 확인
            if self._reload_ok():
                out.update(navigated=True, entered=self._push_new_screen(here, label))
            else:
                out["stop"] = _Stop("PAGE_LOAD_FAILED", _error("PAGE_TIMEOUT"))
        else:                            # ACTION_ERROR 등
            out["stop"] = _Stop("BROWSER_ERROR", _error("BROWSER_ERROR", details={"error": res.get("error")}))
        return out

    def _after_step(self, step: dict, res: dict, here: dict, is_back: bool, outcome: dict) -> None:
        a = step["action"]
        if is_back and outcome.get("navigated"):
            self._event("BACK_EXECUTED", "MODEL_DECISION", step["stepId"], result="NAVIGATED",
                        fromPageSignature=here["signature"], toPageSignature=a["toPageSignature"])
        elif is_back:
            self._event("BACK_FAILED", "MODEL_DECISION", step["stepId"], result="BACK_FAILED",
                        reason=res.get("reason"), fromPageSignature=here["signature"],
                        expectedPageSignature=self.stack[-2]["signature"] if len(self.stack) > 1 else None)
        else:
            self._event("ACTION_COMPLETED", "MODEL_DECISION", step["stepId"], result=a["result"],
                        reason=a["reason"], fromPath=a["fromPath"], toPath=a["toPath"])
        entered = outcome.get("entered")
        if entered and entered["visitCount"] > 1:
            self._event("PAGE_REVISITED", "MODEL_DECISION", step["stepId"], pageSignature=entered["signature"],
                        visitCount=entered["visitCount"], visitCause=entered["visitCause"])
        if outcome.get("recover_out_of_scope"):
            if not self._try_system_back(here, "사이트 밖에서 복귀", same_screen=True):
                raise _Stop("OUT_OF_SCOPE_NAVIGATION")
        self._update_progress()

    # ------------------------------------------------------------ 화면 이동 기록

    def _screen_now(self) -> dict:
        title = (self.page.title() or "").split(" | ")[0]
        return {"pageKey": self.env.page_key(self.page.url), "signature": self.env.page_signature(self.page),
                "path": _norm(self.page.url), "title": title}

    def _enter(self, cause: str, entered_by: str | None, step_id: int | None, keep_screen_state=False) -> dict:
        """화면에 들어감: PathVisit 추가, 방문 수, 화면별 상태 초기화. 스택은 부른 쪽이 관리.

        keep_screen_state: 같은 화면으로 돌아올 때(사이트 밖에서 복귀) 실패한 후보 기록을 지우지 않는다.
        """
        now = self._screen_now()
        if cause == "RUN_START":
            self.stack = [now]
        elif cause == "MODEL_NAVIGATION":
            self.stack.append(now)
        else:                                  # MODEL_BACK, SYSTEM_BACK_RECOVERY: 스택은 이미 줄였음
            self.stack[-1] = now
        self.visit_count[now["signature"]] = self.visit_count.get(now["signature"], 0) + 1
        self.visits.append({"sequence": len(self.visits), "pageKey": now["pageKey"],
                            "pageSignature": now["signature"], "path": now["path"], "enteredBy": entered_by,
                            "visitCause": cause, "stepId": step_id})
        if not keep_screen_state:
            self._reset_screen()
        return {**now, "visitCount": self.visit_count[now["signature"]], "visitCause": cause}

    def _back_succeeded(self, here: dict) -> dict:
        """모델 GO_BACK이 이전 화면에 도착: stepCount·modelBackCount에 넣고 스택에서 뺀다 (계약 6.4)."""
        self.step_count += 1
        self.n["model_back"] += 1
        self.stack.pop()
        self.history.append({"steps": [f"{here['title']} 화면", "이전 화면으로 돌아감"]})
        return self._enter("MODEL_BACK", "GO_BACK", self.step_id + 1)

    def _push_new_screen(self, here: dict, label: str) -> dict:
        entered = self._enter("MODEL_NAVIGATION", "CLICK", self.step_id + 1)
        route = [v["pageKey"] for v in self.stack]
        if not any(p[:len(route)] == route for p in self.allowed):
            self.n["deviation"] += 1   # 계약 14.2 REFERENCE_PATH_DEVIATION과 같은 기준
        self.history.append({"steps": [f"{here['title']} 화면", f"{label} 선택"]})
        return entered

    def _reset_screen(self) -> None:
        self.failed_keys: set[str] = set()
        self.failed: list[dict] = []          # previousFailedActions (지금 화면에서 확인된 실패)
        self.back_failed = False
        self.retry_n = 0

    def _fail_candidate(self, cand: dict, why: str) -> None:
        self.failed_keys.add(cand["semanticKey"])
        self.failed.append({"steps": [f"{cand['label']} 선택", why]})
        self.retry_n += 1

    def _reload_ok(self) -> bool:
        return self.env.reload(self.page, self.limits["pageChangeTimeoutMs"])["result"] == "NAVIGATED"

    def _system_back(self, here: dict, why: str) -> None:
        """클릭 후보 0개: 시스템 BACK (Step 없이 Event만, 계약 12.1·13.3)."""
        if len(self.stack) < 2:
            raise _Stop("NO_VALID_CANDIDATES", _error("NO_CANDIDATES"))
        if not self._try_system_back(here, why, same_screen=False):
            raise _Stop("BACK_RECOVERY_FAILED")
        self._update_progress()

    def _try_system_back(self, here: dict, why: str, same_screen: bool) -> bool:
        """시스템 BACK. same_screen=True면 사이트 밖에서 지금 화면(here)으로, False면 이전 화면으로 돌아간다."""
        if self.n["system_back"] >= self.limits["maxSystemBackActions"]:
            return False
        target = here if same_screen else self.stack[-2]
        res = self.env.go_back(self.page, target["path"], self.limits["pageChangeTimeoutMs"])
        if res["result"] != "NAVIGATED":
            self._event("BACK_FAILED", "SYSTEM_RECOVERY", None, result="BACK_FAILED", reason=res.get("reason"),
                        fromPageSignature=here["signature"], expectedPageSignature=target["signature"])
            return False
        self.n["system_back"] += 1
        if not same_screen:
            self.stack.pop()
        # 같은 화면으로 돌아오면 밖으로 나간 후보를 다시 고르지 않게 실패 기록을 유지한다
        entered = self._enter("SYSTEM_BACK_RECOVERY", "GO_BACK", None, keep_screen_state=same_screen)
        self._event("BACK_EXECUTED", "SYSTEM_RECOVERY", None, result="NAVIGATED",
                    fromPageSignature=here["signature"], toPageSignature=entered["signature"])
        if entered["visitCount"] > 1:
            self._event("PAGE_REVISITED", "SYSTEM_RECOVERY", None, pageSignature=entered["signature"],
                        visitCount=entered["visitCount"], visitCause="SYSTEM_BACK_RECOVERY")
        self.history.append({"steps": [f"{here['title']} 화면", why, "뒤로 감"]})
        return True

    # ------------------------------------------------------------ Step 만들기

    def _decision(self, choice, is_back, probs, back_id, cands, latency) -> dict:
        m = decision_metrics(probs, back_id)
        click_ids = [c["optionId"] for c in cands]
        mass = sum(probs[o] for o in click_ids)
        warnings = []
        if len(click_ids) >= 2 and mass <= PROBABILITY_EPSILON:   # 계약 12.3 판정 순서 2
            m["clickOnlyNormalizedEntropy"] = m["clickOnlyTop2Margin"] = None
            warnings.append({"code": "NO_CLICK_PROBABILITY_MASS",
                             "message": "클릭 후보의 조건부 지표를 계산할 수 없습니다."})
        route = [v["pageKey"] for v in self.stack]
        nxt = {p[len(route)] for p in self.allowed if len(p) > len(route) and p[:len(route)] == route}
        hits = [probs[c["optionId"]] for c in cands if self.env.page_key(c["href"]) in nxt]
        return {
            "selectedOptionId": choice, "selectedActionType": "GO_BACK" if is_back else "CLICK",
            "mode": "DIRECT", "resultOrigin": self.origin, "goalJudgmentMode": self.s["goalJudgmentMode"],
            "modelGoalReached": None, "modelGoalYesProb": None, "verifiedGoalReached": False,
            "goalJudgmentMismatch": False, **m,
            "goalPathProbability": sum(hits) if hits else None,
            "modelLatencyMs": latency, "metricWarnings": warnings,
        }

    def _step(self, step_id, here, extracted, cands, back_id, probs, choice, shot, decision, res, is_back,
              outcome, retry_n) -> dict:
        reasons: dict[str, int] = {}
        for e in extracted["excluded"]:
            reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
        dropped = len(extracted["candidates"]) - len(cands)
        if dropped:
            reasons[FAILED_ACTION_REASON] = dropped
        excluded_n = sum(reasons.values())
        entered = outcome.get("entered")
        stop = outcome.get("stop")
        action_error = None
        if res.get("reason") == "ACTION_ERROR":
            action_error = _error("BROWSER_ERROR", details={"error": res.get("error")})
        set_id = f"{here['signature']}_initial" if retry_n == 0 else f"{here['signature']}_retry_{retry_n}"
        return {
            "schemaVersion": SCHEMA_VERSION, "runId": self.store.run_id, "stepId": step_id,
            "timestamp": shot.get("capturedAt") or now_iso(),
            "page": {"pageKey": here["pageKey"], "pageSignature": here["signature"], "path": here["path"],
                     "title": extracted["title"], "viewport": shot.get("viewport"),
                     "screenshotSize": shot.get("screenshotSize"), "scroll": shot.get("scroll"),
                     "screenshotRef": shot.get("screenshotRef"), "overlayScreenshotRef": shot.get("overlayRef"),
                     "capturedAt": shot.get("capturedAt")},
            "candidateSet": {
                "candidateSetId": set_id, "candidateSetChanged": retry_n > 0,
                "candidateScope": CANDIDATE_SCOPE,
                "candidateCountCollected": len(cands) + excluded_n,
                "candidateCountSent": len(cands) + (1 if back_id else 0),
                "excludedCandidateCount": excluded_n, "exclusionReasons": reasons,
                "actionSetIncludesBack": back_id is not None, "backOptionId": back_id,
                "candidates": [{k: c[k] for k in ("optionId", "label", "region", "context", "href", "bbox",
                                                   "semanticKey")}
                               | {"actionType": "CLICK", "probability": probs[c["optionId"]],
                                  "selected": c["optionId"] == choice} for c in cands],
            },
            "decision": decision,
            "action": {
                "selectedOptionId": choice, "executedOptionId": choice if res.get("executed") else None,
                "actionType": "GO_BACK" if is_back else "CLICK", "eventOrigin": "MODEL_DECISION",
                "result": res["result"], "reason": res.get("reason"), "executed": bool(res.get("executed")),
                "fromPath": here["path"], "toPath": res.get("toPath"),
                "fromPageSignature": here["signature"],
                "toPageSignature": entered["signature"] if entered else
                (here["signature"] if res["result"] == "NO_CHANGE" else None),
                "startedAt": res.get("startedAt"), "finishedAt": res.get("finishedAt"), "error": action_error,
            },
            "statusAfterStep": STATUS_BY_REASON[stop.reason] if stop else "RUNNING",
            "error": None,
        }

    # ------------------------------------------------------------ 저장

    def _event(self, event_type: str, origin: str | None, step_id: int | None, **details) -> None:
        self.events += 1
        self.store.append_event({"schemaVersion": SCHEMA_VERSION, "eventId": f"event_{self.events:02d}",
                                 "runId": self.store.run_id, "stepId": step_id, "timestamp": now_iso(),
                                 "eventType": event_type, "eventOrigin": origin, "details": details})

    def _progress(self) -> dict:
        return {"stepRecordCount": self.step_id, "stepCount": self.step_count, "maxSteps": self.limits["maxSteps"],
                "currentPageKey": self.stack[-1]["pageKey"] if self.stack else None, "lastUpdatedAt": now_iso()}

    def _update_progress(self) -> None:
        run = self.store.read_run()
        run["progress"] = self._progress()
        self.store.write_run(run)

    def _arrive(self, evidence: dict) -> None:
        here = self.stack[-1]
        shot = self.env.capture(self.page, self.store.dir, self.step_id + 1, [], None, None)
        self._event("GOAL_REACHED", None, None, pageKey=here["pageKey"], pageSignature=here["signature"],
                    path=here["path"], screenshotRef=shot.get("screenshotRef"), capturedAt=shot.get("capturedAt"),
                    verification={k: evidence.get(k) for k in ("pathMatched", "textVisible")})

    def _summary(self, reason: str) -> dict:
        reached = reason == "GOAL_VERIFIED"
        n, back = self.n, self.n["model_back"] + self.n["system_back"]
        nav_cost = n["nav_clicks"] + back
        return {
            "goalReached": reached, "stepCount": self.step_count, "stepRecordCount": self.step_id,
            "modelCallCount": n["model_calls"], "clickCount": n["clicks"],
            "successfulNavigationClickCount": n["nav_clicks"], "modelBackCount": n["model_back"],
            "systemBackCount": n["system_back"], "backCount": back,
            "revisitCount": sum(c - 1 for c in self.visit_count.values()),
            "referencePathDeviationCount": n["deviation"], "navigationCost": nav_cost,
            "extraSteps": max(0, n["nav_clicks"] - self.shortest) if reached else None,
            "extraNavigationCost": max(0, nav_cost - self.shortest) if reached else None,
            "attemptedExtraSteps": max(0, self.step_count - self.shortest) if reached else None,
            "executionTimeMs": round((time.monotonic() - self.t0) * 1000), "path": self.visits,
            "detectedIssueTypes": [],   # Issue 분석은 실행 API가 Run 종료 후 채운다 (계약 14.2)
        }

    def _finish(self, reason: str, error: dict | None, best_effort: bool = False) -> dict:
        status = STATUS_BY_REASON[reason]
        result = {"status": status, "terminationReason": reason, "progress": self._progress(),
                  "summary": self._summary(reason), "error": error, "finishedAt": now_iso(),
                  "comparisonContext": {"leakCheckPassed": self.leak_passed}}
        try:
            run = self.store.read_run()
            run.update({k: result[k] for k in ("status", "terminationReason", "progress", "summary", "error",
                                                "finishedAt")})
            run["comparisonContext"]["leakCheckPassed"] = self.leak_passed
            self.store.write_run(run)
            self._event("RUN_FAILED" if status == "ERROR" else "RUN_FINISHED", None, None,
                        status=status, terminationReason=reason)
        except (StorageError, OSError, ValueError):
            if not best_effort:
                return self._finish("STORAGE_FAILED", _error("STORAGE_ERROR"), best_effort=True)
        return result
