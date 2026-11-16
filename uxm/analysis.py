"""UX 분석·비교의 공개 인터페이스.

규칙과 비교 계산은 후속 작업에서 구현한다. 미구현 함수를 호출하면 빈 결과를
정상적인 분석 결과로 오인하지 않도록 명시적으로 실패한다.
"""


def detect_issues(run: dict, steps: list[dict], events: list[dict]) -> list[dict]:
    """종료된 Run과 모델·시스템 이벤트에서 Issue를 탐지한다 (구현 예정)."""
    raise NotImplementedError("UX Issue 탐지는 아직 구현되지 않았습니다.")


def compare(before: dict, after: dict) -> dict:
    """각 {run, steps, issues} 묶음을 비교한다 (구현 예정)."""
    raise NotImplementedError("Before/After 비교는 아직 구현되지 않았습니다.")


# API가 골격을 실제 분석기로 오인하지 않도록 각 함수의 준비 상태를 따로 표시한다.
# 후속 작업에서 해당 함수의 구현과 테스트가 끝나면 그 함수의 표시만 제거한다.
detect_issues.__uxm_pending__ = True
compare.__uxm_pending__ = True
