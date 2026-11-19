import copy
import pytest

from uxm.improve_policy import build_prompt, choose, filter_labels

RULES = {"minMainGain": .05, "maxGuardLoss": .02, "tieTolerance": .01}


def test_prompt_uses_natural_text_and_keeps_internal_button_metadata_out():
    prompt = build_prompt('환불할 때 고객센터를 선택함', {'label': '이용정보', 'semanticKey': 'secret'},
                          ['고객센터'], '초보 사용자', ['이전 이름은 문의 목표를 떨어뜨림'])
    assert all(x in prompt for x in ('이용정보', '고객센터', '초보 사용자', '이전 이름', '한 줄에'))
    assert 'semanticKey' not in prompt and 'secret' not in prompt


def test_prompt_rejects_leak_in_natural_text():
    with pytest.raises(RuntimeError, match='T-LEAK'):
        build_prompt('before 결과', '이용정보', [], '초보', [])


def test_filter_labels_keeps_rejections_and_normalizes_duplicates():
    context = {'currentLabel': '이용정보', 'siblingLabels': ['고객센터'], 'triedLabels': ['주문관리'], 'maxLength': 6}
    result = filter_labels('주문·배송\n주문·환\n주문·배송\n고객센터\n주문관리\n결제\n너무너무긴메뉴이름\n123\n\n1. 안내', context)
    assert [x['rejectedReason'] for x in result] == [None, 'TRUNCATED_LABEL', 'DUPLICATE_LABEL', 'EXISTING_LABEL',
        'EXISTING_LABEL', 'DANGEROUS_LABEL', 'LABEL_TOO_LONG', 'INVALID_FORMAT', 'EMPTY_LABEL', None]
    assert result[0]['accepted'] and all(not r['accepted'] for r in result[1:-1]) and result[-1]['accepted']


def candidate(cid, main=.55, guard=.78, **extra):
    return {'candidateId': cid, 'label': cid, 'source': 'AI', 'order': 0, 'mainScore': main,
            'guardScores': {'S4': guard}, 'evaluationStatus': 'COMPLETED', **extra}


def test_boundaries_and_first_baseline_guard():
    base = {'mainScore': .5, 'guardScores': {'S4': .8}}
    result = choose(base, [candidate('pass'), candidate('low_main', main=.549999),
                           candidate('low_guard', guard=.779999)], RULES)
    assert result == {'adopted': 'pass', 'ranking': ['pass'], 'rejected': [
        {'candidateId': 'low_main', 'reason': 'MAIN_GAIN_INSUFFICIENT'},
        {'candidateId': 'low_guard', 'reason': 'GUARD_LOSS_EXCEEDED', 'guardGoals': ['S4']}]}


def test_nontransitive_tie_group_and_tie_breaks():
    base = {'mainScore': .3, 'guardScores': {'S4': .8}}
    a = candidate('A', main=.60, guard=.80, label='긴이름', order=2)
    b = candidate('B', main=.592, guard=.81, label='짧', order=1)
    c = candidate('C', main=.584, guard=.82, label='짧', order=0)
    assert choose(base, [a, b, c], RULES)['ranking'] == ['B', 'A', 'C']
    h = candidate('H', main=.6, guard=.8, label='동일', source='HUMAN', order=4)
    ai = candidate('I', main=.6, guard=.8, label='동일', source='AI', order=0)
    assert choose(base, [ai, h], RULES)['adopted'] == 'H'


def test_failure_missing_invalid_and_no_input_mutation():
    base = {'mainScore': .5, 'guardScores': {'S4': .8}}
    candidates = [candidate('bad', evaluationStatus='FAILED'), candidate('null', main=None),
                  candidate('missing', guardScores={}), candidate('nan', main=float('nan'))]
    before = copy.deepcopy((base, candidates))
    result = choose(base, candidates, RULES)
    assert result['adopted'] is None and result['ranking'] == []
    assert [r['reason'] for r in result['rejected']] == ['EVALUATION_FAILED', 'SCORE_MISSING', 'SCORE_MISSING', 'SCORE_MISSING']
    assert base == before[0] and candidates[:3] == before[1][:3]
    with pytest.raises(ValueError): choose({'mainScore': None, 'guardScores': {'S4': .8}}, [], RULES)
    with pytest.raises(ValueError): choose(base, [candidate('same'), candidate('same')], RULES)


def test_custom_rules_and_multiple_guards():
    base = {'mainScore': .5, 'guardScores': {'S2': .7, 'S4': .8}}
    c = candidate('B', main=.53, guardScores={'S2': .69, 'S4': .77})
    assert choose(base, [c], {'minMainGain': .03, 'maxGuardLoss': .03, 'tieTolerance': .01})['adopted'] == 'B'
    assert choose(base, [c], RULES)['adopted'] is None


def test_filter_keeps_excess_ai_proposals_with_reason():
    ctx = {'currentLabel': '이용정보', 'siblingLabels': [], 'triedLabels': [], 'maxLength': 6}
    rows = filter_labels('\n'.join(['가', '나', '다', '라', '마', '바', '사']), ctx)
    assert sum(row['accepted'] for row in rows) == 6
    assert rows[-1]['rejectedReason'] == 'CANDIDATE_LIMIT_EXCEEDED'


def test_numbered_bulleted_whitespace_and_purchase_labels():
    ctx = {'currentLabel': '이용정보', 'siblingLabels': ['내  주문'], 'triedLabels': [], 'maxLength': 8}
    rows = filter_labels('1. 주문·조회\n2) 주문  관리\n- 구매내역\n* 구매목록\n• 구매하기\n3. 내 주문\n4. 주문 관리', ctx)
    assert [x['label'] for x in rows] == ['주문·조회', '주문 관리', '구매내역', '구매목록', '구매하기', '내 주문', '주문 관리']
    assert [x['rejectedReason'] for x in rows] == [None, None, None, None, 'DANGEROUS_LABEL', 'EXISTING_LABEL', 'DUPLICATE_LABEL']


def test_rules_are_all_required_and_guard_reasons_identify_goals():
    base = {'mainScore': .5, 'guardScores': {'S2': .7, 'S4': .8}}
    c = candidate('C', main=.7, guardScores={'S2': .67, 'S4': .77})
    for missing in RULES:
        with pytest.raises(ValueError, match='rules must include'):
            choose(base, [c], {k: v for k, v in RULES.items() if k != missing})
    with pytest.raises(ValueError, match='rules must include'):
        choose(base, [c], {'mainMinGain': .05, 'guardMaxDrop': .02, 'tieRange': .01})
    assert choose(base, [c], RULES)['rejected'] == [{'candidateId': 'C', 'reason': 'GUARD_LOSS_EXCEEDED', 'guardGoals': ['S2', 'S4']}]


def test_comma_separated_candidates_use_the_same_filters_and_global_limit():
    ctx = {'currentLabel': '이용정보', 'siblingLabels': ['고객센터'], 'triedLabels': ['주문 관리'], 'maxLength': 6}
    rows = filter_labels('고객센터, 이용정보, 마이페이지\n환불， 배송, 주문, 주문 관리, 환불', ctx)
    assert [r['label'] for r in rows] == ['고객센터', '이용정보', '마이페이지', '환불', '배송', '주문', '주문 관리', '환불']
    assert [r['rejectedReason'] for r in rows] == ['EXISTING_LABEL', 'EXISTING_LABEL', None, None, None, None, 'EXISTING_LABEL', 'DUPLICATE_LABEL']
    limited = filter_labels('가,나,다\n라,마,바,사', ctx)
    assert [x['rejectedReason'] for x in limited] == [None] * 6 + ['CANDIDATE_LIMIT_EXCEEDED']


def test_prompt_explicitly_excludes_existing_and_previous_names():
    prompt = build_prompt('화면 찾기', {'label': '이용정보'}, ['고객센터', '마이페이지'], '초보 사용자',
                          ['이미 시험한 이름: 주문내역, 주문·환불'])
    assert '쓰지 말 이름: 이용정보, 고객센터, 마이페이지' in prompt
    assert '이전 시도에 나온 이름을 제외하고 새 이름만' in prompt
    assert '이미 시험한 이름: 주문내역, 주문·환불' in prompt
