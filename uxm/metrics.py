"""기획안 8.2 지표. 확률 dict는 optionId -> probability."""
import math

PROB_SUM_TOLERANCE = 0.02  # G0 실측 후 확정


def validate_probabilities(probs: dict, option_ids: list[str]) -> None:
    if set(probs) != set(option_ids):
        raise ValueError(f"MODEL_RESPONSE_INVALID: 후보 불일치 {sorted(probs)} vs {sorted(option_ids)}")
    for k, p in probs.items():
        if not isinstance(p, (int, float)) or not (0.0 <= p <= 1.0) or math.isnan(p):
            raise ValueError(f"MODEL_RESPONSE_INVALID: {k}={p}")
    s = sum(probs.values())
    if abs(s - 1.0) > PROB_SUM_TOLERANCE:
        raise ValueError(f"MODEL_RESPONSE_INVALID: 확률 합계 {s:.4f}")


def normalized_entropy(ps: list[float]) -> float | None:
    n = len(ps)
    if n < 2:
        return None
    h = -sum(p * math.log(p) for p in ps if p > 0)
    return h / math.log(n)


def top2_margin(ps: list[float]) -> float | None:
    if len(ps) < 2:
        return None
    a, b = sorted(ps, reverse=True)[:2]
    return a - b


def probability_by_destination(probs: dict, destination_of: dict) -> dict:
    """같은 목적지로 가는 후보의 확률 합계. 후보는 묶지 않고 모델에 그대로 보낸 뒤, 분석할 때만 합친다.

    destination_of: optionId -> 목적지(href 또는 'GO_BACK'). 목적지가 목표 경로인 합계가 goalPathProbability의 재료.
    """
    out: dict = {}
    for k, p in probs.items():
        d = destination_of[k]
        out[d] = out.get(d, 0.0) + p
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def decision_metrics(probs: dict, back_option_id: str | None = None) -> dict:
    """전체 행동 지표와 GO_BACK 제외 클릭 조건부 지표를 분리 계산."""
    all_ps = list(probs.values())
    p_back = probs.get(back_option_id) if back_option_id else None
    click = [p for k, p in probs.items() if k != back_option_id]
    denom = sum(click)
    click_norm = [p / denom for p in click] if denom > 0 else []
    return {
        "normalizedEntropy": normalized_entropy(all_ps),
        "top2Margin": top2_margin(all_ps),
        "goBackProbability": p_back,
        "clickOnlyNormalizedEntropy": normalized_entropy(click_norm) if denom > 0 else None,
        "clickOnlyTop2Margin": top2_margin(click_norm) if denom > 0 else None,
    }
