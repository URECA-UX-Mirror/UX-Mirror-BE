"""설정은 환경변수(.env)로 관리한다. 실행 결과에 그대로 기록해 Before/After 조건 동일성을 증빙한다."""
import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))


GOAL_JUDGMENT_MODES = ("DOM_ONLY", "MODEL_AND_DOM")


def load() -> dict:
    t = os.getenv("ZORO_TEMPERATURE", "1.52")
    mode = os.getenv("GOAL_JUDGMENT_MODE", "DOM_ONLY")
    if mode not in GOAL_JUDGMENT_MODES:
        raise ValueError(f"GOAL_JUDGMENT_MODE must be one of {GOAL_JUDGMENT_MODES}, got {mode!r}")
    return {
        # 목표 도달 판정 모드. Before/After 비교 쌍에 반드시 동일하게 (기획안 10.3.10).
        # 2026-11-15 G0-A: A10 실패(v1 0.672, v2 0.700) → 테스트 시나리오 판정 규칙에 따라 DOM_ONLY.
        "goal_judgment_mode": mode,
        "backend": os.getenv("ZORO_BACKEND", "local"),            # local | http
        "base_url": os.getenv("ZORO_BASE_URL", "http://localhost:30001"),
        "timeout_s": float(os.getenv("ZORO_TIMEOUT_S", "30")),
        "checkpoint": os.getenv("ZORO_CHECKPOINT", r"C:\AI\Zoro-1"),
        "device": os.getenv("ZORO_DEVICE", "cpu"),                 # cpu | cuda-4bit
        # 모델 카드의 보정 temperature 1.52. 서버 temperature.json에 맡기지 않고 항상 명시해서 보낸다.
        "temperature": float(t) if t else None,
    }
