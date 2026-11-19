# 에이전트 실행 로그

UX MIRROR 에이전트가 실제로 돌린 Run 기록이다. Run마다 폴더 하나이고, 모든 클릭 판단은 Zoro-1이 낸 후보별 확률로 정했다.

| Run | 실험 키 | 종류 | 목표 | 결과 | 지나간 화면 | 단계 | 찾은 Issue |
|---|---|---|---|---|---|---|---|
| [run_before_20261119152427_7646](run_before_20261119152427_7646/) | e-s1-1119 | 버전 비교 (before) | 지난주에 산 운동화가 발에 안 맞아서 돌려보내고 돈을 돌려받고 싶다 | BLOCKED · REVISIT_LIMIT_REACHED · VALID | home → support → home → support → home | 4 | CHOICE_AMBIGUITY, GOAL_NOT_REACHED, REPEATED_VISIT |
| [run_after_20261119152445_52ce](run_after_20261119152445_52ce/) | e-s1-1119 | 버전 비교 (after) | 지난주에 산 운동화가 발에 안 맞아서 돌려보내고 돈을 돌려받고 싶다 | SUCCESS · GOAL_VERIFIED · VALID | home → orders → refund | 2 | - |
| [run_before_20261119152716_6342](run_before_20261119152716_6342/) | e-s2-1119 | 버전 비교 (before) | 어제 산 물건이 지금 어디쯤 오고 있는지 알고 싶다 | SUCCESS · GOAL_VERIFIED · VALID | home → orders | 1 | - |
| [run_after_20261119152734_aab5](run_after_20261119152734_aab5/) | e-s2-1119 | 버전 비교 (after) | 어제 산 물건이 지금 어디쯤 오고 있는지 알고 싶다 | SUCCESS · GOAL_VERIFIED · VALID | home → orders | 1 | - |
| [run_before_20261119152752_d372](run_before_20261119152752_d372/) | e-s3-1119 | 버전 비교 (before) | 내가 가진 할인 쿠폰이 몇 장 남았는지 보고 싶다 | SUCCESS · GOAL_VERIFIED · VALID | home → mypage | 1 | - |
| [run_after_20261119152804_2323](run_after_20261119152804_2323/) | e-s3-1119 | 버전 비교 (after) | 내가 가진 할인 쿠폰이 몇 장 남았는지 보고 싶다 | SUCCESS · GOAL_VERIFIED · VALID | home → mypage | 1 | - |
| [run_before_20261119152824_99ae](run_before_20261119152824_99ae/) | e-s4-1119 | 버전 비교 (before) | 받은 상품에 문제가 있어서 직원에게 직접 물어보고 싶다 | SUCCESS · GOAL_VERIFIED · VALID | home → support | 1 | - |
| [run_after_20261119152842_443f](run_after_20261119152842_443f/) | e-s4-1119 | 버전 비교 (after) | 받은 상품에 문제가 있어서 직원에게 직접 물어보고 싶다 | SUCCESS · GOAL_VERIFIED · VALID | home → support | 1 | CHOICE_AMBIGUITY |

## run_before_20261119152427_7646

- 목표: 지난주에 산 운동화가 발에 안 맞아서 돌려보내고 돈을 돌려받고 싶다
- 사용자 유형: 온라인 쇼핑몰 이용 경험이 적은 사용자
- 성공 조건: `/shop/refund` 화면에 '환불 / 반품 신청' 글자가 보이면 도착
- 결과: BLOCKED (REVISIT_LIMIT_REACHED), 목표 도달 False, 유효성 VALID
- 지나간 화면: home → support → home → support → home (단계 4, 되돌아가기 2)
- 찾은 Issue: CHOICE_AMBIGUITY, GOAL_NOT_REACHED, REPEATED_VISIT (분석 COMPLETED)
- 모델: trillionlabs/Zoro-1 · 서버 brev-l40s-bf16 · prompt v2 · temperature 1.52 · 출처 LIVE
- 시각: 2026-11-19T15:24:27.554+09:00 → 2026-11-19T15:24:37.625+09:00
- 파일: 20개 (스크린샷 8개, 원본 모델 요청·응답 8개)

## run_after_20261119152445_52ce

- 목표: 지난주에 산 운동화가 발에 안 맞아서 돌려보내고 돈을 돌려받고 싶다
- 사용자 유형: 온라인 쇼핑몰 이용 경험이 적은 사용자
- 성공 조건: `/shop/refund` 화면에 '환불 / 반품 신청' 글자가 보이면 도착
- 결과: SUCCESS (GOAL_VERIFIED), 목표 도달 True, 유효성 VALID
- 지나간 화면: home → orders → refund (단계 2, 되돌아가기 0)
- 찾은 Issue: 없음 (분석 COMPLETED)
- 모델: trillionlabs/Zoro-1 · 서버 brev-l40s-bf16 · prompt v2 · temperature 1.52 · 출처 LIVE
- 시각: 2026-11-19T15:24:45.442+09:00 → 2026-11-19T15:24:51.921+09:00
- 파일: 13개 (스크린샷 5개, 원본 모델 요청·응답 4개)

## run_before_20261119152716_6342

- 목표: 어제 산 물건이 지금 어디쯤 오고 있는지 알고 싶다
- 사용자 유형: 온라인 쇼핑몰 이용 경험이 적은 사용자
- 성공 조건: `/shop/orders` 화면에 '주문 / 배송 조회' 글자가 보이면 도착
- 결과: SUCCESS (GOAL_VERIFIED), 목표 도달 True, 유효성 VALID
- 지나간 화면: home → orders (단계 1, 되돌아가기 0)
- 찾은 Issue: 없음 (분석 COMPLETED)
- 모델: trillionlabs/Zoro-1 · 서버 brev-l40s-bf16 · prompt v2 · temperature 1.52 · 출처 LIVE
- 시각: 2026-11-19T15:27:16.637+09:00 → 2026-11-19T15:27:22.405+09:00
- 파일: 9개 (스크린샷 3개, 원본 모델 요청·응답 2개)

## run_after_20261119152734_aab5

- 목표: 어제 산 물건이 지금 어디쯤 오고 있는지 알고 싶다
- 사용자 유형: 온라인 쇼핑몰 이용 경험이 적은 사용자
- 성공 조건: `/shop/orders` 화면에 '주문 / 배송 조회' 글자가 보이면 도착
- 결과: SUCCESS (GOAL_VERIFIED), 목표 도달 True, 유효성 VALID
- 지나간 화면: home → orders (단계 1, 되돌아가기 0)
- 찾은 Issue: 없음 (분석 COMPLETED)
- 모델: trillionlabs/Zoro-1 · 서버 brev-l40s-bf16 · prompt v2 · temperature 1.52 · 출처 LIVE
- 시각: 2026-11-19T15:27:34.396+09:00 → 2026-11-19T15:27:39.247+09:00
- 파일: 9개 (스크린샷 3개, 원본 모델 요청·응답 2개)

## run_before_20261119152752_d372

- 목표: 내가 가진 할인 쿠폰이 몇 장 남았는지 보고 싶다
- 사용자 유형: 온라인 쇼핑몰 이용 경험이 적은 사용자
- 성공 조건: `/shop/mypage` 화면에 '사용 가능 쿠폰' 글자가 보이면 도착
- 결과: SUCCESS (GOAL_VERIFIED), 목표 도달 True, 유효성 VALID
- 지나간 화면: home → mypage (단계 1, 되돌아가기 0)
- 찾은 Issue: 없음 (분석 COMPLETED)
- 모델: trillionlabs/Zoro-1 · 서버 brev-l40s-bf16 · prompt v2 · temperature 1.52 · 출처 LIVE
- 시각: 2026-11-19T15:27:52.061+09:00 → 2026-11-19T15:27:56.796+09:00
- 파일: 9개 (스크린샷 3개, 원본 모델 요청·응답 2개)

## run_after_20261119152804_2323

- 목표: 내가 가진 할인 쿠폰이 몇 장 남았는지 보고 싶다
- 사용자 유형: 온라인 쇼핑몰 이용 경험이 적은 사용자
- 성공 조건: `/shop/mypage` 화면에 '사용 가능 쿠폰' 글자가 보이면 도착
- 결과: SUCCESS (GOAL_VERIFIED), 목표 도달 True, 유효성 VALID
- 지나간 화면: home → mypage (단계 1, 되돌아가기 0)
- 찾은 Issue: 없음 (분석 COMPLETED)
- 모델: trillionlabs/Zoro-1 · 서버 brev-l40s-bf16 · prompt v2 · temperature 1.52 · 출처 LIVE
- 시각: 2026-11-19T15:28:04.910+09:00 → 2026-11-19T15:28:10.171+09:00
- 파일: 9개 (스크린샷 3개, 원본 모델 요청·응답 2개)

## run_before_20261119152824_99ae

- 목표: 받은 상품에 문제가 있어서 직원에게 직접 물어보고 싶다
- 사용자 유형: 온라인 쇼핑몰 이용 경험이 적은 사용자
- 성공 조건: `/shop/support` 화면에 '자주 묻는 질문' 글자가 보이면 도착
- 결과: SUCCESS (GOAL_VERIFIED), 목표 도달 True, 유효성 VALID
- 지나간 화면: home → support (단계 1, 되돌아가기 0)
- 찾은 Issue: 없음 (분석 COMPLETED)
- 모델: trillionlabs/Zoro-1 · 서버 brev-l40s-bf16 · prompt v2 · temperature 1.52 · 출처 LIVE
- 시각: 2026-11-19T15:28:24.792+09:00 → 2026-11-19T15:28:30.980+09:00
- 파일: 9개 (스크린샷 3개, 원본 모델 요청·응답 2개)

## run_after_20261119152842_443f

- 목표: 받은 상품에 문제가 있어서 직원에게 직접 물어보고 싶다
- 사용자 유형: 온라인 쇼핑몰 이용 경험이 적은 사용자
- 성공 조건: `/shop/support` 화면에 '자주 묻는 질문' 글자가 보이면 도착
- 결과: SUCCESS (GOAL_VERIFIED), 목표 도달 True, 유효성 VALID
- 지나간 화면: home → support (단계 1, 되돌아가기 0)
- 찾은 Issue: CHOICE_AMBIGUITY (분석 COMPLETED)
- 모델: trillionlabs/Zoro-1 · 서버 brev-l40s-bf16 · prompt v2 · temperature 1.52 · 출처 LIVE
- 시각: 2026-11-19T15:28:42.592+09:00 → 2026-11-19T15:28:47.570+09:00
- 파일: 9개 (스크린샷 3개, 원본 모델 요청·응답 2개)

## Before/After 비교

- [comparisons/e-s1-1119.json](comparisons/e-s1-1119.json)
- [comparisons/e-s2-1119.json](comparisons/e-s2-1119.json)
- [comparisons/e-s3-1119.json](comparisons/e-s3-1119.json)
- [comparisons/e-s4-1119.json](comparisons/e-s4-1119.json)

## 파일 읽는 법

| 파일 | 내용 |
|---|---|
| `run.json` | Run 설정(목표·사용자 유형·성공 조건·모델·경로 기준)과 최종 요약 |
| `steps.jsonl` | 한 줄 = 한 단계. 화면, 클릭 후보, Zoro-1 후보별 확률, 고른 행동과 결과 |
| `events.jsonl` | 되돌아가기·도착·오류 같은 사건 |
| `issues.json` | 찾은 UX 문제와 근거 |
| `screenshots/` | `N.png` 원본 화면, `N_overlay.png` 후보별 확률 배지 |
| `raw/` | Zoro-1에 보낸 원본 요청과 받은 응답 |
