# UX MIRROR — Zoro-1 테스트 프로젝트

테스트 시나리오 문서의 **A 묶음(G0-A, 브라우저 없이 단일 판단)**을 실제 Zoro-1로 돌리는 프로젝트.
이후 B~E(쇼핑몰·Playwright·에이전트)도 여기에 붙인다.

## 구조

```
uxm/
  prompts.py     # 튜닝 대상(지시문, 후보 설명 형식, 페르소나, 이력 표현) + PROMPT_VERSION
  scenarios.py   # A1~A10 정의. semanticKey는 백엔드 전용
  backends.py    # http(/v1/decide 서버) | local(transformers로 같은 헤드 직접 실행)
  metrics.py     # 정규화 엔트로피, Top-2, GO_BACK 조건부 재정규화 (기획안 8.2)
  candidates.py  # 화면에서 클릭 후보 추출(semanticKey·bbox), 확률 배지 그리기
  browser.py     # 브라우저 조작: open/close, click, go_back, reload, capture, page_key, page_signature (예외 대신 ActionResult)
  leak.py        # T-LEAK: 모델 입력에 variant/testMode/정답 마커 차단
  sitemap.py     # 사이트 지도(SITE_MAP): 간선·해시·최단 거리·지도 검증·실제 Run 대조 (#46)
  config.py      # .env 로딩
scripts/run_a.py # A 실행기 → results/json/a/, results/run_log.md
scripts/run_b5.py        # B3+B5 실제 화면 확률 → results/json/b5/, results/screenshots/b5/
scripts/show_candidates.py  # 후보·도착 판정 확인 (모델 없음) → results/json/candidates/, results/screenshots/candidates/
scripts/check_site_instances.py  # Optimizer 후보 사이트 C0~C4 점검 (모델 없음, --serve로 서버 5개 기동): 유효성·겹침·잘림·라벨 외 화면 변화 → results/site_checks/
results/README.md        # 남겨둔 결과별 의미
scripts/build_shop.py  # shop/stitch_moro_market → shop/moro-before(8001), shop/moro-after(8002) 생성 + 라벨 1개 차이 검사
scripts/build_sitemap.py  # 쇼핑몰 화면 5개(Before·After)에서 후보를 뽑아 사이트 지도 생성·검증 → config/sitemaps/ (쇼핑몰 서버 필요)
config/sites/moro-market.json        # 사이트 지도용 사이트 설정 (화면 목록·시작 화면·경로 범위·허용 도메인·주소)
config/sitemaps/sitemap-shop-v1.json  # 고정한 사이트 지도 (siteMapVersion·siteMapHash)
shop/
  server.py              # 쇼핑몰 서버 원본 (build_shop.py가 두 쇼핑몰에 복사)
  stitch_moro_market/    # Stitch 시안 원본 (쇼핑몰 수정은 여기 또는 build_shop.py에서)
  moro-before/, moro-after/  # 생성 결과 (직접 수정 금지). 서버: cd shop\moro-before 후 py -3.11 server.py
                             # 테스트 모드: 첫 주소에 ?testMode=nochange(마이페이지 쿠폰함 눌러도 안 바뀜) | deadend(고객센터 배송 현황 확인 숨김)
  moro-c1/, moro-c3/, moro-c4/  # Optimizer 라벨 후보 변형 (build_shop.py --label, 직접 수정 금지). 서버: cd shop\moro-c1 후 py -3.11 server.py --port 8003 (C3 8004, C4 8005)
api/             # 실행 API (FastAPI). 다른 모듈과의 연결은 api/integrations.py 한 곳
config/presets.json  # 목표·페르소나 preset (성공 조건·허용 경로는 Backend 전용)
tests/           # 모델 없이 도는 테스트
vendor/trida-stack/  # 공식 코드 일부(Apache-2.0): 요청 인코딩·포인터 헤드·서버
```

## 설치 (완료됨)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cu126
.\.venv\Scripts\pip install numpy pillow -r requirements.txt
copy .env.example .env
```

## 실행

```powershell
.\.venv\Scripts\python -m pytest -q tests          # 오프라인 테스트
.\.venv\Scripts\python scripts\run_a.py --dry-run   # 요청 payload 확인
.\.venv\Scripts\python scripts\run_a.py             # A1~A10
.\.venv\Scripts\python scripts\run_a.py A2 A3       # 일부
```

## 실행 API

행사 최종 Before/After 실행과 결과 보존 절차는 [최종 Run 비교·증거 보존 절차](docs/final-run-evaluation.md)를 참고한다.

```powershell
.\.venv\Scripts\python -m uvicorn api.main:app --port 8000
```

| Method | 주소 | 내용 |
|---|---|---|
| GET | `/api/presets` | 목표·페르소나 선택지 (`?includeTest=true`면 C3용 테스트 전용 목표 포함) |
| POST | `/api/runs` | Run 생성 `{experimentKey, variant, goalId|goal+verification, personaId|persona, maxSteps?, startPath?}` → `201` READY, 실행은 별도 스레드 |
| GET | `/api/runs` | Run 목록 `?experimentKey=&variant=` (계약 밖 추가) |
| GET | `/api/runs/{runId}` | 상태·요약 |
| GET | `/api/runs/{runId}/steps` | 저장이 끝난 Step |
| GET | `/api/runs/{runId}/issues` | UX 검토 후보 (`PENDING`/`COMPLETED`/`FAILED`) |
| GET | `/api/runs/{runId}/screenshots/{stepId}?kind=original\|overlay` | 스크린샷 |
| GET | `/api/runs/{runId}/screenshots/goal` | 도착 화면 스크린샷 (Step이 없는 경로 마지막 노드, OI-3). 도착하지 않았거나 사진이 없으면 `404` |
| GET | `/api/comparisons/{experimentKey}` | Before/After 비교 (VALID 최종 Run, 두 Run의 Issue 분석이 모두 `COMPLETED`일 때만) |
| GET | `/api/health` | 모델·브라우저·에이전트 준비 상태 |

- 동시에 1개만 실행 (`409 RUN_ALREADY_ACTIVE`). 같은 `experimentKey`+`variant`에 VALID·진행 중 Run이 있으면 `409 EXPERIMENT_RUN_EXISTS`. 무효(INVALID) Run만 다시 실행할 수 있고 원본의 `replacementRunId`에 연결된다.
- 에이전트 루프(`uxm/agent.py`)나 브라우저(`uxm/browser.py`)가 없으면 `POST /api/runs`는 `503`.
- Issue 분석(`detect_issues(run, steps, events)`)은 Run이 끝날 때 한 번만 돌고 `issues.json`을 쓴다. 조회 API는 파일을 쓰지 않는다. 분석 모듈이 없을 때 끝난 Run은 `PENDING`으로 남고, 비교는 `409 COMPARISON_NOT_READY`(분석 `FAILED`면 `500 ANALYSIS_ERROR`).
- 나중에 분석하려면 `python scripts/analyze_runs.py --all` (`issues.json`만 쓰고 `run.json`은 건드리지 않음). 이렇게 분석한 Run은 저장된 `summary.detectedIssueTypes`가 비어 있을 수 있지만, `GET /api/runs/{runId}`와 비교 API는 완료된 `issues.json`에서 다시 계산한 값을 쓴다.
- 목표는 `goalId`(preset) 또는 `goal` 문장 + 성공 조건 `verification{path, text, exact?, scope?}` 중 하나, 사용자 유형은 `personaId` 또는 `persona` 문장 중 하나. 직접 입력 문장은 빈 값·200자 초과·누출 금지 문자열(`uxm/leak.py` `FORBIDDEN`: `before`, `after`, `variant` 등)이면 실행 전에 `422`. 성공 조건은 preset 로드와 같은 검사(`_check_verification`)를 거친다.
- Run에는 목표·사용자 유형·`verification`을 저장하고 루프·분석은 Run 값만 읽는다. `verification`은 공백 정리 + `exact=false`·`scope=page` 기본값을 채워 저장한다(preset 실행도 복사). 직접 입력한 목표는 허용 경로가 없어 사이트 지도 기준(`referencePathConfig.basis = SITE_MAP`, `distances` 복사)으로 저장한다 (#51). 지도를 쓸 수 없으면 `referencePathConfig: null`로 실행하고 경로 지표는 `null`, 시작 화면에서 도착 화면으로 가는 길이 없으면 `422`.
- 같은 `experimentKey`의 두 번째 Run부터는 첫 Run과 `goalId`·`goal`·`personaId`·`persona`·`verification`이 같아야 한다 (다르면 `422`).
- 실행 전 검사: 직접 입력한 성공 조건은 Run을 만들기 전에 Before·After 쇼핑몰에서 도착 화면을 실제로 열어 확인한다 (`api/preflight.py`, 판정은 루프와 같은 `verify_goal`). 쇼핑몰에 없는 화면·다른 화면으로 이동·글자 없음은 `422`(어느 쪽인지 메시지에 표시), 화면을 열지 못하거나 10초 안에 응답이 없으면 재시도 가능한 `503 BROWSER_ERROR`. 다른 Run이 실행 중이면 브라우저를 띄우지 않고 바로 `409`. preset 목표는 로드 때 검사하므로 건너뛴다.
- 비교의 `GOAL_MATCHED`는 저장된 목표 문장과 완전한 `verification`을, `PERSONA_MATCHED`는 사용자 유형 문장을 비교한다. 과거 Run에 `verification`이 없으면 현재 preset을 추측하지 않고 `GOAL_MATCHED=false`와 확인 불가 사유를 반환한다.
- `REFERENCE_PATH_CONFIG_MATCHED`는 양쪽이 명시적 `null`이면 일치하며, 설정이 있으면 버전·허용 경로·최단 경로 길이를 비교한다.
- preset 목표마다 시작 화면이 정해져 있다 (`refund` = `/shop/home`, C3용 `refund-from-mypage-test` = `/shop/mypage`). 허용 경로가 시작 화면 기준이라 다른 화면에서는 실행하지 않는다. 생략하면 그 값을 쓰고 다르면 `422`.
- `validity`·`invalidReason`은 루프가 넘긴 값을 쓰지 않고, 종료 시 `terminationReason`·`error.code`로 항상 다시 정한다 (계약 6.1 유효성 표).
- `PAGE_LOAD_FAILED`는 `ERROR` + `INVALID`(`BROWSER_CRASH`) (계약 v1.3.3~).

## 시나리오 테스트 API (외부 사이트, #74)

버전 비교(MORO MARKET v1/v2)와 별도로, 등록한 외부 사이트 1곳에서 사용자 상황(목표)으로 Run 1개를 돌린다 (팀 문서 「외부 사이트 시나리오 테스트」 4.3). 사이트 설정은 `config/sites/<siteId>.json`, 탐색 범위는 `uxm/site_scope.py`(#71).

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/sites` | 시나리오 사이트 목록. `{siteId, name, mode: SCENARIO, startPage, pages: [{pageKey, path, label, goalText, goalSelectable, goalUnavailableReason}], siteMap: {available, siteMapVersion, unreachablePages}}` |
| POST | `/api/runs` + `siteId` | 시나리오 Run. 응답에 `siteId`·`mode` |

- `siteId`가 없으면 지금까지와 같은 MORO MARKET 버전 비교 Run이다. Run에 `siteId`·`mode` 필드를 쓰지 않는다.
- 시나리오 Run 규칙: `variant=before`만, 목표는 직접 입력(`goal` + `verification`)만. 시작·도착 화면은 그 사이트에 등록된 화면(`pages`)의 경로와 정확히 같아야 한다(쿼리·끝 `/`가 붙으면 `422`, 화면은 설정의 `open` 주소로 연다). 시작 화면을 생략하면 사이트의 `startPage`. 등록되지 않은 `siteId`·MORO MARKET `siteId`·위 규칙 위반은 `422`.
- 실행 주소와 사이트 버전 값(`siteVersion`, `comparisonContext`의 layout·candidatePolicy·initialBrowserState)은 사이트 설정(`variants`·`meta`)에서 온다. 사이트 주소는 Run과 응답에 넣지 않는다.
- 실행 전 검사는 사이트 1곳만 확인한다. 쿼리가 있어야 열리는 화면(게시판 등)은 설정의 `open` 주소로 연다. 메시지에 Before/After를 붙이지 않는다.
- 사이트 지도가 있으면 그 지도로 `SITE_MAP` 경로 기준을 만든다. 지도상 시작 화면에서 갈 수 없는 도착 화면은 `422` "도착 화면으로 가는 경로가 없습니다." (`/api/sites`의 `goalSelectable: false`. 고를 수 있는 화면 = 확인 글자 있음 + 시작 화면 아님 + 지도에서 닿음). 지도가 없으면 `referencePathConfig: null`.
- 사이트 지도 진단: 도착 화면으로 고를 수 없는 이유는 `goalUnavailableReason`(`START_PAGE` / `NO_GOAL_TEXT` / `UNREACHABLE_ON_SITE_MAP`). `UNREACHABLE_ON_SITE_MAP`은 "시작 화면에 보이는 링크로는 닿지 않는 화면"이고, 목록은 `siteMap.unreachablePages`(지도를 만들 때 기록한 값 그대로). cafe24는 `qna`·`guide`·`company`이며 실행하지 않고 이 진단으로 보여 준다(경고 후 실행 허용은 행사 뒤 고도화).
- 같은 `experimentKey`에 버전 비교 Run과 시나리오 Run을 섞을 수 없다 (`siteId`가 다르면 `422`).
- 비교 API는 시나리오 Run을 대상에서 뺀다. 시나리오 Run만 있는 키는 `404 RUN_NOT_FOUND` "시나리오 실행은 비교하지 않습니다."

## UX Optimizer API (고도화 v1.3, #57)

라벨 후보 풀을 Zoro-1로 평가 → 후보 선정 → 실제 브라우저 재검증 → 최종 판단. 기존 `POST /api/runs`(P0)와 별도 흐름이다.

| Method | Path | 설명 |
|---|---|---|
| POST | `/api/optimizations` | `{sourceRunId?}` → `202` `{optimizationId, status: CREATED, createdAt}`. 후보 평가를 비동기로 시작 |
| GET | `/api/optimizations/{id}` | 상태·진행(`progress`)·후보별 평가(`evaluations`)·선정·검증 Run ID·최종 판단·오류 |
| POST | `/api/optimizations/{id}/verify` | `202` `{optimizationId, status: VERIFYING, verificationPhase: BASELINE, baselineRunId, validationRunId}`. 기준(C0)과 검증 후보 사이트를 차례로 실행 |
| GET | `/api/optimizations/{id}/report` | 완료된 Optimization의 보고서 (미완료 `409 OPTIMIZATION_NOT_READY`) |

- 상태: `CREATED → EVALUATING → EVALUATED → VERIFYING → COMPLETED`, 실패 시 `FAILED`(`error.failedPhase`·`lastStatus` 보존). 프론트는 1초 폴링, 종료 상태에서 멈춘다.
- 후보 풀·대상 메뉴·정책 버전: `config/optimization.json` (C0 `이용정보` 기준, C1~C4). 동률일 때 쓰는 의미 명확성 순위 `semanticClarityRank`(C2 1, C3 2, C4 3, C1 4, 작을수록 우선)도 여기 고정하고 평가마다 넘긴다. 생성 시 모델·프롬프트·목표·사이트 조건과 함께 `frozenConditions`·`modelMetadata`에 동결한다. 요청으로 바꿀 수 없다.
- Site Instance: `config/site_instances.json`에 등록된 사이트만 쓴다 (`baseUrlEnv` 환경변수가 있으면 우선). 후보 5개가 모두 등록돼 있다: C0 8001(`moro-before`), C1 8003(`moro-c1`), C2 8002(`moro-after`), C3 8004(`moro-c3`), C4 8005(`moro-c4`). Optimizer를 돌리기 전에 서버 5개를 띄운다 (`scripts/check_site_instances.py`로 모델 없이 점검). 꺼진 후보는 그 후보만 `FAILED`(`BROWSER_ERROR`)로 남는다. 등록되지 않은 후보는 `SKIPPED`/`INVALID_CANDIDATE`(`SITE_INSTANCE_MISSING`)로 남고 다른 후보로 바꾸지 않는다. `baseUrl`은 응답과 모델 요청에 넣지 않는다.
- 평가: 후보 화면을 실제로 열어 스냅샷(`raw/snapshot_<C>.json`) → 유효성 검사 → Zoro-1 Direct 1회. 실패 시 재시도하지 않는다.
  - 기준(C0) 자체: 대상 semanticKey가 있고 라벨이 `originalLabel`, 목적지가 설정과 같은지 먼저 본다. 다르면 모델을 부르지 않고 `FAILED`(`BASELINE_INVALID`).
  - 후보: C0 대비 후보 수·순서·화면 제목·목적지·영역(region)·다른 라벨·bbox ±2px·누출. 모든 스냅샷은 semanticKey 누락(`SEMANTIC_KEY_MISSING`)·중복(`SEMANTIC_KEY_DUPLICATE`)도 확인한다 (확률을 키로 묶을 때 덮어쓰기 방지).
  - 결과: `targetOptionProbability`(대상 메뉴), `goalPathProbability`(허용 경로 다음 화면으로 가는 후보 확률 합, 기준은 `goalPathBasis: ALLOWED_PATHS`), `probabilities`(semanticKey 기준), `requestHash`·`snapshotHash`, 원본 요청·응답 `raw/model_request_<C>.json`·`model_response_<C>.json`. 계산할 수 없는 지표는 0이 아니라 `null`이고 사유는 `metricNullReasons`.
  - 모델이 제한 시간(`modelTimeoutMs`)을 넘기면 그 후보는 `MODEL_TIMEOUT`. 늦은 호출이 끝날 때까지 같은 시간만큼 더 기다린 뒤 다음 후보로 가고, 그래도 안 끝나면 평가를 `FAILED`(`MODEL_TIMEOUT`)로 멈춘다. 그 호출이 실제로 끝날 때까지는 시간 제한 없이 일반 Run·새 Optimization이 409이고, health `activeOptimization.status`는 `MODEL_CALL_PENDING` (호출 중첩 방지).
  - 후보 화면을 열지 못하면 `BROWSER_ERROR` "후보 화면을 열지 못했습니다." 원본 예외(내부 주소 포함)는 서버 로그에만 남긴다.
- 지표 검증·후보 선정·최종 판단은 4번 `uxm/optimizer_policy.py`(`compute_evaluation_metrics`, `select_candidate`, `judge_verification`)를 부른다. 모듈이 없으면 `503 OPTIMIZER_POLICY_UNAVAILABLE`.
- 검증 Run은 기존 Runner로 만들어 `results/runs/`에 그대로 저장한다. `experimentKey = optimizationId`, 기준은 `variant=before`, 후보는 `after`(기존 비교 API 재사용), 역할은 `optimizationContext.executionRole`(`BASELINE`/`CANDIDATE`).
  - 목표·사용자 유형·성공 조건·경로 기준·제한·viewport는 생성 때 동결한 `frozenConditions`로 만든다. 검증 시작 때 현재 preset·모델 설정(`goalJudgmentMode` 포함)이 동결 값과 다르면 `422 EXPERIMENT_CONDITION_MISMATCH`.
  - 검증 Run을 만들기 전에 기준·선정 후보 화면을 다시 찍어 평가 때 스냅샷과 비교한다(같은 Site Instance, 같은 `snapshotHash`). 다르면 `FAILED`(`SITE_INSTANCE_CHANGED`), 다시 열지 못하면 `FAILED`(`SITE_RECHECK_FAILED`)이고 Run은 만들지 않는다. 확인 결과는 `siteRecheck`.
  - **결과는 `variant`가 아니라 `optimizationContext.siteInstanceId`·`label`로 본다.** C1·C3·C4 Run도 `variant=after`이고 `siteVersion`은 그대로 `shop-v1`이라 Run 필드만으로는 어느 라벨 사이트인지 구분되지 않는다.
- 후보 평가·검증 중에는 `POST /api/runs`도 `409 RUN_ALREADY_ACTIVE`(메시지 "Optimization … 후보 평가가/후보 검증 실행이 진행 중입니다."). 검증 중에는 기준 Run과 후보 Run 사이 빈 구간에도 막고, 이 Optimization의 검증 Run만 통과한다. 진행 중인 Optimization은 `/api/health`의 `activeOptimization`(`{optimizationId, status}` 또는 `null`)에 보인다.
- Optimization 실패 코드(`error.code`): `BASELINE_INVALID`, `BASELINE_NOT_EVALUATED`, `MODEL_TIMEOUT`, `SITE_INSTANCE_CHANGED`, `SITE_RECHECK_FAILED`, `RUN_TIMEOUT`, `OPTIMIZATION_INTERRUPTED`(서버 재시작), `OPTIMIZATION_FAILED`.
- 오류: `404 OPTIMIZATION_NOT_FOUND`, `409 OPTIMIZATION_NOT_READY`·`OPTIMIZATION_ALREADY_PROCESSED`·`RUN_ALREADY_ACTIVE`, `422 INVALID_SITE_INSTANCE`·`EXPERIMENT_CONDITION_MISMATCH`, `503 MODEL_UNAVAILABLE`·`OPTIMIZER_POLICY_UNAVAILABLE`.
- 저장: `results/optimizations/<optimizationId>/optimization.json` + `raw/`.

## 프론트엔드 실제 API 연동 테스트

프론트 저장소에서 `npm ci`, 백엔드 가상환경에서 의존성 설치와
`python -m playwright install chromium`을 마친 뒤 아래 명령으로 실행한다.
`UXM_FRONTEND_PATH`는 Issue 상세에서 관련 Step을 볼 수 있는 프론트 체크아웃을 지정한다.
테스트는 임시 MOCK Run으로 FastAPI와 Vite를 실행하고, 실제 모델은 호출하지 않는다.

```bash
UXM_FRONTEND_PATH="/path/to/UX-Mirror-FE" .venv/bin/python -m pytest -q tests/test_frontend_live_api.py
```

Windows PowerShell에서는 다음과 같이 실행한다.

```powershell
$env:UXM_FRONTEND_PATH = "C:\path\to\UX-Mirror-FE"
.venv\Scripts\python.exe -m pytest -q tests/test_frontend_live_api.py
```

변수를 지정하지 않은 일반 테스트에서는 이 연동 테스트를 건너뛴다. GitHub Actions의
`프론트 실제 API 연동` 워크플로는 PR에서 자동 실행되며 수동 실행에서는 프론트 ref를 지정할 수 있다.
기본 ref는 프론트 PR #22의 검증된 커밋 `cbf79e469d31c6b9173e155491927bbf13bb7542`다.
실행에 사용한 백엔드·프론트 커밋은 워크플로 로그에 남는다. 수동 실행에도 PR #22의 변경이 포함된 ref가 필요하다.

## 백엔드 선택 (.env)

| 설정 | 용도 | 비고 |
|---|---|---|
| `ZORO_BACKEND=local`, `ZORO_DEVICE=cuda-4bit` | 이 PC(GTX 1660 6GB) 개발용 | nf4 양자화. 확률이 원본과 다를 수 있음 → 형식·프롬프트 확인용 |
| `ZORO_BACKEND=local`, `ZORO_DEVICE=cpu` | 원본 bf16 확인 | RAM 여유 9GB+ 필요, 느림 |
| `ZORO_BACKEND=http`, `ZORO_BASE_URL=...` | 행사 GPU 서버 | 서버: `python serve_decide.py C:\AI\Zoro-1 --port 30001` (SGLang ≥0.5.22, Linux). **발표·E 수치는 이것만** |

`ZORO_TEMPERATURE=1.52`(모델 카드 보정값)를 요청마다 명시해서 보낸다. Before/After 동일 필수.

## 결정 사항

### 목표 도달 판정 = `DOM_ONLY` (2026-11-15, G0-A)

테스트 시나리오 판정 규칙 "A9·A10 중 하나라도 틀리면 `DOM_ONLY`"에 따름.

| promptVersion | YES/NO 질문 문구 | A9 refund (기대 YES) | A10 orders (기대 NO) |
|---|---|---|---|
| v1 | 현재 화면에서 사용자의 목표를 바로 수행할 수 있습니까? | 0.566 PASS | 0.672 **FAIL** |
| v2 | 현재 화면이 사용자가 찾던 바로 그 화면입니까? | 0.832 PASS | 0.700 **FAIL** |

- 응답 시간은 1.4~2.2초로 5초 기준 안이었음. 실패 사유는 오탐(정확도).
- 참고 진단(기록 외): orders 화면 글자에서 버튼 문구 `환불 신청`만 빼면 P(yes)=0.204로 NO. 버튼 글자와 목표 문구가 겹쳐서 생긴 오탐으로 추정. 화면 글자 형식 변경은 튜닝 허용 항목이 아니라 적용하지 않음.
- 측정은 cuda-4bit 기준. 원본 bf16·행사 서버에서는 확인하지 않음.
- 구현: `uxm/goal.py`. `DOM_ONLY`에서는 모델 YES/NO를 호출하지 않음(`modelGoalReached=null`). `.env`의 `GOAL_JUDGMENT_MODE`로 설정하며 Before/After 동일 필수.
- **성공 판정은 마커 없이 "주소 + 화면에 보이는 글자"** (2026-11-15 변경, 팀 공유 필요): `/shop/refund`이고 `환불 / 반품 신청`이 화면에 보임.
  사이트를 고치지 않아도 되므로 실제 사이트에도 적용 가능. URL만으로는 성공 처리 안 함. 쇼핑몰에서 `data-test-goal` 마커 제거.
  목표별 조건은 `uxm/goal.py`의 `GOALS` (대시보드 입력으로 받을 때 같은 형식).
- 모델 YES/NO 판정은 기획안 3.2에 따라 P1로 넘김.

### 클릭 후보 규칙 (2026-11-15, B 단계) — 5번은 팀 합의 필요

1. 다른 쇼핑몰 페이지로 이동하는 링크만
2. 첫 화면(1440×900, 스크롤 없음)에 보이는 것만, 라벨도 보이는 글자만
3. 현재 페이지로 가는 링크 제외
4. 위험 동작 제외 (회원 탈퇴, 로그아웃, 접수하기 등)
5. **상단 공통 메뉴(로고·메뉴)는 홈에서만 후보**
   - 근거: orders에서 상단 메뉴가 후보에 있으면 `환불 신청하기` 0.119(4bit) / 0.223(bf16)으로 마이페이지를 고름.
     빼면 0.859 / 0.928. 허용 튜닝(질문 문구 2종, 후보 형식 3종, 페르소나, 이력)으로는 최고 0.25.
   - 후보 형식에 맥락을 붙인 v3는 0.081로 오히려 악화 → 폐기, v2 유지.
   - 홈은 그대로라 Before/After 라벨 비교에는 영향 없음.

구현: `uxm/candidates.py`, 눈으로 확인: `scripts/show_candidates.py`, 실측: `scripts/run_b5.py`

## 사이트 지도 (SITE_MAP, #46)

직접 입력한 목표의 경로 지표를 위해, 위 후보 규칙으로 뽑은 링크만으로 "화면 → 갈 수 있는 화면" 지도를 만든다. 쇼핑몰 HTML 링크 전체를 쓰면 모든 화면이 서로 연결돼 최단 거리가 전부 1이 되기 때문이다. 설계: 「사이트 지도 기반 경로 지표 설계」 v2.1.

```
.venv\Scripts\python.exe scripts\build_sitemap.py --check-runs   # 생성 + 저장된 LIVE Run 후보와 대조
.venv\Scripts\python.exe scripts\build_sitemap.py --dry-run      # 파일을 쓰지 않고 결과만
```

- 지원 화면 5개를 Before·After 각각 새 브라우저(viewport 1440×900, `clean-context-v1`, `candidate-v2`)로 열어 후보를 뽑는다. 간선 = `{fromPageKey, semanticKey, to}`(지원 화면으로 가는 후보만, GO_BACK 제외).
- 검증(하나라도 실패하면 파일을 만들지 않음): 화면 누락(열기 실패·HTTP 오류 응답·다른 화면 도착 포함), Before·After 간선 일치, home에서 모든 화면 도달 가능, preset 허용 경로 대조(`refund` 최단 2, 허용 경로 3개가 지도상 경로). `--check-runs`는 읽기 문제·불일치가 있거나 **실제로 대조한 Step**이 0개여도 실패한다(testMode 화면·재판단 후보는 건너뛴 수로 따로 표시).
- `siteMapHash`: `pages`만(메타데이터 제외), pageKey 순·`{path, edges}`만, 간선은 `{semanticKey, to}`만 중복 제거 후 `(semanticKey, to)` 순, `json.dumps(ensure_ascii=False, sort_keys=True, separators=(",", ":"))` → UTF-8 → SHA-256, `"sha256:"` + hex.
- 현재 지도(`sitemap-v1`): home → mypage·orders·support, mypage → home·orders·refund·support, orders → home·refund, refund → home·orders, support → orders. 저장된 LIVE Run Step 76개와 대조해 불일치 0.
- **루프 (#49)**: Run의 `referencePathConfig.basis`가 `SITE_MAP`이면 Run에 복사된 `distances`·`shortestSteps`로 경로 지표를 계산한다(루프는 지도 파일을 읽지 않음). `extraSteps` = 정상 이동 CLICK 수 − `shortestSteps`, **`detourMoveCount`(우회 이동)** = 모델 CLICK 이동 중 목표까지 거리가 줄지 않은 수(BACK·NO_CHANGE 제외), Step `goalPathProbability` = 거리를 줄이는 CLICK 후보 확률 합(후보 하나라도 지도에 없으면 `null`, 모두 알고 줄이는 후보가 없으면 `0`), `referencePathDeviationCount`는 `null`. 지도 밖 화면으로 가면 우회 이동·추가 단계 `null`. `basis`가 없거나 `ALLOWED_PATHS`면 기존 허용 경로 계산 그대로, 기준이 없거나 형식이 틀리면 경로 지표 `null`.
- Run에 넣을 설정은 `uxm.sitemap.site_map_reference(지도, 도착 화면, 시작 화면)`(실행 API용, 시작 화면이 지도와 다르거나 길이 없으면 `SiteMapError`). API가 저장하기 전에는 `scripts\run_c.py --site-map`으로 확인한다.
- **다른 사이트**: `config/sites/`에 설정 파일(화면 목록, `startPage`, `pathPrefix`, `allowedHosts`, `variants` 주소, `siteMapVersion`, `out`, `presetCheck`)을 만들고 `--site 파일 --url 이름=주소`로 실행한다. 주소는 하나여도 된다(A/B가 아니면). 단, 후보 추출 규칙(`uxm/candidates.py`)이 아직 쇼핑몰(`/shop/`, 시작 화면 `/shop/home`)에 묶여 있어 그 규칙을 사이트별로 바꾸기 전에는 후보가 나오지 않는다.

## 목표 도달 확률 엔진 (19번, 고도화 P1)

실행 없이 사이트 구조 기준으로 "이 목표에 닿을 확률"을 본다. 지도 간선 + 화면마다 모델이 준 후보별 확률(화면당 모델 1번) → 화면 사이 이동 확률 → `maxSteps` 안 도달 확률 (`uxm/reach.py`).

```
.venv\Scripts\python.exe scripts\estimate_reach.py                         # Before/After, 환불 목표
.venv\Scripts\python.exe scripts\estimate_reach.py --goal-page orders --persona "70대, 인터넷 쇼핑이 서툰 사용자"
.venv\Scripts\python.exe scripts\estimate_reach.py --url C1=http://127.0.0.1:8003 --url C2=http://127.0.0.1:8002
```

- 값: `reachProbability`(maxSteps 안), `reachWithinShortestProbability`(지도상 최단 이동 수 안), `reachByStep`, `expectedStepsGivenReach`, `lostProbability`(지도 밖으로 가는 확률), `unfinishedProbability`, `mostLikelyPath`. 결과는 `results/reach/`에 화면별 원본 요청·응답과 함께 저장.
- 외부 사이트 시나리오(#76): `--site <siteId>`면 사이트 설정(`config/sites/<siteId>.json`)의 주소·지도·탐색 규칙으로 1곳만 추정한다. 후보는 그 사이트 규칙(`SiteScope`)으로 뽑고, 쿼리가 있어야 열리는 화면은 설정의 `open` 주소로 연다. 지도 생성과 같은 규칙이라 실제 화면과 지도를 같은 기준으로 대조한다. 지도가 기록한 닿지 않는 화면(`unreachablePages`)은 모델을 부르지 않는다(cafe24 목표당 8번 → 5번, 간선 대조는 함). 모르는 `siteId`는 명령줄 오류.
- 이동 수가 넉넉하면 두 사이트 모두 도달 확률이 1에 가까워져서, 차이는 `reachWithinShortestProbability`와 기대 단계로 본다. `--max-steps`가 지도상 최단 이동 수보다 작으면 이 값은 계산할 수 없어 `null`이고 사유는 `nullReasons`(`MAX_STEPS_BELOW_SHORTEST`, 길이 없으면 `GOAL_UNREACHABLE_ON_MAP`).
- **구조 기준 추정치**다. 이전 화면 기록·되돌아가기·재방문 제한을 넣지 않아 실제 Run과 다를 수 있고, 실제 Run 결과를 대신하지 않는다.
- 화면마다 모델 확률을 합 1로 정규화한다 (검증은 ±0.02 허용). `lostProbability`는 지도 간선이 없는 후보로 간 확률뿐이고 반올림 오차는 들어가지 않는다. 도달·이탈·미도달 합은 항상 1.
- 확률 0인 이동은 따라가지 않는다. 양의 확률로 닿는 화면에 확률이 없으면 `status: INCOMPLETE`(`missingPages`). 이때 **하한인 것은 `reachProbability`뿐**이고, 기대 단계와 두 추정치의 차이는 하한이 아니다. `compare`는 `bothComplete: false`와 `note`를 붙인다. 개선 판단 근거로 쓰지 않는다.
- 화면마다 실제 후보의 지원 화면 간선(`semanticKey`→화면, `sitemap.edges_from_candidates`와 같은 규칙)을 지도와 대조한다. 다르면 `status: MAP_MISMATCH`(`mapMismatches`: 화면별 `added`·`removed`, 목적지가 바뀐 링크는 둘 다에 나옴)이고 `compare`는 `comparable: false`. 지도 밖(지원하지 않는 화면·사이트 밖)으로 가는 후보는 원래 이탈이라 차이로 보지 않는다. `--url`로 라벨 변형 사이트를 넣을 때 구조가 같은지 이것으로 확인된다.

## 확인된 모델 API (trida-stack 코드 기준)

- `POST /v1/decide` body: `{"request": {"state": ..., "questions": {...}}, "reasoning": false, "temperature": 1.52}`
- `choice` 응답: `{"type","choice","confidence","probabilities"}` — `confidence`는 최고 확률이 아니라 `(max-1/k)/(1-1/k)`
- 예/아니요는 `choice`가 아니라 **`noul`** 타입, 응답은 `{"noul": P(yes)}`
- 요청 최상위 키는 `state, questions, image, min_image_pixels, max_image_pixels`만 허용
