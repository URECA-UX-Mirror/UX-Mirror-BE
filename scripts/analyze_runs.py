"""종료된 Run의 Issue 분석을 다시 만든다 (issues.json만 쓴다, run.json은 건드리지 않는다).

조회 API는 파일을 쓰지 않으므로, 분석 모듈이 생기기 전에 끝난 Run이나 샘플은 이 스크립트로 분석한다.

    python scripts/analyze_runs.py run_before_xxx run_after_xxx
    python scripts/analyze_runs.py --all            # issues.json이 없는 종료 Run 전부
    python scripts/analyze_runs.py --force RUN_ID   # 이미 있어도 다시 만든다

Git에 올린 샘플(results/runs/sample_*)을 분석하면 issues.json이 새로 생기므로 커밋 여부를 따로 정한다.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.integrations import Integrations  # noqa: E402
from api.run_factory import TERMINAL_STATUSES  # noqa: E402
from api.runner import Runner  # noqa: E402
from api.store import DEFAULT_RESULTS_DIR, RunStore  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_ids", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--results-dir", default=os.getenv("UXM_RESULTS_DIR") or DEFAULT_RESULTS_DIR)
    a = ap.parse_args(argv)

    store = RunStore(a.results_dir)
    integrations = Integrations()
    if integrations.detect_issues() is None:
        print("분석 모듈(uxm/analysis.py의 detect_issues)이 없습니다.", file=sys.stderr)
        return 1
    runner = Runner(store, integrations)

    runs = store.list_runs() if a.all else [store.load_run(r) for r in a.run_ids if store.exists(r)]
    done = 0
    for run in runs:
        if run.get("status") not in TERMINAL_STATUSES:
            print(f"[skip] {run['runId']}: 아직 종료되지 않음")
            continue
        if store.load_issues(run["runId"]) is not None and not a.force:
            print(f"[skip] {run['runId']}: issues.json 있음 (--force로 다시 만들기)")
            continue
        runner.analyze(run, update_run=False)
        status = (store.load_issues(run["runId"]) or {}).get("analysisStatus")
        print(f"[ok] {run['runId']}: {status}")
        done += 1
    return 0 if done or not runs else 1


if __name__ == "__main__":
    sys.exit(main())
