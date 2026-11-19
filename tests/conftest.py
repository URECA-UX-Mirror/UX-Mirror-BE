"""여러 테스트 파일이 같이 쓰는 fixture."""
import os
import socket
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOP_PORTS = {"before": 18001, "after": 18002}   # 개발용 8001/8002와 겹치지 않게
# after(개선안) 테스트 사이트는 v1 + 이름 변경 사이트 하나(C2 moro-c2)를 쓴다. 고정 v2 사이트는 없다
SHOP_DIRS = {"before": "moro-before", "after": "moro-c2"}


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
        cwd = os.path.join(ROOT, "shop", SHOP_DIRS[variant])
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


@pytest.fixture(autouse=True)
def after_site_configured(monkeypatch):
    """after(개선안) 사이트는 서버 설정(SHOP_AFTER_URL)으로만 정해진다. 테스트는 테스트 쇼핑몰 주소로 설정해 둔다.
    설정이 없는 경우는 해당 테스트에서 None으로 바꿔 확인한다."""
    from api import runner
    monkeypatch.setitem(runner.SHOP_URLS, "after", f"http://127.0.0.1:{SHOP_PORTS['after']}")
    monkeypatch.setattr(runner, "SHOP_AFTER_LABEL", "주문·환불")   # 테스트 after 사이트(moro-c2)의 이름
