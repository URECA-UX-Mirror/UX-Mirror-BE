"""scripts/check_site_instances.py --serve의 서버 기동·정리 (#85 리뷰).

브라우저 없이 서버 프로세스만 띄운다. 포트는 OS가 비어 있는 번호를 고른다.
"""
import os
import socket
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import check_site_instances as chk  # noqa: E402

SERVER_PY = os.path.join(ROOT, "shop", "moro-before", "server.py")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_port_in_use_fails_instead_of_checking_existing_server():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        procs = []
        failed = chk.start_servers([("C0", SERVER_PY, "127.0.0.1", port)], procs)
        chk.stop_servers(procs)
    assert failed == {"C0": f"PORT_IN_USE: {port}"}
    assert procs == []  # 새 서버를 띄우지 않았다


def test_server_that_exits_is_reported():
    procs = []
    failed = chk.start_servers([("C1", os.path.join(ROOT, "no_such_dir", "server.py"), "127.0.0.1", free_port())], procs)
    chk.stop_servers(procs)
    assert failed["C1"].startswith("SERVER_START_FAILED")


def test_started_servers_are_ready_and_stopped():
    procs = []
    port = free_port()
    try:
        failed = chk.start_servers([("C0", SERVER_PY, "127.0.0.1", port)], procs)
        assert failed == {}
        assert chk.port_in_use("127.0.0.1", port)
    finally:
        chk.stop_servers(procs)
    assert all(p.poll() is not None for p in procs)
    assert not chk.port_in_use("127.0.0.1", port)


def test_exception_midway_still_stops_already_started_servers():
    """두 번째 대상에서 예외가 나도 첫 번째로 띄운 서버가 `procs`에 남아 정리된다."""
    procs = []
    port = free_port()
    with pytest.raises(TypeError):
        try:
            chk.start_servers([("C0", SERVER_PY, "127.0.0.1", port), ("C1", SERVER_PY, "127.0.0.1", "not-a-port")], procs)
        finally:
            chk.stop_servers(procs)
    assert len(procs) == 1 and procs[0].poll() is not None
    assert not chk.port_in_use("127.0.0.1", port)
