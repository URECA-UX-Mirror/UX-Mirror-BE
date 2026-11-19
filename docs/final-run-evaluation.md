# 최종 Run 비교·증거 보존 절차 (#63)

행사에서 확정한 코드와 GPU 모델 서버를 사용해 Before(E-1)·After(E-2)를 실행하고 결과를 확인한다. 이 문서는 실행 절차와 기록 양식이다. 실제 실행 결과를 대신하지 않는다.

기본 저장·비교 계약은 v1.3.5를 기준으로 하며, SITE_MAP 항목은 이후 합의된 설계를 반영한다. 행사 전 확정 계약과 구현이 바뀌면 이 절차도 다시 확인한다.

## 1. 실행 전 고정

- [ ] 팀이 발표에 사용할 목표, 사용자 유형, 성공 조건, 시작 화면과 최대 탐색 횟수를 확정한다.
- [ ] 환불 preset(`ALLOWED_PATHS`) 또는 직접 입력 목표(`SITE_MAP`) 중 E 실행 방식을 확정한다. 지도 기준을 쓰기로 했으면 생성된 Run의 `referencePathConfig.basis`에 `SITE_MAP`이 실제로 저장됐는지 확인한다.
- [ ] `SHOP_BEFORE_URL`과 `SHOP_AFTER_URL`의 홈 화면을 각각 열어 올바른 쇼핑몰이 서빙되는지 확인한다. 현재 데모의 상단 주문 메뉴는 Before(`moro-before`)가 `이용정보`, After(`SHOP_AFTER_URL`, 기본 `moro-final` 8010)가 자동 개선 최종안 `주문 환불`이다. 11/19 E 실행 당시 After는 고정 사이트 `moro-after`(`주문·환불`, 지금은 삭제)였다. 환경 변수 주소와 실제 화면이 일치하지 않으면 실행 전에 바로잡는다.
- [ ] 행사 GPU 서버를 쓰도록 `ZORO_BACKEND=http`와 `ZORO_BASE_URL`이 실제 행사 서버를 가리키는지 확인한다. `ZORO_ENDPOINT_ID`는 기본 예시 `local`을 그대로 두지 말고 행사 서버를 식별할 값으로 설정한다. 식별자에는 비밀 주소나 인증 정보를 넣지 않는다.
- [ ] 사용할 Before/After 쇼핑몰, 백엔드·프론트 커밋, 모델 버전·endpoint 식별자, temperature와 `GOAL_JUDGMENT_MODE`를 기록한다. 서버가 고정하는 promptVersion과 viewport는 Run에 저장된 값을 기록한다. Before/After에는 같은 실행 조건을 적용한다.
- [ ] 행사 모델 서버와 쇼핑몰·프론트·백엔드를 실행하고 `GET /api/health`의 `status=READY`, `model.endpointId`가 설정한 행사 식별자와 같은지 확인한다. `READY`와 endpointId는 연결 설정 확인이며 실제 행사 모델 추론의 증거는 아니다. 실제 사용 서버와 Run의 모델 식별자도 대조한다.
- [ ] 실행 결과 폴더를 확인한다. 기본은 백엔드 저장소의 `results/`, `UXM_RESULTS_DIR`을 설정했다면 해당 폴더다. 결과를 팀이 접근할 수 있는 별도 보존 위치도 정한다.

프론트에서 실행하거나 `POST /api/runs`를 사용한다. API의 요청 형식은 README의 **실행 API** 절을 따른다. 서로 다른 결과를 섞지 않도록 이번 실행에만 사용할 `experimentKey`를 정한다.

## 2. Before/After 실행

1. Before를 실행하고 `runId`를 기록한다. `GET /api/runs/{runId}`로 종료를 확인한다.
2. 같은 `experimentKey`와 목표·사용자 유형·성공 조건, 같은 실행 설정으로 After를 실행하고 종료를 확인한다. 서버는 동시에 한 Run만 실행한다.
3. 두 Run의 `resultOrigin`, 모델 식별자, `status`, `terminationReason`, `validity`, `error`와 `summary`를 확인한다. API로 만든 로컬 리허설 Run도 `resultOrigin=LIVE`이므로 이 값만으로 행사 GPU 실행을 구분하지 않는다.
4. 목표 미도달, 기술적 오류, 무효 Run은 구분해 기록한다. `validity=VALID`만으로 기술적 오류가 없다고 판단하지 않는다.
5. 비교 대상은 API가 선택한 최종 Run이다. 재실행했다면 결과의 `beforeRunId`·`afterRunId`가 기록하려는 Run과 같은지 확인한다.

### 재실행·서버 재시작

- 같은 실험 키·variant의 Run이 모두 `INVALID`이면 동일 입력으로 재실행할 수 있고, 이전 Run에 `replacementRunId`가 연결된다.
- 같은 키·variant에 `VALID` Run이 있으면 `409 EXPERIMENT_RUN_EXISTS`다. `BLOCKED`·`MAX_STEPS`로 목표에 도달하지 못했어도 `VALID`일 수 있다. 다시 측정하려면 실패 기록을 보존하고 새 키로 Before와 After를 모두 실행한다.
- 같은 키의 첫 Run과 목표·사용자 유형의 ID/문장 또는 성공 조건이 다르면 `422`다. 첫 Run 입력을 바꾸려면 새 키로 두 버전을 실행한다. 단, 이전 Run에 성공 조건 필드가 없는 경우 서버의 과거 Run 호환 규칙을 따른다.
- 설정은 Before 실행 전에 확정하고 가능하면 두 Run 사이에 서버를 재시작하지 않는다. `.env` 변경 등에 재시작이 필요하면 두 Run의 비교 조건을 다시 확인하고, 조건이 바뀌었다면 새 키로 두 버전을 실행한다. 실행 중 서버가 꺼지면 다음 서버 시작 시 남은 `READY`·`RUNNING` Run은 `BROWSER_ERROR`로 무효 처리된다.

## 3. 분석과 비교 확인

- [ ] 각 Run의 `GET /api/runs/{runId}/issues`에서 `analysisStatus=COMPLETED`를 확인한다. `PENDING`은 Issue 없음이 아니며, `FAILED`는 분석 실패로 기록한다. `INVALID` Run은 분석하지 않고 `COMPLETED`와 빈 목록을 저장하므로 이것도 Issue가 없다는 증거가 아니다.
- [ ] `GET /api/runs/{runId}/steps`와 로컬 `events.jsonl`, 스크린샷을 대조해 발표에 쓸 Issue의 `stepId`, `relatedStepIds`, `evidence`를 확인한다. 요약의 관찰값과 다른 경우 원인을 기록한다.
- [ ] `GET /api/comparisons/{experimentKey}`를 호출한다. 이 호출은 현재 선택된 Run을 다시 비교하고 `results/comparisons/{experimentKey}.json`을 저장한다.
- [ ] `conditionsMatched`, 각 `conditionChecks`, `comparisonStatus`, 화면별 `screenComparable`과 `reason`을 확인한다. `PARTIAL`도 결과지만 비교 불가 화면을 성공적인 비교로 표현하지 않는다.
- [ ] 적용된 경로 기준과 Run·화면 지표를 확인한다. `null`은 계산 불가 또는 해당 없음이며 `0`으로 바꾸지 않는다. SITE_MAP 우회 이동과 허용 경로 이탈은 구분한다.
- [ ] 프론트 결과·비교 화면의 Run ID, 수치, Issue 근거, 스크린샷, 비교 불가 사유가 API 및 저장된 기록과 일치하는지 확인한다. 발표에 쓸 화면의 증거를 따로 남긴다.

비교 API가 실패하면 진행 중인 Run 또는 분석 대기는 `409 COMPARISON_NOT_READY`, 최종 Run이 없거나 VALID가 아니면 `409 COMPARISON_RUN_INVALID`, 분석 실패는 `500 ANALYSIS_ERROR`다. 실험 키에 Run이 하나도 없으면 `404 RUN_NOT_FOUND`다.

## 4. 증거 보존

Run이 끝나고 분석이 완료된 후, **실제 선택된** Before/After Run 폴더와 비교 결과를 정한 보존 위치에 복사한다. 원본을 이동하거나 덮어쓰지 않는다.

```text
<results>/runs/<beforeRunId>/
  run.json, steps.jsonl, events.jsonl, issues.json, screenshots/, raw/
<results>/runs/<afterRunId>/
  run.json, steps.jsonl, events.jsonl, issues.json, screenshots/, raw/
<results>/comparisons/<experimentKey>.json
```

각 Run의 `raw/` 원본 모델 요청·응답도 보존한다. 실패 등으로 파일이 없으면 없었던 이유를 기록한다. 원본 모델 요청·응답과 스크린샷의 공유 범위는 팀이 정한다. `.env`나 인증 정보는 증거 묶음에 넣지 않는다. 결과 파일은 기본적으로 Git 추적 대상이 아니므로 복사한 위치와 접근 방법을 별도로 기록한다.

- [ ] 복사한 파일이 열리고 스크린샷이 실제로 존재하는지 확인한다.
- [ ] 분석·비교 결과의 Run ID와 복사한 폴더가 일치하는지 확인한다.
- [ ] 실행 실패·재실행이 있었다면 원본 Run ID, 실패 사유, 최종 선택 Run ID를 기록한다.
- [ ] 발표 수치마다 Run ID·필드 또는 Issue ID·Step ID를 연결한다.

## 5. 최종 기록 양식

아래를 팀이 접근할 수 있는 곳에 복사해 채운다. 확인되지 않은 값은 비워 두거나 확인 불가 사유를 기록한다.

```markdown
## 최종 Run 기록

- 실행 일시·환경:
- 백엔드 커밋 / 프론트 커밋:
- 모델 버전 / endpoint 식별자 / promptVersion / temperature / goalJudgmentMode:
- 사이트·레이아웃 버전:
- 목표 / 사용자 유형 / 성공 조건 / 시작 화면 / 최대 탐색 횟수:
- 경로 기준 및 버전·해시(해당 시):
- experimentKey:
- Before Run ID / status / terminationReason / validity / resultOrigin:
- After Run ID / status / terminationReason / validity / resultOrigin:
- Before / After 분석 상태:
- 비교 상태 / 조건 일치 여부 / 비교 불가 사유:
- 발표 수치와 근거(Run ID, 필드, Issue ID, Step ID):
- API와 프론트 확인 결과:
- 실패·재실행 및 최종 Run 선택 기록:
- 증거 보존 위치 / 접근 담당:
- 제한사항·확인하지 못한 항목:
```

최종 완료는 행사에서 선택한 LIVE Run과 결과·근거를 확인한 뒤 기록한다. 리허설과 최종 실행은 별도의 `experimentKey`와 확인된 endpoint 식별자로 구분하고 실제 서버·실행 환경을 함께 기록한다. `resultOrigin`이나 임의로 설정한 endpointId 하나만으로 실제 GPU 사용을 입증하지 않는다. MOCK 결과도 최종 수치와 구분한다. 단일 에이전트 실행에서 관찰된 결과를 실제 사용자의 개선 효과로 단정하지 않는다.
