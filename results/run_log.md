| 시각 | 시나리오 | promptVersion | 백엔드 | 결과 | 통과 |
|---|---|---|---|---|---|
| 20261115_110806 | A1 | v1 | local/cuda-4bit | btn_refund=0.758  btn_tracking=0.160  menu_home=0.081 / H_click=0.644 top2=0.598 기대최고=btn_refund PASS | PASS |
| 20261115_111001 | A1 | v1 | local/cuda-4bit | btn_refund=0.758  btn_tracking=0.160  menu_home=0.081 / H_click=0.644 top2=0.598 기대최고=btn_refund PASS | PASS |
| 20261115_111001 | A2 | v1 | local/cuda-4bit | menu_orders=0.048  menu_support=0.159  menu_mypage=0.794 / H_click=0.565 top2=0.635 | 기록 |
| 20261115_111001 | A3 | v1 | local/cuda-4bit | menu_orders=0.563  menu_support=0.083  menu_mypage=0.354 / H_click=0.817 top2=0.210 | 기록 |
| 20261115_111001 | A4 | v1 | local/cuda-4bit | menu_new=0.784  menu_event=0.140  menu_brand=0.077 / H_click=0.603 top2=0.644 | 기록 |
| 20261115_111001 | A5 | v1 | local/cuda-4bit | menu_mypage=0.730  menu_orders=0.135  menu_support=0.135 / H_click=0.701 top2=0.595 | 기록 |
| 20261115_111001 | A6#1 | v1 | local/cuda-4bit | menu_orders=0.048  menu_support=0.159  menu_mypage=0.794 / H_click=0.565 top2=0.635 | 기록 |
| 20261115_111001 | A6#2 | v1 | local/cuda-4bit | menu_orders=0.048  menu_support=0.159  menu_mypage=0.794 / H_click=0.565 top2=0.635 | 기록 |
| 20261115_111001 | A6#3 | v1 | local/cuda-4bit | menu_orders=0.048  menu_support=0.159  menu_mypage=0.794 / H_click=0.565 top2=0.635 | 기록 |
| 20261115_111001 | A7 | v1 | local/cuda-4bit | menu_home=0.388  GO_BACK=0.612 / H_click=N/A top2=N/A P(back)=0.612 | 기록 |
| 20261115_111001 | A8 | v1 | local/cuda-4bit | menu_orders=0.027  menu_support=0.157  menu_mypage=0.816 / H_click=0.505 top2=0.658 | 기록 |
| 20261115_111001 | A9 | v1 | local/cuda-4bit | P(yes)=0.566 기대=yes PASS | PASS |
| 20261115_111001 | A10 | v1 | local/cuda-4bit | P(yes)=0.672 기대=no FAIL | FAIL |
| 20261115_123334 | A9 | v2 | local/cuda-4bit | P(yes)=0.832 기대=yes PASS | PASS |
| 20261115_123334 | A10 | v2 | local/cuda-4bit | P(yes)=0.700 기대=no FAIL | FAIL |
| 20261115 결정 | A9·A10 | v1·v2 | local/cuda-4bit | A10 두 버전 모두 FAIL(0.672, 0.700) → 판정 규칙에 따라 goalJudgmentMode=DOM_ONLY 확정. 모델 YES/NO는 P1 | 결정 |
| 20261115_133606 | A2 | v2 | local/cpu | menu_orders=0.074  menu_support=0.165  menu_mypage=0.761 / H_click=0.635 top2=0.596 | 기록 |
| 20261115_133606 | A3 | v2 | local/cpu | menu_orders=0.685  menu_support=0.056  menu_mypage=0.259 / H_click=0.701 top2=0.427 | 기록 |
| 20261115_133606 | A10 | v2 | local/cpu | P(yes)=0.628 기대=no FAIL | FAIL |
| 20261115_151904 | B5 before/home | v2 | local/cuda-4bit | 이용정보=0.114, 고객센터=0.153, 마이페이지=0.602, 실시간 조회 이용정보 =0.096, 24H 빠른 접수 고객=0.035 / H_click=0.735 | 기록 |
| 20261115_151904 | B5 after/home | v2 | local/cuda-4bit | 주문·환불=0.502, 고객센터=0.114, 마이페이지=0.255, 실시간 조회 이용정보 =0.088, 24H 빠른 접수 고객=0.041 / H_click=0.799 | 기록 |
| 20261115_151904 | B5 before/orders | v2 | local/cuda-4bit | MORO MARKET=0.061, 고객센터=0.378, 마이페이지=0.408, 홈=0.034, 환불 신청하기=0.119 / H_click=0.791 | 기록 |
| 20261115_151904 | B5 after/orders | v2 | local/cuda-4bit | MORO MARKET=0.061, 고객센터=0.378, 마이페이지=0.408, 홈=0.034, 환불 신청하기=0.119 / H_click=0.791 | 기록 |
| 20261115_152610 | A1 | v3 | local/cuda-4bit | btn_refund=0.758  btn_tracking=0.160  menu_home=0.081 / H_click=0.644 top2=0.598 기대최고=btn_refund PASS | PASS |
| 20261115_152610 | A2 | v3 | local/cuda-4bit | menu_orders=0.048  menu_support=0.159  menu_mypage=0.794 / H_click=0.565 top2=0.635 | 기록 |
| 20261115_152610 | A3 | v3 | local/cuda-4bit | menu_orders=0.563  menu_support=0.083  menu_mypage=0.354 / H_click=0.817 top2=0.210 | 기록 |
| 20261115_152610 | A4 | v3 | local/cuda-4bit | menu_new=0.784  menu_event=0.140  menu_brand=0.077 / H_click=0.603 top2=0.644 | 기록 |
| 20261115_152610 | A5 | v3 | local/cuda-4bit | menu_mypage=0.730  menu_orders=0.135  menu_support=0.135 / H_click=0.701 top2=0.595 | 기록 |
| 20261115_152610 | A6#1 | v3 | local/cuda-4bit | menu_orders=0.048  menu_support=0.159  menu_mypage=0.794 / H_click=0.565 top2=0.635 | 기록 |
| 20261115_152610 | A6#2 | v3 | local/cuda-4bit | menu_orders=0.048  menu_support=0.159  menu_mypage=0.794 / H_click=0.565 top2=0.635 | 기록 |
| 20261115_152610 | A6#3 | v3 | local/cuda-4bit | menu_orders=0.048  menu_support=0.159  menu_mypage=0.794 / H_click=0.565 top2=0.635 | 기록 |
| 20261115_152610 | A7 | v3 | local/cuda-4bit | menu_home=0.388  GO_BACK=0.612 / H_click=N/A top2=N/A P(back)=0.612 | 기록 |
| 20261115_152610 | A8 | v3 | local/cuda-4bit | menu_orders=0.027  menu_support=0.157  menu_mypage=0.816 / H_click=0.505 top2=0.658 | 기록 |
| 20261115_152610 | A9 | v3 | local/cuda-4bit | P(yes)=0.832 기대=yes PASS | PASS |
| 20261115_152610 | A10 | v3 | local/cuda-4bit | P(yes)=0.700 기대=no FAIL | FAIL |
| 20261115_152658 | B5 before/home | v3 | local/cuda-4bit | 이용정보=0.114, 고객센터=0.153, 마이페이지=0.602, 실시간 조회 이용정보 =0.096, 24H 빠른 접수 고객=0.035 / H_click=0.735 | 기록 |
| 20261115_152658 | B5 after/home | v3 | local/cuda-4bit | 주문·환불=0.502, 고객센터=0.114, 마이페이지=0.255, 실시간 조회 이용정보 =0.088, 24H 빠른 접수 고객=0.041 / H_click=0.799 | 기록 |
| 20261115_152658 | B5 before/orders | v3 | local/cuda-4bit | MORO MARKET=0.062, 고객센터=0.380, 마이페이지=0.439, 홈=0.037, 환불 신청하기=0.081 / H_click=0.763 | 기록 |
| 20261115_152658 | B5 after/orders | v3 | local/cuda-4bit | MORO MARKET=0.062, 고객센터=0.380, 마이페이지=0.439, 홈=0.037, 환불 신청하기=0.081 / H_click=0.763 | 기록 |
| 20261115_153652 | B5 before/home | v2 | local/cuda-4bit | 이용정보=0.114, 고객센터=0.153, 마이페이지=0.602, 실시간 조회 이용정보 =0.096, 24H 빠른 접수 고객=0.035 / H_click=0.735 | 기록 |
| 20261115_153652 | B5 after/home | v2 | local/cuda-4bit | 주문·환불=0.502, 고객센터=0.114, 마이페이지=0.255, 실시간 조회 이용정보 =0.088, 24H 빠른 접수 고객=0.041 / H_click=0.799 | 기록 |
| 20261115_153652 | B5 before/mypage | v2 | local/cuda-4bit | 홈=0.013, 고객지원센터=0.099, 주문/배송 조회 NEW=0.236, 취소/교환/반품 내역 =0.223, 1:1 문의내역 1=0.031, 주문/배송 전체보기=0.179, 입금대기 0=0.009, 결제완료 1=0.034, 상품준비중 0=0.005, 배송중 2=0.012, 배송완료 4=0.063, 취소접수 0=0.021, 반품/교환 진행 1=0.019, 환불완료 2=0.007, 반품 및 환불 가이드 =0.051 / H_click=0.785 | 기록 |
| 20261115_153652 | B5 after/mypage | v2 | local/cuda-4bit | 홈=0.013, 고객지원센터=0.099, 주문/배송 조회 NEW=0.236, 취소/교환/반품 내역 =0.223, 1:1 문의내역 1=0.031, 주문/배송 전체보기=0.179, 입금대기 0=0.009, 결제완료 1=0.034, 상품준비중 0=0.005, 배송중 2=0.012, 배송완료 4=0.063, 취소접수 0=0.021, 반품/교환 진행 1=0.019, 환불완료 2=0.007, 반품 및 환불 가이드 =0.051 / H_click=0.785 | 기록 |
| 20261115_153652 | B5 before/support | v2 | local/cuda-4bit | 배송 현황 확인=1.000 / H_click=N/A | 기록 |
| 20261115_153652 | B5 after/support | v2 | local/cuda-4bit | 배송 현황 확인=1.000 / H_click=N/A | 기록 |
| 20261115_153652 | B5 before/orders | v2 | local/cuda-4bit | 홈=0.136, 환불 신청하기=0.864 / H_click=0.573 | 기록 |
| 20261115_153652 | B5 after/orders | v2 | local/cuda-4bit | 홈=0.136, 환불 신청하기=0.864 / H_click=0.573 | 기록 |
| 20261115_153652 | B5 before/refund | v2 | local/cuda-4bit | 홈=0.091, 이용정보 (주문내역)=0.436, 이전 화면으로 (주문내=0.473 / H_click=0.851 | 기록 |
| 20261115_153652 | B5 after/refund | v2 | local/cuda-4bit | 홈=0.091, 이용정보 (주문내역)=0.436, 이전 화면으로 (주문내=0.473 / H_click=0.851 | 기록 |
| 20261115_155510 | B5 before/home | v2 | local/cuda-4bit | 이용정보=0.114, 고객센터=0.153, 마이페이지=0.602, 실시간 조회 이용정보 =0.096, 24H 빠른 접수 고객=0.035 / H_click=0.735 | 기록 |
| 20261115_155510 | B5 after/home | v2 | local/cuda-4bit | 주문·환불=0.502, 고객센터=0.114, 마이페이지=0.255, 실시간 조회 이용정보 =0.088, 24H 빠른 접수 고객=0.041 / H_click=0.799 | 기록 |
| 20261115_155510 | B5 before/orders | v2 | local/cuda-4bit | 홈=0.136, 환불 신청하기=0.864 / H_click=0.573 | 기록 |
| 20261115_155510 | B5 after/orders | v2 | local/cuda-4bit | 홈=0.136, 환불 신청하기=0.864 / H_click=0.573 | 기록 |
| 20261115_160748 | B5 before/home | v2 | local/cuda-4bit | 이용정보=0.114, 고객센터=0.153, 마이페이지=0.602, 실시간 조회 이용정보 =0.096, 24H 빠른 접수 고객=0.035 / H_click=0.735 | 기록 |
| 20261115_160748 | B5 after/home | v2 | local/cuda-4bit | 주문·환불=0.502, 고객센터=0.114, 마이페이지=0.255, 실시간 조회 이용정보 =0.088, 24H 빠른 접수 고객=0.041 / H_click=0.799 | 기록 |
| 20261115_160748 | B5 before/orders | v2 | local/cuda-4bit | 홈=0.136, 환불 신청하기=0.864 / H_click=0.573 | 기록 |
| 20261115_160748 | B5 after/orders | v2 | local/cuda-4bit | 홈=0.136, 환불 신청하기=0.864 / H_click=0.573 | 기록 |

## E 실행 기록 (final-plan-1119, #93)

E-1 전 확인 (2026-11-19 15:20, 실행 커밋 `230fd67`, 모델 서버 `brev-l40s-bf16` — 행사 GPU L40S 원본 bf16, `/api/health` modelVersion `trillionlabs/Zoro-1`):
- `git diff --name-only ac153e6 230fd67` → 계획 파일 외에 #87·#88 파일(`.gitignore`, `README.md`, `scripts/check_site_instances.py`, `uxm/optimizer_policy.py`, 테스트 3개). 정책 모듈은 Optimizer에서만 불러오고 Explorer 루프는 쓰지 않아 E 결과에 영향 없음 → 팀 확인 후 E-1 시작
- E 전 MORO 회귀 `regress-gpu-230fd67`: `run_before_20261119152012_73ab` / `run_after_20261119152031_8536` 둘 다 SUCCESS·VALID, 비교 조건 일치
- 실행 방법: 화면 대신 실행 API(`POST /api/runs`)로 같은 조건(계획의 목표 문장·사용자 유형·성공 조건·maxSteps 8, siteId 없음)을 보내 실행 (FE 수정 중, 팀 확인)

| finalExecutionPlanId | pairIndex | experimentKey | variant | actualOrder | startedAt | runId | executionCommit | modelEndpointId |
|---|---|---|---|---|---|---|---|---|
| final-plan-1119 | 1 | e-s1-1119 | before | 1 | 2026-11-19T15:24:27.609+09:00 | run_before_20261119152427_7646 | 230fd67 | brev-l40s-bf16 |
| final-plan-1119 | 1 | e-s1-1119 | after | 2 | 2026-11-19T15:24:45.500+09:00 | run_after_20261119152445_52ce | 230fd67 | brev-l40s-bf16 |
| final-plan-1119 | 1 | e-s2-1119 | before | 1 | 2026-11-19T15:27:16.693+09:00 | run_before_20261119152716_6342 | 230fd67 | brev-l40s-bf16 |
| final-plan-1119 | 1 | e-s2-1119 | after | 2 | 2026-11-19T15:27:34.448+09:00 | run_after_20261119152734_aab5 | 230fd67 | brev-l40s-bf16 |
| final-plan-1119 | 1 | e-s3-1119 | before | 1 | 2026-11-19T15:27:52.122+09:00 | run_before_20261119152752_d372 | 230fd67 | brev-l40s-bf16 |
| final-plan-1119 | 1 | e-s3-1119 | after | 2 | 2026-11-19T15:28:04.968+09:00 | run_after_20261119152804_2323 | 230fd67 | brev-l40s-bf16 |
| final-plan-1119 | 1 | e-s4-1119 | before | 1 | 2026-11-19T15:28:24.850+09:00 | run_before_20261119152824_99ae | 230fd67 | brev-l40s-bf16 |
| final-plan-1119 | 1 | e-s4-1119 | after | 2 | 2026-11-19T15:28:42.652+09:00 | run_after_20261119152842_443f | 230fd67 | brev-l40s-bf16 |

E 결과 요약 (#94 리뷰 반영, 결과 원본은 Run 폴더와 `results/comparisons/e-s*-1119.json`):
- Run 8개 모두 `VALID`, 4개 목표 모두 비교 조건 일치(`conditionsMatched`), 재실행 없음
- 메인 지표 = home에서 정답 메뉴(`correctFirstLinks`)를 고를 확률 합 (= home `goalPathProbability`, SITE_MAP)

| 목표 | experimentKey | Before | After | 변화 | 경로 Before / After |
|---|---|---|---|---|---|
| S1 환불 | e-s1-1119 | 0.537 | 0.717 | +0.180 | BLOCKED(REVISIT_LIMIT_REACHED, 미도달) / SUCCESS 2단계 |
| S2 배송 | e-s2-1119 | 0.653 | 0.773 | +0.120 | SUCCESS 1단계 / SUCCESS 1단계 |
| S3 쿠폰 | e-s3-1119 | 0.758 | 0.935 | +0.177 | SUCCESS 1단계 / SUCCESS 1단계 |
| S4 문의 | e-s4-1119 | 0.771 | 0.611 | −0.160 | SUCCESS 1단계 / SUCCESS 1단계 (CHOICE_AMBIGUITY) |

- 바꾼 메뉴(상단 `/shop/orders`, 이용정보 → 주문·환불) 확률: S1 0.205 → 0.596, S2 0.060 → 0.181, S3 0.170 → 0.023, S4 0.105 → 0.260
- 해석: 환불·배송·쿠폰은 개선, 상품 문제로 문의하려는 S4에서는 "주문·환불"이 사용자를 끌어당기는 부작용. 목표당 Run 1쌍, 확률은 모델의 선택 분포이며 사람 클릭률이 아님

## 자동 개선 본 실행 기록 (improve-plan-1119, #106)

- 기준 고정: `results/improvement_plan.json` (FROZEN 2026-11-19T23:31:13+09:00, 기준 커밋 `731337c`)
- 실행: 2026-11-19 23:35, 실행 커밋 `30f547c`(기준 커밋 + 동결 파일·.gitignore만), 모델 서버 `brev-l40s-bf16`, `scripts/improve.py loop` (동결 조건 확인 통과)
- 결과 `imp-1119`: **NO_IMPROVEMENT** — 2회차 연속 채택 없음(`NO_ADOPTION_STREAK`), 실제 Run 검증 없음(`NOT_RUN`, `NO_IMPROVEMENT`). 결과 폴더 `results/improvements/imp-1119/`(git 제외)
- v1 기준(최단 이동 수 안 도달): S1 환불 0.402 / S2 배송 0.654 / S3 쿠폰 0.762 / S4 문의 0.767

| 회차 | 후보 (출처) | S1 | S4 | 판정 |
|---|---|---|---|---|
| 1 | 주문·배송 (사람) | 0.249 | 0.637 | GUARD_LOSS_EXCEEDED:S4 |
| 1 | 주문·환불 (사람) | 0.655 | 0.627 | GUARD_LOSS_EXCEEDED:S4 |
| 1 | 주문·반품 (사람) | 0.640 | 0.634 | GUARD_LOSS_EXCEEDED:S4 |
| 1 | 주문 관리 (사람) | 0.522 | 0.522 | GUARD_LOSS_EXCEEDED:S4 |
| 1 | 배송 조회 (AI) | 0.285 | 0.811 | MAIN_GAIN_INSUFFICIENT |
| 1 | 주문 내역 (AI) | 0.476 | 0.523 | GUARD_LOSS_EXCEEDED:S4 |
| 1 | 주문 상태 (AI) | 0.376 | 0.624 | GUARD_LOSS_EXCEEDED:S4 |
| 2 | 주문 조회 (AI) | 0.425 | 0.547 | GUARD_LOSS_EXCEEDED:S4 |
| 2 | 배송 추적 (AI) | 0.211 | 0.847 | MAIN_GAIN_INSUFFICIENT |
| 2 | 배송 확인 (AI) | 0.270 | 0.758 | MAIN_GAIN_INSUFFICIENT |
| 2 | 주문 확인 (AI) | 0.391 | 0.577 | GUARD_LOSS_EXCEEDED:S4 |

- 해석: "주문"이 들어간 이름은 사람·AI 후보 모두 문의(S4) 사용자를 끌어당겨 −12~24%p로 탈락, "배송" 계열은 환불(S1) 개선이 +5%p에 못 미침. 지킬 목표 안전장치가 다른 사용자를 해치는 이름을 모두 막았다.
- 팀 결정(11/19 23:40): 동결 결과를 그대로 공개, 추가 실행 없음(결과를 보고 다시 돌리지 않음). 동결 전 연습 실행 3회차(DRAFT 기준)에서 나온 "환불신청"(S1 +16.2%p, S4 +0.7%p)은 공식 결과가 아닌 **탐색 실행 예시**로만 표시.

### 2차 실행 사전 등록 (imp-1119b)

- 등록: 2026-11-19T23:57:17+09:00 (실행 전), 계획 파일 `results/improvement_plan_1119b.json`
- 이유: 1차(imp-1119) NO_IMPROVEMENT 확인 후, 팀 결정으로 같은 규칙의 2차 실행을 **1회만** 추가한다 (이름 제안이 실행마다 달라짐)
- 바뀐 것: 실행 ID(`imp-1119b`)와 검증 키만. 목표·점수·채택 규칙·사람 후보·제안 설정·모델 조건은 1차와 동일
- 공개 원칙: **1차·2차 결과를 모두 공개**한다. 2차도 채택이 없으면 '개선안 없음'으로 확정하고 더 실행하지 않는다
- 위 '팀 결정(11/19 23:40) 추가 실행 없음'은 이 등록으로 변경됨 (1차 결과 이후의 결정임을 밝힘)
