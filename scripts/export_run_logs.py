"""제출용 실행 로그 내보내기 (#83, 해커톤 Track 1 제출물 "시나리오 3가지의 에이전트 실행 로그").

Run 결과(`results/runs/<runId>/`)는 Git에서 빠져 있으므로, 고른 Run을 저장소의 `run-logs/`로 복사하고
심사위원이 바로 읽을 수 있는 목록 문서(`run-logs/README.md`)를 만든다.

    .venv\\Scripts\\python scripts\\export_run_logs.py run_before_xxx run_after_yyy run_before_zzz
    .venv\\Scripts\\python scripts\\export_run_logs.py --results-dir D:\\gpu_results --env D:\\gpu\\.env run_...
    .venv\\Scripts\\python scripts\\export_run_logs.py --force run_...        # 이 스크립트가 만든 run-logs/를 다시 만들기
    .venv\\Scripts\\python scripts\\export_run_logs.py --allow "password: 1234" run_...   # 확인한 화면 글자는 건너뛰기

- 증거 보존 절차(docs/final-run-evaluation.md 4장)대로 Run 폴더 전체를 **복사**한다 (임시·로그 파일만 뺀다).
  원본은 옮기거나 고치지 않는다. 같은 실험 키의 비교 결과도 있으면 복사한다.
- 복사하기 전에 공개되면 안 되는 값을 모두 찾아 한 번에 보여 주고, 하나라도 있으면 아무것도 쓰지 않고 멈춘다.
  인증 정보(Bearer 토큰, JSON 키 형태의 키·비밀번호), `.env`의 키·토큰·서버 주소 값(`--env`로 GPU 서버 `.env`도),
  서버 주소(IP·`host:port`, 내 컴퓨터 주소는 제외), 로컬·서버 경로(`/Users`, `/home`, `/root`, `/workspace`, `/mnt` …).
- 출력 폴더는 결과 폴더와 겹칠 수 없다(어느 방향이든). `--force`는 이 스크립트가 만든 폴더(`index.json`)만 바꾸고,
  옆 임시 폴더에 다 쓴 뒤 바꿔치기하므로 중간에 실패해도 이전 제출본이 남는다.
- 리허설·가짜 결과, 무효 Run, 분석 미완료는 목록에 "확인 필요"로 표시한다 (막지는 않는다. 고른 사람이 판단).
"""
import argparse
import fnmatch
import json
import os
import re
import secrets
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from api.store import is_safe_id  # noqa: E402  (실행 API와 같은 Run ID 규칙)

DEFAULT_RESULTS = os.path.join(ROOT, "results")
DEFAULT_OUT = os.path.join(ROOT, "run-logs")
MARKER = "index.json"   # 이 스크립트가 만든 폴더 표시
TEXT_EXT = (".json", ".jsonl", ".txt", ".md", ".log", ".csv", ".html", ".yaml", ".yml")
EXCLUDE = (".env", ".env.*", "*.tmp", ".tmp_*", "*.lock", "__pycache__", ".DS_Store", "Thumbs.db")

# 공개 저장소에 올리면 안 되는 값
SECRET_PATTERNS = [
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer 토큰"),
    # JSON 키 형태만 (화면 글자 "password: …"는 걸지 않는다)
    (re.compile(r'(?i)"(api[_-]?key|secret|password|passwd|access[_-]?token|auth[_-]?token|authorization)"\s*:\s*"[^"]{6,}"'),
     "키·비밀번호 값"),
    (re.compile(r"\bsk-[A-Za-z0-9]{16,}"), "API 키 형식"),
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}"), "Hugging Face 토큰"),
    # 서버 주소: IP 주소, 포트가 붙은 주소 (내 컴퓨터 127.0.0.1·localhost는 제외)
    (re.compile(r"(?i)\bhttps?://(?!(?:127\.\d+\.\d+\.\d+|localhost|\[::1\])(?:[:/\"\s]|$))"
                r"(?:\d{1,3}(?:\.\d{1,3}){3}|[\w.-]+:\d{2,5})[^\s\"'<>]*"), "서버 주소"),
    # 로컬·서버 경로 (주소 안의 /data/ 같은 경로는 앞 글자가 있어 걸리지 않는다)
    (re.compile(r"(?i)\b[A-Z]:[\\/]{1,2}(?:Users|home|workspace)[\\/]{1,2}[^\s\"']+"), "Windows 사용자 경로"),
    (re.compile(r"(?<![\w.:/-])/(?:Users|home|root|workspace|mnt|data|opt|srv)/[^\s\"']+"), "로컬·서버 경로"),
]
ENV_SECRET_KEYS = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|BASE_URL|ENDPOINT|HOST)", re.I)


class ExportError(Exception):
    pass


def env_values(paths: list[str]) -> dict[str, str]:
    """`.env`들의 인증 관련 값(키·토큰·서버 주소). 결과 파일에 그대로 들어 있으면 멈춘다."""
    out = {}
    for path in paths:
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                value = value.strip().strip('"').strip("'")
                if ENV_SECRET_KEYS.search(key) and len(value) >= 8:
                    out[f"{os.path.basename(path)}:{key.strip()}"] = value
    return out


def _snippet(text: str, start: int, end: int) -> str:
    return text[start:end].replace("\n", " ")[:80]


def scan_text(text: str, where: str, env: dict[str, str], allow: list[str]) -> list[str]:
    """찾은 값마다 `위치: 종류 (값)`. allow에 든 글자를 포함한 값은 건너뛴다(사람이 확인한 화면 글자 등)."""
    found = []
    for pattern, label in SECRET_PATTERNS:
        for m in pattern.finditer(text):
            snip = _snippet(text, m.start(), m.end())
            if not any(a in snip for a in allow):
                found.append(f"{where}: {label} ({snip})")
                break
    for key, value in env.items():
        if value in text and not any(a in value for a in allow):
            found.append(f"{where}: .env 값 {key}")
    return found


def _excluded(name: str) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in EXCLUDE)


def run_files(run_dir: str) -> list[str]:
    """Run 폴더 전체 (임시·로그·.env만 뺀다). Run 폴더 기준 상대 경로."""
    files = []
    for base, dirs, names in os.walk(run_dir):
        dirs[:] = sorted(d for d in dirs if not _excluded(d))
        for name in sorted(names):
            if not _excluded(name):
                files.append(os.path.relpath(os.path.join(base, name), run_dir))
    return files


def _overlaps(a: str, b: str) -> bool:
    a, b = os.path.realpath(a), os.path.realpath(b)
    return os.path.commonpath([a, b]) in (a, b)


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
        "fileCount": len(files),
        "screenshotCount": sum(1 for f in files if f.replace("\\", "/").startswith("screenshots/")),
        "rawCount": sum(1 for f in files if f.replace("\\", "/").startswith("raw/")),
        "warnings": warnings_of(run, issues),
    }


def _md(value) -> str:
    """Markdown 표·목록에 넣을 값. 없으면 '-', '|'·줄바꿈은 표가 깨지지 않게."""
    if value is None or value == "" or value == []:
        return "-"
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def readme(rows: list[dict], comparisons: list[str]) -> str:
    lines = ["# 에이전트 실행 로그", "",
             "UX MIRROR 에이전트가 실제로 돌린 Run 기록이다. Run마다 폴더 하나이고, 모든 클릭 판단은 Zoro-1이 낸 후보별 확률로 정했다.",
             "", "| Run | 실험 키 | 종류 | 목표 | 결과 | 지나간 화면 | 단계 | 찾은 Issue |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        kind = f"시나리오 ({r['siteId']})" if r["mode"] == "SCENARIO" else f"버전 비교 ({r['variant']})"
        result = f"{r['status']} · {r['terminationReason']} · {r['validity']}"
        lines.append(f"| [{r['runId']}]({r['runId']}/) | {_md(r['experimentKey'])} | {_md(kind)} | {_md(r['goal'])} | "
                     f"{_md(result)} | {_md(' → '.join(r['path']))} | {_md(r['stepCount'])} | {_md(', '.join(r['issueTypes']))} |")
    for r in rows:
        v = r["verification"] or {}
        condition = (f"`{_md(v.get('path'))}` 화면에 '{_md(v.get('text'))}' 글자가 보이면 도착"
                     if v.get("path") and v.get("text") else "-")
        lines += ["", f"## {r['runId']}", "",
                  f"- 목표: {_md(r['goal'])}",
                  f"- 사용자 유형: {_md(r['persona'])}",
                  f"- 성공 조건: {condition}",
                  f"- 결과: {_md(r['status'])} ({_md(r['terminationReason'])}), 목표 도달 {_md(r['goalReached'])}, "
                  f"유효성 {_md(r['validity'])}",
                  f"- 지나간 화면: {_md(' → '.join(r['path']))} (단계 {_md(r['stepCount'])}, 되돌아가기 {_md(r['backCount'])})",
                  f"- 찾은 Issue: {_md(', '.join(r['issueTypes'])) if r['issueTypes'] else '없음'} (분석 {_md(r['analysisStatus'])})",
                  f"- 모델: {_md(r['modelVersion'])} · 서버 {_md(r['modelEndpointId'])} · prompt {_md(r['promptVersion'])} · "
                  f"temperature {_md(r['temperature'])} · 출처 {_md(r['resultOrigin'])}",
                  f"- 시각: {_md(r['createdAt'])} → {_md(r['finishedAt'])}",
                  f"- 파일: {r['fileCount']}개 (스크린샷 {r['screenshotCount']}개, 원본 모델 요청·응답 {r['rawCount']}개)"]
        if r["warnings"]:
            lines += ["", "> 확인 필요: " + " / ".join(_md(w) for w in r["warnings"])]
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


def _check_out(out_dir: str, results_dir: str, force: bool) -> None:
    for protected, what in ((results_dir, "결과 폴더"), (ROOT, "저장소")):
        if _overlaps(out_dir, protected) and not (protected == ROOT and _inside(out_dir, ROOT)):
            raise ExportError(f"내보낼 폴더({out_dir})가 {what}({protected})와 겹칩니다. 다른 폴더를 주세요.")
    if os.path.exists(out_dir) and os.listdir(out_dir):
        if not force:
            raise ExportError(f"{out_dir}에 이미 내용이 있습니다. 다시 만들려면 --force")
        if not os.path.isfile(os.path.join(out_dir, MARKER)):
            raise ExportError(f"{out_dir}은 이 스크립트가 만든 폴더가 아니라(--force로도) 바꾸지 않습니다.")


def _inside(path: str, parent: str) -> bool:
    """path가 parent 안의 하위 폴더인지 (같은 폴더는 아님)."""
    path, parent = os.path.realpath(path), os.path.realpath(parent)
    return path != parent and os.path.commonpath([path, parent]) == parent


def export(run_ids: list[str], results_dir: str, out_dir: str, force: bool = False,
           env_paths: list[str] | None = None, allow: list[str] | None = None) -> dict:
    """검사를 모두 통과해야 쓰기 시작한다. 반환: {"rows", "comparisons", "out"}."""
    if not run_ids:
        raise ExportError("Run ID를 하나 이상 주세요.")
    if len(set(run_ids)) != len(run_ids):
        raise ExportError("같은 Run ID가 두 번 들어 있습니다.")
    for run_id in run_ids:
        if not is_safe_id(run_id):
            raise ExportError(f"Run ID 형식이 올바르지 않습니다: {run_id!r} ([A-Za-z0-9_-] 1~64자)")
    _check_out(out_dir, results_dir, force)
    env = env_values(env_paths if env_paths is not None else [os.path.join(ROOT, ".env")])
    allow = list(allow or [])

    runs_dir = os.path.join(results_dir, "runs")
    plans, problems = [], []
    for run_id in run_ids:
        src = os.path.join(runs_dir, run_id)
        if not os.path.isfile(os.path.join(src, "run.json")):
            raise ExportError(f"Run을 찾을 수 없습니다: {src}")
        files = run_files(src)
        for rel in files:
            if rel.lower().endswith(TEXT_EXT):
                with open(os.path.join(src, rel), encoding="utf-8", errors="replace") as f:
                    problems += scan_text(f.read(), f"{run_id}/{rel}", env, allow)
        run = load_json(os.path.join(src, "run.json"))
        issues_path = os.path.join(src, "issues.json")
        issues = load_json(issues_path) if os.path.isfile(issues_path) else None
        plans.append((run_id, src, files, run, issues))
    comparisons = []
    for key in sorted({p[3].get("experimentKey") for p in plans if p[3].get("experimentKey")}):
        path = os.path.join(results_dir, "comparisons", f"{key}.json")
        if is_safe_id(key) and os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                problems += scan_text(f.read(), f"comparisons/{key}.json", env, allow)
            comparisons.append((key, path))
    if problems:
        raise ExportError("공개 저장소에 올리면 안 되는 값이 있어 멈춥니다 (확인한 화면 글자면 --allow):\n  "
                          + "\n  ".join(problems))

    # 옆 임시 폴더에 다 쓴 뒤 바꿔치기: 중간에 실패해도 이전 제출본과 원본이 그대로 남는다
    parent = os.path.dirname(os.path.abspath(out_dir))
    os.makedirs(parent, exist_ok=True)
    tmp = os.path.join(parent, f".{os.path.basename(out_dir)}.tmp-{secrets.token_hex(4)}")
    rows = []
    try:
        os.makedirs(tmp)
        for run_id, src, files, run, issues in plans:
            for rel in files:
                dst = os.path.join(tmp, run_id, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(os.path.join(src, rel), dst)
            rows.append(summarize(run, issues, files))
        if comparisons:
            os.makedirs(os.path.join(tmp, "comparisons"))
            for key, path in comparisons:
                shutil.copy2(path, os.path.join(tmp, "comparisons", f"{key}.json"))
        with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as f:
            f.write(readme(rows, [f"{k}.json" for k, _ in comparisons]))
        with open(os.path.join(tmp, MARKER), "w", encoding="utf-8") as f:
            json.dump({"runs": rows, "comparisons": [k for k, _ in comparisons]}, f, ensure_ascii=False, indent=2)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    old = None
    if os.path.exists(out_dir):
        old = os.path.join(parent, f".{os.path.basename(out_dir)}.old-{secrets.token_hex(4)}")
        os.rename(out_dir, old)
    os.rename(tmp, out_dir)
    if old:
        shutil.rmtree(old, ignore_errors=True)
    return {"rows": rows, "comparisons": [k for k, _ in comparisons], "out": out_dir}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_ids", nargs="+", metavar="RUN_ID")
    ap.add_argument("--results-dir", default=os.getenv("UXM_RESULTS_DIR") or DEFAULT_RESULTS)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--env", action="append", default=None, metavar="PATH",
                    help="검사에 쓸 .env (여러 번 가능, 예: GPU 서버 .env). 주지 않으면 저장소 .env")
    ap.add_argument("--allow", action="append", default=[], metavar="TEXT",
                    help="확인한 값은 건너뛴다 (걸린 값에 이 글자가 들어 있으면). 예: 화면에 보이는 문구")
    ap.add_argument("--force", action="store_true", help="이 스크립트가 만든 내보낼 폴더를 다시 만든다")
    args = ap.parse_args(argv)
    try:
        result = export(args.run_ids, args.results_dir, args.out, force=args.force, env_paths=args.env,
                        allow=args.allow)
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
