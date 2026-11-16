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
  config.py      # .env 로딩
scripts/run_a.py # A 실행기 → results/json/a/, results/run_log.md
scripts/run_b5.py        # B3+B5 실제 화면 확률 → results/json/b5/, results/screenshots/b5/
scripts/show_candidates.py  # 후보·도착 판정 확인 (모델 없음) → results/json/candidates/, results/screenshots/candidates/
results/README.md        # 남겨둔 결과별 의미
scripts/build_shop.py  # shop/stitch_moro_market → shop/moro-before(8001), shop/moro-after(8002) 생성 + 라벨 1개 차이 검사
shop/
  server.py              # 쇼핑몰 서버 원본 (build_shop.py가 두 쇼핑몰에 복사)
  stitch_moro_market/    # Stitch 시안 원본 (쇼핑몰 수정은 여기 또는 build_shop.py에서)
  moro-before/, moro-after/  # 생성 결과 (직접 수정 금지). 서버: cd shop\moro-before 후 py -3.11 server.py
                             # 테스트 모드: 첫 주소에 ?testMode=nochange(마이페이지 쿠폰함 눌러도 안 바뀜) | deadend(고객센터 배송 현황 확인 숨김)
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

```powershell
.\.venv\Scripts\python -m uvicorn api.main:app --port 8000
```

| Method | 주소 | 내용 |
|---|---|---|
| GET | `/api/presets` | 목표·페르소나 선택지 (`?includeTest=true`면 C3용 테스트 전용 목표 포함) |
| POST | `/api/runs` | Run 생성 `{experimentKey, variant, goalId, personaId, maxSteps?, startPath?}` → `201` READY, 실행은 별도 스레드 |
| GET | `/api/runs` | Run 목록 `?experimentKey=&variant=` (계약 밖 추가) |
| GET | `/api/runs/{runId}` | 상태·요약 |
| GET | `/api/runs/{runId}/steps` | 저장이 끝난 Step |
| GET | `/api/runs/{runId}/issues` | UX 검토 후보 (`PENDING`/`COMPLETED`/`FAILED`) |
| GET | `/api/runs/{runId}/screenshots/{stepId}?kind=original\|overlay` | 스크린샷 |
| GET | `/api/comparisons/{experimentKey}` | Before/After 비교 (VALID 최종 Run, 두 Run의 Issue 분석이 모두 `COMPLETED`일 때만) |
| GET | `/api/health` | 모델·브라우저·에이전트 준비 상태 |

- 동시에 1개만 실행 (`409 RUN_ALREADY_ACTIVE`). 같은 `experimentKey`+`variant`에 VALID·진행 중 Run이 있으면 `409 EXPERIMENT_RUN_EXISTS`. 무효(INVALID) Run만 다시 실행할 수 있고 원본의 `replacementRunId`에 연결된다.
- 에이전트 루프(`uxm/agent.py`)나 브라우저(`uxm/browser.py`)가 없으면 `POST /api/runs`는 `503`.
- Issue 분석(`detect_issues(run, steps, events)`)은 Run이 끝날 때 한 번만 돌고 `issues.json`을 쓴다. 조회 API는 파일을 쓰지 않는다. 분석 모듈이 없을 때 끝난 Run은 `PENDING`으로 남고, 비교는 `409 COMPARISON_NOT_READY`(분석 `FAILED`면 `500 ANALYSIS_ERROR`).
- 나중에 분석하려면 `python scripts/analyze_runs.py --all` (`issues.json`만 쓰고 `run.json`은 건드리지 않음). 이렇게 분석한 Run은 저장된 `summary.detectedIssueTypes`가 비어 있을 수 있지만, `GET /api/runs/{runId}`와 비교 API는 완료된 `issues.json`에서 다시 계산한 값을 쓴다.
- 목표마다 시작 화면이 정해져 있다 (`refund` = `/shop/home`, C3용 `refund-from-mypage-test` = `/shop/mypage`). 허용 경로가 시작 화면 기준이라 다른 화면에서는 실행하지 않는다. 생략하면 그 값을 쓰고 다르면 `422`.
- `validity`·`invalidReason`은 루프가 넘긴 값을 쓰지 않고, 종료 시 `terminationReason`·`error.code`로 항상 다시 정한다 (계약 6.1 유효성 표).
- `PAGE_LOAD_FAILED`는 `ERROR` + `INVALID`(`BROWSER_CRASH`) (계약 v1.3.3~).

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

## 확인된 모델 API (trida-stack 코드 기준)

- `POST /v1/decide` body: `{"request": {"state": ..., "questions": {...}}, "reasoning": false, "temperature": 1.52}`
- `choice` 응답: `{"type","choice","confidence","probabilities"}` — `confidence`는 최고 확률이 아니라 `(max-1/k)/(1-1/k)`
- 예/아니요는 `choice`가 아니라 **`noul`** 타입, 응답은 `{"noul": P(yes)}`
- 요청 최상위 키는 `state, questions, image, min_image_pixels, max_image_pixels`만 허용
