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
| 보안 그룹 | 22: 내 IP만 · 80·443: 모두 (인증서 발급·FE 접속). **8000~8005는 열지 않는다** |

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

`setup.sh`가 하는 일: 패키지·Caddy 설치 → `.venv`에 `deploy/requirements-server.txt`(torch 없음) → Chromium → `uxm-shops`·`uxm-api` 서비스 등록·시작 → Caddy 설정 → 내부 `/api/health` 확인.

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

- API에는 로그인이 없다. 주소를 아는 사람은 Run을 만들 수 있지만 한 번에 하나만 돈다(`409 RUN_ALREADY_ACTIVE`). 행사가 끝나면 인스턴스를 멈춘다.
- 자동 개선 검증 Run(`api/improvement.run_verification`)을 돌리는 동안에는 FE에서 Run을 만들지 않는다 (서로 다른 프로세스라 겹칠 수 있음).

## 6. 수동 배포 (GitHub Actions)

처음 설치(`setup.sh`)가 끝난 뒤의 코드 갱신은 GitHub에서 버튼으로 한다. **머지할 때마다 자동으로 배포하지 않는다** — 재시작하면 진행 중인 Run이 끊기기 때문.

Actions 탭 → **서버 배포 (수동)** → Run workflow → 브랜치(기본 develop) 선택.

1. 그 브랜치로 전체 테스트(pytest)
2. 통과하면 EC2에 SSH로 접속해 `deploy/update.sh <브랜치>` 실행
   - 진행 중인 Run이 있으면 **아무것도 바꾸지 않고 실패**로 끝난다: `/api/health`의 `activeRun`·`activeOptimization`, 그리고 결과 폴더에서 최근 30분 안에 바뀐 READY·RUNNING Run(자동 개선 검증 Run처럼 API 밖에서 도는 Run)
   - 서버 저장소에 커밋 안 된 수정이 있어도 멈춘다
   - `git pull`(fast-forward만) → 패키지 → 서비스 파일 다시 쓰기 → 쇼핑몰·API 재시작 → `/api/health` 출력
3. 꼭 지금 배포해야 하면 `force`를 켠다 (진행 중인 Run은 끊긴다)

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
