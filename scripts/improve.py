"""자동 개선 루프 실행 (#106, 「자동 개선 에이전트 설계 v2.2」·역할 배분 5·6).

두 단계로 돌린다. 쇼핑몰 서버(v1 8001)와 모델(.env, 행사 GPU)이 켜져 있어야 한다.

    # 1) 루프: v1에서 진단 → 제안 → 글자 바꾸기(A)·화면 검사 → 점수 → 채택. 결과 저장 (공식 점수·검증 없음)
    .venv\\Scripts\\python scripts\\improve.py loop --plan results\\improvement_plan.json

    # 2) 마무리: 채택 이름으로 만든 최종안 사이트(B, 이성집)로 공식 점수 + 실제 Run 검증 → 같은 결과 파일 갱신
    .venv\\Scripts\\python scripts\\improve.py finalize --plan results\\improvement_plan.json --final-url http://127.0.0.1:8010

- 결과: `results/improvements/<improvementId>/improvement.json`·`raw/`·`report.md` (FE 샘플 형식 그대로)
- 부품: 진단 `uxm/improve_diagnose`, 루프 `uxm/improve_loop`, 제안·채택 `uxm/improve_policy`(#105),
  글자 바꾸기·화면 검사 `uxm/label_edit`(#107), 도달 확률 `uxm/reach`(#100), 검증 Run `api/improvement`(#102)
- A(글자 바꾸기)는 걸러 내기 전용. 발표 수치는 2)의 B 공식 점수만 쓴다 (설계 §4.3).
- 검증 Run은 이 프로세스에서 만들므로, 2) 동안에는 API(FE)로 Run을 만들지 않는다.
- 결과에는 모델 서버 주소·최종안 사이트 주소를 남기지 않는다 (모델은 endpointId 이름만).
"""
import argparse
import json
import os
import sys
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from uxm import config, improve_loop, reach  # noqa: E402
from uxm.sitemap import load_site_map  # noqa: E402

DEFAULT_PLAN = os.path.join(ROOT, "results", "improvement_plan.json")
SITE_MAP = os.path.join(ROOT, "config", "sitemaps", "sitemap-shop-v1.json")
V1_URL = os.getenv("SHOP_BEFORE_URL", "http://127.0.0.1:8001")
CANDIDATE_POLICY = "candidate-v2"


class LabelNotApplied(Exception):
    """`apply_label`이 글자를 바꾸지 못함 (다른 ValueError와 구분하려고 따로 둔다)."""


# ---------------------------------------------------------------- 화면 확률 수집 (브라우저 + 모델)

class BrowserCollector:
    """`improve_loop.Deps.collect` 실제 구현. 브라우저 하나를 열어 두고 화면 확률을 모은다.

    label이 있으면 화면을 열 때마다 `apply_label`로 글자를 바꾸고(A), home에서 `check_layout`으로 화면 검사.
    `apply_label`의 ValueError는 화면 검사 실패(`LABEL_NOT_APPLIED`)로 돌려준다 (역할 배분 §3.1).
    """

    def __init__(self, base_url: str, site_map: dict, backend, cfg: dict, persona: str, start: str, *, apply=True):
        from uxm.browser import open_browser
        self.base_url, self.site_map, self.backend, self.cfg = base_url.rstrip("/"), site_map, backend, cfg
        self.persona, self.start, self.apply = persona, start, apply
        self.session = open_browser(self.base_url + site_map["pages"][start]["path"])
        self.page = self.session["page"]
        self.baseline = None   # v1 home 기준 스냅샷 (check_layout용)
        self._paths = {info["path"]: key for key, info in site_map["pages"].items()}

    def close(self):
        from uxm.browser import close_browser
        close_browser(self.session)

    def baseline_snapshot(self) -> dict:
        from api.site_instances import snapshot_from_extracted
        from uxm.candidates import VIEWPORT, extract
        home = self.site_map["pages"][self.start]["path"]
        self.page.goto(self.base_url + home, wait_until="networkidle")
        self.baseline = snapshot_from_extracted(extract(self.page), home, VIEWPORT, CANDIDATE_POLICY)
        return self.baseline

    def __call__(self, goal: dict, label, pages: str) -> dict:
        from uxm.candidates import extract as base_extract
        from uxm.label_edit import EDITABLE_KEY, apply_label, check_layout
        labels = {}

        def extract(p):
            if label and self.apply:
                try:
                    apply_label(p, EDITABLE_KEY, label)   # 화면을 열 때마다 다시 적용 (A)
                except ValueError as e:
                    raise LabelNotApplied(str(e)) from e
            got = base_extract(p)
            key = self._paths.get(urlsplit(p.url).path)
            if key:
                labels[key] = {c["semanticKey"]: c["label"] for c in got["candidates"]}
            return got

        sub = self.site_map if pages == "all" else {**self.site_map, "pages": {self.start: self.site_map["pages"][self.start]}}
        try:
            probs, records, mismatches = reach.collect_page_probabilities(
                self.page, self.base_url, sub, self.backend, goal=goal["goal"], persona=self.persona,
                temperature=self.cfg["temperature"], extract=extract, skip={goal["goalPage"]})
        except LabelNotApplied as e:   # apply_label 실패 → 화면 검사 실패 (점수 계산 안 함)
            return {"probs": {}, "labels": labels, "records": [{"error": str(e)[:300]}],
                    "layout": {"valid": False, "reasons": ["LABEL_NOT_APPLIED"]}}
        if mismatches:
            if not label:
                raise RuntimeError(f"v1 화면이 사이트 지도와 다릅니다: {mismatches}")
            return {"probs": {}, "labels": labels, "records": records,
                    "layout": {"valid": False, "reasons": ["MAP_MISMATCH"]}}
        layout = None
        if label:
            home = self.site_map["pages"][self.start]["path"]
            self.page.goto(self.base_url + home, wait_until="networkidle")
            if self.apply:
                apply_label(self.page, EDITABLE_KEY, label)
            layout = check_layout(self.baseline, self.page, EDITABLE_KEY, label, extract=base_extract)
        return {"probs": probs, "labels": labels, "records": _strip_records(records), "layout": layout}


def _strip_records(records: list) -> list:
    """원본 저장용: 모델 요청·응답은 남기고 그 밖의 런타임 값은 뺀다."""
    return [{k: r.get(k) for k in ("pageKey", "status", "candidateCount", "request", "response")} for r in records]


# ---------------------------------------------------------------- 제안 (모델 채팅)

def make_proposer(cfg: dict, plan: dict):
    """`Deps.propose`: 행사 GPU 서버의 채팅 API. 제안 프롬프트는 정책(`build_prompt`)이 만든다."""
    import requests
    url = cfg["base_url"].rstrip("/") + "/v1/chat/completions"
    ai = plan.get("aiProposal", {})

    def propose(prompt: str) -> str:
        body = {"model": "Zoro-1", "messages": [{"role": "user", "content": prompt}], "max_tokens": 300,
                "temperature": ai.get("chatTemperature", 0.6),
                "chat_template_kwargs": {"enable_thinking": bool(ai.get("enableThinking", False))}}
        r = requests.post(url, json=body, timeout=max(cfg["timeout_s"], 60))
        r.raise_for_status()
        return r.json()["choices"][0]["message"].get("content") or ""
    return propose


# ---------------------------------------------------------------- 실행

def load_plan(path: str, allow_draft: bool) -> dict:
    with open(path, encoding="utf-8") as f:
        plan = json.load(f)
    if plan.get("status") != "FROZEN" and not allow_draft:
        raise SystemExit(f"기준 고정 파일이 FROZEN이 아닙니다({plan.get('status')}). 동결 후 실행하거나 --allow-draft (연습용).")
    return plan


def cmd_loop(args) -> str:
    from uxm import improve_policy
    from uxm.backends import make_backend
    plan = load_plan(args.plan, args.allow_draft)
    site_map = load_site_map(SITE_MAP)
    cfg = config.load()
    collector = BrowserCollector(args.v1_url, site_map, make_backend(cfg), cfg, plan["persona"],
                                 plan.get("startPage", site_map["startPageKey"]))
    try:
        collector.baseline_snapshot()
        deps = improve_loop.Deps(collector, make_proposer(cfg, plan), improve_policy)
        out = improve_loop.run_loop(plan, site_map, deps)
    finally:
        collector.close()
    folder = improve_loop.save_result(out, args.results_dir)
    s = out["improvement"]["summary"]
    print(f"[루프] {s['result']}  '{s['beforeLabel']}' → '{s['afterLabel']}'  주 {s['main']['before']} → {s['main']['after']}"
          f"  멈춤 {out['improvement']['stop']['reason']}")
    print("저장:", folder)
    return folder


def cmd_finalize(args) -> str:
    """최종안 B: 공식 점수(choose 재판정, 기준은 주·지킬 모두 최초 v1) + 실제 Run 검증 → improvement.json 갱신."""
    from uxm import improve_policy
    from uxm.backends import make_backend
    plan = load_plan(args.plan, args.allow_draft)
    folder = os.path.join(args.results_dir, "improvements", plan["improvementId"])
    path = os.path.join(folder, "improvement.json")
    with open(path, encoding="utf-8") as f:
        imp = json.load(f)
    label = imp["summary"]["afterLabel"]
    if not label:
        raise SystemExit("채택된 이름이 없어 마무리할 것이 없습니다 (NO_IMPROVEMENT).")
    site_map = load_site_map(SITE_MAP)
    cfg = config.load()
    start = plan.get("startPage", site_map["startPageKey"])
    goals = improve_loop._goals(plan)
    max_steps = plan["score"].get("maxSteps", 8)

    # B 공식 점수: 실제 변형 사이트라 글자를 바꾸지 않는다(apply=False). 화면 검사는 B 화면 그대로
    v1_collector = BrowserCollector(args.v1_url, site_map, None, cfg, plan["persona"], start)
    try:
        baseline = v1_collector.baseline_snapshot()
    finally:
        v1_collector.close()
    b = BrowserCollector(args.final_url, site_map, make_backend(cfg), cfg, plan["persona"], start, apply=False)
    b.baseline = baseline
    scores, layout = {}, None
    try:
        for g in goals:
            got = b(g, label, "all")
            layout = got["layout"] or layout
            score, _ = improve_loop._score(site_map, got["probs"], start, g["goalPage"], max_steps) if got["probs"] else (None, None)
            scores[g["goalKey"]] = score
    finally:
        b.close()
    main_key = plan["mainGoal"]["goalKey"]
    guard_keys = [g["goalKey"] for g in plan["guardGoals"]]
    v1_scores = {"mainScore": imp["summary"]["main"]["before"],
                 "guardScores": {g["goalKey"]: g["before"] for g in imp["summary"]["guards"]}}
    cand = {"candidateId": "OFFICIAL-B", "label": label, "source": imp["summary"]["afterSource"] or "AI", "order": 0,
            "mainScore": scores.get(main_key), "guardScores": {k: scores.get(k) for k in guard_keys},
            "evaluationStatus": "COMPLETED" if (layout or {}).get("valid", True) and None not in scores.values() else "FAILED"}
    chosen = improve_policy.choose(v1_scores, [cand], improve_loop._policy_rules(plan["rules"]))
    adopted_a = next((c for r in imp["rounds"] for c in r.get("candidates", [])
                      if c["decision"] == "ADOPTED" and c["label"] == label), None)
    official = {"method": "B", "label": label, "site": {"siteLabel": f"moro-improve-{label}", "buildCheck": "ONLY_LABEL_DIFF"},
                "layout": layout, "scores": {"mainScore": improve_loop._round_score(scores.get(main_key)),
                                             "guardScores": {k: improve_loop._round_score(scores.get(k)) for k in guard_keys}},
                "scoresA": None if adopted_a is None else {"mainScore": adopted_a["mainScore"], "guardScores": adopted_a["guardScores"]},
                "choose": chosen}
    imp["official"] = official
    if chosen["adopted"] and None not in scores.values():   # 발표 수치는 B 공식 점수
        s = imp["summary"]
        s["main"].update(after=official["scores"]["mainScore"],
                         delta=improve_loop._round_score(scores[main_key] - s["main"]["before"]))
        for g in s["guards"]:
            g.update(after=official["scores"]["guardScores"][g["goalKey"]],
                     delta=improve_loop._round_score(scores[g["goalKey"]] - g["before"]))
        s["scoreSource"] = "OFFICIAL_B"

    if not args.skip_verify:
        imp["verification"] = verify(plan, args.final_url, label)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(imp, f, ensure_ascii=False, indent=2)
    with open(os.path.join(folder, "report.md"), "w", encoding="utf-8") as f:
        f.write(improve_loop.report_md(imp))
    print(f"[마무리] 공식 B 주 {official['scores']['mainScore']}  채택 {chosen['adopted']}  검증 {imp['verification']['status']}")
    print("저장:", folder)
    return folder


def verify(plan: dict, final_url: str, label: str) -> dict:
    """`api.improvement.run_verification`(#102)으로 v1·최종안 Run → FE 형식 `verification`."""
    from api.improvement import run_verification
    from api.store import DEFAULT_RESULTS_DIR, RunStore
    v = plan["verification"]
    goal_by_key = {g["goalKey"]: g for g in improve_loop._goals(plan)}
    goals = [{"goalId": k, "goal": goal_by_key[k]["goal"], "verification": goal_by_key[k]["verification"]}
             for k in v["goals"]]
    error, runs = None, []
    try:
        runs = run_verification(v["frozen"], final_url, label, goals)
    except Exception as e:   # 중간에 멈춰도 끝난 Run은 e.runs (#102)
        runs = getattr(e, "runs", [])
        error = {"code": type(e).__name__, "message": str(e)[:300]}
    store = RunStore(os.getenv("UXM_RESULTS_DIR") or DEFAULT_RESULTS_DIR)
    pairs = []
    for k in v["goals"]:
        mine = [r for r in runs if r["goalId"] == k]
        if not mine and error is None:
            continue
        key = f"{v['frozen']['improvementId']}-{k}"
        pair = {"goalKey": k, "name": goal_by_key[k]["name"], "experimentKey": key, "comparisonPath": f"/comparisons/{key}",
                "before": None, "after": None}
        for r in mine:
            pair["before" if r["variant"] == "before" else "after"] = _run_view(store, r)
        pairs.append(pair)
    status = "FAILED" if error else "COMPLETED"
    return {"status": status, "error": error, "method": "run_verification", "pairs": pairs}


def _run_view(store, r: dict) -> dict:
    run = store.load_run(r["runId"])
    s = run.get("summary") or {}
    return {"label": r["label"], "variant": r["variant"], "runId": r["runId"],
            "resultPath": f"/runs/{r['runId']}/result", "progressPath": f"/runs/{r['runId']}",
            "status": run.get("status"), "validity": run.get("validity"), "goalReached": s.get("goalReached"),
            "path": [p["pageKey"] for p in s.get("path", [])], "stepCount": s.get("stepCount"),
            "issues": s.get("detectedIssueTypes") or [], "resultOrigin": run.get("resultOrigin"),
            "modelEndpointId": run.get("modelEndpointId")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("loop", "finalize"):
        p = sub.add_parser(name)
        p.add_argument("--plan", default=DEFAULT_PLAN)
        p.add_argument("--results-dir", default=os.getenv("UXM_RESULTS_DIR") or os.path.join(ROOT, "results"))
        p.add_argument("--v1-url", default=V1_URL)
        p.add_argument("--allow-draft", action="store_true", help="동결 전 초안으로 연습 실행 (발표 수치 아님)")
        if name == "finalize":
            p.add_argument("--final-url", required=True, help="최종안 B 사이트 주소 (결과에 남기지 않는다)")
            p.add_argument("--skip-verify", action="store_true", help="공식 점수만, 실제 Run 검증은 하지 않음")
    args = ap.parse_args(argv)
    return cmd_loop(args) if args.cmd == "loop" else cmd_finalize(args)


if __name__ == "__main__":
    main()
