# 🚀 실시간 BESS 모니터링 시스템 가이드

## 📊 아키텍처

```
┌──────────────────────────────┐
│  Realtime Engine             │
│  (realtime_engine.py)        │
│  - 1초마다 BESS 제어 실행   │
│  - SQLite에 저장             │
└──────────────┬───────────────┘
               │
               ↓
┌──────────────────────────────┐
│  API 서버                    │
│  (web_app_realtime.py)       │
│  - /api/status (최신)        │
│  - /api/history (과거)       │
│  - WebSocket 실시간 전송     │
└──────────────┬───────────────┘
               │
               ↓
┌──────────────────────────────┐
│  웹 대시보드                 │
│  (dashboard_rt.html)         │
│  - 1초 주기 실시간 갱신     │
│  - 완전 실시간 모니터링     │
└──────────────────────────────┘
```

---

## 🎯 핵심 특징

✅ **완전 실시간 (1초 주기)**
- BESS 제어가 1초마다 실행
- 데이터가 즉시 저장
- 웹에서 1초마다 갱신

✅ **24시간 연속 운영**
- realtime_engine이 계속 실행
- 자동 순환 (데이터 끝나면 처음부터)
- Pi4에서 24/7 운영 가능

✅ **SQLite 기반 저장**
- CSV 파일 불필요
- 빠른 조회
- 히스토리 관리 용이

---

## 🚀 사용 방법

### Step 1: 파일 준비

outputs에서 받은 파일:
- `realtime_engine.py` → `Bess_Project/`
- `web_app_realtime.py` → `Bess_Project/`
- `dashboard_rt.html` → `Bess_Project/templates/`

### Step 2: 터미널 2개 열기

#### 터미널 1️⃣ (실시간 제어 엔진)

```bash
cd Bess_Project
python realtime_engine.py
```

**출력:**
```
[실시간 엔진] 초기화 완료
[데이터] 로드 완료: 1256 행
[DB] 초기화 완료: realtime_data/realtime.db
[실시간 엔진] 시작...
[진행] 10시점 - SOC: 45.2% | BESS: -15.5kW | 부하: 52.3kW
[진행] 20시점 - SOC: 46.1% | BESS: 12.0kW | 부하: 48.7kW
...
```

#### 터미널 2️⃣ (API 서버)

```bash
cd Bess_Project
python web_app_realtime.py
```

**출력:**
```
[경로] DB 존재: True
[DB] 연결 성공
[시작] 실시간 업데이트 스레드 시작
[서버] http://0.0.0.0:5000 에서 실행 중...
```

### Step 3: 브라우저에서 접속

```
http://localhost:5000
```

**실시간 데이터가 1초마다 갱신됩니다!** ⚡

---

## 📊 데이터 흐름

```
초당 1회:
  ├─ realtime_engine이 BESS 제어 실행
  ├─ SQLite에 저장
  ├─ web_app_realtime이 감지
  ├─ WebSocket으로 브라우저 전송
  └─ 대시보드 1초마다 갱신
```

---

## 💾 데이터베이스

### 위치
```
Bess_Project/realtime_data/realtime.db
```

### 스키마
```sql
CREATE TABLE realtime (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,
    hour INTEGER,
    load_kw REAL,
    solar_kw REAL,
    soc REAL,
    bess_power_kw REAL,
    charge_kw REAL,
    discharge_kw REAL,
    grid_power_kw REAL,
    tariff_rate REAL,
    tariff_period TEXT,
    action TEXT,
    created_at TIMESTAMP
)
```

### 데이터 조회

```bash
# SQLite 접속
sqlite3 realtime_data/realtime.db

# 최신 데이터 보기
SELECT * FROM realtime ORDER BY id DESC LIMIT 10;

# 시간대별 통계
SELECT hour, AVG(soc)*100, AVG(bess_power_kw), COUNT(*) 
FROM realtime 
GROUP BY hour 
ORDER BY hour;

# 종료
.quit
```

---

## 🔧 설정 변경

### 엔진 매개변수 (realtime_engine.py)

```python
class RealtimeEngine:
    def run(self):
        while self.running:
            # ... 제어 로직 ...
            time.sleep(1)  # ← 1초 주기 (조절 가능)
            
            if self.current_idx % 10 == 0:  # ← 10초마다 출력
                print(f"[진행] ...")
```

### 차트 갱신 주기 (dashboard_rt.html)

```javascript
setInterval(updateChart, 10000);  // ← 10초마다 갱신 (조절 가능)
```

### API 폴백 주기 (dashboard_rt.html)

```javascript
setInterval(() => {
    fetch('/api/status')
        .then(r => r.json())
        .then(data => updateMetrics(data));
}, 5000);  // ← 5초마다 (조절 가능)
```

---

## 🖥️ Pi4에 배포

### 자동 시작 설정

#### systemd 서비스 (터미널 1️⃣ - 엔진)

```bash
sudo nano /etc/systemd/system/bess-engine.service
```

내용:
```ini
[Unit]
Description=BESS Realtime Engine
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/Bess_Project
ExecStart=/usr/bin/python3 /home/pi/Bess_Project/realtime_engine.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

#### systemd 서비스 (터미널 2️⃣ - API)

```bash
sudo nano /etc/systemd/system/bess-api.service
```

내용:
```ini
[Unit]
Description=BESS Realtime API Server
After=network.target bess-engine.service

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/Bess_Project
ExecStart=/usr/bin/python3 /home/pi/Bess_Project/web_app_realtime.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### 등록 및 시작

```bash
# 활성화
sudo systemctl enable bess-engine
sudo systemctl enable bess-api

# 시작
sudo systemctl start bess-engine
sudo systemctl start bess-api

# 상태 확인
sudo systemctl status bess-engine
sudo systemctl status bess-api

# 실시간 로그
sudo journalctl -u bess-engine -f
sudo journalctl -u bess-api -f
```

---

## 📈 모니터링

### 엔진 상태 확인

```bash
# 프로세스 확인
ps aux | grep realtime_engine

# 로그 확인
sudo journalctl -u bess-engine -f

# 데이터 저장 확인
sqlite3 realtime_data/realtime.db "SELECT COUNT(*) FROM realtime;"
```

### API 서버 상태 확인

```bash
# 포트 확인
netstat -tuln | grep 5000

# 요청 테스트
curl http://localhost:5000/api/status

# 로그 확인
sudo journalctl -u bess-api -f
```

---

## 🆘 문제 해결

### 1. 엔진이 시작 안 됨

```bash
# 에러 확인
python realtime_engine.py

# 일반적인 원인
- DL_LSTM 폴더 경로 확인
- 데이터 파일 확인
- Python 패키지 설치 확인
```

### 2. API가 데이터를 못 읽음

```bash
# DB 파일 확인
ls -la realtime_data/

# DB 손상 시
rm realtime_data/realtime.db
# 엔진 재시작 (자동 생성됨)
```

### 3. 웹이 느림

```bash
# DB 최적화
sqlite3 realtime_data/realtime.db "VACUUM;"
sqlite3 realtime_data/realtime.db "ANALYZE;"

# 오래된 데이터 정리
sqlite3 realtime_data/realtime.db "DELETE FROM realtime WHERE id < 100000;"
```

### 4. 메모리 누수

```bash
# 프로세스 재시작
sudo systemctl restart bess-engine
sudo systemctl restart bess-api

# 또는 수동 재시작
pkill -f realtime_engine.py
python realtime_engine.py
```

---

## 📊 성능 최적화

### DB 쿼리 최적화

```bash
# 인덱스 생성 (속도 향상)
sqlite3 realtime_data/realtime.db
CREATE INDEX idx_timestamp ON realtime(timestamp);
CREATE INDEX idx_hour ON realtime(hour);
.quit
```

### 데이터 보관 정책

```python
# 1주일만 유지 (공간 절약)
DELETE FROM realtime 
WHERE created_at < datetime('now', '-7 days');
```

---

## 🎯 완성!

이제 **24시간 계속 실행되는 완전 실시간 BESS 모니터링 시스템**이 완성됐습니다! 🎉

**특징:**
- ✅ 1초 주기 실시간 데이터
- ✅ 24/7 연속 운영
- ✅ 완전 자동화 (Pi4)
- ✅ 클라우드 배포 가능
- ✅ 1초마다 대시보드 갱신

