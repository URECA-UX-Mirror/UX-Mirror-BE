"""api/store.py 파일 쓰기·읽기 (#72).

Windows는 다른 스레드가 연 파일을 교체·열기할 때 PermissionError를 낸다. 실행기가 run.json을 쓰는 순간
조회 API가 같은 파일을 읽으면 생기므로, 잠깐 기다렸다 다시 한다. 여기서는 그 상황을 PermissionError로 흉내 낸다.
"""
import builtins
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from api import store  # noqa: E402


@pytest.fixture(autouse=True)
def fast_retry(monkeypatch):
    monkeypatch.setattr(store, "LOCK_RETRY_DELAY_S", 0)


def flaky(real, fails: int):
    """처음 `fails`번은 PermissionError, 그 뒤로는 진짜 함수."""
    calls = {"n": 0}

    def fn(*a, **kw):
        calls["n"] += 1
        if calls["n"] <= fails:
            raise PermissionError(13, "The process cannot access the file because it is being used")
        return real(*a, **kw)
    return fn, calls


def test_write_retries_while_file_is_locked(tmp_path, monkeypatch):
    path = tmp_path / "run.json"
    store.write_json_atomic(str(path), {"status": "READY"})
    replace, calls = flaky(os.replace, 3)
    monkeypatch.setattr(store.os, "replace", replace)
    store.write_json_atomic(str(path), {"status": "RUNNING"})
    assert calls["n"] == 4
    assert store.read_json(str(path)) == {"status": "RUNNING"}
    assert [p.name for p in tmp_path.iterdir()] == ["run.json"]   # 임시 파일이 남지 않는다


def test_read_retries_while_file_is_locked(tmp_path, monkeypatch):
    path = tmp_path / "run.json"
    store.write_json_atomic(str(path), {"status": "READY"})
    opener, calls = flaky(builtins.open, 2)
    monkeypatch.setattr(builtins, "open", opener)
    assert store.read_json(str(path)) == {"status": "READY"}
    assert calls["n"] == 3


def test_gives_up_after_retries_and_cleans_temp_file(tmp_path, monkeypatch):
    path = tmp_path / "run.json"
    replace, calls = flaky(os.replace, 10_000)
    monkeypatch.setattr(store.os, "replace", replace)
    with pytest.raises(PermissionError):
        store.write_json_atomic(str(path), {"status": "READY"})
    assert calls["n"] == store.LOCK_RETRIES
    assert list(tmp_path.iterdir()) == []


def test_other_errors_are_not_retried(tmp_path, monkeypatch):
    missing = tmp_path / "nope.json"
    opener, calls = flaky(builtins.open, 0)
    monkeypatch.setattr(builtins, "open", opener)
    with pytest.raises(FileNotFoundError):
        store.read_json(str(missing))
    assert calls["n"] == 1
