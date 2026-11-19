"""사용자가 직접 쓰는 문장(고객이 하려는 일·고객 유형)의 실행 전 검사 (#130).

AI 고객(Zoro-1)은 이 문장을 그대로 읽고 행동하므로, 뜻이 없는 값(".", "ㅋㅋㅋㅋ", "123")은 실행 전에 거부한다.
진단(`POST /api/runs`)과 AI 개선(`POST /api/improvements`)이 같은 규칙을 쓴다. FE도 같은 규칙으로 미리 검사한다.
"""
import re
from collections import Counter

from uxm.leak import FORBIDDEN

MIN_CHARS = 4
MIN_LETTERS = 2
MAX_SAME_CHAR_RATIO = 0.6
_LETTER = re.compile(r"[A-Za-z가-힣ㄱ-ㆎ]")


def sentence_problem(text, field: str) -> str | None:
    """문제가 있으면 사용자에게 보여 줄 문장, 없으면 None."""
    if not isinstance(text, str) or not text.strip():
        return f"{field}을(를) 적어 주세요."
    compact = re.sub(r"\s+", "", text)
    if len(compact) < MIN_CHARS:
        return f"{field}을(를) 조금 더 구체적으로 적어 주세요 ({MIN_CHARS}자 이상)."
    if len(_LETTER.findall(compact)) < MIN_LETTERS:
        return f"{field}은(는) 글자로 적어 주세요 (기호·숫자만으로는 안 돼요)."
    if Counter(compact).most_common(1)[0][1] / len(compact) > MAX_SAME_CHAR_RATIO:
        return f"{field}을(를) 뜻이 있는 문장으로 적어 주세요."
    words = [w for w in FORBIDDEN if w in text]
    if words:
        return f"{field}에 쓸 수 없는 단어가 있어요: {', '.join(words)}"
    return None
