import copy
import pytest

from uxm.improve_policy import build_prompt, choose, filter_labels


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
        'EXISTING_LABEL', 'DANGEROUS_LABEL', 'LABEL_TOO_LONG', 'INVALID_FORMAT', 'EMPTY_LABEL', 'INVALID_FORMAT']
    assert result[0]['accepted'] and all(not r['accepted'] for r in result[1:])


def candidate(cid, main=.55, guard=.78, **extra):
    return {'candidateId': cid, 'label': cid, 'source': 'AI', 'order': 0, 'mainScore': main,
            'guardScores': {'S4': guard}, 'evaluationStatus': 'COMPLETED', **extra}


def test_boundaries_and_first_baseline_guard():
    base = {'mainScore': .5, 'guardScores': {'S4': .8}}
    result = choose(base, [candidate('pass'), candidate('low_main', main=.549999),
                           candidate('low_guard', guard=.779999)], {})
    assert result == {'adopted': 'pass', 'ranking': ['pass'], 'rejected': [
        {'candidateId': 'low_main', 'reason': 'MAIN_GAIN_INSUFFICIENT'},
        {'candidateId': 'low_guard', 'reason': 'GUARD_LOSS_EXCEEDED'}]}


def test_nontransitive_tie_group_and_tie_breaks():
    base = {'mainScore': .3, 'guardScores': {'S4': .8}}
    a = candidate('A', main=.60, guard=.80, label='긴이름', order=2)
    b = candidate('B', main=.592, guard=.81, label='짧', order=1)
    c = candidate('C', main=.584, guard=.82, label='짧', order=0)
    assert choose(base, [a, b, c], {})['ranking'] == ['B', 'A', 'C']
    h = candidate('H', main=.6, guard=.8, label='동일', source='HUMAN', order=4)
    ai = candidate('I', main=.6, guard=.8, label='동일', source='AI', order=0)
    assert choose(base, [ai, h], {})['adopted'] == 'H'


def test_failure_missing_invalid_and_no_input_mutation():
    base = {'mainScore': .5, 'guardScores': {'S4': .8}}
    candidates = [candidate('bad', evaluationStatus='FAILED'), candidate('null', main=None),
                  candidate('missing', guardScores={}), candidate('nan', main=float('nan'))]
    before = copy.deepcopy((base, candidates))
    result = choose(base, candidates, {})
    assert result['adopted'] is None and result['ranking'] == []
    assert [r['reason'] for r in result['rejected']] == ['EVALUATION_FAILED', 'SCORE_MISSING', 'SCORE_MISSING', 'SCORE_MISSING']
    assert base == before[0] and candidates[:3] == before[1][:3]
    with pytest.raises(ValueError): choose({'mainScore': None, 'guardScores': {'S4': .8}}, [], {})
    with pytest.raises(ValueError): choose(base, [candidate('same'), candidate('same')], {})


def test_custom_rules_and_multiple_guards():
    base = {'mainScore': .5, 'guardScores': {'S2': .7, 'S4': .8}}
    c = candidate('B', main=.53, guardScores={'S2': .69, 'S4': .77})
    assert choose(base, [c], {'minMainGain': .03, 'maxGuardLoss': .03})['adopted'] == 'B'
    assert choose(base, [c], {})['adopted'] is None


def test_filter_keeps_excess_ai_proposals_with_reason():
    ctx = {'currentLabel': '이용정보', 'siblingLabels': [], 'triedLabels': [], 'maxLength': 6}
    rows = filter_labels('\n'.join(['가', '나', '다', '라', '마', '바', '사']), ctx)
    assert sum(row['accepted'] for row in rows) == 6
    assert rows[-1]['rejectedReason'] == 'CANDIDATE_LIMIT_EXCEEDED'
