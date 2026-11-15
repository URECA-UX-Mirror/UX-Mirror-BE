"""MORO MARKET 테스트 쇼핑몰 서버 (표준 라이브러리만 사용).

    python server.py                 # Before: 8001, After: 8002 (폴더 이름으로 자동 결정)
    python server.py --port 9000
    python server.py --host 0.0.0.0  # 같은 와이파이의 다른 PC(노트북)에서 접속 허용

주소: /shop/home, /shop/mypage, /shop/support, /shop/orders, /shop/refund
Before/After는 서버(포트)로만 구분한다. 페이지 주소·제목·HTML에는 variant 정보가 없다.
이 파일은 uxmirror/shop/server.py가 원본이며 build_shop.py가 두 프로젝트에 복사한다.
"""
import argparse
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
PAGES_DIR = os.path.join(HERE, "pages")
PAGES = ("home", "mypage", "support", "orders", "refund")
DEFAULT_PORT = {"moro-before": 8001, "moro-after": 8002}.get(os.path.basename(HERE), 8000)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path.rstrip("/")
        if path in ("", "/shop"):
            self.send_response(302)
            self.send_header("Location", "/shop/home")
            self.end_headers()
            return
        key = path[len("/shop/"):] if path.startswith("/shop/") else None
        if key not in PAGES:
            self.send_error(404)
            return
        with open(os.path.join(PAGES_DIR, f"{key}.html"), "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass   # 브라우저가 먼저 닫힌 경우(테스트 중단 등). 서버는 정상이므로 무시

    def handle(self):
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"MORO MARKET ({os.path.basename(HERE)}) → http://{a.host}:{a.port}/shop/home", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
