# 에이전트 실행 로그

UX MIRROR 에이전트가 실제로 돌린 Run 기록이다. Run마다 폴더 하나이고, 모든 클릭 판단은 Zoro-1이 낸 후보별 확률로 정했다.

11/20 07:34, 배포 서버(행사 GPU `brev-l40s-bf16`, BE `7a8306e`)에서 돌린 시나리오 3개다. 같은 고객 유형("온라인 쇼핑몰 이용 경험이 적은 사용자")으로 예시 쇼핑몰 MORO MARKET을 진단했다.

1. **환불: 헤매다가 개선안으로 해결**
   - 실험 키 `imp-live-20261120073424-5254-MAIN`. AI 개선(`imp-live-20261120073424-5254`)이 상단 메뉴 '이용정보'를 **'주문 환불'** 로 바꾸자고 제안했다. 찾아올 가능성은 40.0% → 59.7%로 올랐다.
   - 지금 화면에서는 홈과 고객센터를 오가다 못 찾았다(4단계, 같은 화면 반복으로 멈춤).
   - 개선안에서는 홈 → 주문·배송 조회 → 환불 신청, 2번 만에 도착했다.
2. **문의: 개선 후에도 다른 고객이 손해 보지 않음**
   - 실험 키 `imp-live-20261120073424-5254-S4`. 같은 개선안으로 문의 목표를 확인했다.
   - 두 화면 모두 1번 만에 고객센터에 도착했다.
3. **배송: 처음부터 잘 찾음** (대조군)
   - 실험 키 `exp_b02a56dc84414574a4dfc3ebc1d28f92`.
   - 지금 화면에서 1번 만에 주문·배송 조회에 도착했다.

1·2는 `comparisons/`에 두 화면의 비교 결과가 함께 있다.

| Run | 실험 키 | 종류 | 목표 | 결과 | 지나간 화면 | 단계 | 찾은 Issue |
|---|---|---|---|---|---|---|---|
| [run_before_20261120073628_0ba9](run_before_20261120073628_0ba9/) | imp-live-20261120073424-5254-MAIN | 버전 비교 (before) | 지난주에 산 운동화가 발에 안 맞아서 돌려보내고 돈을 돌려받고 싶다 | BLOCKED · REVISIT_LIMIT_REACHED · VALID | home → support → home → support → home | 4 | CHOICE_AMBIGUITY, GOAL_NOT_REACHED, REPEATED_VISIT |
| [run_after_20261120073638_384e](run_after_20261120073638_384e/) | imp-live-20261120073424-5254-MAIN | 버전 비교 (after) | 지난주에 산 운동화가 발에 안 맞아서 돌려보내고 돈을 돌려받고 싶다 | SUCCESS · GOAL_VERIFIED · VALID | home → orders → refund | 2 | - |
| [run_before_20261120073645_2357](run_before_20261120073645_2357/) | imp-live-20261120073424-5254-S4 | 버전 비교 (before) | 받은 상품에 문제가 있어서 직원에게 직접 물어보고 싶다 | SUCCESS · GOAL_VERIFIED · VALID | home → support | 1 | - |
| [run_after_20261120073650_bdec](run_after_20261120073650_bdec/) | imp-live-20261120073424-5254-S4 | 버전 비교 (after) | 받은 상품에 문제가 있어서 직원에게 직접 물어보고 싶다 | SUCCESS · GOAL_VERIFIED · VALID | home → support | 1 | CHOICE_AMBIGUITY |
| [run_before_20261120073657_8b5c](run_before_20261120073657_8b5c/) | exp_b02a56dc84414574a4dfc3ebc1d28f92 | 버전 비교 (before) | 어제 산 물건이 지금 어디쯤 오고 있는지 알고 싶다 | SUCCESS · GOAL_VERIFIED · VALID | home → orders | 1 | - |

## run_before_20261120073628_0ba9

- 목표: 지난주에 산 운동화가 발에 안 맞아서 돌려보내고 돈을 돌려받고 싶다
- 사용자 유형: 온라인 쇼핑몰 이용 경험이 적은 사용자
- 성공 조건: `/shop/refund` 화면에 '환불 / 반품 신청' 글자가 보이면 도착
- 결과: BLOCKED (REVISIT_LIMIT_REACHED), 목표 도달 False, 유효성 VALID
- 지나간 화면: home → support → home → support → home (단계 4, 되돌아가기 2)
- 찾은 Issue: CHOICE_AMBIGUITY, GOAL_NOT_REACHED, REPEATED_VISIT (분석 COMPLETED)
- 모델: trillionlabs/Zoro-1 · 서버 brev-l40s-bf16 · prompt v2 · temperature 1.52 · 출처 LIVE
- 시각: 2026-11-20T07:36:28.552+09:00 → 2026-11-20T07:36:38.336+09:00
- 파일: 20개 (스크린샷 8개, 원본 모델 요청·응답 8개)

## run_after_20261120073638_384e

- 목표: 지난주에 산 운동화가 발에 안 맞아서 돌려보내고 돈을 돌려받고 싶다
- 사용자 유형: 온라인 쇼핑몰 이용 경험이 적은 사용자
- 성공 조건: `/shop/refund` 화면에 '환불 / 반품 신청' 글자가 보이면 도착
- 결과: SUCCESS (GOAL_VERIFIED), 목표 도달 True, 유효성 VALID
- 지나간 화면: home → orders → refund (단계 2, 되돌아가기 0)
- 찾은 Issue: 없음 (분석 COMPLETED)
- 모델: trillionlabs/Zoro-1 · 서버 brev-l40s-bf16 · prompt v2 · temperature 1.52 · 출처 LIVE
- 시각: 2026-11-20T07:36:38.416+09:00 → 2026-11-20T07:36:45.237+09:00
- 파일: 13개 (스크린샷 5개, 원본 모델 요청·응답 4개)

## run_before_20261120073645_2357

- 목표: 받은 상품에 문제가 있어서 직원에게 직접 물어보고 싶다
- 사용자 유형: 온라인 쇼핑몰 이용 경험이 적은 사용자
- 성공 조건: `/shop/support` 화면에 '자주 묻는 질문' 글자가 보이면 도착
- 결과: SUCCESS (GOAL_VERIFIED), 목표 도달 True, 유효성 VALID
- 지나간 화면: home → support (단계 1, 되돌아가기 0)
- 찾은 Issue: 없음 (분석 COMPLETED)
- 모델: trillionlabs/Zoro-1 · 서버 brev-l40s-bf16 · prompt v2 · temperature 1.52 · 출처 LIVE
- 시각: 2026-11-20T07:36:45.363+09:00 → 2026-11-20T07:36:50.523+09:00
- 파일: 9개 (스크린샷 3개, 원본 모델 요청·응답 2개)

## run_after_20261120073650_bdec

- 목표: 받은 상품에 문제가 있어서 직원에게 직접 물어보고 싶다
- 사용자 유형: 온라인 쇼핑몰 이용 경험이 적은 사용자
- 성공 조건: `/shop/support` 화면에 '자주 묻는 질문' 글자가 보이면 도착
- 결과: SUCCESS (GOAL_VERIFIED), 목표 도달 True, 유효성 VALID
- 지나간 화면: home → support (단계 1, 되돌아가기 0)
- 찾은 Issue: CHOICE_AMBIGUITY (분석 COMPLETED)
- 모델: trillionlabs/Zoro-1 · 서버 brev-l40s-bf16 · prompt v2 · temperature 1.52 · 출처 LIVE
- 시각: 2026-11-20T07:36:50.600+09:00 → 2026-11-20T07:36:55.422+09:00
- 파일: 9개 (스크린샷 3개, 원본 모델 요청·응답 2개)

## run_before_20261120073657_8b5c

- 목표: 어제 산 물건이 지금 어디쯤 오고 있는지 알고 싶다
- 사용자 유형: 온라인 쇼핑몰 이용 경험이 적은 사용자
- 성공 조건: `/shop/orders` 화면에 '주문 / 배송 조회' 글자가 보이면 도착
- 결과: SUCCESS (GOAL_VERIFIED), 목표 도달 True, 유효성 VALID
- 지나간 화면: home → orders (단계 1, 되돌아가기 0)
- 찾은 Issue: 없음 (분석 COMPLETED)
- 모델: trillionlabs/Zoro-1 · 서버 brev-l40s-bf16 · prompt v2 · temperature 1.52 · 출처 LIVE
- 시각: 2026-11-20T07:36:57.843+09:00 → 2026-11-20T07:37:02.607+09:00
- 파일: 9개 (스크린샷 3개, 원본 모델 요청·응답 2개)

## Before/After 비교

- [comparisons/imp-live-20261120073424-5254-MAIN.json](comparisons/imp-live-20261120073424-5254-MAIN.json)
- [comparisons/imp-live-20261120073424-5254-S4.json](comparisons/imp-live-20261120073424-5254-S4.json)

## 파일 읽는 법

| 파일 | 내용 |
|---|---|
| `run.json` | Run 설정(목표·사용자 유형·성공 조건·모델·경로 기준)과 최종 요약 |
| `steps.jsonl` | 한 줄 = 한 단계. 화면, 클릭 후보, Zoro-1 후보별 확률, 고른 행동과 결과 |
| `events.jsonl` | 되돌아가기·도착·오류 같은 사건 |
| `issues.json` | 찾은 UX 문제와 근거 |
| `screenshots/` | `N.png` 원본 화면, `N_overlay.png` 후보별 확률 배지 |
| `raw/` | Zoro-1에 보낸 원본 요청과 받은 응답 |
