# Pi4 + 웹 대시보드 설치 가이드

## 시스템 요구사항

- **Raspberry Pi 4** (4GB 이상 권장)
- **OS**: Raspberry Pi OS Lite 또는 Desktop
- **Python**: 3.8 이상
- **터치디스플레이** (HDMI 연결, 선택사항)

---

## 1단계: Pi4에 필요한 라이브러리 설치

```bash
# 시스템 패키지 업데이트
sudo apt update && sudo apt upgrade -y

# Python 개발 패키지
sudo apt install -y python3-pip python3-dev python3-venv

# Pi4의 경우 numpy 미리 컴파일된 버전 설치 (속도 향상)
sudo apt install -y python3-numpy python3-pandas
```

## 2단계: 프로젝트 폴더 정리

```
Bess_Project/
├── DL_LSTM/                    # LSTM 프로젝트
│   ├── main.py                # ← 시뮬레이션 실행
│   ├── config.py
│   ├── evaluator.py
│   ├── requirements.txt
│   └── results/
│       └── lstm_simulation_result.csv   # ← 웹앱이 읽음
│
├── web_app.py                 # ← 웹서버 실행 파일
├── requirements_web.txt       # ← 웹 라이브러리
└── templates/
    └── dashboard.html         # ← UI 파일
```

> 위 구조는 Pi4 배포용 평면 레이아웃이며, 저장소 구조(`web_dashboard/`)와 다르다. 배포 시 파일을 평면으로 복사한다.

## 3단계: 웹 라이브러리 설치

```bash
cd Bess_Project

# 가상환경 생성 (선택사항이지만 권장)
python3 -m venv venv
source venv/bin/activate

# 웹 라이브러리 설치
pip install -r requirements_web.txt
```

## 4단계: 웹 대시보드 실행 (수동)

```bash
cd Bess_Project
python3 web_app.py
```

**출력 예시:**
```
============================================================
  BESS 실시간 모니터링 웹 대시보드
============================================================
[DB] 초기화 완료: results/bess_data.db
[시작] 실시간 업데이트 스레드 시작

[서버] http://0.0.0.0:5000 에서 실행 중...
[접속] http://localhost:5000 (로컬)
[접속] http://<Pi4 IP>:5000 (원격)
```

## 5단계: 웹 브라우저에서 접속

### 옵션 A: 같은 Pi4에서
```
http://localhost:5000
```

### 옵션 B: 다른 기기에서 (PC/폰/태블릿)
```
http://<Pi4 IP주소>:5000
```

Pi4 IP 확인:
```bash
hostname -I
```
예: `192.168.1.100:5000`

---

## 6단계: 자동 시작 설정 (systemd)

Pi4가 부팅될 때 자동으로 웹서버 시작하려면:

### 6-1. 서비스 파일 생성

```bash
sudo nano /etc/systemd/system/bess-web.service
```

다음 내용 입력 (경로는 본인 상황에 맞게 수정):

```ini
[Unit]
Description=BESS Real-time Monitoring Web Dashboard
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/Bess_Project
ExecStart=/usr/bin/python3 /home/pi/Bess_Project/web_app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 6-2. 서비스 활성화

```bash
sudo systemctl daemon-reload
sudo systemctl enable bess-web
sudo systemctl start bess-web

# 상태 확인
sudo systemctl status bess-web
```

### 6-3. 로그 확인

```bash
sudo journalctl -u bess-web -f
```

---

## 7단계: Pi4 터치디스플레이 자동 실행 (선택)

Pi4가 부팅될 때 Chromium에서 자동으로 대시보드를 띄우려면:

### 7-1. Chromium 설치

```bash
sudo apt install -y chromium-browser
```

### 7-2. 자동 시작 스크립트

```bash
mkdir -p ~/.config/autostart
nano ~/.config/autostart/bess-dashboard.desktop
```

다음 내용 입력:

```ini
[Desktop Entry]
Type=Application
Name=BESS Dashboard
Exec=chromium-browser --kiosk --no-first-run http://localhost:5000
X-GNOME-Autostart-enabled=true
```

저장 후 (`Ctrl+O`, `Enter`, `Ctrl+X`):

```bash
chmod +x ~/.config/autostart/bess-dashboard.desktop
```

### 7-3. 재부팅 테스트

```bash
sudo reboot
```

부팅 후 자동으로 웹 대시보드가 전체화면으로 띄워집니다.

---

## 8단계: 매일 자동으로 시뮬레이션 실행 (선택)

매일 새벽 2시에 LSTM 시뮬레이션을 자동 실행하려면:

```bash
crontab -e
```

다음 라인 추가:

```
0 2 * * * cd /home/pi/Bess_Project/DL_LSTM && python3 main.py >> /tmp/bess_sim.log 2>&1
```

---

## 시스템 아키텍처

```
┌─────────────────────────────────────────────────┐
│  Raspberry Pi 4                                 │
├─────────────────────────────────────────────────┤
│                                                 │
│  매일 새벽 2시:                                │
│  └─ main.py (LSTM 시뮬레이션)                  │
│     └─ results/lstm_simulation_result.csv      │
│                                                 │
│  24/7 실행:                                    │
│  └─ web_app.py (Flask 웹서버)                  │
│     ├─ CSV 읽기                                │
│     ├─ SQLite DB 저장                         │
│     ├─ WebSocket 실시간 스트림                │
│     └─ REST API 제공                          │
│                                                 │
│  터치디스플레이:                               │
│  └─ Chromium (전체화면)                       │
│     └─ http://localhost:5000                  │
│                                                 │
└─────────────────────────────────────────────────┘
           ↓ (네트워크)
┌─────────────────────────────────────────────────┐
│  PC / 폰 / 태블릿                              │
├─────────────────────────────────────────────────┤
│  웹브라우저                                     │
│  http://192.168.1.100:5000                    │
│  (어디서든 접속 가능)                          │
└─────────────────────────────────────────────────┘
```

---

## 트러블슈팅

### Q1: "포트 5000이 이미 사용 중입니다" 오류

```bash
# 포트 5000 사용 중인 프로세스 종료
sudo lsof -i :5000
sudo kill -9 <PID>

# 또는 web_app.py에서 포트 변경
# app 실행 부분에서 port=5000 → port=8080
```

### Q2: CSV 파일을 못 찾습니다

```bash
# CSV 파일이 있는지 확인
ls -la Bess_Project/results/

# 없으면 main.py를 한 번 실행해서 생성
cd Bess_Project/DL_LSTM
python3 main.py
```

### Q3: 웹 대시보드가 느려요

Pi4는 사양이 제한적이므로:
- 브라우저 탭을 많이 열지 않기
- 다른 무거운 프로그램 종료
- Python `debug=False` 확인

### Q4: 터치가 반응이 없어요

Chromium 키오스크 모드에서:
- 왼쪽 상단: 마우스 우클릭으로 메뉴 열기
- 강제 종료: `Alt+F4`
- 새로고침: `F5`

---

## 성능 팁

### Pi4 성능 향상

```bash
# GPU 메모리 할당 증가
sudo raspi-config
# Advanced Options → GPU Memory → 256MB

# CPU 클럭 오버클럭 (선택)
sudo nano /boot/config.txt
# arm_freq=2000
# gpu_freq=600
```

### 웹 대시보드 최적화

- 브라우저 캐시 활성화 (배포용)
- 차트 업데이트 간격 조정 (기본 3초)
- WebSocket 대신 REST API 폴링 옵션

---

## 다음 단계

1. ✅ **현재**: 웹 대시보드 구축
2. **다음**: 클라우드 백업 (Google Drive, AWS S3)
3. **다음**: 데이터 분석 대시보드 (Grafana)
4. **다음**: 모바일 앱 (PWA)

---

## 문제 해결 로그 수집

문제 발생 시 다음 정보를 수집해주세요:

```bash
# 웹서버 로그
sudo journalctl -u bess-web -n 50

# 시스템 리소스 사용율
top -b -n 1 | head -20

# 디스크 사용율
df -h

# Python 버전
python3 --version

# 네트워크 상태
ip addr show
```

성공하셨나요? 혹은 문제가 있으면 위 정보와 함께 알려주세요!
