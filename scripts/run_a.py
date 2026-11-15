"""테스트 시나리오 A (G0-A) 실행기.

    python scripts/run_a.py              # A1~A10 전부
    python scripts/run_a.py A1 A2        # 일부만
    python scripts/run_a.py --dry-run    # 모델 없이 요청 payload와 누출 검사만

결과: results/json/a/a_<timestamp>.jsonl (원시 응답 포함) + 콘솔 요약 + results/run_log.md 실행 기록 행.
--dry-run이면 파일을 만들지 않는다.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from uxm import config, prompts  # noqa: E402
from uxm.leak import assert_no_leak  # noqa: E402
from uxm.metrics import decision_metrics, validate_probabilities  # noqa: E402
from uxm.scenarios import SCENARIOS, build_request  # noqa: E402


def fmt(x):
    return "N/A" if x is None else f"{x:.3f}"


def summarize(sid, meta, resp):
    s = SCENARIOS[sid]
    if s["kind"] == "yesno":
        p_yes = resp["answers"]["goal_reached"]["noul"]
        ok = (p_yes >= 0.5) == (s["expect"] == "yes")
        return {"pYes": p_yes, "pass": ok}, f"P(yes)={p_yes:.3f} 기대={s['expect']} {'PASS' if ok else 'FAIL'}"

    a = resp["answers"]["next_click"]
    probs = a["probabilities"]
    validate_probabilities(probs, list(meta["optionMap"]))
    m = decision_metrics(probs, meta["backOptionId"])
    by_key = {meta["optionMap"][k]: v for k, v in probs.items()}
    out = {"choice": a["choice"], "choiceKey": meta["optionMap"][a["choice"]], "bySemanticKey": by_key, **m}
    line = "  ".join(f"{k}={v:.3f}" for k, v in by_key.items())
    line += f" | H_click={fmt(m['clickOnlyNormalizedEntropy'])} top2={fmt(m['clickOnlyTop2Margin'])}"
    if m["goBackProbability"] is not None:
        line += f" P(back)={m['goBackProbability']:.3f}"
    if s.get("expect_top"):
        out["pass"] = out["choiceKey"] == s["expect_top"]
        line += f" 기대최고={s['expect_top']} {'PASS' if out['pass'] else 'FAIL'}"
    return out, line


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*", default=list(SCENARIOS))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cfg = config.load()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(ROOT, "results", "json", "a", f"a_{stamp}.jsonl")
    if not a.dry_run:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

    backend = None
    if not a.dry_run:
        from uxm.backends import make_backend
        print(f"[load] backend={cfg['backend']} device={cfg['device']} ...", flush=True)
        backend = make_backend(cfg)
        print(f"[load] {backend.info()}", flush=True)

    rows = []
    with open(os.devnull if a.dry_run else out_path, "w", encoding="utf-8") as f:
        for sid in a.ids:
            req, meta = build_request(sid)
            assert_no_leak(req)
            if a.dry_run:
                print(f"--- {sid} {SCENARIOS[sid]['title']}\n{json.dumps(req, ensure_ascii=False, indent=2)}")
                continue
            for i in range(SCENARIOS[sid].get("repeat", 1)):
                t0 = time.perf_counter()
                resp = backend.decide(req, cfg["temperature"])
                wall = round((time.perf_counter() - t0) * 1000)
                summary, line = summarize(sid, meta, resp)
                tag = f"{sid}#{i + 1}" if SCENARIOS[sid].get("repeat") else sid
                print(f"{tag:6} {wall:>7}ms  {line}", flush=True)
                rec = {"ts": datetime.now().isoformat(timespec="seconds"), "scenario": sid, "iter": i + 1,
                       "promptVersion": prompts.PROMPT_VERSION, "modelConfig": backend.info(),
                       "temperature": cfg["temperature"], "goalJudgmentMode": cfg["goal_judgment_mode"],
                       "wallMs": wall, "request": req,
                       "optionMap": meta["optionMap"], "response": resp, "summary": summary,
                       "resultOrigin": "LIVE"}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                rows.append((tag, line))

    if rows:
        log = os.path.join(ROOT, "results", "run_log.md")
        new = not os.path.exists(log)
        with open(log, "a", encoding="utf-8") as f:
            if new:
                f.write("| 시각 | 시나리오 | promptVersion | 백엔드 | 결과 | 통과 |\n|---|---|---|---|---|---|\n")
            for tag, line in rows:
                verdict = "PASS" if "PASS" in line else ("FAIL" if "FAIL" in line else "기록")
                f.write(f"| {stamp} | {tag} | {prompts.PROMPT_VERSION} | {cfg['backend']}/{cfg['device']} "
                        f"| {line.replace('|', '/')} | {verdict} |\n")
        print(f"\n저장: {out_path}\n기록: {log}")


if __name__ == "__main__":
    main()
