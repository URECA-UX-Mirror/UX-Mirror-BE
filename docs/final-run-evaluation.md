# 최종 Run 비교·증거 보존 절차 (#63)

행사에서 확정한 코드와 GPU 모델 서버를 사용해 Before(E-1)·After(E-2)를 실행하고 결과를 확인한다. 이 문서는 실행 절차와 기록 양식이다. 실제 실행 결과를 대신하지 않는다.

## 1. 실행 전 고정

- [ ] 팀이 발표에 사용할 목표, 사용자 유형, 성공 조건, 시작 화면과 최대 탐색 횟수를 확정한다.
- [ ] 환불 preset(`ALLOWED_PATHS`) 또는 직접 입력 목표(`SITE_MAP`) 중 E 실행 방식을 확정한다. 지도 기준을 쓰기로 했으면 생성된 Run에 `basis: SITE_MAP`이 실제로 저장됐는지 확인한다.
- [ ] 사용할 Before/After 쇼핑몰, 백엔드·프론트 커밋, 모델 버전·endpoint 식별자, promptVersion, temperature, viewport를 기록한다. Before/After에는 같은 실행 조건을 적용한다.
- [ ] 행사 모델 서버와 쇼핑몰·프론트·백엔드를 실행하고 `GET /api/health`가 `READY`인지 확인한다. 모델 식별자와 실제 서버 설정이 맞는지도 확인한다. `READY`만으로 실제 모델 추론 성공을 확정하지 않는다.
- [ ] 실행 결과 폴더를 확인한다. 기본은 백엔드 저장소의 `results/`, `UXM_RESULTS_DIR`을 설정했다면 해당 폴더다. 결과를 팀이 접근할 수 있는 별도 보존 위치도 정한다.

프론트에서 실행하거나 `POST /api/runs`를 사용한다. API의 요청 형식은 README의 **실행 API** 절을 따른다. 서로 다른 결과를 섞지 않도록 이번 실행에만 사용할 `experimentKey`를 정한다.

## 2. Before/After 실행

1. Before를 실행하고 `runId`를 기록한다. `GET /api/runs/{runId}`로 종료를 확인한다.
2. 같은 `experimentKey`와 목표·사용자 유형·성공 조건, 같은 실행 설정으로 After를 실행하고 종료를 확인한다. 서버는 동시에 한 Run만 실행한다.
3. 두 Run의 `resultOrigin`, 모델 식별자, `status`, `terminationReason`, `validity`, `error`와 `summary`를 확인한다. 행사 LIVE 결과에는 실제 행사 모델이 사용됐는지도 확인한다.
4. 목표 미도달, 기술적 오류, 무효 Run은 구분해 기록한다. `validity=VALID`만으로 기술적 오류가 없다고 판단하지 않는다. 같은 키·버전의 재실행 가능 여부는 서버 규칙을 따른다. 불가능하면 실패 기록을 보존하고 새로운 실험 키로 두 버전을 다시 실행한다.
5. 비교 대상은 API가 선택한 최종 Run이다. 재실행했다면 결과의 `beforeRunId`·`afterRunId`가 기록하려는 Run과 같은지 확인한다.

## 3. 분석과 비교 확인

- [ ] 각 Run의 `GET /api/runs/{runId}/issues`에서 `analysisStatus=COMPLETED`를 확인한다. `PENDING`은 Issue 없음이 아니며, `FAILED`는 분석 실패로 기록한다.
- [ ] `GET /api/runs/{runId}/steps`와 로컬 `events.jsonl`, 스크린샷을 대조해 발표에 쓸 Issue의 `stepId`, `relatedStepIds`, `evidence`를 확인한다. 요약의 관찰값과 다른 경우 원인을 기록한다.
- [ ] `GET /api/comparisons/{experimentKey}`를 호출한다. 이 호출은 현재 선택된 Run을 다시 비교하고 `results/comparisons/{experimentKey}.json`을 저장한다.
- [ ] `conditionsMatched`, 각 `conditionChecks`, `comparisonStatus`, 화면별 `screenComparable`과 `reason`을 확인한다. `PARTIAL`도 결과지만 비교 불가 화면을 성공적인 비교로 표현하지 않는다.
- [ ] 적용된 경로 기준과 Run·화면 지표를 확인한다. `null`은 계산 불가 또는 해당 없음이며 `0`으로 바꾸지 않는다. SITE_MAP 우회 이동과 허용 경로 이탈은 구분한다.
- [ ] 프론트 결과·비교 화면의 Run ID, 수치, Issue 근거, 스크린샷, 비교 불가 사유가 API 및 저장된 기록과 일치하는지 확인한다. 발표에 쓸 화면의 증거를 따로 남긴다.

## 4. 증거 보존

Run이 끝나고 분석이 완료된 후, **실제 선택된** Before/After Run 폴더와 비교 결과를 정한 보존 위치에 복사한다. 원본을 이동하거나 덮어쓰지 않는다.

```text
<results>/runs/<beforeRunId>/
  run.json, steps.jsonl, events.jsonl, issues.json, screenshots/
<results>/runs/<afterRunId>/
  run.json, steps.jsonl, events.jsonl, issues.json, screenshots/
<results>/comparisons/<experimentKey>.json
```

필요하면 각 Run의 `raw/`도 보존한다. 원본 모델 요청·응답과 스크린샷의 공유 범위는 팀이 정한다. `.env`나 인증 정보는 증거 묶음에 넣지 않는다. 결과 파일은 기본적으로 Git 추적 대상이 아니므로 복사한 위치와 접근 방법을 별도로 기록한다.

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
- 모델 버전 / endpoint 식별자 / promptVersion / temperature:
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

최종 완료는 행사에서 선택한 LIVE Run과 결과·근거를 확인한 뒤 기록한다. 리허설·MOCK 결과는 최종 수치와 구분한다. 단일 에이전트 실행에서 관찰된 결과를 실제 사용자의 개선 효과로 단정하지 않는다.
