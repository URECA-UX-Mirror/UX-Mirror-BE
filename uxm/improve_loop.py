"""자동 개선 루프 (#106, 「자동 개선 에이전트 설계 v2.2」 §2·§4, 역할 배분 5).

진단 → 제안 → 걸러 내기 → 글자 바꾸기(A)·화면 검사 → 점수 → 채택을 회차마다 반복하고,
결과를 FE 샘플 형식(`improvement.json`)으로 만든다. 저장은 `save_result`.

다른 담당의 부품은 **주입**받는다. 머지 전에는 가짜 함수로 끝까지 돌려 보고, 머지 후 진짜 함수로 바꿔 끼운다.
- `collect(goal, label, pages)`: 화면 확률 수집. 실제 구현은 `scripts/improve.py`(브라우저·모델·`apply_label`·`check_layout`)
  반환 {"probs": {pageKey: {semanticKey: p}}, "labels": {pageKey: {semanticKey: 글자}}, "records": [...],
        "layout": {"valid": bool, "reasons": [...]} 또는 None}
  label=None이면 v1 그대로. pages="all"이면 지도의 모든 화면, "home"이면 고칠 버튼이 있는 화면만.
- `propose(prompt)`: 모델 채팅 응답 원문 (정자비: 모델 호출·재요청·원본 저장)
- `policy`: `build_prompt`·`filter_labels`·`choose` (정윤희 `uxm/improve_policy.py`, #105)
- `official(label)`, `verify(label)`: 최종안 B 공식 점수·실제 Run (선택, 없으면 결과에 null·NOT_RUN)

점수는 `reachWithinShortestProbability`(§4.4). MORO 상단 메뉴는 home에서만 후보라, 후보 평가는 home만 다시 수집하고
다른 화면은 v1 확률을 재사용한다(§4.4 계산 비용). 확률은 목표마다 다르므로 목표별로 따로 수집한다.
"""
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from uxm import reach
from uxm.improve_diagnose import describe, diagnose, region_of

KST = timezone(timedelta(hours=9))
SCHEMA_VERSION = "improvement-v1"
SCORE = "reachWithinShortestProbability"


class Deps:
    """루프에 주입하는 부품 묶음."""

    def __init__(self, collect, propose, policy, official=None, verify=None, clock=None):
        self.collect, self.propose, self.policy = collect, propose, policy
        self.official, self.verify = official, verify
        self.clock = clock or (lambda: datetime.now(KST).isoformat(timespec="seconds"))


def _goals(plan: dict) -> list[dict]:
    return [plan["mainGoal"], *plan["guardGoals"]]


def _score(site_map, probs, start, goal_page, max_steps) -> tuple[float | None, dict]:
    est = reach.estimate(site_map, probs, start, goal_page, max_steps)
    if est.get("status") != "COMPLETE":
        return None, est
    return est.get(SCORE), est


def _round_score(x):
    return None if x is None else round(x, 6)


@dataclass
class _Ctx:
    """한 번의 루프 실행에서 회차마다 같이 쓰는 값."""
    plan: dict
    site_map: dict
    deps: Deps
    start: str
    max_steps: int
    goals: list
    main_key: str
    guard_keys: list
    editable: tuple
    raw: list = field(default_factory=list)   # 원본 (모델 판단 기록·제안 프롬프트·응답·실패 메시지)
    v1: dict = field(default_factory=dict)    # {goalKey: {probs, labels, score, est}}

    @property
    def v1_guards(self) -> dict:
        return {k: self.v1[k]["score"] for k in self.guard_keys}


def run_loop(plan: dict, site_map: dict, deps: Deps) -> dict:
    """회차 루프를 돌리고 `{"improvement": improvement.json 내용, "raw": 원본 목록}`을 돌려준다. 저장·FE 전달은 호출자."""
    rules = plan["rules"]
    ctx = _Ctx(plan=plan, site_map=site_map, deps=deps, start=plan.get("startPage", "home"),
               max_steps=plan["score"].get("maxSteps", 8), goals=_goals(plan), main_key=plan["mainGoal"]["goalKey"],
               guard_keys=[g["goalKey"] for g in plan["guardGoals"]],
               editable=tuple(plan["edit"]["editableSemanticKeys"]))
    created = deps.clock()
    ctx.v1 = _collect_v1(ctx)
    v1_label = _v1_label(ctx)
    current = {"label": None, "source": None, "main": ctx.v1[ctx.main_key]["score"], "guards": dict(ctx.v1_guards),
               "probs": {k: v["probs"] for k, v in ctx.v1.items()}}
    tried, history, rounds = set(), [], []
    no_adopt_streak, stop = 0, None

    for rnd in range(1, rules["maxRounds"] + 1):
        record, chosen, cands, evaluated = _run_round(ctx, rnd, current, v1_label, tried, history)
        rounds.append(record)
        if chosen is None:   # 진단에서 멈춤
            stop = {"reason": record["diagnosis"]["reason"], "detail": f"{rnd}회차 진단에서 멈춤"}
            break
        history.append(_history_line(cands, plan))
        if chosen["adopted"]:
            won = next(c for c in cands if c["candidateId"] == chosen["adopted"])
            current = {"label": won["label"], "source": won["source"], "main": won["mainScore"],
                       "guards": dict(won["guardScores"]), "probs": evaluated[won["candidateId"]]}
            no_adopt_streak = 0
            if current["main"] >= rules["targetScore"]:
                stop = {"reason": "TARGET_REACHED", "detail": f"{rnd}회차에 목표치 {rules['targetScore']} 도달"}
                break
        else:
            no_adopt_streak += 1
            if no_adopt_streak >= rules.get("stopAfterRoundsWithoutAdoption", 2):
                stop = {"reason": "NO_ADOPTION_STREAK", "detail": f"{no_adopt_streak}회차 연속 채택 없음"}
                break
    if stop is None:
        stop = {"reason": "MAX_ROUNDS", "detail": f"최대 {rules['maxRounds']}회차 도달"}

    final_label = current["label"]
    official = deps.official(final_label) if (deps.official and final_label) else None
    verification = deps.verify(final_label) if (deps.verify and final_label) else {
        "status": "NOT_RUN", "error": None, "method": "run_verification", "pairs": []}
    result = _build_result(ctx, created, v1_label, current, rounds, stop, official, verification)
    return {"improvement": result, "raw": ctx.raw}


def _collect_v1(ctx: _Ctx) -> dict:
    """v1 기준: 목표마다 모든 화면 확률과 점수. 점수를 못 내면 루프를 시작하지 않는다."""
    v1 = {}
    for g in ctx.goals:
        got = ctx.deps.collect(g, None, "all")
        ctx.raw.append({"kind": "collect", "goalKey": g["goalKey"], "label": None, "records": got.get("records", [])})
        score, est = _score(ctx.site_map, got["probs"], ctx.start, g["goalPage"], ctx.max_steps)
        v1[g["goalKey"]] = {"probs": got["probs"], "labels": got.get("labels", {}), "score": score, "est": est}
    bad = [k for k, v in v1.items() if v["score"] is None]
    if bad:
        raise RuntimeError(f"v1 점수를 계산할 수 없습니다 (도달 확률 엔진 결과가 COMPLETE가 아님): {bad}")
    return v1


def _v1_label(ctx: _Ctx) -> str | None:
    """고칠 수 있는 버튼의 v1 글자 (예: 이용정보). 진단 결과와 관계없이 v1 수집 직후에 정한다."""
    for page_labels in ctx.v1[ctx.main_key]["labels"].values():
        for key in ctx.editable:
            if page_labels.get(key):
                return page_labels[key]
    return None


def _run_round(ctx: _Ctx, rnd: int, current: dict, v1_label, tried: set, history: list):
    """회차 하나: 진단 → 제안 → 후보 평가 → 채택.

    반환 (기록, choose 결과 또는 None(진단에서 멈춤), 후보 목록, {candidateId: 목표별 확률}).
    """
    main_goal = ctx.plan["mainGoal"]
    main_probs = current["probs"][ctx.main_key]
    est = reach.estimate(ctx.site_map, main_probs, ctx.start, main_goal["goalPage"], ctx.max_steps)
    labels = _merge_labels(ctx.v1[ctx.main_key]["labels"], current["label"], ctx.editable)
    diag = diagnose(ctx.site_map, main_probs, ctx.start, main_goal["goalPage"], est, labels=labels,
                    editable_keys=ctx.editable)
    record = {"round": rnd, "diagnosis": diag,
              "baseline": {"label": current["label"] or v1_label, "mainScore": _round_score(current["main"]),
                           "guardScores": {k: _round_score(v) for k, v in current["guards"].items()}}}
    if diag["reason"]:
        record.update(proposal=None, candidates=[], adopted=None)
        return record, None, [], {}

    target = diag["targetButton"]
    siblings = sorted({lbl for k, lbl in (labels.get(diag["bottleneckPage"]) or {}).items()
                       if k != target["semanticKey"] and region_of(k) == target["region"] and lbl})
    diag["text"] = describe(diag, main_goal.get("situation") or main_goal["name"])
    tried.add(target["label"])
    attempts = _propose(ctx, rnd, diag, siblings, tried, history)
    ai_labels = [f["label"] for f in attempts[-1]["filtered"] if f["accepted"]]

    # 후보: 1회차는 사람 후보 + AI, 이후 AI만. 이미 시험한 이름은 뺀다
    pool = [(c["label"], "HUMAN") for c in ctx.plan.get("humanCandidates", []) if rnd == 1 and c["label"] not in tried]
    pool += [(lbl, "AI") for lbl in ai_labels if lbl not in tried]
    cands, evaluated = _evaluate_candidates(ctx, rnd, pool, tried)

    # 채택 (#103: mainScore = 현재 기준, guardScores = 최초 v1)
    baseline = {"mainScore": current["main"], "guardScores": dict(ctx.v1_guards)}
    chosen = ctx.deps.policy.choose(baseline, [_policy_view(c) for c in cands], _policy_rules(ctx.plan["rules"]))
    _apply_decisions(cands, chosen, ctx.plan["rules"])
    record.update(proposal={"filtered": attempts[-1]["filtered"], "attempts": len(attempts)},
                  candidates=cands, adopted=chosen["adopted"])
    return record, chosen, cands, evaluated


def _propose(ctx: _Ctx, rnd: int, diag: dict, siblings: list, tried: set, history: list) -> list[dict]:
    """제안 (모델 호출·통과 후보가 없으면 재요청·원본 저장). 시도 기록 목록을 돌려준다."""
    target = diag["targetButton"]
    prompt = ctx.deps.policy.build_prompt(diag["text"], target, siblings, ctx.plan["persona"], history)
    context = {"currentLabel": target["label"], "siblingLabels": siblings, "triedLabels": sorted(tried),
               "maxLength": ctx.plan["edit"]["maxNewLabelLength"]}
    attempts = []
    for _ in range(1 + ctx.plan.get("aiProposal", {}).get("retryOnEmpty", 1)):
        response = ctx.deps.propose(prompt)
        filtered = ctx.deps.policy.filter_labels(response, context)
        attempts.append({"prompt": prompt, "response": response, "filtered": filtered})
        if any(f["accepted"] for f in filtered):
            break
    ctx.raw.append({"kind": "proposal", "round": rnd, "attempts": attempts})
    return attempts


def _evaluate_candidates(ctx: _Ctx, rnd: int, pool: list, tried: set) -> tuple[list[dict], dict]:
    """후보마다 A(글자만 바꾸기) 평가. 실패·화면 검사 탈락은 0점으로 바꾸지 않고 사유만 남긴다."""
    cands, evaluated, counts = [], {}, {"HUMAN": 0, "AI": 0}
    for order, (label, source) in enumerate(pool):
        counts[source] += 1
        cid = f"R{rnd}-{'H' if source == 'HUMAN' else 'A'}{counts[source]}"
        c = {"candidateId": cid, "label": label, "source": source, "order": order, "method": "A",
             "evaluationStatus": "COMPLETED", "layout": None, "mainScore": None, "guardScores": None,
             "decision": None, "reason": None}
        cands.append(c)
        tried.add(label)
        try:
            scores, layout, probs = _evaluate_one(ctx, rnd, cid, label)
        except Exception as e:   # 모델·브라우저 실패: 원인은 raw에 남긴다
            ctx.raw.append({"kind": "error", "round": rnd, "candidateId": cid, "label": label,
                            "error": type(e).__name__, "message": str(e)[:500]})
            c.update(evaluationStatus="FAILED", reason=f"EVALUATION_FAILED:{type(e).__name__}")
            continue
        c["layout"] = layout
        if layout is not None and not layout.get("valid"):
            c.update(evaluationStatus="SKIPPED", reason="INVALID_CANDIDATE:" + ",".join(layout.get("reasons") or []))
        elif any(v is None for v in scores.values()):
            c.update(evaluationStatus="FAILED", reason="EVALUATION_FAILED:ESTIMATE_NOT_COMPLETE")
        else:
            c.update(mainScore=_round_score(scores[ctx.main_key]),
                     guardScores={k: _round_score(scores[k]) for k in ctx.guard_keys})
            evaluated[cid] = probs
    return cands, evaluated


def _evaluate_one(ctx: _Ctx, rnd: int, cid: str, label: str):
    """후보 하나: 목표마다 고칠 버튼 화면만 다시 수집(A) + 나머지 화면은 v1 재사용 → 점수."""
    scores, probs_by_goal, layout = {}, {}, None
    for g in ctx.goals:
        got = ctx.deps.collect(g, label, "home")
        ctx.raw.append({"kind": "collect", "round": rnd, "candidateId": cid, "goalKey": g["goalKey"], "label": label,
                        "records": got.get("records", [])})
        if got.get("layout") is not None:
            layout = got["layout"]
            if not layout.get("valid"):
                return {}, layout, {}
        merged = {**ctx.v1[g["goalKey"]]["probs"], **got["probs"]}
        score, _ = _score(ctx.site_map, merged, ctx.start, g["goalPage"], ctx.max_steps)
        scores[g["goalKey"]], probs_by_goal[g["goalKey"]] = score, merged
    return scores, layout, probs_by_goal


def _merge_labels(v1_labels: dict, new_label, editable_keys) -> dict:
    """현재 기준의 버튼 글자 (채택된 이름이 있으면 고칠 버튼 글자를 바꿔서)."""
    out = {p: dict(m) for p, m in (v1_labels or {}).items()}
    if new_label:
        for m in out.values():
            for k in editable_keys:
                if k in m:
                    m[k] = new_label
    return out


def _policy_view(c: dict) -> dict:
    return {k: c[k] for k in ("candidateId", "label", "source", "order", "mainScore", "guardScores", "evaluationStatus")}


def _policy_rules(rules: dict) -> dict:
    """`choose`에 넘길 규칙 (#105 키 이름). 없는 키는 오류 — 조용히 기본값을 쓰지 않게 한다."""
    return {"minMainGain": rules["minMainGain"], "maxGuardLoss": rules["maxGuardLoss"], "tieTolerance": rules["tieTolerance"]}


def _apply_decisions(cands: list, chosen: dict, rules: dict) -> None:
    """`choose` 결과를 후보 기록에 옮긴다. 탈락 사유 이름은 #105 기준, NOT_BEST·TIE_LOST는 ranking에서 만든다."""
    rejected = {r["candidateId"]: r for r in chosen.get("rejected", [])}
    by_id = {c["candidateId"]: c for c in cands}
    winner = by_id.get(chosen.get("adopted"))
    for c in cands:
        cid = c["candidateId"]
        if winner is not None and cid == winner["candidateId"]:
            c.update(decision="ADOPTED", reason=None)
        elif cid in rejected:
            r = rejected[cid]
            reason = r["reason"]
            if r.get("guardGoals"):
                reason += ":" + ",".join(r["guardGoals"])
            c.update(decision="REJECTED", reason=c["reason"] or reason)
        elif cid in chosen.get("ranking", []):
            tie = winner is not None and abs(winner["mainScore"] - c["mainScore"]) <= rules["tieTolerance"] + 1e-12
            c.update(decision="REJECTED", reason="TIE_LOST" if tie else "NOT_BEST")
        else:
            c.update(decision="REJECTED", reason=c["reason"] or "EVALUATION_FAILED")


def _history_line(cands: list, plan: dict) -> str:
    """다음 회차 제안 프롬프트에 넣을 이전 결과 요약 (자연어, 점수·식별자 없음)."""
    names = {g["goalKey"]: g["name"] for g in plan["guardGoals"]}
    parts = []
    for c in cands:
        reason = c.get("reason") or ""
        if c["decision"] == "ADOPTED":
            parts.append(f"'{c['label']}'는 채택됐다")
        elif reason.startswith("GUARD_LOSS_EXCEEDED"):
            goals = reason.split(":", 1)[1].split(",") if ":" in reason else []
            who = ", ".join(names.get(g, g) for g in goals) or "다른 목표"
            parts.append(f"'{c['label']}'는 {who} 사용자를 잘못 끌어당겨 탈락했다")
    return "; ".join(parts) or "이전 후보는 개선 폭이 작았다"


def _build_result(ctx: _Ctx, created, v1_label, current, rounds, stop, official, verification) -> dict:
    """FE 샘플 형식(`improvement.sample.json`)의 결과."""
    plan, v1 = ctx.plan, ctx.v1
    names = {g["goalKey"]: g for g in ctx.goals}
    improved = current["label"] is not None

    def pair(key, before, after):
        return {"goalKey": key, "name": names[key]["name"], "before": _round_score(before),
                "after": _round_score(after) if improved else None,
                "delta": _round_score(after - before) if improved else None}

    target = next((r["diagnosis"]["targetButton"] for r in rounds if r["diagnosis"].get("targetButton")), None)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "improvementId": plan["improvementId"],
        "sample": False,
        "status": "COMPLETED",
        "createdAt": created,
        "finishedAt": ctx.deps.clock(),
        "plan": {k: plan[k] for k in ("improvementPlanId", "site", "persona", "mainGoal", "guardGoals", "rules", "model")
                 if k in plan},
        "summary": {
            "result": "IMPROVED" if improved else "NO_IMPROVEMENT",
            "target": None if target is None else {"pageKey": rounds[0]["diagnosis"]["bottleneckPage"],
                                                   "semanticKey": target["semanticKey"], "region": target["region"]},
            "beforeLabel": v1_label,
            "afterLabel": current["label"],
            "afterSource": current["source"],
            "roundsRun": len(rounds),
            "main": pair(ctx.main_key, v1[ctx.main_key]["score"], current["main"]),
            "guards": [pair(k, v1[k]["score"], current["guards"][k]) for k in ctx.guard_keys],
            "scoreSource": ("OFFICIAL_B" if official else "LOOP_A") if improved else None,
        },
        "rounds": rounds,
        "stop": stop,
        "official": official,
        "verification": verification,
        "artifacts": {"report": "report.md", "raw": "raw/"},
        "limitations": "Zoro-1의 선택 분포 기준 추정이며 실제 사람 클릭률이 아니다. 사람 사용성 테스트에 올릴 후보로 사용한다.",
    }


def save_result(out: dict, results_dir: str) -> str:
    """`results/improvements/<improvementId>/`에 improvement.json·raw/·report.md 저장. 폴더 경로를 돌려준다."""
    imp = out["improvement"]
    folder = os.path.join(results_dir, "improvements", imp["improvementId"])
    os.makedirs(os.path.join(folder, "raw"), exist_ok=True)
    with open(os.path.join(folder, "improvement.json"), "w", encoding="utf-8") as f:
        json.dump(imp, f, ensure_ascii=False, indent=2)
    for i, item in enumerate(out["raw"], 1):
        with open(os.path.join(folder, "raw", f"{i:03d}_{item['kind']}.json"), "w", encoding="utf-8") as f:
            json.dump(item, f, ensure_ascii=False, indent=2)
    with open(os.path.join(folder, "report.md"), "w", encoding="utf-8") as f:
        f.write(report_md(imp))
    return folder


def report_md(imp: dict) -> str:
    """사람이 읽는 한 장 요약 (설계 §4.7)."""
    s = imp["summary"]
    lines = [f"# 자동 개선 리포트 {imp['improvementId']}", ""]
    if s["result"] == "IMPROVED":
        m = s["main"]
        lines += [f"[결론] 상단 메뉴 '{s['beforeLabel']}' → '{s['afterLabel']}'",
                  f"  주 목표({m['name']}) 최단 도달 {m['before']:.3f} → {m['after']:.3f} ({m['delta'] * 100:+.1f}%p)"]
        for g in s["guards"]:
            lines.append(f"  지킬 목표({g['name']}) {g['before']:.3f} → {g['after']:.3f} ({g['delta'] * 100:+.1f}%p)")
        lines.append("  지킬 목표는 허용 감소 범위(−2%p) 이내 유지")
    else:
        lines.append(f"[결론] 개선안 없음 — 기존 '{s['beforeLabel']}' 유지")
    lines += ["", "[과정]"]
    for r in imp["rounds"]:
        cands = r.get("candidates") or []
        adopted = next((c["label"] for c in cands if c["decision"] == "ADOPTED"), None)
        lines.append(f"  회차 {r['round']}: 후보 {len(cands)}개 → 채택 {adopted or '없음'}")
        for c in cands:
            if c["decision"] != "ADOPTED":
                lines.append(f"    - {c['label']} ({'사람' if c['source'] == 'HUMAN' else 'AI'}): {c['reason']}")
    lines += ["", f"[멈춤] {imp['stop']['reason']} — {imp['stop']['detail']}",
              f"[검증] {imp['verification']['status']}", "", f"[한계] {imp['limitations']}", ""]
    return "\n".join(lines)
