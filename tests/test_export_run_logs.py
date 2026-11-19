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


def export(results, out, *ids, **kw):
    kw.setdefault("env_paths", [])
    return ex.export(list(ids), str(results), str(out), **kw)


def test_exports_selected_runs_with_index_and_leaves_originals(results, tmp_path):
    before = tree_hash(str(results))
    out = tmp_path / "run-logs"
    result = export(results, out, "run_before_1", "run_after_1")
    assert tree_hash(str(results)) == before   # 원본은 그대로
    for run_id in ("run_before_1", "run_after_1"):
        for name in ("run.json", "steps.jsonl", "events.jsonl", "issues.json"):
            assert (out / run_id / name).is_file()
        assert (out / run_id / "screenshots" / "1.png").is_file() and any((out / run_id / "raw").iterdir())
    assert (out / "comparisons" / "e-refund-1119.json").is_file() and result["comparisons"] == ["e-refund-1119"]
    text = (out / "README.md").read_text(encoding="utf-8")
    assert "[run_before_1](run_before_1/)" in text and "최근 구매한 상품의 환불 신청 화면 찾기" in text
    assert "EXTRA_STEPS" in text and "확인 필요" not in text
    row = json.loads((out / "index.json").read_text(encoding="utf-8"))["runs"][0]
    assert row["path"][0] == "home" and row["screenshotCount"] > 0 and row["warnings"] == []
    assert not [p for p in tmp_path.iterdir() if ".tmp-" in p.name or ".old-" in p.name]   # 임시 폴더가 남지 않음


def test_copies_whole_run_folder_except_temp_files(results, tmp_path):
    """screenshots/·raw/ 하위 폴더와 그 밖의 파일도 복사·검사한다. 임시·.env 파일만 뺀다 (리뷰 반영)."""
    run = results / "runs" / "run_before_1"
    (run / "raw" / "retry").mkdir()
    (run / "raw" / "retry" / "model_request_step_001_retry.json").write_text("{}", encoding="utf-8")
    (run / "notes.txt").write_text("ok", encoding="utf-8")
    (run / ".env").write_text("X=1", encoding="utf-8")
    (run / "run.json.tmp").write_text("x", encoding="utf-8")
    out = tmp_path / "run-logs"
    export(results, out, "run_before_1")
    assert (out / "run_before_1" / "raw" / "retry" / "model_request_step_001_retry.json").is_file()
    assert (out / "run_before_1" / "notes.txt").is_file()
    assert not (out / "run_before_1" / ".env").exists() and not (out / "run_before_1" / "run.json.tmp").exists()
    (run / "raw" / "retry" / "leak.json").write_text('{"path": "/root/models/zoro"}', encoding="utf-8")
    with pytest.raises(ex.ExportError, match="raw/retry/leak.json"):   # 하위 폴더도 검사
        export(results, tmp_path / "o2", "run_before_1")


def test_rehearsal_and_unfinished_runs_are_flagged(tmp_path):
    runs = tmp_path / "results" / "runs"
    shutil.copytree(SAMPLE, runs / "sample_before")   # MOCK·mock 서버·issues.json 없음
    out = tmp_path / "run-logs"
    row = export(tmp_path / "results", out, "sample_before")["rows"][0]
    assert any("resultOrigin=MOCK" in w for w in row["warnings"])
    assert any("modelEndpointId=mock" in w for w in row["warnings"])
    assert any("analysisStatus=PENDING" in w for w in row["warnings"])
    assert "확인 필요" in (out / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("leak, label", [
    ('"authorization": "Bearer abcdefghijklmnop"', "Bearer"),
    ('"api_key": "abcdef123456"', "키·비밀번호"),
    ('"password": "hunter2222"', "키·비밀번호"),
    ('"path": "/Users/chang/Desktop/UX-Mirror-BE/results"', "경로"),
    ('"path": "C:\\\\Users\\\\chang\\\\UX-Mirror-BE"', "Windows"),
    ('"path": "C:/Users/chang/UX-Mirror-BE"', "Windows"),
    ('"ckpt": "/root/.cache/zoro-1"', "경로"),
    ('"dir": "/workspace/UX-Mirror-BE/results"', "경로"),
    ('"dir": "/mnt/data/run"', "경로"),
    ('"endpoint": "http://10.1.2.3:30001/v1"', "서버 주소"),
    ('"endpoint": "https://gpu-box.internal:8443/decide"', "서버 주소"),
])
def test_stops_before_writing_when_secret_path_or_server_found(results, tmp_path, leak, label):
    with open(results / "runs" / "run_after_1" / "events.jsonl", "a", encoding="utf-8") as f:
        f.write("{" + leak + "}\n")
    out = tmp_path / "run-logs"
    with pytest.raises(ex.ExportError) as e:
        export(results, out, "run_before_1", "run_after_1")
    assert "run_after_1/events.jsonl" in str(e.value) and label in str(e.value)
    assert not out.exists()   # 하나라도 걸리면 아무것도 쓰지 않는다


@pytest.mark.parametrize("ok", [
    '"label": "비밀번호 찾기", "text": "password: 영문·숫자 8자 이상"',   # 화면 글자 (리뷰: 오탐)
    '"href": "https://ecudemo360127.cafe24.com/board/review/list.html?board_no=4"',   # 공개 사이트 주소
    '"href": "https://shop.example.com/data/list.html"',   # 주소 안의 /data/
    '"error": "누른 뒤 사이트 밖으로 나감: http://127.0.0.1:8001/shop/cart"',   # 내 컴퓨터 주소
    '"url": "http://localhost:8002/shop/home"',
])
def test_screen_text_and_public_urls_are_not_blocked(results, tmp_path, ok):
    with open(results / "runs" / "run_after_1" / "steps.jsonl", "a", encoding="utf-8") as f:
        f.write("{" + ok + "}\n")
    export(results, tmp_path / "run-logs", "run_after_1")


def test_allow_skips_confirmed_values(results, tmp_path):
    with open(results / "runs" / "run_after_1" / "steps.jsonl", "a", encoding="utf-8") as f:
        f.write('{"label": "/data/notice/list.html"}\n')
    with pytest.raises(ex.ExportError, match="--allow"):
        export(results, tmp_path / "a", "run_after_1")
    export(results, tmp_path / "b", "run_after_1", allow=["/data/notice/list.html"])


def test_env_from_gpu_server_is_checked(results, tmp_path):
    """--env로 GPU 서버 .env를 주면 그 서버 주소·키가 기록에 있는지 본다 (리뷰 반영)."""
    gpu_env = tmp_path / "gpu.env"
    gpu_env.write_text("ZORO_BASE_URL=gpu-l40s.trillion.example\nZORO_TEMPERATURE=1.52\n", encoding="utf-8")
    with open(results / "runs" / "run_before_1" / "raw" / "leak.json", "w", encoding="utf-8") as f:
        f.write('{"server": "gpu-l40s.trillion.example"}')
    export(results, tmp_path / "local", "run_before_1", env_paths=[str(tmp_path / "local.env")])   # 로컬 .env만이면 못 잡음
    with pytest.raises(ex.ExportError) as e:
        export(results, tmp_path / "gpu", "run_before_1", env_paths=[str(gpu_env)])
    assert ".env 값 gpu.env:ZORO_BASE_URL" in str(e.value)


def test_run_and_comparison_problems_are_reported_together(results, tmp_path):
    with open(results / "runs" / "run_before_1" / "events.jsonl", "a", encoding="utf-8") as f:
        f.write('{"x": "/root/a"}\n')
    (results / "comparisons" / "e-refund-1119.json").write_text('{"x": "/home/u/b"}', encoding="utf-8")
    with pytest.raises(ex.ExportError) as e:
        export(results, tmp_path / "out", "run_before_1")
    assert "run_before_1/events.jsonl" in str(e.value) and "comparisons/e-refund-1119.json" in str(e.value)


@pytest.mark.parametrize("out_of", ["results", "results_parent", "runs", "inside_runs"])
def test_out_dir_may_not_overlap_results_in_either_direction(results, out_of):
    """--force로도 결과 폴더를 지우지 않는다. 결과 폴더와 겹치는 출력은 어느 방향이든 거절 (리뷰 반영)."""
    out = {"results": results, "results_parent": results.parent, "runs": results / "runs",
           "inside_runs": results / "runs" / "x"}[out_of]
    before = tree_hash(str(results))
    with pytest.raises(ex.ExportError, match="겹칩니다"):
        export(results, out, "run_before_1", force=True)
    assert tree_hash(str(results)) == before


def test_out_dir_may_not_be_repo_or_its_parent(results):
    for out in (ROOT, os.path.dirname(ROOT)):
        with pytest.raises(ex.ExportError, match="겹칩니다"):
            export(results, out, "run_before_1", force=True)


def test_force_only_replaces_folder_made_by_this_script(results, tmp_path):
    other = tmp_path / "docs"
    other.mkdir()
    (other / "keep.md").write_text("x", encoding="utf-8")
    with pytest.raises(ex.ExportError, match="만든 폴더가 아니라"):
        export(results, other, "run_before_1", force=True)
    assert (other / "keep.md").is_file()
    out = tmp_path / "run-logs"
    export(results, out, "run_before_1")
    with pytest.raises(ex.ExportError, match="--force"):
        export(results, out, "run_after_1")
    export(results, out, "run_after_1", force=True)
    assert not (out / "run_before_1").exists() and (out / "run_after_1").is_dir()


def test_failure_while_copying_keeps_previous_export(results, tmp_path, monkeypatch):
    """복사 중 실패해도 이전 제출본이 그대로 남고 임시 폴더도 남지 않는다 (리뷰 반영)."""
    out = tmp_path / "run-logs"
    export(results, out, "run_before_1")
    snapshot = tree_hash(str(out))
    calls = {"n": 0}
    real = shutil.copy2

    def flaky(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("디스크 부족")
        return real(src, dst, *a, **kw)
    monkeypatch.setattr(ex.shutil, "copy2", flaky)
    with pytest.raises(OSError):
        export(results, out, "run_after_1", force=True)
    assert tree_hash(str(out)) == snapshot
    assert not [p for p in tmp_path.iterdir() if ".tmp-" in p.name or ".old-" in p.name]


def test_markdown_cells_are_escaped_and_missing_condition_shown_as_dash(results, tmp_path):
    path = results / "runs" / "run_before_1" / "run.json"
    run = json.loads(path.read_text(encoding="utf-8"))
    run["goal"] = "환불 | 반품\n신청"
    run.pop("verification", None)
    path.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "run-logs"
    export(results, out, "run_before_1")
    text = (out / "README.md").read_text(encoding="utf-8")
    assert "환불 \\| 반품 신청" in text and "- 성공 조건: -" in text and "None" not in text


def test_input_rules_follow_api_run_id_rule(results, tmp_path):
    out = tmp_path / "run-logs"
    with pytest.raises(ex.ExportError, match="찾을 수 없습니다"):
        export(results, out, "run_nope")
    with pytest.raises(ex.ExportError, match="형식"):
        export(results, out, "../run_before_1")
    with pytest.raises(ex.ExportError, match="형식"):
        export(results, out, "r" * 65)   # 실행 API와 같은 64자 제한
    with pytest.raises(ex.ExportError, match="두 번"):
        export(results, out, "run_before_1", "run_before_1")


def test_cli_returns_error_code_and_message(results, tmp_path, capsys):
    assert ex.main(["--results-dir", str(results), "--out", str(tmp_path / "o"), "--env", str(tmp_path / "x"),
                    "run_nope"]) == 1
    assert "[중단]" in capsys.readouterr().err
