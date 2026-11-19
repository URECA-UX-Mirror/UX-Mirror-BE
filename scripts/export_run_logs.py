"""제출용 실행 로그 내보내기 (#83, 해커톤 Track 1 제출물 "시나리오 3가지의 에이전트 실행 로그").

Run 결과(`results/runs/<runId>/`)는 Git에서 빠져 있으므로, 고른 Run을 저장소의 `run-logs/`로 복사하고
심사위원이 바로 읽을 수 있는 목록 문서(`run-logs/README.md`)를 만든다.

    .venv\\Scripts\\python scripts\\export_run_logs.py run_before_xxx run_after_yyy run_before_zzz
    .venv\\Scripts\\python scripts\\export_run_logs.py --results-dir D:\\gpu_results run_...   # 다른 결과 폴더
    .venv\\Scripts\\python scripts\\export_run_logs.py --force run_...                        # 다시 만들기

- 증거 보존 절차(docs/final-run-evaluation.md 4장)대로 Run 폴더의 run.json, steps.jsonl, events.jsonl, issues.json,
  screenshots/, raw/를 **복사**한다. 원본은 옮기거나 고치지 않는다. 같은 실험 키의 비교 결과도 있으면 복사한다.
- 복사하기 전에 인증 정보(키·토큰·.env 값)와 로컬 경로가 섞였는지 검사하고, 걸리면 아무것도 쓰지 않고 멈춘다.
- 리허설·가짜 결과, 무효 Run, 분석 미완료는 목록에 경고로 표시한다 (막지는 않는다. 고른 사람이 판단).
"""
import argparse
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RESULTS = os.path.join(ROOT, "results")
DEFAULT_OUT = os.path.join(ROOT, "run-logs")
RUN_FILES = ("run.json", "steps.jsonl", "events.jsonl", "issues.json")
RUN_DIRS = ("screenshots", "raw")
TEXT_EXT = (".json", ".jsonl", ".txt", ".md")
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

# 걸리면 멈추는 것: 인증 정보·로컬 경로 (공개 저장소에 올라가면 안 된다)
SECRET_PATTERNS = [
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer 토큰"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|password|access[_-]?token)\b\"?\s*[:=]\s*\"?[^\s\",]{6,}"), "키·비밀번호 값"),
    (re.compile(r"\bsk-[A-Za-z0-9]{16,}"), "API 키 형식"),
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}"), "Hugging Face 토큰"),
    (re.compile(r"(?i)[A-Z]:\\\\?Users\\\\?[^\\\"\s]+"), "Windows 사용자 경로"),
    (re.compile(r"/(Users|home)/[^/\"\s]+/"), "로컬 사용자 경로"),
]
ENV_SECRET_KEYS = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|BASE_URL)", re.I)


class ExportError(Exception):
    pass


def env_values(path: str) -> dict[str, str]:
    """`.env`의 인증 관련 값(키·토큰·서버 주소). 결과 파일에 그대로 들어 있으면 멈춘다."""
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip('"').strip("'")
            if ENV_SECRET_KEYS.search(key) and len(value) >= 8:
                out[key.strip()] = value
    return out


def scan_text(text: str, where: str, env: dict[str, str]) -> list[str]:
    problems = [f"{where}: {label}" for pattern, label in SECRET_PATTERNS if pattern.search(text)]
    problems += [f"{where}: .env의 {key} 값" for key, value in env.items() if value in text]
    return problems


def run_files(run_dir: str) -> list[str]:
    """복사할 파일 (Run 폴더 기준 상대 경로)."""
    files = [name for name in RUN_FILES if os.path.isfile(os.path.join(run_dir, name))]
    for d in RUN_DIRS:
        base = os.path.join(run_dir, d)
        if os.path.isdir(base):
            files += sorted(os.path.join(d, n) for n in os.listdir(base) if os.path.isfile(os.path.join(base, n)))
    return files


def load_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def warnings_of(run: dict, issues: dict | None) -> list[str]:
    """목록 문서에 표시할 경고. 제출에 쓰기 전에 사람이 확인할 것."""
    out = []
    if run.get("resultOrigin") != "LIVE":
        out.append(f"resultOrigin={run.get('resultOrigin')} (실제 모델 실행 결과가 아님)")
    if (run.get("modelEndpointId") or "") in ("", "local", "mock"):
        out.append(f"modelEndpointId={run.get('modelEndpointId')} (행사 서버 식별자가 아님, 리허설과 구분되지 않음)")
    if run.get("validity") != "VALID":
        out.append(f"validity={run.get('validity')} ({run.get('invalidReason') or '종료 전'})")
    if run.get("status") in ("READY", "RUNNING"):
        out.append(f"status={run.get('status')} (아직 끝나지 않음)")
    status = (issues or {}).get("analysisStatus", "PENDING")
    if status != "COMPLETED":
        out.append(f"analysisStatus={status} (Issue 분석이 끝나지 않음)")
    return out


def summarize(run: dict, issues: dict | None, files: list[str]) -> dict:
    s = run.get("summary") or {}
    return {
        "runId": run["runId"], "experimentKey": run.get("experimentKey"), "variant": run.get("variant"),
        "siteId": run.get("siteId"), "mode": run.get("mode") or "VERSION_COMPARE",
        "goal": run.get("goal"), "persona": run.get("persona"),
        "verification": run.get("verification"),
        "status": run.get("status"), "terminationReason": run.get("terminationReason"),
        "validity": run.get("validity"), "goalReached": s.get("goalReached"),
        "stepCount": s.get("stepCount"), "backCount": s.get("backCount"),
        "path": [v.get("pageKey") for v in s.get("path") or []],
        "issueTypes": sorted({i.get("issueType") for i in (issues or {}).get("items", []) if i.get("issueType")}),
        "analysisStatus": (issues or {}).get("analysisStatus", "PENDING"),
        "resultOrigin": run.get("resultOrigin"), "modelVersion": run.get("modelVersion"),
        "modelEndpointId": run.get("modelEndpointId"), "promptVersion": run.get("promptVersion"),
        "temperature": run.get("temperature"), "createdAt": run.get("createdAt"), "finishedAt": run.get("finishedAt"),
        "screenshotCount": sum(1 for f in files if f.startswith("screenshots")),
        "rawCount": sum(1 for f in files if f.startswith("raw")),
        "warnings": warnings_of(run, issues),
    }


def readme(rows: list[dict], comparisons: list[str]) -> str:
    lines = ["# 에이전트 실행 로그", "",
             "UX MIRROR 에이전트가 실제로 돌린 Run 기록이다. Run마다 폴더 하나이고, 모든 클릭 판단은 Zoro-1이 낸 후보별 확률로 정했다.",
             "", "| Run | 실험 키 | 종류 | 목표 | 결과 | 지나간 화면 | 단계 | 찾은 Issue |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        kind = f"시나리오 ({r['siteId']})" if r["mode"] == "SCENARIO" else f"버전 비교 ({r['variant']})"
        result = f"{r['status']} · {r['terminationReason']} · {r['validity']}"
        lines.append(f"| [{r['runId']}]({r['runId']}/) | {r['experimentKey']} | {kind} | {r['goal']} | {result} | "
                     f"{' → '.join(r['path']) or '-'} | {r['stepCount']} | {', '.join(r['issueTypes']) or '-'} |")
    for r in rows:
        v = r["verification"] or {}
        lines += ["", f"## {r['runId']}", "",
                  f"- 목표: {r['goal']}",
                  f"- 사용자 유형: {r['persona']}",
                  f"- 성공 조건: `{v.get('path')}` 화면에 '{v.get('text')}' 글자가 보이면 도착",
                  f"- 결과: {r['status']} ({r['terminationReason']}), 목표 도달 {r['goalReached']}, 유효성 {r['validity']}",
                  f"- 지나간 화면: {' → '.join(r['path']) or '-'} (단계 {r['stepCount']}, 되돌아가기 {r['backCount']})",
                  f"- 찾은 Issue: {', '.join(r['issueTypes']) or '없음'} (분석 {r['analysisStatus']})",
                  f"- 모델: {r['modelVersion']} · 서버 {r['modelEndpointId']} · prompt {r['promptVersion']} · "
                  f"temperature {r['temperature']} · 출처 {r['resultOrigin']}",
                  f"- 시각: {r['createdAt']} → {r['finishedAt']}",
                  f"- 파일: 스크린샷 {r['screenshotCount']}개, 원본 모델 요청·응답 {r['rawCount']}개"]
        if r["warnings"]:
            lines += ["", "> 확인 필요: " + " / ".join(r["warnings"])]
    if comparisons:
        lines += ["", "## Before/After 비교", ""] + [f"- [comparisons/{c}](comparisons/{c})" for c in comparisons]
    lines += ["", "## 파일 읽는 법", "",
              "| 파일 | 내용 |", "|---|---|",
              "| `run.json` | Run 설정(목표·사용자 유형·성공 조건·모델·경로 기준)과 최종 요약 |",
              "| `steps.jsonl` | 한 줄 = 한 단계. 화면, 클릭 후보, Zoro-1 후보별 확률, 고른 행동과 결과 |",
              "| `events.jsonl` | 되돌아가기·도착·오류 같은 사건 |",
              "| `issues.json` | 찾은 UX 문제와 근거 |",
              "| `screenshots/` | `N.png` 원본 화면, `N_overlay.png` 후보별 확률 배지 |",
              "| `raw/` | Zoro-1에 보낸 원본 요청과 받은 응답 |", ""]
    return "\n".join(lines)


def export(run_ids: list[str], results_dir: str, out_dir: str, force: bool = False, env_path: str | None = None) -> dict:
    """검사를 모두 통과해야 쓰기 시작한다. 반환: {"rows", "comparisons", "out"}."""
    if not run_ids:
        raise ExportError("Run ID를 하나 이상 주세요.")
    if len(set(run_ids)) != len(run_ids):
        raise ExportError("같은 Run ID가 두 번 들어 있습니다.")
    runs_dir = os.path.join(results_dir, "runs")
    if os.path.realpath(out_dir).startswith(os.path.realpath(runs_dir) + os.sep) or \
            os.path.realpath(out_dir) == os.path.realpath(runs_dir):
        raise ExportError("내보낼 폴더가 원본 결과 폴더 안입니다.")
    env = env_values(env_path or os.path.join(ROOT, ".env"))

    plans, problems = [], []
    for run_id in run_ids:
        if not SAFE_ID.match(run_id):
            raise ExportError(f"Run ID 형식이 올바르지 않습니다: {run_id!r}")
        src = os.path.join(runs_dir, run_id)
        if not os.path.isfile(os.path.join(src, "run.json")):
            raise ExportError(f"Run을 찾을 수 없습니다: {src}")
        files = run_files(src)
        for rel in files:
            if rel.endswith(TEXT_EXT):
                with open(os.path.join(src, rel), encoding="utf-8", errors="replace") as f:
                    problems += scan_text(f.read(), f"{run_id}/{rel}", env)
        run = load_json(os.path.join(src, "run.json"))
        issues_path = os.path.join(src, "issues.json")
        issues = load_json(issues_path) if os.path.isfile(issues_path) else None
        plans.append((run_id, src, files, run, issues))
    if problems:
        raise ExportError("공개 저장소에 올리면 안 되는 값이 있어 멈춥니다:\n  " + "\n  ".join(problems))

    comparisons = []
    for key in sorted({p[3].get("experimentKey") for p in plans if p[3].get("experimentKey")}):
        path = os.path.join(results_dir, "comparisons", f"{key}.json")
        if os.path.isfile(path) and SAFE_ID.match(key):
            with open(path, encoding="utf-8", errors="replace") as f:
                found = scan_text(f.read(), f"comparisons/{key}.json", env)
            if found:
                raise ExportError("공개 저장소에 올리면 안 되는 값이 있어 멈춥니다:\n  " + "\n  ".join(found))
            comparisons.append((key, path))

    if os.path.exists(out_dir) and os.listdir(out_dir):
        if not force:
            raise ExportError(f"{out_dir}에 이미 내용이 있습니다. 다시 만들려면 --force")
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    for run_id, src, files, run, issues in plans:
        for rel in files:
            dst = os.path.join(out_dir, run_id, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(src, rel), dst)
        rows.append(summarize(run, issues, files))
    if comparisons:
        os.makedirs(os.path.join(out_dir, "comparisons"), exist_ok=True)
        for key, path in comparisons:
            shutil.copy2(path, os.path.join(out_dir, "comparisons", f"{key}.json"))
    with open(os.path.join(out_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write(readme(rows, [f"{k}.json" for k, _ in comparisons]))
    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"runs": rows, "comparisons": [k for k, _ in comparisons]}, f, ensure_ascii=False, indent=2)
    return {"rows": rows, "comparisons": [k for k, _ in comparisons], "out": out_dir}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_ids", nargs="+", metavar="RUN_ID")
    ap.add_argument("--results-dir", default=os.getenv("UXM_RESULTS_DIR") or DEFAULT_RESULTS)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--force", action="store_true", help="내보낼 폴더에 내용이 있어도 다시 만든다")
    args = ap.parse_args(argv)
    try:
        result = export(args.run_ids, args.results_dir, args.out, force=args.force)
    except ExportError as e:
        print(f"[중단] {e}", file=sys.stderr)
        return 1
    for r in result["rows"]:
        mark = " [확인 필요: " + " / ".join(r["warnings"]) + "]" if r["warnings"] else ""
        print(f"{r['runId']}: {r['status']} {r['validity']} {' → '.join(r['path'])}{mark}")
    print(f"저장: {result['out']} (Run {len(result['rows'])}개, 비교 {len(result['comparisons'])}개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
