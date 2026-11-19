"""자동 개선 루프 (#106). 브라우저·모델·다른 담당 함수 없이 가짜 부품으로 끝까지 도는지 확인한다.

가짜 정책(`FakePolicy`)은 확정된 함수 모양(역할 배분 §3.1)만 흉내 낸다. 실제 정책은 #105 머지 후 같은 자리에 넣는다.
"""
import copy
import json
import os
from decimal import Decimal

from uxm.improve_loop import Deps, report_md, run_loop, save_result

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "config", "sitemaps", "sitemap-shop-v1.json"), encoding="utf-8") as f:
    SITE_MAP = json.load(f)

INFO, SUPPORT, MYPAGE = "상단 메뉴>/shop/orders#1", "상단 메뉴>/shop/support#1", "상단 메뉴>/shop/mypage#1"
BODY_ORDERS, BODY_SUPPORT = "본문>/shop/orders#1", "본문>/shop/support#1"
OTHER_PAGES = {   # home 말고 다른 화면 (11/19 S1 실측 일부 + refund 가짜)
    "mypage": {"경로 표시>/shop/home#1": 0.01, "사이드 메뉴>/shop/refund#1": 0.5, "사이드 메뉴>/shop/orders#1": 0.2,
               "사이드 메뉴>/shop/support#1": 0.29},
    "orders": {"경로 표시>/shop/home#1": 0.0216, "본문>/shop/refund#1": 0.9784},
    "support": {"본문>/shop/orders#1": 1.0},
    "refund": {"경로 표시>/shop/home#1": 0.2, "본문>/shop/orders#1": 0.8},
}
# (목표, 상단 메뉴 이름) → home 확률. 주문·환불은 문의(S4) 사용자를 끌어당긴다 (E 결과와 같은 방향)
HOME = {
    ("S1", None): {INFO: 0.20, SUPPORT: 0.35, MYPAGE: 0.26, BODY_ORDERS: 0.07, BODY_SUPPORT: 0.12},
    ("S1", "주문·환불"): {INFO: 0.60, SUPPORT: 0.22, MYPAGE: 0.10, BODY_ORDERS: 0.02, BODY_SUPPORT: 0.06},
    ("S1", "주문 관리"): {INFO: 0.45, SUPPORT: 0.25, MYPAGE: 0.18, BODY_ORDERS: 0.04, BODY_SUPPORT: 0.08},
    ("S1", "주문내역"): {INFO: 0.62, SUPPORT: 0.18, MYPAGE: 0.12, BODY_ORDERS: 0.03, BODY_SUPPORT: 0.05},
    ("S4", None): {INFO: 0.10, SUPPORT: 0.52, MYPAGE: 0.05, BODY_ORDERS: 0.08, BODY_SUPPORT: 0.25},
    ("S4", "주문·환불"): {INFO: 0.26, SUPPORT: 0.41, MYPAGE: 0.07, BODY_ORDERS: 0.06, BODY_SUPPORT: 0.20},
}
LABELS = {"home": {INFO: "이용정보", SUPPORT: "고객센터", MYPAGE: "마이페이지", BODY_ORDERS: "실시간 조회", BODY_SUPPORT: "24H 상담"}}

PLAN = {
    "improvementId": "imp-test", "improvementPlanId": "improve-plan-test",
    "persona": "온라인 쇼핑몰 이용 경험이 적은 사용자", "startPage": "home",
    "mainGoal": {"goalKey": "S1", "name": "환불", "situation": "환불하려는", "goalPage": "refund"},
    "guardGoals": [{"goalKey": "S4", "name": "문의", "goalPage": "support"}],
    "score": {"maxSteps": 8},
    "rules": {"minMainGain": 0.05, "maxGuardLoss": 0.02, "tieTolerance": 0.01, "targetScore": 0.95, "maxRounds": 3,
              "stopAfterRoundsWithoutAdoption": 2},
    "edit": {"editableSemanticKeys": [INFO], "maxNewLabelLength": 6},
    "humanCandidates": [{"candidateId": "H2", "label": "주문·환불"}, {"candidateId": "H4", "label": "주문 관리"}],
    "aiProposal": {"retryOnEmpty": 1},
}


def fake_collect(calls):
    def collect(goal, label, pages):
        calls.append((goal["goalKey"], label, pages))
        home = HOME.get((goal["goalKey"], label)) or HOME[(goal["goalKey"], None)]
        probs = {"home": dict(home)} if pages == "home" else {"home": dict(home), **OTHER_PAGES}
        return {"probs": probs, "labels": LABELS, "records": [{"pageKey": "home"}], "layout": None if label is None else
                {"valid": label != "너무긴이름입니다", "reasons": [] if label != "너무긴이름입니다" else ["LABEL_OVERFLOW"]}}
    return collect


class FakePolicy:
    def __init__(self):
        self.prompts, self.baselines = [], []

    def build_prompt(self, diagnosis_text, button, siblings, persona, history):
        self.prompts.append((diagnosis_text, button["label"], siblings, list(history)))
        return f"{diagnosis_text}\n현재 메뉴: {button['label']}"

    def filter_labels(self, raw_text, context):
        out = []
        for line in raw_text.splitlines():
            label = line.strip()
            bad = (not label or label in context["triedLabels"] or label in context["siblingLabels"])
            out.append({"label": label, "accepted": not bad, "rejectedReason": "EXISTING_LABEL" if bad else None})
        return out

    def choose(self, baseline, candidates, rules):
        self.baselines.append(baseline)
        d = lambda x: Decimal(str(x))  # noqa: E731
        ok, rejected = [], []
        for c in candidates:
            if c["evaluationStatus"] != "COMPLETED" or c["mainScore"] is None:
                rejected.append({"candidateId": c["candidateId"], "reason": "EVALUATION_FAILED"})
                continue
            lost = [g for g, v in baseline["guardScores"].items() if d(v) - d(c["guardScores"][g]) > d(rules["maxGuardLoss"])]
            if lost:
                rejected.append({"candidateId": c["candidateId"], "reason": "GUARD_LOSS_EXCEEDED", "guardGoals": lost})
            elif d(c["mainScore"]) - d(baseline["mainScore"]) < d(rules["minMainGain"]):
                rejected.append({"candidateId": c["candidateId"], "reason": "MAIN_GAIN_INSUFFICIENT"})
            else:
                ok.append(c)
        ranking = [c["candidateId"] for c in sorted(ok, key=lambda c: -c["mainScore"])]
        return {"adopted": ranking[0] if ranking else None, "ranking": ranking, "rejected": rejected}


def _run(responses, plan=None):
    calls, policy = [], FakePolicy()
    it = iter(responses)
    deps = Deps(fake_collect(calls), lambda prompt: next(it, ""), policy, clock=lambda: "2026-11-19T21:00:00+09:00")
    return run_loop(plan or copy.deepcopy(PLAN), SITE_MAP, deps), calls, policy


def test_loop_runs_end_to_end_with_fakes_and_rejects_c2_for_s4():
    out, calls, policy = _run(["주문내역\n너무긴이름입니다", "구매내역"])
    imp = out["improvement"]
    r1 = imp["rounds"][0]
    assert r1["diagnosis"]["targetButton"]["label"] == "이용정보" and r1["diagnosis"]["rivalButton"]["label"] == "고객센터"
    by_label = {c["label"]: c for c in r1["candidates"]}
    assert [c["source"] for c in r1["candidates"]] == ["HUMAN", "HUMAN", "AI", "AI"]
    assert by_label["주문·환불"]["reason"] == "GUARD_LOSS_EXCEEDED:S4"            # 발표 핵심 장면
    assert by_label["너무긴이름입니다"]["reason"] == "INVALID_CANDIDATE:LABEL_OVERFLOW"
    assert by_label["너무긴이름입니다"]["mainScore"] is None                       # 0으로 바꾸지 않음
    assert by_label["주문내역"]["decision"] == "ADOPTED" and r1["adopted"] == by_label["주문내역"]["candidateId"]
    assert by_label["주문 관리"]["reason"] == "NOT_BEST"
    s = imp["summary"]
    assert (s["result"], s["beforeLabel"], s["afterLabel"], s["afterSource"]) == ("IMPROVED", "이용정보", "주문내역", "AI")
    assert s["main"]["after"] > s["main"]["before"] and s["guards"][0]["goalKey"] == "S4"
    assert imp["verification"]["status"] == "NOT_RUN" and imp["official"] is None
    # 후보 평가는 home만 다시 수집 (다른 화면은 v1 재사용)
    assert all(p == "home" for _, label, p in calls if label is not None)


def test_baseline_uses_current_main_and_original_v1_guards():
    out, _, policy = _run(["주문내역", "구매내역"])
    first, second = policy.baselines[0], policy.baselines[1]
    assert second["guardScores"] == first["guardScores"]                 # 지킬은 끝까지 최초 v1
    assert second["mainScore"] > first["mainScore"]                      # 주는 직전 채택안
    # 2회차 프롬프트에 1회차 결과가 자연어로 들어간다 (S4 사용자를 끌어당겨 탈락)
    assert "문의 사용자를 잘못 끌어당겨 탈락" in policy.prompts[1][3][0]
    assert policy.prompts[1][1] == "주문내역"                              # 2회차 고칠 버튼 글자 = 채택된 이름


def test_no_improvement_when_nothing_passes_and_streak_stops():
    plan = copy.deepcopy(PLAN)
    plan["humanCandidates"] = [{"candidateId": "H2", "label": "주문·환불"}]
    out, _, _ = _run(["", "", "", ""], plan)
    imp = out["improvement"]
    assert imp["summary"]["result"] == "NO_IMPROVEMENT" and imp["summary"]["afterLabel"] is None
    assert imp["summary"]["beforeLabel"] == "이용정보"                    # 채택이 없어도 v1 이름은 남는다
    assert imp["summary"]["main"]["after"] is None and imp["stop"]["reason"] == "NO_ADOPTION_STREAK"


def test_evaluation_failure_keeps_error_message_in_raw():
    def broken_collect(goal, label, pages):
        if label == "주문·환불":
            raise ConnectionError("모델 서버 응답 없음")
        return fake_collect([])(goal, label, pages)
    it = iter(["주문내역"])
    deps = Deps(broken_collect, lambda prompt: next(it, ""), FakePolicy(), clock=lambda: "t")
    out = run_loop(copy.deepcopy(PLAN), SITE_MAP, deps)
    failed = next(c for c in out["improvement"]["rounds"][0]["candidates"] if c["label"] == "주문·환불")
    assert failed["reason"] == "EVALUATION_FAILED:ConnectionError" and failed["mainScore"] is None
    err = next(r for r in out["raw"] if r["kind"] == "error")
    assert err["candidateId"] == failed["candidateId"] and "응답 없음" in err["message"]


def test_save_writes_json_raw_and_report(tmp_path):
    out, _, _ = _run(["주문내역", "구매내역"])
    folder = save_result(out, str(tmp_path))
    with open(os.path.join(folder, "improvement.json"), encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["schemaVersion"] == "improvement-v1" and saved["improvementId"] == "imp-test"
    assert os.listdir(os.path.join(folder, "raw"))
    text = report_md(saved)
    assert "'이용정보' → '주문내역'" in text and "허용 감소 범위" in text and "GUARD_LOSS_EXCEEDED:S4" in text


def test_loop_with_real_policy_module():
    """#105 `uxm.improve_policy`(진짜 정책)로도 끝까지 돈다. 번호 붙은 응답·S4 탈락 사유(guardGoals)까지."""
    from uxm import improve_policy
    it = iter(["1. 주문내역\n2. 너무너무긴이름이다", "1. 구매내역"])
    deps = Deps(fake_collect([]), lambda prompt: next(it, ""), improve_policy, clock=lambda: "t")
    imp = run_loop(copy.deepcopy(PLAN), SITE_MAP, deps)["improvement"]
    r1 = imp["rounds"][0]
    by_label = {c["label"]: c for c in r1["candidates"]}
    assert by_label["주문·환불"]["reason"] == "GUARD_LOSS_EXCEEDED:S4"
    assert by_label["주문내역"]["decision"] == "ADOPTED"                       # 번호를 떼고 통과
    filtered = {f["label"]: f["rejectedReason"] for f in r1["proposal"]["filtered"]}
    assert filtered["주문내역"] is None and filtered["너무너무긴이름이다"] == "LABEL_TOO_LONG"
    assert imp["summary"]["afterLabel"] == "주문내역"


# ---- #110 리뷰 반영: 공식 B 판정, 동결 조건, 같은 ID 재실행

def _improved():
    from uxm import improve_policy
    it = iter(["주문내역"])
    deps = Deps(fake_collect([]), lambda prompt: next(it, ""), improve_policy, clock=lambda: "t")
    return run_loop(copy.deepcopy(PLAN), SITE_MAP, deps)["improvement"]


def test_official_b_confirmed_replaces_summary_scores():
    from uxm import improve_policy
    from uxm.improve_loop import apply_official
    imp = _improved()
    before = imp["summary"]["main"]["before"]
    ok = apply_official(imp, PLAN, {"S1": before + 0.30, "S4": imp["summary"]["guards"][0]["before"]},
                        {"valid": True, "reasons": []}, improve_policy)
    s = imp["summary"]
    assert ok and imp["official"]["decision"] == "CONFIRMED" and s["result"] == "IMPROVED"
    assert s["scoreSource"] == "OFFICIAL_B" and s["main"]["after"] == round(before + 0.30, 6)
    assert imp["official"]["site"]["buildCheck"] is None            # 여기서 하지 않은 검사는 완료로 적지 않음


def test_official_b_rejected_turns_summary_into_no_improvement_and_keeps_loop_history():
    from uxm import improve_policy
    from uxm.improve_loop import apply_official
    imp = _improved()
    v1_s4 = imp["summary"]["guards"][0]["before"]
    ok = apply_official(imp, PLAN, {"S1": 0.9, "S4": v1_s4 - 0.10}, {"valid": True, "reasons": []}, improve_policy)
    s = imp["summary"]
    assert not ok and imp["official"]["decision"] == "REJECTED"
    assert (s["result"], s["afterLabel"], s["scoreSource"], s["main"]["after"]) == ("NO_IMPROVEMENT", None, None, None)
    assert s["officialRejected"] == {"label": "주문내역", "reason": "GUARD_LOSS_EXCEEDED:S4"}
    assert s["loopResult"]["afterLabel"] == "주문내역" and s["loopResult"]["main"]["after"] is not None
    assert "공식 평가에서 탈락" in report_md(imp)
    # 화면 검사 실패도 탈락
    imp = _improved()
    assert not apply_official(imp, PLAN, {"S1": 0.9, "S4": 0.9}, {"valid": False, "reasons": ["LABEL_OVERFLOW"]},
                              improve_policy)


def test_frozen_conditions_must_match_current_environment():
    from uxm.improve_loop import check_frozen_conditions
    plan = {"model": {"modelVersion": "trillionlabs/Zoro-1", "modelEndpointId": "brev-l40s-bf16", "promptVersion": "v2"},
            "verification": {"frozen": {"modelMetadata": {"temperature": 1.52, "goalJudgmentMode": "DOM_ONLY"}}},
            "referencePath": {"siteMapVersion": SITE_MAP["siteMapVersion"], "siteMapHash": SITE_MAP["siteMapHash"]}}
    now = {"modelVersion": "trillionlabs/Zoro-1", "modelEndpointId": "brev-l40s-bf16", "temperature": 1.52,
           "promptVersion": "v2", "goalJudgmentMode": "DOM_ONLY"}
    assert check_frozen_conditions(plan, now, SITE_MAP) == []
    diffs = check_frozen_conditions(plan, {**now, "modelEndpointId": "local", "temperature": 1.0}, SITE_MAP)
    assert len(diffs) == 2 and diffs[0].startswith("modelEndpointId") and diffs[1].startswith("temperature")
    assert check_frozen_conditions({**plan, "referencePath": {}}, now, SITE_MAP)   # 동결 값이 없어도 다른 것으로 본다


def test_same_improvement_id_is_refused_unless_overwrite(tmp_path):
    out, _, _ = _run(["주문내역"])
    folder = save_result(out, str(tmp_path))
    stale = os.path.join(folder, "raw", "999_old.json")
    open(stale, "w").close()
    try:
        save_result(out, str(tmp_path))
        raise AssertionError("FileExistsError가 나야 함")
    except FileExistsError:
        pass
    save_result(out, str(tmp_path), overwrite=True)
    assert not os.path.exists(stale) and os.path.exists(os.path.join(folder, "improvement.json"))   # 이전 원본 안 섞임
    assert not os.path.exists(folder + ".tmp")


def test_finalize_rerun_restores_loop_summary_before_judging_again():
    """finalize를 다시 돌리면 이전 B 판정 전의 루프(A) 요약에서 다시 시작한다."""
    from uxm import improve_policy
    from uxm.improve_loop import apply_official, restore_loop_summary
    imp = _improved()
    loop_summary = copy.deepcopy(imp["summary"])
    v1_s4 = imp["summary"]["guards"][0]["before"]
    apply_official(imp, PLAN, {"S1": 0.9, "S4": v1_s4 - 0.10}, None, improve_policy)   # 1차 finalize: 탈락
    restore_loop_summary(imp)
    assert imp["summary"] == loop_summary and imp["official"] is None
    apply_official(imp, PLAN, {"S1": 0.9, "S4": v1_s4}, None, improve_policy)          # 2차 finalize: 채택
    restore_loop_summary(imp)
    assert imp["summary"] == loop_summary


def test_official_scores_collects_every_goal_on_b_site_without_browser():
    from uxm.improve_loop import official_scores
    calls = []
    scores, layout, raw = official_scores(fake_collect(calls), copy.deepcopy(PLAN), SITE_MAP, "주문내역")
    assert set(scores) == {"S1", "S4"} and all(0 < v <= 1 for v in scores.values())
    assert [p for _, _, p in calls] == ["all", "all"] and layout == {"valid": True, "reasons": []}
    assert [r["kind"] for r in raw] == ["finalize_collect", "finalize_collect"]


def test_finalize_rerun_does_not_keep_previous_verification():
    """#110 추가 리뷰: 이전 finalize의 COMPLETED 검증이 다음 finalize(예: --skip-verify)에 남지 않는다."""
    from uxm.improve_loop import restore_loop_summary, verification_not_run
    imp = _improved()
    assert imp["verification"]["status"] == "NOT_RUN" and imp["verification"]["reason"] == "NOT_FINALIZED"
    imp["verification"] = {"status": "COMPLETED", "error": None, "method": "run_verification", "pairs": [{"goalKey": "S1"}]}
    restore_loop_summary(imp)                                  # finalize 시작 때 되돌림
    assert imp["verification"] == verification_not_run("NOT_FINALIZED")
    assert verification_not_run("SKIPPED_BY_OPTION")["reason"] == "SKIPPED_BY_OPTION"


# ---- 11/19 연습 실행 반영: 목적지 설명, 여러 번 요청해 통과 이름 합치기

def test_proposal_collects_accepted_names_across_attempts_until_min_accepted():
    plan = copy.deepcopy(PLAN)
    plan["aiProposal"] = {"maxAttempts": 3, "minAccepted": 2}
    plan["edit"]["targetPageDescription"] = "주문 목록 화면(주문·배송 조회, 환불·반품 신청)"
    responses = iter(["고객센터", "주문내역\n고객센터", "주문내역\n구매내역", "안 쓰임"])
    policy = FakePolicy()
    deps = Deps(fake_collect([]), lambda prompt: next(responses, ""), policy, clock=lambda: "t")
    imp = run_loop(plan, SITE_MAP, deps)["improvement"]
    r1 = imp["rounds"][0]
    assert r1["proposal"]["attempts"] == 3                                  # 2개 모일 때까지 3번
    accepted = [f["label"] for f in r1["proposal"]["filtered"] if f["accepted"]]
    assert accepted == ["주문내역", "구매내역"]                               # 여러 시도 합침, 중복 제거
    dup = [f for f in r1["proposal"]["filtered"] if f["label"] == "주문내역" and not f["accepted"]]
    assert dup and dup[0]["rejectedReason"] == "DUPLICATE_LABEL" and dup[0]["attempt"] == 3
    assert {c["label"] for c in r1["candidates"] if c["source"] == "AI"} == {"주문내역", "구매내역"}
    # 진단 문장에 목적지 설명이 들어간다 (모델이 메뉴가 어디로 가는지 알게)
    assert policy.prompts[0][0].startswith("'이용정보' 메뉴를 누르면 주문 목록 화면(주문·배송 조회, 환불·반품 신청)으로 간다.")


# ---- #113: 진짜 정책 프롬프트에 이미 시험한 이름 전달, 쉼표 응답 나누기

def test_real_policy_prompt_receives_previously_tried_names_and_comma_response():
    from uxm import improve_policy
    prompts = []
    responses = iter(['고객센터, 이용정보, 주문내역', '주문내역, 구매내역'])
    def propose(prompt):
        prompts.append(prompt)
        return next(responses, '')
    deps = Deps(fake_collect([]), propose, improve_policy, clock=lambda: 't')
    result = run_loop(copy.deepcopy(PLAN), SITE_MAP, deps)['improvement']
    assert len(prompts) >= 2
    assert '쓰지 말 이름:' in prompts[0] and '고객센터' in prompts[0]
    assert '이미 시험한 이름:' in prompts[1]
    assert '주문내역' in prompts[1] and '주문·환불' in prompts[1]
    filtered = result['rounds'][0]['proposal']['filtered']
    assert [(x['label'], x['accepted']) for x in filtered] == [
        ('고객센터', False), ('이용정보', False), ('주문내역', True)]


# ---- #114 리뷰: 시도 간 중복은 filter_labels와 같은 정규화로, 회차당 수 제한을 넘은 이름은 사유와 함께

def _proposal_round(responses, ai):
    plan = copy.deepcopy(PLAN)
    plan["humanCandidates"] = []
    plan["aiProposal"] = ai
    it = iter(responses)
    from uxm import improve_policy
    deps = Deps(fake_collect([]), lambda prompt: next(it, ""), improve_policy, clock=lambda: "t")
    return run_loop(plan, SITE_MAP, deps)["improvement"]["rounds"][0]


def test_duplicate_across_attempts_ignores_case_and_spacing():
    r1 = _proposal_round(["Order", "ORDER\n주문  내역", "주문 내역\n구매내역"], {"maxAttempts": 3, "minAccepted": 3})
    rows = [(f["label"], f["accepted"], f["rejectedReason"], f["attempt"]) for f in r1["proposal"]["filtered"]]
    assert ("ORDER", False, "DUPLICATE_LABEL", 2) in rows                 # Order와 같은 이름
    assert ("주문 내역", False, "DUPLICATE_LABEL", 3) in rows             # 2번째 시도 '주문  내역'과 같은 이름
    assert [c["label"] for c in r1["candidates"]] == ["Order", "주문 내역", "구매내역"]


def test_names_over_max_per_round_are_marked_and_not_evaluated():
    r1 = _proposal_round(["주문내역\n구매내역\n내 주문"], {"maxAttempts": 1, "minAccepted": 1, "maxPerRound": 2})
    filtered = {f["label"]: (f["accepted"], f["rejectedReason"]) for f in r1["proposal"]["filtered"]}
    assert filtered["내 주문"] == (False, "CANDIDATE_LIMIT_EXCEEDED")
    evaluated = [c["label"] for c in r1["candidates"]]
    assert evaluated == ["주문내역", "구매내역"]
    assert [f["label"] for f in r1["proposal"]["filtered"] if f["accepted"]] == evaluated   # accepted = 평가 대상


def test_tried_names_and_tie_lost_use_the_same_rules_as_policy():
    from uxm.improve_loop import _apply_decisions
    # 이미 시험한 이름 비교도 정규화: 현재 이름 '이용정보'와 앞뒤 공백만 다른 사람 후보는 다시 평가하지 않는다
    plan = copy.deepcopy(PLAN)
    plan["humanCandidates"] = [{"candidateId": "H1", "label": " 이용정보 "}, {"candidateId": "H2", "label": "주문 관리"}]
    it = iter(["Order"])
    deps = Deps(fake_collect([]), lambda prompt: next(it, ""), FakePolicy(), clock=lambda: "t")
    r1 = run_loop(plan, SITE_MAP, deps)["improvement"]["rounds"][0]
    labels = [c["label"] for c in r1["candidates"]]
    assert " 이용정보 " not in labels and "주문 관리" in labels
    # 정확히 1%p 차이는 동률(choose와 같은 Decimal 기준) → TIE_LOST, float 오차로 NOT_BEST가 되지 않는다
    cands = [{"candidateId": "A", "mainScore": 0.6, "reason": None}, {"candidateId": "B", "mainScore": 0.59, "reason": None}]
    _apply_decisions(cands, {"adopted": "A", "ranking": ["A", "B"], "rejected": []}, {"tieTolerance": 0.01})
    assert [c["reason"] for c in cands] == [None, "TIE_LOST"]
