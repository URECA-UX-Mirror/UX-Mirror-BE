"""UX Optimizer: Optimization 엔진·비동기 API (#57, 고도화 기획안 v1.3).

정책 함수(4번)는 v1.3과 4번이 공유한 계약 모양대로 만든 가짜로 끼운다:
`compute_evaluation_metrics(evaluation) -> dict`, `select_candidate(evaluations) -> dict`,
`judge_verification(baseline_run, candidate_run) -> dict`. 실제 모듈이 develop에 들어오면 같은 테스트를 그것으로도 돌린다.
"""
import json
import os
import sys
import threading
import time

import pytest

pytest.importorskip("fastapi")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from fastapi.testclient import TestClient  # noqa: E402

import api.main as api_main  # noqa: E402
from api.integrations import Integrations  # noqa: E402
from api.main import create_app  # noqa: E402
from api import run_factory  # noqa: E402
from api.errors import ApiError  # noqa: E402
from api.site_instances import (SiteRegistry, snapshot_from_extracted, validate_against_baseline,  # noqa: E402
                                validate_baseline)

TARGET = "상단 메뉴>/shop/orders#1"
LABELS = {"C0": "이용정보", "C1": "주문·배송", "C2": "주문·환불", "C3": "주문·반품", "C4": "주문 관리"}
TARGET_PROB = {"이용정보": 0.11, "주문·배송": 0.30, "주문·환불": 0.50, "주문·반품": 0.42, "주문 관리": 0.25}
SAMPLE_STEPS = os.path.join(ROOT, "results", "runs", "sample_before", "steps.jsonl")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def home_candidates() -> list[dict]:
    with open(SAMPLE_STEPS, encoding="utf-8") as f:
        step = json.loads(f.readline())
    keep = ("optionId", "semanticKey", "label", "region", "href", "bbox")
    return [{k: c[k] for k in keep} for c in step["candidateSet"]["candidates"] if c["actionType"] == "CLICK"]


def label_snapshot(label: str, mutate=None) -> dict:
    """단위 테스트 전용: 기준 화면 후보의 대상 라벨만 바꾼 스냅샷 (v1.3 4.3: 공식 평가 경로 아님)."""
    cands = home_candidates()
    for c in cands:
        if c["semanticKey"] == TARGET:
            c["label"] = label
    if mutate:
        mutate(cands)
    return snapshot_from_extracted({"title": "MORO MARKET", "url": "/shop/home", "candidates": cands, "excluded": []},
                                   "/shop/home", {"width": 1440, "height": 900}, "candidate-v2")


# ---------------------------------------------------------------- 계약 모양의 가짜 정책 (4번)

def fake_metrics(ev):
    p = sorted(ev["probabilities"].values(), reverse=True)
    return {"normalizedEntropy": 0.5, "top2Margin": p[0] - p[1]}


def fake_select(evaluations):
    base = next(e for e in evaluations if e["role"] == "BASELINE")
    ok = [e for e in evaluations if e["role"] == "CANDIDATE" and e["evaluationStatus"] == "COMPLETED"
          and e["candidateValidity"] == "VALID"]
    if not ok:
        return {"selectionStatus": "NOT_SELECTABLE", "recommendationStatus": "NO_RECOMMENDATION",
                "recommendedCandidateId": None, "verificationCandidateId": None, "rankedCandidates": [],
                "selectionReasonCode": "NO_VALID_CANDIDATE", "selectionPolicyVersion": "selection-v1"}
    ranked = sorted(ok, key=lambda e: (-e["targetOptionProbability"], e["candidateOrder"]))
    top = ranked[0]
    rec = top["targetOptionProbability"] - base["targetOptionProbability"] >= 0.05
    return {"selectionStatus": "SELECTED", "recommendationStatus": "RECOMMENDED" if rec else "NO_RECOMMENDATION",
            "recommendedCandidateId": top["candidateId"] if rec else None,
            "verificationCandidateId": top["candidateId"],
            "rankedCandidates": [e["candidateId"] for e in ranked],
            "selectionReasonCode": "TOP_TARGET_PROBABILITY", "selectionPolicyVersion": "selection-v1"}


def fake_judge(baseline, candidate):
    b, c = baseline["summary"], candidate["summary"]
    decision = "IMPROVEMENT_OBSERVED" if c["navigationCost"] < b["navigationCost"] else "NEEDS_MORE_EVIDENCE"
    return {"finalDecision": decision, "reasonCode": "NAVIGATION_COST",
            "navigationCostDelta": c["navigationCost"] - b["navigationCost"], "verificationPolicyVersion": "verification-v1"}


class LabelBackend:
    """대상 메뉴 확률을 라벨별로 정해 둔 가짜 모델. 나머지 확률은 고르게 나눈다."""

    def __init__(self, fail_labels=()):
        self.fail_labels, self.calls = set(fail_labels), 0

    def decide(self, request, temperature=None):
        self.calls += 1
        crit = request["questions"]["next_click"]["criteria"]
        target_oid = next(oid for oid, t in crit.items() if t.startswith("[상단 메뉴]") and
                          t.split("] ", 1)[1] in TARGET_PROB)
        label = crit[target_oid].split("] ", 1)[1]
        if label in self.fail_labels:
            raise ConnectionError("model down")
        p = TARGET_PROB[label]
        rest = [o for o in crit if o != target_oid]
        probs = {o: (1 - p) / len(rest) for o in rest} | {target_oid: p}
        return {"answers": {"next_click": {"type": "choice", "choice": max(probs, key=probs.get), "probabilities": probs}}}


class FakeIntegrations(Integrations):
    def __init__(self, backend=None, has_policy=True, gate=None):
        super().__init__()
        self._fake_backend = backend or LabelBackend()
        self.has_policy, self.gate = has_policy, gate
        self.start_urls, self.judged = [], None

    def optimizer_policy(self):
        if not self.has_policy:
            return None

        def judge(b, c):
            self.judged = (b, c)
            return fake_judge(b, c)
        return fake_metrics, fake_select, judge

    def backend(self, cfg):
        if self.gate is not None:
            self.gate.wait(5)
        return self._fake_backend

    def browser(self):
        return (lambda url: {"page": object()}), (lambda s: None)

    def agent(self):
        def run_agent(settings, backend, page):
            self.start_urls.append(settings["startUrl"])
            cost = 3 if "8101" in settings["startUrl"] else 2
            return {"status": "SUCCESS", "terminationReason": "GOAL_VERIFIED",
                    "summary": {"goalReached": True, "stepCount": cost, "navigationCost": cost, "backCount": 0,
                                "path": [{"pageKey": "home"}, {"pageKey": "orders"}, {"pageKey": "refund"}],
                                "detectedIssueTypes": []},
                    "comparisonContext": {"leakCheckPassed": True}}
        return run_agent

    def detect_issues(self):
        return None


def registry(tmp_path, ids=("C0", "C1", "C2", "C3", "C4")) -> SiteRegistry:
    path = tmp_path / "site_instances.json"
    path.write_text(json.dumps({"instances": [
        {"siteInstanceId": f"site-{cid.lower()}", "candidateId": cid, "label": LABELS[cid],
         "baseUrl": f"http://127.0.0.1:81{i:02d}"} for i, cid in enumerate(ids, 1)]}, ensure_ascii=False),
        encoding="utf-8")
    return SiteRegistry(str(path))


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setattr(api_main, "_model_reachable", lambda cfg: True)
    clients = []

    def _make(integ=None, sites=None, snapshot=None):
        integ = integ or FakeIntegrations()
        app = create_app(results_dir=str(tmp_path / "results"), integrations=integ,
                         site_registry=sites or registry(tmp_path))
        app.state.optimizer.snapshot = snapshot or (lambda inst, frozen: label_snapshot(inst["label"]))
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client, app, integ
    yield _make
    for c in clients:
        c.__exit__(None, None, None)


def create_and_evaluate(client, app, **body):
    r = client.post("/api/optimizations", json=body or None)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "CREATED"
    app.state.optimizer.wait(10)
    return client.get(f"/api/optimizations/{r.json()['optimizationId']}").json()


# ---------------------------------------------------------------- 평가 (17.3)

def test_evaluates_frozen_candidate_pool_and_selects(make):
    client, app, integ = make()
    opt = create_and_evaluate(client, app)
    assert opt["status"] == "EVALUATED" and opt["progress"] == {"evaluatedCount": 5, "totalCount": 5}
    assert [c["candidateId"] for c in opt["candidateLabels"]] == ["C0", "C1", "C2", "C3", "C4"]
    evs = {e["candidateId"]: e for e in opt["evaluations"]}
    for cid, label in LABELS.items():
        e = evs[cid]
        assert e["evaluationStatus"] == "COMPLETED" and e["candidateValidity"] == "VALID"
        assert e["targetOptionProbability"] == pytest.approx(TARGET_PROB[label])
        assert sum(e["probabilities"].values()) == pytest.approx(1.0) and TARGET in e["probabilities"]
        # 목표 경로 확률: refund 허용 경로의 다음 화면(orders·mypage)으로 가는 후보 확률 합
        to_next = [c["semanticKey"] for c in home_candidates() if c["href"] in ("/shop/orders", "/shop/mypage")]
        assert e["goalPathProbability"] == pytest.approx(sum(e["probabilities"][k] for k in to_next))
        assert e["requestHash"].startswith("sha256:") and e["snapshotHash"].startswith("sha256:")
        assert e["goalPathBasis"] == "ALLOWED_PATHS" and e["metricNullReasons"] == {}   # 평가에 쓴 기준을 기록
        assert e["top2Margin"] is not None   # 4번 compute_evaluation_metrics 결과가 합쳐짐
        for ref in (e["rawRequestRef"], e["rawResponseRef"]):
            assert os.path.isfile(app.state.optimizer.store.path(opt["optimizationId"], *ref.split("/")))
    assert integ._fake_backend.calls == 5   # 유효 후보당 1회 (6.7)
    assert (opt["recommendationStatus"], opt["recommendedCandidateId"], opt["verificationCandidateId"]) == \
        ("RECOMMENDED", "C2", "C2")
    assert opt["selection"]["rankedCandidates"] == ["C2", "C3", "C1", "C4"]
    # 동률용 의미 명확성 순위: 설정에 고정한 값을 동결하고 평가마다 넘긴다 (정윤희 11-18)
    assert {e["candidateId"]: e["semanticClarityRank"] for e in opt["evaluations"]} == \
        {"C0": None, "C1": 4, "C2": 1, "C3": 2, "C4": 3}
    assert [c["semanticClarityRank"] for c in opt["candidateLabels"]] == [None, 4, 1, 2, 3]
    assert opt["frozenConditions"]["goalId"] == "refund" and opt["modelMetadata"]["decisionMode"] == "DIRECT"
    assert all("baseUrl" not in s for s in opt["siteInstances"])   # 내부 주소는 응답에 없음
    raw = json.load(open(app.state.optimizer.store.path(opt["optimizationId"], "raw", "model_request_C2.json"),
                         encoding="utf-8"))
    assert "site-c2" not in json.dumps(raw) and "8103" not in json.dumps(raw)   # 모델 입력에 사이트 정보 없음


def test_same_inputs_give_same_selection(make):
    client, app, _ = make()
    first = create_and_evaluate(client, app)
    second = create_and_evaluate(client, app)
    for key in ("recommendedCandidateId", "verificationCandidateId", "recommendationStatus"):
        assert first[key] == second[key]
    assert [e["requestHash"] for e in first["evaluations"]] == [e["requestHash"] for e in second["evaluations"]]


def test_missing_site_instance_is_skipped_not_replaced(make, tmp_path):
    client, app, integ = make(sites=registry(tmp_path, ids=("C0", "C2")))
    opt = create_and_evaluate(client, app)
    evs = {e["candidateId"]: e for e in opt["evaluations"]}
    assert [cid for cid, e in evs.items() if e["evaluationStatus"] == "SKIPPED"] == ["C1", "C3", "C4"]
    assert evs["C1"]["invalidReasons"] == ["SITE_INSTANCE_MISSING"] and evs["C1"]["candidateValidity"] == "INVALID_CANDIDATE"
    assert integ._fake_backend.calls == 2 and opt["verificationCandidateId"] == "C2"


def test_candidate_snapshot_differing_beyond_label_is_invalid(make):
    def snapshot(inst, frozen):
        if inst["candidateId"] == "C3":   # 다른 라벨까지 바뀐 변형
            return label_snapshot(inst["label"], lambda cs: cs[1].update(label="고객 지원"))
        if inst["candidateId"] == "C4":   # 배치가 3px 어긋난 변형
            return label_snapshot(inst["label"], lambda cs: cs[0]["bbox"].update(x=cs[0]["bbox"]["x"] + 3))
        return label_snapshot(inst["label"])
    client, app, integ = make(snapshot=snapshot)
    evs = {e["candidateId"]: e for e in create_and_evaluate(client, app)["evaluations"]}
    assert evs["C3"]["invalidReasons"] == ["OTHER_LABEL_CHANGED"] and evs["C3"]["evaluationStatus"] == "SKIPPED"
    assert evs["C4"]["invalidReasons"] == ["BBOX_MISMATCH"]
    assert integ._fake_backend.calls == 3   # 무효 후보는 모델 평가에서 제외 (4.5)


def test_validate_against_baseline_rules():
    base = label_snapshot("이용정보")
    assert validate_against_baseline(label_snapshot("주문·환불"), base, TARGET, "주문·환불") == []
    assert "TARGET_LABEL_MISMATCH" in validate_against_baseline(label_snapshot("주문·배송"), base, TARGET, "주문·환불")
    moved = label_snapshot("주문·환불", lambda cs: cs[0]["bbox"].update(width=cs[0]["bbox"]["width"] + 2))
    assert validate_against_baseline(moved, base, TARGET, "주문·환불") == []   # 2px까지 허용
    dest = label_snapshot("주문·환불", lambda cs: cs[2].update(href="/shop/support"))
    assert "DESTINATION_MISMATCH" in validate_against_baseline(dest, base, TARGET, "주문·환불")


def test_candidate_request_inputs_beyond_label_must_match():
    """라벨 말고 모델 요청에 들어가는 값(영역·화면 제목)이 바뀐 변형도 무효다 (리뷰 #58-2)."""
    base = label_snapshot("이용정보")
    region = label_snapshot("주문·환불", lambda cs: cs[1].update(region="본문"))
    assert validate_against_baseline(region, base, TARGET, "주문·환불") == ["REGION_MISMATCH"]
    titled = label_snapshot("주문·환불")
    titled["pageTitle"] = "MORO MARKET 이벤트"
    assert validate_against_baseline(titled, base, TARGET, "주문·환불") == ["PAGE_MISMATCH"]


def test_semantic_key_missing_or_duplicate_is_invalid():
    """semanticKey가 빠지거나 겹치면 확률을 키로 묶을 때 값이 덮어써지므로 평가하지 않는다 (정윤희 11-18)."""
    base = label_snapshot("이용정보")
    dup = label_snapshot("주문·환불", lambda cs: cs[1].update(semanticKey=cs[0]["semanticKey"]))
    assert "SEMANTIC_KEY_DUPLICATE" in validate_against_baseline(dup, base, TARGET, "주문·환불")
    missing = label_snapshot("주문·환불", lambda cs: cs[1].update(semanticKey=None))
    assert "SEMANTIC_KEY_MISSING" in validate_against_baseline(missing, base, TARGET, "주문·환불")
    no_target = label_snapshot("주문·환불", lambda cs: cs.remove(next(c for c in cs if c["semanticKey"] == TARGET)))
    assert "TARGET_NOT_FOUND" in validate_against_baseline(no_target, base, TARGET, "주문·환불")


def test_duplicate_key_candidate_is_skipped_before_model(make):
    def snapshot(inst, frozen):
        if inst["candidateId"] == "C1":
            return label_snapshot(inst["label"], lambda cs: cs[1].update(semanticKey=cs[0]["semanticKey"]))
        return label_snapshot(inst["label"])
    client, app, integ = make(snapshot=snapshot)
    evs = {e["candidateId"]: e for e in create_and_evaluate(client, app)["evaluations"]}
    assert evs["C1"]["candidateValidity"] == "INVALID_CANDIDATE" and "SEMANTIC_KEY_DUPLICATE" in evs["C1"]["invalidReasons"]
    assert evs["C1"]["probabilities"] is None and integ._fake_backend.calls == 4


def test_validate_baseline_rules():
    target = {"pageKey": "home", "semanticKey": TARGET, "href": "/shop/orders", "originalLabel": "이용정보"}
    assert validate_baseline(label_snapshot("이용정보"), target) == []
    assert validate_baseline(label_snapshot("주문·환불"), target) == ["TARGET_LABEL_MISMATCH"]
    moved = label_snapshot("이용정보", lambda cs: next(c for c in cs if c["semanticKey"] == TARGET).update(href="/shop/mypage"))
    assert validate_baseline(moved, target) == ["DESTINATION_MISMATCH"]


@pytest.mark.parametrize("mutate, reason", [
    (None, "TARGET_LABEL_MISMATCH"),   # C0 사이트에 대상 메뉴가 "이용정보"가 아닌 경우
    (lambda cs: cs.remove(next(c for c in cs if c["semanticKey"] == TARGET)), "TARGET_NOT_FOUND"),
])
def test_invalid_baseline_stops_before_model(make, mutate, reason):
    """기준(C0) 화면이 설정과 다르면 잘못된 기준과 비교하지 않도록 평가를 멈춘다 (리뷰 #58-2)."""
    def snapshot(inst, frozen):
        if inst["candidateId"] == "C0":
            return label_snapshot("주문·환불" if mutate is None else "이용정보", mutate)
        return label_snapshot(inst["label"])
    client, app, integ = make(snapshot=snapshot)
    opt = create_and_evaluate(client, app)
    assert opt["status"] == "FAILED" and opt["error"]["code"] == "BASELINE_INVALID", opt["error"]
    assert reason in opt["error"]["message"]
    assert [e["candidateId"] for e in opt["evaluations"]] == ["C0"]
    assert opt["evaluations"][0]["invalidReasons"] == [reason]
    assert integ._fake_backend.calls == 0 and opt["selection"] is None


def test_snapshot_error_does_not_expose_internal_address(make):
    """브라우저 예외에 섞인 내부 주소는 API 응답에 나오지 않고 서버 로그에만 남는다 (리뷰 #58-3)."""
    def snapshot(inst, frozen):
        if inst["candidateId"] == "C1":
            raise RuntimeError(f"브라우저를 시작하지 못함: {inst['baseUrl']}/shop/home (net::ERR_CONNECTION_REFUSED)")
        return label_snapshot(inst["label"])
    client, app, _ = make(snapshot=snapshot)
    opt = create_and_evaluate(client, app)
    c1 = next(e for e in opt["evaluations"] if e["candidateId"] == "C1")
    assert (c1["error"]["code"], c1["error"]["message"]) == ("BROWSER_ERROR", "후보 화면을 열지 못했습니다.")
    body = client.get(f"/api/optimizations/{opt['optimizationId']}").text
    assert "127.0.0.1" not in body and "8102" not in body and "ERR_CONNECTION" not in body


class SlowBackend(LabelBackend):
    """지정한 라벨에서 늦게 답하는 가짜 모델. 동시에 몇 개가 돌았는지 센다."""

    def __init__(self, slow: dict):
        super().__init__()
        self.slow, self.running, self.max_running = slow, 0, 0
        self.lock = threading.Lock()

    def decide(self, request, temperature=None):
        crit = request["questions"]["next_click"]["criteria"].values()
        label = next(t.split("] ", 1)[1] for t in crit if t.startswith("[상단 메뉴]") and t.split("] ", 1)[1] in TARGET_PROB)
        with self.lock:
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        try:
            time.sleep(self.slow.get(label, 0))
            return super().decide(request, temperature)
        finally:
            with self.lock:
                self.running -= 1


def test_model_timeout_waits_for_late_call_before_next(make, monkeypatch):
    """제한 시간을 넘긴 호출이 끝날 때까지 기다린 뒤 다음 후보를 부른다. 호출은 겹치지 않는다 (리뷰 #58 권장)."""
    monkeypatch.setitem(run_factory.DEFAULT_LIMITS, "modelTimeoutMs", 200)
    backend = SlowBackend({"주문·배송": 0.3})
    client, app, _ = make(FakeIntegrations(backend=backend))
    opt = create_and_evaluate(client, app)
    evs = {e["candidateId"]: e for e in opt["evaluations"]}
    assert evs["C1"]["evaluationStatus"] == "FAILED" and evs["C1"]["error"]["code"] == "MODEL_TIMEOUT"
    assert opt["status"] == "EVALUATED" and evs["C2"]["evaluationStatus"] == "COMPLETED"
    assert backend.max_running == 1


class HangingBackend(LabelBackend):
    """지정한 라벨에서 테스트가 풀어 줄 때까지 돌아오지 않는 가짜 모델."""

    def __init__(self, label: str):
        super().__init__()
        self.label, self.release, self.running, self.max_running = label, threading.Event(), 0, 0
        self.lock = threading.Lock()

    def decide(self, request, temperature=None):
        crit = request["questions"]["next_click"]["criteria"].values()
        with self.lock:
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        try:
            if any(t.endswith("] " + self.label) for t in crit):
                self.release.wait(30)
            return super().decide(request, temperature)
        finally:
            with self.lock:
                self.running -= 1


def test_stuck_model_call_keeps_model_work_blocked_until_it_ends(make, monkeypatch):
    """두 배를 기다려도 안 끝나면 평가를 멈추고, 그 호출이 실제로 끝날 때까지 시간 제한 없이
    일반 Run·새 Optimization을 막는다 (리뷰 #58 2차-1)."""
    monkeypatch.setitem(run_factory.DEFAULT_LIMITS, "modelTimeoutMs", 200)
    backend = HangingBackend("주문·배송")
    client, app, _ = make(FakeIntegrations(backend=backend))
    opt_id = client.post("/api/optimizations").json()["optimizationId"]
    app.state.optimizer.wait(5)   # 평가 작업 자체는 끝났다 (_job 해제)
    opt = app.state.optimizer.store.load(opt_id)
    assert opt["status"] == "FAILED" and opt["error"]["code"] == "MODEL_TIMEOUT"
    assert app.state.optimizer._job is None and backend.running == 1   # 늦은 호출은 아직 돈다
    time.sleep(0.5)   # 어떤 대기 제한을 지나도 풀리지 않는다
    r = client.post("/api/runs", json={"experimentKey": "after-stuck", "variant": "before", "goalId": "refund",
                                       "personaId": "novice-shopper"})
    assert r.status_code == 409 and "이전 모델 호출이 아직 끝나지 않았습니다" in r.json()["error"]["message"]
    assert client.post("/api/optimizations").json()["error"]["code"] == "RUN_ALREADY_ACTIVE"
    assert client.get("/api/health").json()["activeOptimization"] == {"optimizationId": opt_id,
                                                                      "status": "MODEL_CALL_PENDING"}
    backend.release.set()
    deadline = time.monotonic() + 5
    while backend.running and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.05)
    assert app.state.optimizer.busy_reason() is None and client.get("/api/health").json()["activeOptimization"] is None
    assert client.post("/api/runs", json={"experimentKey": "after-stuck", "variant": "before", "goalId": "refund",
                                          "personaId": "novice-shopper"}).status_code == 201
    app.state.runner.wait(5)
    assert backend.max_running == 1


def test_model_failure_is_recorded_without_retry(make):
    integ = FakeIntegrations(backend=LabelBackend(fail_labels={"주문·반품"}))
    client, app, _ = make(integ)
    opt = create_and_evaluate(client, app)
    evs = {e["candidateId"]: e for e in opt["evaluations"]}
    assert evs["C3"]["evaluationStatus"] == "FAILED" and evs["C3"]["error"]["code"] == "MODEL_UNAVAILABLE"
    assert integ._fake_backend.calls == 5 and opt["status"] == "EVALUATED"


def test_baseline_failure_fails_optimization(make):
    client, app, _ = make(FakeIntegrations(backend=LabelBackend(fail_labels={"이용정보"})))
    opt = create_and_evaluate(client, app)
    assert opt["status"] == "FAILED"
    assert (opt["error"]["code"], opt["error"]["failedPhase"]) == ("BASELINE_NOT_EVALUATED", "EVALUATING")


# ---------------------------------------------------------------- API·상태 (17.5)

def test_policy_module_missing_is_503(make):
    client, _, _ = make(FakeIntegrations(has_policy=False))
    r = client.post("/api/optimizations")
    assert r.status_code == 503 and r.json()["error"]["code"] == "OPTIMIZER_POLICY_UNAVAILABLE"


def test_model_unavailable_is_503(make, monkeypatch):
    client, _, _ = make()
    monkeypatch.setattr(api_main, "_model_reachable", lambda cfg: False)
    r = client.post("/api/optimizations")
    assert r.status_code == 503 and r.json()["error"]["code"] == "MODEL_UNAVAILABLE"


def test_unknown_optimization_and_source_run(make):
    client, _, _ = make()
    for path in ("/api/optimizations/nope", "/api/optimizations/nope/report", "/api/optimizations/bad.id"):
        r = client.get(path)
        assert r.status_code == 404 and r.json()["error"]["code"] == "OPTIMIZATION_NOT_FOUND"
    assert client.post("/api/optimizations/nope/verify").json()["error"]["code"] == "OPTIMIZATION_NOT_FOUND"
    r = client.post("/api/optimizations", json={"sourceRunId": "no_such_run"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "RUN_NOT_FOUND"
    assert client.post("/api/optimizations", json={"candidateLabels": ["x"]}).status_code == 422   # 후보 풀은 서버가 정함


def test_evaluation_blocks_other_runs_and_shows_progress(make):
    gate = threading.Event()
    client, app, _ = make(FakeIntegrations(gate=gate))
    opt_id = client.post("/api/optimizations").json()["optimizationId"]
    assert client.get(f"/api/optimizations/{opt_id}").json()["status"] in ("CREATED", "EVALUATING")
    r = client.post("/api/runs", json={"experimentKey": "exp", "variant": "before", "goalId": "refund",
                                       "personaId": "novice-shopper"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "RUN_ALREADY_ACTIVE"   # 평가 중 모델 동시 사용 방지
    assert "후보 평가가 진행 중" in r.json()["error"]["message"]   # FE에 이유가 보이게 (정자비 11-18)
    assert client.get("/api/health").json()["activeOptimization"] == {"optimizationId": opt_id, "status": "EVALUATING"}
    assert client.post("/api/optimizations").json()["error"]["code"] == "RUN_ALREADY_ACTIVE"
    assert client.post(f"/api/optimizations/{opt_id}/verify").json()["error"]["code"] == "OPTIMIZATION_NOT_READY"
    gate.set()
    app.state.optimizer.wait(10)
    assert client.get(f"/api/optimizations/{opt_id}").json()["status"] == "EVALUATED"
    assert client.get("/api/health").json()["activeOptimization"] is None


# ---------------------------------------------------------------- 실제 검증·판단·보고서

def test_verify_runs_baseline_then_candidate_and_reports(make):
    client, app, integ = make()
    opt = create_and_evaluate(client, app)
    opt_id = opt["optimizationId"]
    assert client.get(f"/api/optimizations/{opt_id}/report").json()["error"]["code"] == "OPTIMIZATION_NOT_READY"
    r = client.post(f"/api/optimizations/{opt_id}/verify")
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "VERIFYING" and r.json()["verificationPhase"] == "BASELINE"
    app.state.optimizer.wait(20)
    opt = client.get(f"/api/optimizations/{opt_id}").json()
    assert opt["status"] == "COMPLETED" and opt["verificationPhase"] is None, opt["error"]
    assert opt["finalDecision"] == "IMPROVEMENT_OBSERVED" and opt["verification"]["navigationCostDelta"] == -1
    # 기준(C0)과 검증 후보(C2) 사이트를 등록된 주소로 차례로 실행
    assert integ.start_urls == ["http://127.0.0.1:8101/shop/home", "http://127.0.0.1:8103/shop/home"]
    base, cand = (client.get(f"/api/runs/{opt[k]}").json() for k in ("baselineRunId", "validationRunId"))
    assert (base["optimizationContext"]["executionRole"], cand["optimizationContext"]["executionRole"]) == \
        ("BASELINE", "CANDIDATE")
    assert base["experimentKey"] == cand["experimentKey"] == opt_id
    assert integ.judged[0]["runId"] == base["runId"] and integ.judged[1]["runId"] == cand["runId"]
    report = client.get(f"/api/optimizations/{opt_id}/report").json()
    assert report["finalDecision"] == "IMPROVEMENT_OBSERVED"
    assert report["runs"]["candidate"]["label"] == "주문·환불" and report["runs"]["baseline"]["navigationCost"] == 3
    assert report["limitations"] == {"baselineRunCount": 1, "candidateRunCount": 1, "repeated": False,
                                     "note": report["limitations"]["note"]}
    assert report["trace"]["rawRefs"]["C2"]["request"] == "raw/model_request_C2.json"
    r = client.post(f"/api/optimizations/{opt_id}/verify")
    assert r.status_code == 409 and r.json()["error"]["code"] == "OPTIMIZATION_ALREADY_PROCESSED"
    # 기존 P0 실행은 그대로: 같은 쇼핑몰 주소(variant)로 돈다
    r = client.post("/api/runs", json={"experimentKey": "p0", "variant": "after", "goalId": "refund",
                                       "personaId": "novice-shopper"})
    assert r.status_code == 201
    app.state.runner.wait(5)
    assert integ.start_urls[-1].endswith(":8002/shop/home")


def test_verifying_blocks_outside_runs_between_baseline_and_candidate(make):
    """기준 Run이 끝나고 후보 Run이 시작되기 전 빈 구간에 일반 Run이 끼어들지 못한다 (리뷰 #58-1)."""
    client, app, integ = make()
    opt = create_and_evaluate(client, app)
    runner, seen = app.state.runner, []
    original_wait = runner.wait

    def wait_then_intrude(timeout=None):
        original_wait(timeout)   # 기준 Run 종료 직후 (runner는 비어 있음)
        if len(seen) == 0:
            assert runner.active_run_id is None
            outside = run_factory.build_run(
                type("R", (), {"viewport": None, "maxSteps": 8, "variant": "before", "experimentKey": "intruder",
                               "startPath": "/shop/home"})(),
                app.state.presets.goal("refund"), app.state.presets.persona("novice-shopper"),
                run_factory.model_settings())
            try:
                runner.create(outside, app.state.presets.goal("refund"))
                seen.append("created")
            except ApiError as e:
                seen.append((e.status, e.code, e.message))
            seen.append(client.get("/api/health").json()["activeOptimization"])
    runner.wait = wait_then_intrude
    assert client.post(f"/api/optimizations/{opt['optimizationId']}/verify").status_code == 202
    app.state.optimizer.wait(20)
    runner.wait = original_wait
    status, code, message = seen[0]
    assert (status, code) == (409, "RUN_ALREADY_ACTIVE") and "후보 검증 실행이 진행 중" in message
    assert seen[1] == {"optimizationId": opt["optimizationId"], "status": "VERIFYING"}
    done = client.get(f"/api/optimizations/{opt['optimizationId']}").json()
    assert done["status"] == "COMPLETED", done["error"]   # 자기 검증 Run은 막히지 않는다
    assert not any(r.get("experimentKey") == "intruder" for r in app.state.store.list_runs())


def test_verify_uses_frozen_conditions_and_refuses_changed_preset(make):
    """검증 Run은 평가 때 동결한 목표·성공 조건으로 만들고, preset이 바뀌었으면 시작하지 않는다 (리뷰 #58-4)."""
    client, app, _ = make()
    opt = create_and_evaluate(client, app)
    presets, frozen = app.state.presets, opt["frozenConditions"]
    original = presets.goals["refund"]
    changed = json.loads(json.dumps(original))
    changed["verification"]["text"] = "환불 신청"
    presets.goals["refund"] = changed
    try:
        r = client.post(f"/api/optimizations/{opt['optimizationId']}/verify")
        assert r.status_code == 422 and r.json()["error"]["code"] == "EXPERIMENT_CONDITION_MISMATCH"
        assert "verification" in r.json()["error"]["message"]
        # 직접 만들어도 현재 preset이 아니라 동결 값을 쓴다
        run, goal = app.state.optimizer._build_run(app.state.optimizer.store.load(opt["optimizationId"]),
                                                   "C2", "after", "CANDIDATE")
        assert run["verification"]["text"] == frozen["verification"]["text"] == goal["verification"]["text"]
        assert run["goal"] == frozen["goal"] and run["referencePathConfig"] == frozen["referencePathConfig"]
        assert run["limits"] == frozen["limits"] and run["viewport"] == frozen["viewport"]
        changed_persona = dict(presets.personas["novice-shopper"], persona="다른 사용자")
        presets.goals["refund"], saved_persona = original, presets.personas["novice-shopper"]
        presets.personas["novice-shopper"] = changed_persona
        r = client.post(f"/api/optimizations/{opt['optimizationId']}/verify")
        assert r.status_code == 422 and "persona" in r.json()["error"]["message"]
        presets.personas["novice-shopper"] = saved_persona
    finally:
        presets.goals["refund"] = original
    assert client.post(f"/api/optimizations/{opt['optimizationId']}/verify").status_code == 202
    app.state.optimizer.wait(20)


@pytest.mark.parametrize("change, code", [
    ("label", "SITE_INSTANCE_CHANGED"),      # 평가 뒤 후보 사이트의 화면이 바뀜
    ("error", "SITE_RECHECK_FAILED"),        # 다시 열지 못함
    ("instance", "SITE_INSTANCE_CHANGED"),   # 등록된 사이트 인스턴스가 다른 것으로 바뀜
])
def test_verify_stops_when_site_changed_after_evaluation(make, tmp_path, change, code):
    """검증 직전에 기준·선정 후보 화면을 평가 때 스냅샷과 다시 비교하고, 다르면 Run을 만들지 않는다 (리뷰 #58 2차-2)."""
    sites = registry(tmp_path)
    client, app, integ = make(sites=sites)
    opt = create_and_evaluate(client, app)
    if change == "label":
        app.state.optimizer.snapshot = lambda inst, frozen: label_snapshot(
            inst["label"], (lambda cs: cs[1].update(label="고객 지원")) if inst["candidateId"] == "C2" else None)
    elif change == "error":
        def broken(inst, frozen):
            raise RuntimeError(f"connection refused {inst['baseUrl']}")
        app.state.optimizer.snapshot = broken
    else:
        sites.instances["site-c2-new"] = dict(sites.instances.pop("site-c2"), siteInstanceId="site-c2-new")
    r = client.post(f"/api/optimizations/{opt['optimizationId']}/verify")
    assert r.status_code == 202
    app.state.optimizer.wait(10)
    done = client.get(f"/api/optimizations/{opt['optimizationId']}")
    body = done.json()
    assert body["status"] == "FAILED" and body["error"]["code"] == code, body["error"]
    assert body["baselineRunId"] is None and integ.start_urls == []   # 검증 Run을 만들지 않았다
    assert "127.0.0.1" not in done.text


def test_verify_records_site_recheck(make):
    client, app, _ = make()
    opt = create_and_evaluate(client, app)
    client.post(f"/api/optimizations/{opt['optimizationId']}/verify")
    app.state.optimizer.wait(20)
    done = client.get(f"/api/optimizations/{opt['optimizationId']}").json()
    evs = {e["candidateId"]: e for e in done["evaluations"]}
    assert done["status"] == "COMPLETED" and set(done["siteRecheck"]) == {"C0", "C2"}
    assert all(done["siteRecheck"][c]["snapshotHash"] == evs[c]["snapshotHash"] for c in ("C0", "C2"))


def test_verify_requires_registered_site_for_selected_candidate(make, tmp_path):
    sites = registry(tmp_path)
    client, app, _ = make(sites=sites)
    opt = create_and_evaluate(client, app)
    sites.instances.pop("site-c2")   # 평가 뒤 검증 후보 사이트가 사라진 경우
    r = client.post(f"/api/optimizations/{opt['optimizationId']}/verify")
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_SITE_INSTANCE"


def test_verify_refused_while_run_active(make):
    client, app, integ = make()
    opt = create_and_evaluate(client, app)
    gate = threading.Event()
    original = integ.agent()

    def slow_agent():
        def run_agent(settings, backend, page):
            gate.wait(5)
            return original(settings, backend, page)
        return run_agent
    integ.agent = slow_agent
    assert client.post("/api/runs", json={"experimentKey": "busy", "variant": "before", "goalId": "refund",
                                          "personaId": "novice-shopper"}).status_code == 201
    r = client.post(f"/api/optimizations/{opt['optimizationId']}/verify")
    assert r.status_code == 409 and r.json()["error"]["code"] == "RUN_ALREADY_ACTIVE"
    gate.set()
    app.state.runner.wait(5)


def test_verify_refused_when_model_settings_changed(make, monkeypatch):
    client, app, _ = make()
    opt = create_and_evaluate(client, app)
    monkeypatch.setenv("ZORO_TEMPERATURE", "1.0")
    r = client.post(f"/api/optimizations/{opt['optimizationId']}/verify")
    assert r.status_code == 422 and r.json()["error"]["code"] == "EXPERIMENT_CONDITION_MISMATCH"


def test_interrupted_optimization_is_failed_on_restart(make, tmp_path):
    client, app, _ = make()
    opt = create_and_evaluate(client, app)
    store = app.state.optimizer.store
    saved = store.load(opt["optimizationId"])
    saved["status"] = "VERIFYING"
    store.save(saved)
    assert app.state.optimizer.recover_interrupted() == [opt["optimizationId"]]
    fixed = store.load(opt["optimizationId"])
    assert fixed["status"] == "FAILED" and fixed["error"]["code"] == "OPTIMIZATION_INTERRUPTED"


# ---------------------------------------------------------------- 실제 쇼핑몰·브라우저·루프

class KeywordBackend:
    """실제 루프용 가짜 모델: '주문'·'환불'이 든 후보를 고른다. 대상 메뉴 확률은 라벨에 따라 달라진다."""

    def decide(self, request, temperature=None):
        crit = request["questions"]["next_click"]["criteria"]
        score = {o: (2.0 if "환불" in t else 1.0 if "주문" in t else 0.2) for o, t in crit.items()}
        total = sum(score.values())
        probs = {o: s / total for o, s in score.items()}
        return {"answers": {"next_click": {"type": "choice", "choice": max(probs, key=probs.get), "probabilities": probs}}}


def test_real_shop_snapshots_evaluation_and_verification(tmp_path, shops, monkeypatch):
    """실제 쇼핑몰 화면 스냅샷 → 평가 → 실제 브라우저·루프로 기준(C0)·후보(C2) 검증. 모델과 4번 정책만 가짜."""
    pytest.importorskip("playwright")
    monkeypatch.setattr(api_main, "_model_reachable", lambda cfg: True)
    path = tmp_path / "site_instances.json"
    path.write_text(json.dumps({"instances": [
        {"siteInstanceId": "site-c0", "candidateId": "C0", "label": "이용정보", "baseUrl": shops["before"]},
        {"siteInstanceId": "site-c2", "candidateId": "C2", "label": "주문·환불", "baseUrl": shops["after"]}]},
        ensure_ascii=False), encoding="utf-8")

    class RealIntegrations(Integrations):
        def optimizer_policy(self):
            return fake_metrics, fake_select, fake_judge

        def backend(self, cfg):
            return KeywordBackend()

    app = create_app(results_dir=str(tmp_path / "results"), integrations=RealIntegrations(),
                     site_registry=SiteRegistry(str(path)))
    with TestClient(app) as client:
        opt_id = client.post("/api/optimizations").json()["optimizationId"]
        app.state.optimizer.wait(60)
        opt = client.get(f"/api/optimizations/{opt_id}").json()
        assert opt["status"] == "EVALUATED", opt["error"]
        evs = {e["candidateId"]: e for e in opt["evaluations"]}
        assert evs["C0"]["candidateValidity"] == evs["C2"]["candidateValidity"] == "VALID"   # 실제 화면도 라벨 1곳만 다름
        assert evs["C2"]["targetOptionProbability"] > evs["C0"]["targetOptionProbability"]
        assert opt["verificationCandidateId"] == "C2"
        assert client.post(f"/api/optimizations/{opt_id}/verify").status_code == 202
        app.state.optimizer.wait(120)
        opt = client.get(f"/api/optimizations/{opt_id}").json()
        assert opt["status"] == "COMPLETED", opt["error"]
        for key in ("baselineRunId", "validationRunId"):
            run = client.get(f"/api/runs/{opt[key]}").json()
            assert run["validity"] == "VALID" and run["summary"]["goalReached"] is True
        assert client.get(f"/api/optimizations/{opt_id}/report").status_code == 200


def test_real_policy_module_follows_contract(make):
    """4번 `uxm/optimizer_policy.py`가 develop에 들어오면 실제 정책 함수로 같은 흐름을 돌린다 (없으면 건너뜀)."""
    real = Integrations().optimizer_policy()
    if real is None:
        pytest.skip("uxm/optimizer_policy.py 아직 없음 (4번)")

    class RealPolicy(FakeIntegrations):
        def optimizer_policy(self):
            return real
    client, app, _ = make(RealPolicy())
    opt = create_and_evaluate(client, app)
    assert opt["status"] == "EVALUATED", opt["error"]
    assert opt["selection"]["selectionPolicyVersion"] == "selection-v1"
    assert opt["recommendationStatus"] == "RECOMMENDED"
    assert opt["recommendedCandidateId"] == opt["verificationCandidateId"] == "C2"
    assert all(e["normalizedEntropy"] is not None and e["top2Margin"] is not None for e in opt["evaluations"])
    assert client.post(f"/api/optimizations/{opt['optimizationId']}/verify").status_code == 202
    app.state.optimizer.wait(20)
    done = client.get(f"/api/optimizations/{opt['optimizationId']}").json()
    assert done["status"] == "COMPLETED", done["error"]
    assert done["finalDecision"] == "IMPROVEMENT_OBSERVED"
    report = client.get(f"/api/optimizations/{opt['optimizationId']}/report")
    assert report.status_code == 200
    assert report.json()["verification"]["finalDecision"] == "IMPROVEMENT_OBSERVED"
