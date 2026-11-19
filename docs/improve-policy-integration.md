# 자동 개선 제안·채택 함수 연동 (#103)

`uxm.improve_policy`는 순수 함수다. 모델 호출·재요청·원본 저장, 최초 v1 기준 동결, B 생성·재평가 및 호출 순서는 루프가 담당한다. 기존 Optimizer 정책과 독립적이다.

- `build_prompt(diagnosis_text, button, siblings, persona, history)`는 자연어를 받아 프롬프트를 반환한다. `button`은 이름 문자열 또는 `label`을 가진 진단 결과 객체다. `siblings`, `history`는 각각 이름과 자연어 요약 문자열 목록이다. 내부 식별자·수치·성공 조건을 자연어에 섞지 않는다. 기존 누출 검사에 걸리면 `RuntimeError`다.
- `filter_labels(raw_text, context)`의 context는 `currentLabel`, `siblingLabels`(목록), `triedLabels`(목록), `maxLength`(양의 정수)를 갖는다. AI 응답의 한 줄을 하나의 후보로 보며 정규화한 이름, 채택 여부와 사유를 원래 순서로 반환한다. 최대 6개만 채택한다. 빈 줄, 길이, 중복, 기존 이름, 위험 단어, 일부 잘린 형태, 숫자·기호만 있는 형식, 목록 번호나 설명을 걸러 낸다. 한국어 단어가 잘렸는지 완벽하게 판정할 수는 없다. 탈락 사유는 `EMPTY_LABEL`, `LABEL_TOO_LONG`, `DUPLICATE_LABEL`, `EXISTING_LABEL`, `DANGEROUS_LABEL`, `TRUNCATED_LABEL`, `INVALID_FORMAT`, `CANDIDATE_LIMIT_EXCEEDED`다.
- `choose(baseline, candidates, rules)`의 `baseline.mainScore`는 현재 기준 주 점수, `baseline.guardScores`는 **최초 v1에서 동결한** 지킬 목표별 점수다. 현재 점수와 최초 점수를 호출자가 명시적으로 구분해 전달해야 한다. 후보는 `candidateId`, `label`, `source`(`HUMAN`/`AI`), `order`(0 이상의 정수), `mainScore`, `guardScores`, `evaluationStatus`(`COMPLETED`)를 포함한다. 지킬 목표 키는 기준과 정확히 같아야 한다. 모든 점수는 0~1의 유한한 숫자다.
- `rules`는 `minMainGain`, `maxGuardLoss`, `tieTolerance`를 확률 단위로 받는다. 생략 기본값은 각각 0.05, 0.02, 0.01이므로 실행 전 동결한 값을 명시적으로 넘긴다. 주 점수는 현재 대비, 지킬 목표는 최초 기준 대비 검사한다. 유효한 기준이 없거나 잘못된 규칙·중복 후보 ID는 `ValueError`다. 후보별 거절 사유는 `EVALUATION_FAILED`, `SCORE_MISSING`, `INVALID_CANDIDATE`, `GUARD_LOSS_EXCEEDED`, `MAIN_GAIN_INSUFFICIENT` 중 하나다.
- 반환값은 `{adopted: ID 또는 null, ranking: [ID], rejected: [{candidateId, reason}]}`다. `ranking`에는 적격 후보만 담는다. 남은 후보 중 최고 주 점수와 동률 범위 이내인 후보를 한 그룹으로 묶고, 그룹마다 지킬 점수 평균 내림차순 → 이름 길이 오름차순 → 사람 후보 → order → ID 순으로 정렬한다. 그룹을 제거하고 반복하므로 연쇄 비교하지 않는다.
- A는 예비 평가다. B 공식 점수의 재평가·채택 여부와 결과 보존은 루프에서 수행하며, B 점수를 같은 형식의 후보로 전달할 수 있다. 규칙 동결과 B 재판정 책임은 통합 시 확인한다.
