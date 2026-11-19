# 백엔드 서버 배포 (EC2, #108)

서버 한 대에 실행 API·브라우저(Chromium)·쇼핑몰 C0~C4를 같이 띄우고, 모델은 행사 GPU 서버를 쓴다.

```
Vercel FE (https) ──→ EC2: Caddy(443, HTTPS 자동) → uvicorn api.main:app (127.0.0.1:8000, 프로세스 1개)
                              ├ Chromium (Playwright, 화면 없이)
                              └ 쇼핑몰 C0~C4 (127.0.0.1:8001~8005, 밖에 안 열림)
                         API ──→ GPU 서버 serve_decide.py /v1/decide  (.env의 ZORO_BASE_URL)
```

- Vercel 페이지가 https라서 API도 https여야 한다 (http 호출은 브라우저가 막는다). 도메인이 없으면 `<공인 IP>.sslip.io` 주소로 Caddy가 인증서를 자동으로 받는다.
- 실행 API는 "한 번에 Run 하나"를 프로세스 안에서 지킨다 → uvicorn은 반드시 프로세스 1개(`--workers 1`).
- Run을 돌릴 때 브라우저가 쇼핑몰을 열어야 하므로 쇼핑몰도 같은 서버에서 띄운다.

## 1. EC2 준비

| 항목 | 값 |
|---|---|
| 이미지 | Ubuntu 24.04 LTS |
| 유형 | t3.large 이상 (메모리 8GB, Chromium) |
| 디스크 | 30GB |
| 공인 IP | **Elastic IP** 연결 (IP가 바뀌면 HTTPS 주소도 바뀜) |
| 보안 그룹 | 22: 아래 "수동 배포"를 쓰면 모두(키로만 접속), 서버에서 직접 배포만 하면 내 IP만 · 80·443: 모두 (인증서 발급·FE 접속). **8000~8005는 열지 않는다** |
| 사용자 | `ubuntu` (비밀번호 없이 `sudo` 가능한 EC2 기본 사용자. 스크립트가 sudo를 묻지 않고 쓴다) |

## 2. 설치

```bash
git clone https://github.com/UX-Mirror/UX-Mirror-BE.git && cd UX-Mirror-BE
```

```bash
./deploy/setup.sh
```

처음 실행하면 `.env`를 만들고 멈춘다. 아래 값을 채운 뒤 **같은 명령을 다시 실행**한다.

| 키 | 값 |
|---|---|
| `ZORO_BASE_URL` | GPU 서버 decide 주소 (아래 "GPU 연결") |
| `ZORO_ENDPOINT_ID`, `ZORO_TEMPERATURE`, `GOAL_JUDGMENT_MODE`, `ZORO_MODEL_VERSION` | **GPU 담당자 `.env`·E 실행 때와 같은 값.** 다르면 E 결과와 비교할 수 없고, 자동 개선 검증 함수도 실행을 거부한다 |
| `UXM_CORS_ORIGINS` | Vercel production·preview 주소를 쉼표로 (`https://...vercel.app`) |

두 번째 실행이 끝나면 `https://<공인 IP 점→대시>.sslip.io/api/health` 주소가 출력된다. 도메인이 있으면 `./deploy/setup.sh <도메인>`.

`setup.sh`가 하는 일: 패키지·Caddy 설치 → `.venv`에 `deploy/requirements-server.txt`(torch 없음) → Chromium → `uxm-shops`·`uxm-api` 서비스 등록·시작 → Caddy 설정(쓰기 잠금 꺼짐) → **실제로 Run을 돌릴 수 있는지 확인**: `/api/health`가 `READY`, 쇼핑몰 5개 응답, 바깥 HTTPS 주소 응답. 하나라도 안 되면 무엇이 안 되는지 출력하고 실패로 끝난다 (GPU가 꺼져 있으면 `model` 준비 안 됨).

다시 실행해도 된다. 이미 설치된 서버에서는 `update.sh`와 같이 쓰기 잠금 → 진행 중인 Run 확인(있으면 멈춤) → 재시작·확인 → 원래 잠금 상태로 되돌린다 (`./deploy/setup.sh "" --force`면 진행 중인 Run을 무시). 처음 설치는 돌고 있는 Run이 없으므로 잠금 없이 진행한다.

## 3. GPU 연결

API 서버가 GPU 서버의 `/v1/decide`에 닿기만 하면 된다. GPU 담당자가 지금 어떻게 붙는지에 따라:

- **GPU 쪽 포트가 공개돼 있으면**: `ZORO_BASE_URL=http://<GPU 주소>:<포트>`. GPU 쪽 방화벽은 EC2 Elastic IP만 받게 한다.
- **SSH로만 들어갈 수 있으면**: EC2에서 터널을 띄우고 `ZORO_BASE_URL=http://127.0.0.1:9000`.
  ```bash
  sudo apt-get install -y autossh
  ```
  ```bash
  autossh -M 0 -f -N -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -L 127.0.0.1:9000:127.0.0.1:<GPU decide 포트> <GPU SSH 접속 이름>
  ```
  SSH 키는 EC2의 `~/.ssh`에만 두고 저장소에 올리지 않는다.

GPU 주소가 바뀌면 `.env`의 `ZORO_BASE_URL`만 고치고 `sudo systemctl restart uxm-api`.

## 4. 프론트 연결 (Vercel)

Vercel 프로젝트 → Settings → Environment Variables:

- `VITE_API_BASE_URL` = `https://<위 주소>`
- `VITE_USE_MOCK` = `false`

저장 후 **재배포**해야 반영된다 (Vite는 빌드할 때 값을 넣는다).

## 5. 확인·운영

```bash
curl -s https://<위 주소>/api/health
```

`status: READY`면 모델·브라우저·루프가 준비된 것. `NOT_READY`면 `model.ready`(GPU 연결)부터 본다.

| 할 일 | 명령 |
|---|---|
| 로그 | `sudo journalctl -u uxm-api -f` / `-u uxm-shops` / `-u caddy` |
| 재시작 | `sudo systemctl restart uxm-api` |
| 코드 갱신 | `./deploy/update.sh` (또는 아래 수동 배포 workflow) |
| 결과 | 저장소의 `results/` (서버 디스크) |

- API에는 로그인이 없다. 주소를 아는 사람은 Run을 만들 수 있지만 한 번에 하나만 돈다(`409 RUN_ALREADY_ACTIVE`). 발표·실제 검증 시간에는 아래 쓰기 잠금을 켠다. 행사가 끝나면 인스턴스를 멈춘다.
- 자동 개선 검증 Run(`api/improvement.run_verification`)을 서버에서 돌리는 동안에는 쓰기 잠금을 켜고(FE Run 차단), 배포하지 않는다 (API 밖 프로세스라 배포의 잠금으로 막을 수 없다).

### 쓰기 잠금

```bash
./deploy/write_lock.sh on
```

```bash
./deploy/write_lock.sh off
```

- 켜면 Caddy가 **POST만** 막는다 (Run·Optimization 생성, 검증 시작 → `503 SERVER_MAINTENANCE`, `retryable: true`). 조회·비교·재생(GET)은 그대로. 응답에 CORS 헤더가 있어 FE가 메시지를 읽을 수 있다.
- `status`로 현재 상태를 본다. 상태는 파일이 아니라 실제 요청(`POST /api/health`, 아무것도 만들지 않음)으로 확인한다: 잠김 503(Caddy가 직접 답함, API가 꺼져 있어도 확인됨) / 풀림 405·502(요청이 API까지 감). Caddy가 응답하지 않으면 `unknown`이고, 켜기·끄기·배포는 실패로 멈춘다.
- 배포(`update.sh`)도 이 잠금을 쓴다. 운영자가 미리 켜 두었으면 배포 뒤에도 켜진 채로 둔다.
- 로그인이 아니라 전체 켜기/끄기 스위치다. 꺼져 있는 동안에는 누구나 Run을 만들 수 있다.

## 6. 코드 갱신 (수동 배포)

처음 설치(`setup.sh`)가 끝난 뒤의 코드 갱신. **머지할 때마다 자동으로 배포하지 않는다** — 재시작하면 진행 중인 Run이 끊기기 때문. 배포 전에 팀에 알린다.

- GitHub: Actions 탭 → **서버 배포 (수동)** → Run workflow → 브랜치(기본 develop). 그 브랜치로 pytest(서버와 같은 Python 3.12·고정 패키지) → 통과하면 EC2에 SSH로 `deploy/update.sh <브랜치> --sha <테스트한 커밋>`.
- 서버에서 직접: `./deploy/update.sh` (브랜치 생략 시 develop).

`update.sh` 순서 — 재시작 전에 멈추면 서버는 바뀌지 않는다.

1. **쓰기 잠금** on, 실제 요청으로 503을 확인했을 때만 진행 → 새 Run이 API로 못 들어온다. 5초 기다려 잠금 직전에 들어온 요청을 마저 받는다. 잠금 상태를 확인할 수 없으면(Caddy 응답 없음) 멈춘다.
2. **진행 중인 Run 확인** → 있으면 멈춤: `/api/health`의 `activeRun`·`activeOptimization`, API가 켜져 있는데 health가 응답 없음, 결과 폴더에서 최근 30분 안에 바뀐 READY·RUNNING Run(API 밖 스크립트 Run).
3. **받을 커밋 확인** → 서버 저장소에 커밋 안 된 수정, `--sha`와 지금 브랜치 커밋이 다름(테스트 뒤 새 커밋), fast-forward 불가면 멈춤. 그 커밋의 `deploy/requirements-server.txt`로 패키지를 **먼저** 설치.
4. **코드 전환 → 진행 중인 Run 한 번 더 확인**(있으면 코드를 되돌리고 멈춤) → 서비스 파일 다시 쓰기 → 쇼핑몰·API 재시작.
5. **실제로 Run을 돌릴 수 있는지**: `READY`, 쇼핑몰 5개, 바깥 HTTPS. 안 되면 무엇이 안 되는지와 되돌리는 명령을 출력하고 실패 (모델 서버 문제일 수 있어 자동으로 되돌리지 않는다).
6. **잠금 해제** (원래 켜져 있었으면 그대로). 풀지 못하면 실패.

- 두 배포가 동시에 돌지 않는다 (`flock`).
- 꼭 지금 배포해야 하면 `force`(서버에서는 `--force`)를 켠다. 진행 중인 Run은 끊긴다.
- **이전 버전 `setup.sh`로 설치한 서버**는 Caddy 설정에 쓰기 잠금이 없어 `update.sh`가 멈춘다. `./deploy/setup.sh`를 한 번 다시 실행한다.

### 처음 한 번: 배포 키와 Secrets

```bash
ssh-keygen -t ed25519 -N "" -C uxm-deploy -f uxm-deploy
```

- `uxm-deploy.pub` 내용을 EC2의 `~/.ssh/authorized_keys`에 한 줄 추가
- 저장소 Settings → Secrets and variables → Actions → New repository secret (저장소 관리자):

| 이름 | 값 |
|---|---|
| `EC2_HOST` | Elastic IP 또는 도메인 |
| `EC2_USER` | `ubuntu` |
| `EC2_SSH_KEY` | `uxm-deploy` (개인 키) 파일 전체 |
| `EC2_KNOWN_HOSTS` (권장) | `ssh-keyscan -H <EC2_HOST>` 출력 |

- 키 파일 두 개는 Secrets에 넣은 뒤 PC에서 지운다. 저장소에는 절대 올리지 않는다.
- 보안 그룹 22번을 "내 IP만"으로 막았으면 GitHub Actions가 접속하지 못한다. 배포할 때만 22번을 열거나, 배포는 서버에서 `./deploy/update.sh`로 직접 한다.
