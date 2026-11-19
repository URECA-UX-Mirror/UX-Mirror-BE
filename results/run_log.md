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
