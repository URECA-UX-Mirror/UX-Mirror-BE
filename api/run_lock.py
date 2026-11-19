"""프로세스 사이 "한 번에 Run 하나" (#122).

API 서버 안의 Run, 서버에서 직접 돌리는 스크립트(자동 개선 검증 `run_verification`)의 Run, Optimization이
같은 결과 폴더의 파일 잠금(`.active_run.lock`) 하나를 쓴다.

- 잠금은 운영체제 파일 잠금(POSIX `flock`, Windows `msvcrt.locking`)이라, 잡은 프로세스가 죽으면 저절로 풀린다.
- 같은 프로세스 안에서는 겹쳐 잡을 수 있다(횟수를 센다). 검증 함수가 두 Run 사이에도 잠금을 놓지 않고,
  Optimization이 평가·검증 동안 잡은 채로 자기 검증 Run을 만들 수 있게.
- 누가 잡고 있는지는 `.active_run.json`(pid·종류·runId)에 적는다. 표시용이고, 잠겼는지는 항상 실제 잠금으로 판단한다.
- 배포 스크립트: `python -m api.run_lock status` → 잠겨 있으면 잡은 쪽을 출력하고 종료 코드 1.
"""
import json
import logging
import os
import socket
import sys
import threading

from api.timeutil import now_iso

log = logging.getLogger("uxm.api.run_lock")

LOCK_NAME = ".active_run.lock"
INFO_NAME = ".active_run.json"

if os.name == "nt":   # pragma: no cover - Windows
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class RunLock:
    """결과 폴더 하나에 한 번에 Run 하나. 결과 폴더마다 프로세스 안에서 같은 객체를 쓴다 (`for_results`)."""

    _instances: dict[str, "RunLock"] = {}
    _instances_lock = threading.Lock()

    @classmethod
    def for_results(cls, results_dir: str) -> "RunLock":
        key = os.path.realpath(results_dir)
        with cls._instances_lock:
            if key not in cls._instances:
                cls._instances[key] = cls(results_dir)
            return cls._instances[key]

    def __init__(self, results_dir: str):
        self.results_dir = results_dir
        self.path = os.path.join(results_dir, LOCK_NAME)
        self.info_path = os.path.join(results_dir, INFO_NAME)
        self._mutex = threading.RLock()
        self._fd: int | None = None
        self._stack: list[dict] = []

    # ---- 잡기·놓기
    def acquire(self, owner: dict) -> bool:
        """잠그면 True. 다른 프로세스가 잡고 있으면 False. 이 프로세스가 이미 잡고 있으면 겹쳐 잡는다."""
        with self._mutex:
            if self._fd is None:
                os.makedirs(self.results_dir, exist_ok=True)
                fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
                if not _try_lock(fd):
                    os.close(fd)
                    return False
                self._fd = fd
            info = {"pid": os.getpid(), "host": socket.gethostname(), "kind": owner.get("kind"),
                    "runId": owner.get("runId"), "since": now_iso()}
            outer = self._stack[0] if self._stack else {}
            for key in ("improvementId", "optimizationId"):   # 무슨 작업인지 (자동 개선·Optimization id)
                # 겹쳐 잡은 Run(자동 개선의 검증 Run 등)도 바깥 작업 id를 이어받는다 → health에서 진행 중인 작업을 계속 찾는다 (#131 리뷰)
                if owner.get(key) or outer.get(key):
                    info[key] = owner.get(key) or outer[key]
            self._stack.append(info)
            self._write_info()
            return True

    def release(self) -> None:
        with self._mutex:
            if self._fd is None or not self._stack:
                raise RuntimeError("잡지 않은 Run 잠금을 놓으려 했습니다.")
            self._stack.pop()
            if self._stack:
                self._write_info()
                return
            try:
                os.remove(self.info_path)
            except OSError:
                pass
            fd, self._fd = self._fd, None
            try:
                _unlock(fd)
            finally:
                os.close(fd)

    @property
    def held_here(self) -> bool:
        return self._fd is not None

    # ---- 누가 잡고 있나
    def holder(self) -> dict | None:
        """잠겨 있지 않으면 None. 잠겨 있으면 잡은 쪽 정보 (다른 프로세스면 `.active_run.json`, 없으면 pid None)."""
        with self._mutex:
            if self._fd is not None:
                return dict(self._stack[-1])
            if not os.path.exists(self.path):
                return None
            fd = os.open(self.path, os.O_RDWR)
            try:
                if _try_lock(fd):
                    _unlock(fd)
                    return None
            finally:
                os.close(fd)
            try:
                with open(self.info_path, encoding="utf-8") as f:
                    info = json.load(f)
                return info if isinstance(info, dict) else {"pid": None}
            except (OSError, ValueError):
                return {"pid": None, "kind": None, "runId": None}

    def _write_info(self) -> None:
        """표시용 정보 파일. 쓰기에 실패해도(Windows에서 다른 프로세스가 읽는 중 등) 잠금에는 영향이 없으므로 무시한다."""
        tmp = self.info_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._stack[-1], f, ensure_ascii=False)
            os.replace(tmp, self.info_path)
        except OSError:
            log.warning("Run 잠금 정보 파일을 쓰지 못했습니다 (잠금은 정상): %s", self.info_path)
            try:
                os.remove(tmp)
            except OSError:
                pass


def describe(holder: dict) -> str:
    parts = [holder.get("kind") or "실행", holder.get("runId") or "", f"pid {holder.get('pid')}" if holder.get("pid") else ""]
    return " ".join(p for p in parts if p)


def _main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="결과 폴더의 Run 잠금 상태 (배포 스크립트용)")
    ap.add_argument("command", choices=["status"])
    ap.add_argument("--results", help="결과 폴더 (기본: UXM_RESULTS_DIR 또는 저장소 results/)")
    a = ap.parse_args(argv)
    results = a.results or os.getenv("UXM_RESULTS_DIR")
    if not results:
        try:
            from dotenv import dotenv_values
            results = dotenv_values(".env").get("UXM_RESULTS_DIR")
        except ImportError:
            results = None
    if not results:
        from api.store import DEFAULT_RESULTS_DIR
        results = DEFAULT_RESULTS_DIR
    h = RunLock(results).holder()
    if h is None:
        return 0
    print(describe(h))
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
