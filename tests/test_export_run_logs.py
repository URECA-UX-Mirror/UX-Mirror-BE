"""제출용 실행 로그 내보내기 (scripts/export_run_logs.py, #83)."""
import hashlib
import json
import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import export_run_logs as ex  # noqa: E402

SAMPLE = os.path.join(ROOT, "results", "runs", "sample_before")


def tree_hash(path: str) -> str:
    h = hashlib.sha256()
    for base, _, files in sorted(os.walk(path)):
        for name in sorted(files):
            p = os.path.join(base, name)
            h.update(os.path.relpath(p, path).encode())
            with open(p, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


@pytest.fixture
def results(tmp_path):
    """sample_before를 LIVE Run 2개(Before/After)로 바꿔 둔 결과 폴더."""
    runs = tmp_path / "results" / "runs"
    for run_id, variant in (("run_before_1", "before"), ("run_after_1", "after")):
        shutil.copytree(SAMPLE, runs / run_id)
        path = runs / run_id / "run.json"
        run = json.loads(path.read_text(encoding="utf-8"))
        run.update(runId=run_id, variant=variant, experimentKey="e-refund-1119", resultOrigin="LIVE",
                   modelVersion="trillionlabs/Zoro-1", modelEndpointId="event-l40s")
        path.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
        (runs / run_id / "issues.json").write_text(json.dumps(
            {"runId": run_id, "analysisStatus": "COMPLETED", "items": [{"issueType": "EXTRA_STEPS"}]}), encoding="utf-8")
    cmp = tmp_path / "results" / "comparisons"
    cmp.mkdir()
    (cmp / "e-refund-1119.json").write_text('{"status": "COMPLETED"}', encoding="utf-8")
    return tmp_path / "results"


def test_exports_selected_runs_with_index_and_leaves_originals(results, tmp_path):
    before = tree_hash(str(results))
    out = tmp_path / "run-logs"
    result = ex.export(["run_before_1", "run_after_1"], str(results), str(out), env_path=str(tmp_path / "none.env"))
    assert tree_hash(str(results)) == before   # 원본은 그대로
    for run_id in ("run_before_1", "run_after_1"):
        for name in ("run.json", "steps.jsonl", "events.jsonl", "issues.json"):
            assert (out / run_id / name).is_file()
        assert (out / run_id / "screenshots" / "1.png").is_file() and any((out / run_id / "raw").iterdir())
    assert (out / "comparisons" / "e-refund-1119.json").is_file() and result["comparisons"] == ["e-refund-1119"]
    text = (out / "README.md").read_text(encoding="utf-8")
    assert "[run_before_1](run_before_1/)" in text and "최근 구매한 상품의 환불 신청 화면 찾기" in text
    assert "EXTRA_STEPS" in text and "확인 필요" not in text   # LIVE·행사 서버·VALID·분석 완료라 경고 없음
    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    row = index["runs"][0]
    assert row["path"][0] == "home" and row["screenshotCount"] > 0 and row["warnings"] == []


def test_rehearsal_and_unfinished_runs_are_flagged(tmp_path):
    runs = tmp_path / "results" / "runs"
    shutil.copytree(SAMPLE, runs / "sample_before")   # MOCK·mock 서버·issues.json 없음
    out = tmp_path / "run-logs"
    row = ex.export(["sample_before"], str(tmp_path / "results"), str(out), env_path=str(tmp_path / "x"))["rows"][0]
    assert any("resultOrigin=MOCK" in w for w in row["warnings"])
    assert any("modelEndpointId=mock" in w for w in row["warnings"])
    assert any("analysisStatus=PENDING" in w for w in row["warnings"])
    assert "확인 필요" in (out / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("leak", [
    '"authorization": "Bearer abcdefghijklmnop"',
    '"api_key": "abcdef123456"',
    '"path": "/Users/chang/Desktop/UX-Mirror-BE/results"',
    '"path": "C:\\\\Users\\\\chang\\\\UX-Mirror-BE"',
])
def test_stops_before_writing_when_secret_or_local_path_found(results, tmp_path, leak):
    with open(results / "runs" / "run_after_1" / "events.jsonl", "a", encoding="utf-8") as f:
        f.write("{" + leak + "}\n")
    out = tmp_path / "run-logs"
    with pytest.raises(ex.ExportError) as e:
        ex.export(["run_before_1", "run_after_1"], str(results), str(out), env_path=str(tmp_path / "x"))
    assert "run_after_1/events.jsonl" in str(e.value)
    assert not out.exists()   # 하나라도 걸리면 아무것도 쓰지 않는다


def test_stops_when_env_value_appears(results, tmp_path):
    env = tmp_path / ".env"
    env.write_text("ZORO_BASE_URL=http://10.1.2.3:8000\nZORO_TEMPERATURE=1.52\n", encoding="utf-8")
    with open(results / "runs" / "run_before_1" / "raw" / "leak.json", "w", encoding="utf-8") as f:
        f.write('{"server": "http://10.1.2.3:8000"}')
    with pytest.raises(ex.ExportError) as e:
        ex.export(["run_before_1"], str(results), str(tmp_path / "out"), env_path=str(env))
    assert ".env의 ZORO_BASE_URL" in str(e.value)


def test_input_and_overwrite_rules(results, tmp_path):
    out = tmp_path / "run-logs"
    env = str(tmp_path / "x")
    with pytest.raises(ex.ExportError, match="찾을 수 없습니다"):
        ex.export(["run_nope"], str(results), str(out), env_path=env)
    with pytest.raises(ex.ExportError, match="형식"):
        ex.export(["../run_before_1"], str(results), str(out), env_path=env)
    with pytest.raises(ex.ExportError, match="두 번"):
        ex.export(["run_before_1", "run_before_1"], str(results), str(out), env_path=env)
    with pytest.raises(ex.ExportError, match="원본 결과 폴더 안"):
        ex.export(["run_before_1"], str(results), str(results / "runs" / "x"), env_path=env)
    ex.export(["run_before_1"], str(results), str(out), env_path=env)
    with pytest.raises(ex.ExportError, match="--force"):
        ex.export(["run_after_1"], str(results), str(out), env_path=env)
    ex.export(["run_after_1"], str(results), str(out), force=True, env_path=env)
    assert not (out / "run_before_1").exists() and (out / "run_after_1").is_dir()   # 다시 만들면 고른 것만 남음


def test_cli_returns_error_code_and_message(results, tmp_path, capsys):
    assert ex.main(["--results-dir", str(results), "--out", str(tmp_path / "o"), "run_nope"]) == 1
    assert "[중단]" in capsys.readouterr().err
