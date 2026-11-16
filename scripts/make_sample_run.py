"""가짜 실행 샘플 results/runs/sample_before/ 생성 (#3). 3·4·5번이 루프 완성 전에 개발할 때 쓴다.

모델·브라우저·쇼핑몰 서버 없이 만든다.
- 후보·화면: scripts/capture_sample_screens.py로 Before 8001에서 같은 순간에 찍은 사진과 후보 (candidate-v2).
  먼저 그 스크립트를 실행해야 한다. 사진과 bbox를 다른 시점에 모으면 글자 폭 차이로 박스가 어긋난다.
- 확률: home·orders 첫 방문은 B5 실측값(Before), 나머지는 정해 둔 값. 전체 Run은 resultOrigin=MOCK
- 배지 스크린샷: Pillow로 bbox 위에 확률을 그림 (실제 Run에서는 1번 browser.capture가 그림)

경로 (Before, testMode=nochange로 시작한 것으로 가정):
  1 home    마이페이지 클릭              → mypage   NAVIGATED
  2 mypage  쿠폰함 클릭                  → 그대로   NO_CHANGE   (쿠폰함은 nochange 화면에만 있음)
  3 mypage  쿠폰함 뺀 후보로 다시 판단, GO_BACK → home  NAVIGATED (모델 BACK, home 재방문)
  4 home    이용정보 클릭                → orders   NAVIGATED
  5 orders  환불 신청하기 클릭           → refund   NAVIGATED
    refund  도착 판정 → SUCCESS (도착 화면 원본만 저장, Step 없음)

마이페이지 사진·후보는 실제 nochange 화면이다 (쇼핑몰 testMode #11, 쿠폰함 후보 `사이드 메뉴>/shop/coupons#1`).
확률은 후보 번호가 아니라 semanticKey로 정해서, 후보 순서가 바뀌어도 같은 후보에 같은 값이 간다.

    .venv\\Scripts\\python scripts\\make_sample_run.py
"""
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from PIL import Image, ImageDraw  # noqa: E402

from uxm import prompts as P  # noqa: E402
from uxm.leak import assert_no_leak  # noqa: E402
from uxm.metrics import decision_metrics, validate_probabilities  # noqa: E402
from uxm.screen_request import build_screen_request  # noqa: E402
from uxm.storage import RUNS_DIR, SCHEMA_VERSION, RunStore  # noqa: E402

RUN_ID = "sample_before"
# scripts/capture_sample_screens.py가 같은 순간에 찍은 원본 사진(.png)과 후보(.json)
SRC = SHOTS = os.path.join(ROOT, "results", "screenshots", "sample_raw")
KST = timezone(timedelta(hours=9))
T0 = datetime(2026, 11, 16, 10, 0, 0, tzinfo=KST)
CANDIDATE_SCOPE = ["상단 메뉴", "본문", "사이드 메뉴", "경로 표시", "하단"]
ALLOWED_PATHS = [["home", "orders", "refund"], ["home", "mypage", "refund"], ["home", "mypage", "orders", "refund"]]
SHORTEST = 2
COUPON = "사이드 메뉴>/shop/coupons#1"          # nochange 화면에만 있는 쿠폰함 (#11)
MYPAGE_REFUND = "사이드 메뉴>/shop/refund#1"    # 취소/교환/반품 내역
MYPAGE_ORDERS = "사이드 메뉴>/shop/orders#1"    # 주문/배송 조회
GO_BACK = "GO_BACK"

_clock = [0.0]


def ts(advance: float = 0.0) -> str:
    _clock[0] += advance
    return (T0 + timedelta(seconds=_clock[0])).isoformat(timespec="milliseconds")


def load_page(name: str) -> dict:
    with open(os.path.join(SRC, f"before_{name}.json"), encoding="utf-8") as f:
        d = json.load(f)
    return {k: d[k] for k in ("url", "title", "candidates", "excluded")}


def renumber(cands: list[dict]) -> list[dict]:
    """다시 판단할 때도 option_01부터 빈틈없이 매긴다 (GO_BACK 번호가 겹치지 않게)."""
    return [{**c, "optionId": f"option_{i:02d}"} for i, c in enumerate(cands, 1)]


def probs_by_key(cands: list[dict], weights: dict, back: bool) -> dict:
    """semanticKey(또는 GO_BACK)별 확률을 optionId별로. 정하지 않은 후보는 남은 확률을 똑같이 나눠 갖는다."""
    ids = {c["semanticKey"]: c["optionId"] for c in cands}
    if back:
        ids[GO_BACK] = f"option_{len(cands) + 1:02d}"   # build_screen_request와 같은 번호 (클릭 후보 다음)
    missing = [k for k in weights if k not in ids]
    assert not missing, f"샘플 화면에 없는 후보: {missing}"
    rest = [k for k in ids if k not in weights]
    # 다른 값처럼 소수 4자리로 (파이썬 3.12부터 sum()의 보정 합산 때문에 버전마다 끝자리가 달라져 의미 없는 diff가 생김, #17 리뷰)
    share = round((1.0 - sum(weights.values())) / len(rest), 4)
    return {ids[k]: weights.get(k, share) for k in ids}


def option_of(cands: list[dict], key: str) -> str:
    return f"option_{len(cands) + 1:02d}" if key == GO_BACK else next(
        c["optionId"] for c in cands if c["semanticKey"] == key)


def page_key(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]


def goal_path_probability(cands, probs, visited_keys):
    nxt = {p[len(visited_keys)] for p in ALLOWED_PATHS
           if len(p) > len(visited_keys) and p[:len(visited_keys)] == visited_keys}
    hits = [probs[c["optionId"]] for c in cands if page_key(c["href"]) in nxt]
    return round(sum(hits), 4) if hits else None


def draw_overlay(src_png: str, dst_png: str, cands, probs, chosen) -> None:
    img = Image.open(src_png).convert("RGB")
    d = ImageDraw.Draw(img)
    for c in cands:
        b, sel = c["bbox"], c["optionId"] == chosen
        color = (220, 38, 38) if sel else (37, 99, 235)
        d.rectangle([b["x"], b["y"], b["x"] + b["width"], b["y"] + b["height"]], outline=color, width=3 if sel else 2)
        tag = f"{probs[c['optionId']]:.3f}"
        d.rectangle([b["x"], b["y"] - 14, b["x"] + 6 * len(tag) + 6, b["y"]], fill=color)
        d.text((b["x"] + 3, b["y"] - 13), tag, fill=(255, 255, 255))
    img.save(dst_png)


def png_size(path: str) -> dict:
    with Image.open(path) as im:
        return {"width": im.width, "height": im.height}


# ---------------------------------------------------------------- 단계 정의

def build_steps():
    home, mypage_nochange, orders = load_page("home"), load_page("mypage"), load_page("orders")
    mp = mypage_nochange["candidates"]
    assert any(c["semanticKey"] == COUPON for c in mp), "마이페이지가 nochange 화면이 아님 (capture_sample_screens.py 다시)"
    mypage_retry = {**mypage_nochange, "candidates": renumber([c for c in mp if c["semanticKey"] != COUPON])}
    b5_home = {"option_01": 0.1142, "option_02": 0.1531, "option_03": 0.602, "option_04": 0.0957, "option_05": 0.035}

    # mypage(nochange) + GO_BACK: 쿠폰함 0.30, 취소/교환/반품 내역 0.22, 주문/배송 조회 0.12, GO_BACK 0.10, 나머지 나눔
    p2 = probs_by_key(mp, {COUPON: 0.30, MYPAGE_REFUND: 0.22, MYPAGE_ORDERS: 0.12, GO_BACK: 0.10}, back=True)
    # 쿠폰함 뺀 다시 판단 + GO_BACK: GO_BACK 0.34, 취소/교환/반품 내역 0.28, 주문/배송 조회 0.12, 나머지 나눔
    rc = mypage_retry["candidates"]
    p3 = probs_by_key(rc, {GO_BACK: 0.34, MYPAGE_REFUND: 0.28, MYPAGE_ORDERS: 0.12}, back=True)

    return [
        dict(page=home, path="/shop/home", sig="home_default", shot="before_home.png", set_id="home_default_initial",
             changed=False, back=False, probs=b5_home, choice="option_03", latency=3978,
             to_path="/shop/mypage", to_sig="mypage_nochange", result="NAVIGATED"),
        dict(page=mypage_nochange, path="/shop/mypage", sig="mypage_nochange", shot="before_mypage.png",
             set_id="mypage_nochange_initial", changed=False, back=True, probs=p2, choice=option_of(mp, COUPON),
             latency=4120,
             to_path="/shop/mypage", to_sig="mypage_nochange", result="NO_CHANGE"),
        dict(page=mypage_retry, path="/shop/mypage", sig="mypage_nochange", shot="before_mypage.png",
             set_id="mypage_nochange_retry_1", changed=True, back=True, probs=p3, choice=option_of(rc, GO_BACK),
             latency=4010, to_path="/shop/home", to_sig="home_default", result="NAVIGATED", dropped=1,
             failed=[f"{next(c['label'] for c in mp if c['semanticKey'] == COUPON)} 선택 → 화면 변화 없음"]),
        dict(page=home, path="/shop/home", sig="home_default", shot="before_home.png", set_id="home_default_initial",
             changed=False, back=False, probs={**b5_home}, choice="option_01", latency=3890,
             to_path="/shop/orders", to_sig="orders_default", result="NAVIGATED"),
        dict(page=orders, path="/shop/orders", sig="orders_default", shot="before_orders.png",
             set_id="orders_default_initial", changed=False, back=True,
             probs={"option_01": 0.08, "option_02": 0.84, "option_03": 0.08}, choice="option_02", latency=1650,
             to_path="/shop/refund", to_sig="refund_default", result="NAVIGATED"),
    ]


# ---------------------------------------------------------------- 생성

def main():
    # 입력(사진·후보)을 먼저 다 읽고 확인한 뒤에 기존 샘플을 지운다.
    # 순서가 반대면 capture를 안 했을 때 커밋된 샘플만 지워지고 오류가 난다 (#17 리뷰).
    needed = [os.path.join(SHOTS, f"before_{n}.{ext}") for n in ("home", "mypage", "orders") for ext in ("png", "json")]
    needed.append(os.path.join(SHOTS, "before_refund.png"))
    missing = [p for p in needed if not os.path.isfile(p)]
    if missing:
        raise SystemExit("샘플 입력이 없어요. 먼저 scripts/capture_sample_screens.py를 실행하세요:\n  "
                         + "\n  ".join(missing))
    steps = build_steps()

    target = os.path.join(RUNS_DIR, RUN_ID)
    if os.path.isdir(target):
        shutil.rmtree(target)
    store = RunStore(RUN_ID)

    run = {
        "schemaVersion": SCHEMA_VERSION, "runId": RUN_ID, "experimentKey": "sample_demo_001", "variant": "before",
        "resultOrigin": "MOCK", "siteVersion": "shop-v1", "modelVersion": None, "modelEndpointId": "mock",
        "promptVersion": P.PROMPT_VERSION, "temperature": 1.52, "decisionMode": "DIRECT",
        "goalJudgmentMode": "DOM_ONLY", "goalId": "refund", "goal": P.GOAL,
        "personaId": "novice-shopper", "persona": P.PERSONA, "startPath": "/shop/home",
        "viewport": {"width": 1440, "height": 900},
        "limits": {"maxSteps": 8, "modelTimeoutMs": 30000, "pageChangeTimeoutMs": 10000, "runTimeoutMs": 180000,
                   "maxModelBackActions": 2, "maxSystemBackActions": 1, "maxSamePageVisits": 3},
        "thresholdConfig": {"version": "threshold-v1", "entropyThreshold": 0.8, "minCandidateCount": 2,
                            # API(api/run_factory.py THRESHOLD_CONFIG)와 같은 값. 비교 조건(15.2)에 들어가서 표기까지 같아야 함
                            "configuredAt": "2026-11-15T09:00:00+09:00", "changeReason": "초기 사전 지정 기준"},
        "analysisVersion": "analysis-v1",
        "referencePathConfig": {"version": "refpath-v1", "allowedPaths": ALLOWED_PATHS,
                                "shortestAllowedSteps": SHORTEST},
        "comparisonContext": {"leakCheckPassed": None, "initialBrowserStateId": "clean-context-v1",
                              "layoutVersion": "shop-layout-v1", "candidatePolicyVersion": "candidate-v2"},
        "status": "READY", "createdAt": ts(), "startedAt": None, "finishedAt": None, "terminationReason": None,
        "progress": {"stepRecordCount": 0, "stepCount": 0, "maxSteps": 8, "currentPageKey": None,
                     "lastUpdatedAt": None},
        "summary": None, "error": None, "validity": None, "invalidReason": None, "replacementRunId": None,
    }
    store.create(run)

    events = []

    def event(event_type, origin=None, step_id=None, **details):
        e = {"schemaVersion": SCHEMA_VERSION, "eventId": f"event_{len(events) + 1:02d}", "runId": RUN_ID,
             "stepId": step_id, "timestamp": ts(0.05), "eventType": event_type, "eventOrigin": origin,
             "details": details}
        events.append(e)
        store.append_event(e)

    run.update(status="RUNNING", startedAt=ts(0.5))
    run["progress"].update(currentPageKey="home", lastUpdatedAt=run["startedAt"])
    store.write_run(run)
    event("RUN_STARTED", startPath="/shop/home")

    path_visits = [{"sequence": 0, "pageKey": "home", "pageSignature": "home_default", "path": "/shop/home",
                    "enteredBy": None, "visitCause": "RUN_START", "stepId": None}]
    visit_count = {"home_default": 1}
    current_route = ["home"]              # 되돌아간 구간을 뺀 현재 경로 (goalPathProbability용)
    history, step_count, counters = [], 0, dict(model_calls=0, clicks=0, nav_clicks=0, model_back=0)
    metric_rows = []

    for step_id, s in enumerate(steps, 1):
        cands = s["page"]["candidates"]
        req, meta = build_screen_request(s["page"], history=history,
                                         failed=[{"steps": [f]} for f in s.get("failed", [])], go_back=s["back"])
        assert_no_leak(req)
        option_ids = list(req["questions"]["next_click"]["criteria"])
        validate_probabilities(s["probs"], option_ids)
        back_id = meta["backOptionId"]
        response = {"answers": {"next_click": {"type": "choice", "choice": s["choice"], "confidence": None,
                                               "probabilities": s["probs"]}}, "timing_ms": s["latency"]}
        captured_at = ts(1.0)
        store.save_raw(step_id, "request", req)
        store.save_raw(step_id, "response", response)
        counters["model_calls"] += 1

        # 화면 (1번 capture가 할 일을 흉내)
        raw_ref, overlay_ref = f"screenshots/{step_id}.png", f"screenshots/{step_id}_overlay.png"
        shutil.copyfile(os.path.join(SHOTS, s["shot"]), store.path(*raw_ref.split("/")))
        draw_overlay(store.path(*raw_ref.split("/")), store.path(*overlay_ref.split("/")), cands, s["probs"],
                     s["choice"])

        m = decision_metrics(s["probs"], back_id)
        r4 = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()}
        is_back = s["choice"] == back_id
        excluded = s["page"]["excluded"]
        reasons = {}
        for e in excluded:
            reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
        if s.get("dropped"):   # 다시 판단할 때 뺀 후보 (루프 uxm/agent.py와 같은 FAILED_ACTION)
            reasons["FAILED_ACTION"] = s["dropped"]
        excluded_n = sum(reasons.values())

        started = ts(s["latency"] / 1000)
        finished = ts(1.2 if s["result"] == "NAVIGATED" else 10.0)   # NO_CHANGE는 10초 기다린 뒤 판정
        executed = True   # 샘플은 모두 실제로 누른 행동
        step = {
            "schemaVersion": SCHEMA_VERSION, "runId": RUN_ID, "stepId": step_id, "timestamp": captured_at,
            "page": {"pageKey": page_key(s["path"]), "pageSignature": s["sig"], "path": s["path"],
                     "title": s["page"]["title"], "viewport": {"width": 1440, "height": 900},
                     "screenshotSize": png_size(store.path(*raw_ref.split("/"))), "scroll": {"x": 0, "y": 0},
                     "screenshotRef": raw_ref, "overlayScreenshotRef": overlay_ref, "capturedAt": captured_at},
            "candidateSet": {
                "candidateSetId": s["set_id"], "candidateSetChanged": s["changed"], "candidateScope": CANDIDATE_SCOPE,
                "candidateCountCollected": len(cands) + excluded_n, "candidateCountSent": len(option_ids),
                "excludedCandidateCount": excluded_n, "exclusionReasons": reasons,
                "actionSetIncludesBack": back_id is not None, "backOptionId": back_id,
                "candidates": [{**c, "actionType": "CLICK", "probability": s["probs"][c["optionId"]],
                                "selected": c["optionId"] == s["choice"]} for c in cands],
            },
            "decision": {
                "selectedOptionId": s["choice"], "selectedActionType": "GO_BACK" if is_back else "CLICK",
                "mode": "DIRECT", "resultOrigin": "MOCK", "goalJudgmentMode": "DOM_ONLY",
                "modelGoalReached": None, "modelGoalYesProb": None, "verifiedGoalReached": False,
                "goalJudgmentMismatch": False, **r4,
                "goalPathProbability": goal_path_probability(cands, s["probs"], current_route),
                "modelLatencyMs": s["latency"], "metricWarnings": [],
            },
            "action": {
                "selectedOptionId": s["choice"], "executedOptionId": s["choice"] if executed else None,
                "actionType": "GO_BACK" if is_back else "CLICK", "eventOrigin": "MODEL_DECISION",
                "result": s["result"], "reason": None, "executed": executed,
                "fromPath": s["path"], "toPath": s["to_path"], "fromPageSignature": s["sig"],
                "toPageSignature": s["to_sig"], "startedAt": started, "finishedAt": finished, "error": None,
            },
            "statusAfterStep": "RUNNING", "error": None,
        }
        metric_rows.append(step["decision"])

        # 집계
        step_count += 1                      # 실행한 CLICK(NO_CHANGE 포함) + 성공한 모델 BACK
        if is_back:
            counters["model_back"] += 1
        else:
            counters["clicks"] += 1
            if s["result"] == "NAVIGATED":
                counters["nav_clicks"] += 1
        if s["result"] == "NAVIGATED":
            history.append({"steps": [f"{s['page']['title'].split(' | ')[0]} 화면",
                                      "이전 화면으로 돌아감" if is_back else
                                      f"{next(c['label'] for c in cands if c['optionId'] == s['choice'])} 선택"]})

        store.append_step(step)
        run["progress"].update(stepRecordCount=step_id, stepCount=step_count,
                               currentPageKey=page_key(s["to_path"]), lastUpdatedAt=finished)
        store.write_run(run)

        if is_back:
            event("BACK_EXECUTED", "MODEL_DECISION", step_id, result="NAVIGATED",
                  fromPageSignature=s["sig"], toPageSignature=s["to_sig"])
        else:
            event("ACTION_COMPLETED", "MODEL_DECISION", step_id, result=s["result"], reason=None,
                  fromPath=s["path"], toPath=s["to_path"])

        if s["result"] == "NAVIGATED":
            key = page_key(s["to_path"])
            if is_back:
                current_route.pop()
            else:
                current_route.append(key)
            path_visits.append({"sequence": len(path_visits), "pageKey": key, "pageSignature": s["to_sig"],
                                "path": s["to_path"], "enteredBy": "GO_BACK" if is_back else "CLICK",
                                "visitCause": "MODEL_BACK" if is_back else "MODEL_NAVIGATION", "stepId": step_id})
            visit_count[s["to_sig"]] = visit_count.get(s["to_sig"], 0) + 1
            if visit_count[s["to_sig"]] > 1:
                event("PAGE_REVISITED", "MODEL_DECISION", step_id, pageSignature=s["to_sig"],
                      visitCount=visit_count[s["to_sig"]], visitCause=path_visits[-1]["visitCause"])

    # 도착 화면: 원본만 저장, Step 없음
    arrival_ref = "screenshots/6.png"
    shutil.copyfile(os.path.join(SHOTS, "before_refund.png"), store.path(*arrival_ref.split("/")))
    event("GOAL_REACHED", None, None, pageKey="refund", pageSignature="refund_default", path="/shop/refund",
          screenshotRef=arrival_ref, capturedAt=ts(0.8),
          verification={"pathMatched": True, "textVisible": True})

    finished_at = ts(0.2)
    shortest = SHORTEST
    back_count = counters["model_back"]
    nav_cost = counters["nav_clicks"] + back_count
    run.update(
        status="SUCCESS", terminationReason="GOAL_VERIFIED", finishedAt=finished_at,
        validity="VALID", invalidReason=None,
        summary={
            "goalReached": True, "stepCount": step_count, "stepRecordCount": len(metric_rows),
            "modelCallCount": counters["model_calls"], "clickCount": counters["clicks"],
            "successfulNavigationClickCount": counters["nav_clicks"], "modelBackCount": counters["model_back"],
            "systemBackCount": 0, "backCount": back_count,
            "revisitCount": sum(v - 1 for v in visit_count.values()),
            "referencePathDeviationCount": 0, "navigationCost": nav_cost,
            "extraSteps": max(0, counters["nav_clicks"] - shortest),
            "extraNavigationCost": max(0, nav_cost - shortest),
            "attemptedExtraSteps": max(0, step_count - shortest),
            "executionTimeMs": int(_clock[0] * 1000), "path": path_visits,
            "detectedIssueTypes": [],   # Issue 분석 전(issues.json 없음). 분석이 끝나면 실행 API가 채운다
        },
    )
    run["comparisonContext"]["leakCheckPassed"] = True
    run["progress"].update(currentPageKey="refund", lastUpdatedAt=finished_at)
    store.write_run(run)
    event("RUN_FINISHED", None, None, status="SUCCESS", terminationReason="GOAL_VERIFIED")

    print(f"saved {store.dir}")
    print(f"  steps={len(metric_rows)} events={len(events)} stepCount={step_count} "
          f"entropy(home)={metric_rows[0]['clickOnlyNormalizedEntropy']}")


if __name__ == "__main__":
    main()
