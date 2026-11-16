"""여러 테스트 파일이 같이 쓰는 fixture."""
import os
import socket
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOP_PORTS = {"before": 18001, "after": 18002}   # 개발용 8001/8002와 겹치지 않게


def _wait_port(port, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"shop server on {port} did not start")


@pytest.fixture(scope="session")
def shops():
    """Before/After 쇼핑몰을 직접 띄우고 끈다. {"before": base_url, "after": base_url}"""
    procs = []
    for variant, port in SHOP_PORTS.items():
        cwd = os.path.join(ROOT, "shop", f"moro-{variant}")
        procs.append(subprocess.Popen([sys.executable, "server.py", "--port", str(port)], cwd=cwd,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    try:
        for port in SHOP_PORTS.values():
            _wait_port(port)
        yield {v: f"http://127.0.0.1:{p}" for v, p in SHOP_PORTS.items()}
    finally:
        for p in procs:
            p.terminate()
            p.wait(timeout=5)
