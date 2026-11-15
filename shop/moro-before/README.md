# MORO MARKET — Before (기본)

상단 메뉴 라벨 `이용정보`. After(`shop\moro-after`)와 이 라벨 하나만 다르다.

```
cd C:\workspace\uxmirror\shop\moro-before
py -3.11 server.py                 # http://127.0.0.1:8001/shop/home
py -3.11 server.py --host 0.0.0.0  # 노트북 등 다른 PC에서 접속
```

| 주소 | 화면 |
|---|---|
| `/shop/home` | 홈 |
| `/shop/mypage` | 마이페이지 (`회원 탈퇴` 버튼 포함) |
| `/shop/support` | 고객센터 |
| `/shop/orders` | 주문/배송 조회 |
| `/shop/refund` | 환불 신청 (목표 화면: 주소 + 제목 `환불 / 반품 신청`으로 판정) |

**직접 수정 금지.** `pages/`와 `server.py`는 `scripts\build_shop.py`가 `shop\stitch_moro_market`(Stitch 시안)에서 생성한다.
고칠 게 있으면 시안이나 빌드 스크립트를 고치고 다시 생성한다. 생성할 때 Before/After 차이를 자동 검사한다.
