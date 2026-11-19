"""자동 개선 진단 (#106). 모델·브라우저 없이 11/19 행사 GPU 실측 확률(추가 분석용, E 아님)로 확인한다."""
import json
import os

import pytest

from uxm import reach
from uxm.improve_diagnose import (NO_BLOCKAGE, NOT_COMPLETE, NOT_EDITABLE, correct_keys, describe, diagnose,
                                  region_of)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "config", "sitemaps", "sitemap-shop-v1.json"), encoding="utf-8") as f:
    SITE_MAP = json.load(f)

# 11/19 brev-l40s-bf16, MORO v1(8001), scripts/estimate_reach.py — S1 환불
S1_V1 = {
    "home": {"상단 메뉴>/shop/orders#1": 0.1954, "상단 메뉴>/shop/support#1": 0.3483, "상단 메뉴>/shop/mypage#1": 0.2634,
             "본문>/shop/orders#1": 0.0751, "본문>/shop/support#1": 0.1178},
    "mypage": {"경로 표시>/shop/home#1": 0.0096, "본문>/shop/support#1": 0.2533, "사이드 메뉴>/shop/orders#1": 0.0441,
               "사이드 메뉴>/shop/refund#1": 0.2771, "사이드 메뉴>/shop/support#1": 0.1235, "본문>/shop/orders#1": 0.0247,
               "본문>/shop/orders#2": 0.0042, "본문>/shop/orders#3": 0.017, "본문>/shop/orders#4": 0.0024,
               "본문>/shop/orders#5": 0.0076, "본문>/shop/orders#6": 0.0584, "본문>/shop/refund#1": 0.0128,
               "본문>/shop/refund#2": 0.062, "본문>/shop/refund#3": 0.0277, "본문>/shop/refund#4": 0.0755},
    "orders": {"경로 표시>/shop/home#1": 0.0216, "본문>/shop/refund#1": 0.9784},
    "support": {"본문>/shop/orders#1": 1.0},
}
# 같은 실측 — S3 쿠폰 (도착 mypage)
S3_V1 = {
    "home": {"상단 메뉴>/shop/orders#1": 0.1596, "상단 메뉴>/shop/support#1": 0.0071, "상단 메뉴>/shop/mypage#1": 0.7706,
             "본문>/shop/orders#1": 0.0612, "본문>/shop/support#1": 0.0016},
    "orders": {"경로 표시>/shop/home#1": 0.9011, "본문>/shop/refund#1": 0.0989},
    "refund": {"경로 표시>/shop/home#1": 0.1272, "경로 표시>/shop/orders#1": 0.3569, "본문>/shop/orders#1": 0.5158},
    "support": {"본문>/shop/orders#1": 1.0},
}
HOME_LABELS = {"home": {"상단 메뉴>/shop/orders#1": "이용정보", "상단 메뉴>/shop/support#1": "고객센터",
                        "상단 메뉴>/shop/mypage#1": "마이페이지"}}


def _est(probs, goal):
    return reach.estimate(SITE_MAP, probs, "home", goal, 8)


def test_s1_real_data_picks_home_and_the_info_menu():
    d = diagnose(SITE_MAP, S1_V1, "home", "refund", _est(S1_V1, "refund"), labels=HOME_LABELS)
    assert d["reason"] is None and d["bottleneckPage"] == "home"
    assert d["blockScore"] == pytest.approx(0.466, abs=0.001)          # 설계 §4.1 예시 값
    assert d["correctProbability"] == pytest.approx(0.534, abs=0.001)  # 이용정보 + 마이페이지 + 본문 주문
    assert d["rivalButton"]["label"] == "고객센터" and d["rivalButton"]["region"] == "상단 메뉴"
    # 같은 영역(상단 메뉴) 정답 버튼: 이용정보 0.195 < 마이페이지 0.263 → 이용정보. 본문 주문(0.075)은 다른 영역이라 아님
    assert d["targetButton"] == {"semanticKey": "상단 메뉴>/shop/orders#1", "label": "이용정보", "region": "상단 메뉴",
                                 "probability": pytest.approx(0.1954, abs=1e-4)}
    assert set(d["blockScores"]) == {"home", "mypage", "orders", "support"} and d["blockScores"]["support"] == 0


def test_correct_keys_follow_site_map_distance():
    assert correct_keys(SITE_MAP, "home", "refund") == {"상단 메뉴>/shop/orders#1", "본문>/shop/orders#1",
                                                         "상단 메뉴>/shop/mypage#1"}
    assert correct_keys(SITE_MAP, "home", "mypage") == {"상단 메뉴>/shop/mypage#1"}
    assert region_of("상단 메뉴>/shop/orders#1") == "상단 메뉴"


def test_other_button_stops_with_not_editable_but_keeps_info_for_report():
    # S3: home에서 정답은 상단 마이페이지 하나, 이용정보는 정답이 아님 → 1단계에서 바꿀 수 있는 정답 버튼이 없어 멈춘다
    d = diagnose(SITE_MAP, S3_V1, "home", "mypage", _est(S3_V1, "mypage"), labels=HOME_LABELS)
    assert d["reason"] == NOT_EDITABLE and d["bottleneckPage"] == "home"
    assert d["targetButton"]["label"] == "마이페이지" and d["rivalButton"]["label"] == "이용정보"


def test_incomplete_estimate_is_not_diagnosed():
    probs = {"home": S1_V1["home"]}                     # 다른 화면 확률이 없어 INCOMPLETE
    est = _est(probs, "refund")
    assert est["status"] == "INCOMPLETE"
    d = diagnose(SITE_MAP, probs, "home", "refund", est)
    assert d["reason"] == NOT_COMPLETE and d["targetButton"] is None


def test_no_blockage_when_every_choice_is_correct():
    probs = {**S1_V1, "home": {"상단 메뉴>/shop/orders#1": 1.0}, "mypage": {"사이드 메뉴>/shop/refund#1": 1.0},
             "orders": {"본문>/shop/refund#1": 1.0}}
    d = diagnose(SITE_MAP, probs, "home", "refund", _est(probs, "refund"))
    assert d["reason"] == NO_BLOCKAGE and d["targetButton"] is None


def test_rule_picks_body_button_but_stage1_edits_the_info_menu_instead():
    # 오답 1등이 본문 고객센터 → 규칙상 같은 영역 정답(본문 주문)이 고를 버튼이지만 1단계에선 못 바꿈
    # → 그 화면의 바꿀 수 있는 정답 버튼(이용정보)을 고치고, 원래 고른 버튼은 freeTargetButton으로 남김
    probs = {**S1_V1, "home": {"상단 메뉴>/shop/orders#1": 0.3, "상단 메뉴>/shop/support#1": 0.05,
                               "상단 메뉴>/shop/mypage#1": 0.3, "본문>/shop/orders#1": 0.05, "본문>/shop/support#1": 0.3}}
    d = diagnose(SITE_MAP, probs, "home", "refund", _est(probs, "refund"))
    assert d["rivalButton"]["semanticKey"] == "본문>/shop/support#1"
    assert d["freeTargetButton"]["semanticKey"] == "본문>/shop/orders#1"
    assert d["targetButton"]["semanticKey"] == "상단 메뉴>/shop/orders#1" and d["reason"] is None
    # 같은 상황에서 바꿀 수 있는 버튼이 없으면 멈춤
    d = diagnose(SITE_MAP, probs, "home", "refund", _est(probs, "refund"), editable_keys=())
    assert d["reason"] == NOT_EDITABLE and d["targetButton"]["semanticKey"] == "본문>/shop/orders#1"


def test_after_renaming_info_menu_next_round_still_targets_it():
    # 이용정보를 바꿔 채택한 뒤: 같은 영역 정답 중 가장 약한 건 마이페이지지만, 1단계는 계속 이용정보 자리를 고친다
    probs = {**S1_V1, "home": {"상단 메뉴>/shop/orders#1": 0.62, "상단 메뉴>/shop/support#1": 0.18,
                               "상단 메뉴>/shop/mypage#1": 0.12, "본문>/shop/orders#1": 0.03, "본문>/shop/support#1": 0.05}}
    d = diagnose(SITE_MAP, probs, "home", "refund", _est(probs, "refund"))
    assert d["freeTargetButton"]["semanticKey"] == "상단 메뉴>/shop/mypage#1"
    assert d["targetButton"]["semanticKey"] == "상단 메뉴>/shop/orders#1" and d["reason"] is None


def test_describe_is_natural_language_without_ids_or_scores():
    d = diagnose(SITE_MAP, S1_V1, "home", "refund", _est(S1_V1, "refund"), labels=HOME_LABELS)
    text = describe(d, "환불하려는")
    assert text == "환불하려는 사용자가 '이용정보' 메뉴를 잘 고르지 않고 '고객센터' 쪽으로 간다."
    assert ">" not in text and "0." not in text
    with pytest.raises(ValueError):
        describe(diagnose(SITE_MAP, S1_V1, "home", "refund", _est(S1_V1, "refund")), "환불하려는")  # 글자 없음
