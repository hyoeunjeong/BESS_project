# BESS 실시간 모니터링 시스템

LSTM 기반 BESS(Battery Energy Storage System) 충방전 제어 + 실시간 웹 대시보드

## 프로젝트 개요

이 프로젝트는 국내 BESS 산업 표준 운영 방식(피크 컷 + 재생에너지 연계 하이브리드)을 기반으로 한 실시간 BESS 모니터링 시스템입니다.

### 주요 기능

- **실시간 BESS 제어**: 1초 주기로 충방전 의사결정
- **하이브리드 데이터 소스**: 한전 API + 분 단위 시뮬레이션
- **웹 대시보드**: 12개 박스 + 차트 + 상세 모달
- **Rule-Based vs LSTM 비교**: 시간별 성능 분석
- **Raspberry Pi 4 배포**: 7인치 터치 디스플레이 지원

## 시스템 구성

```
[Raspberry Pi 4]
  ├─ realtime_engine.py   (BESS 제어 엔진)
  ├─ web_app_realtime.py  (Flask 웹 서버)
  └─ Chromium (전체화면)  (터치 디스플레이)
       ↓
  [Cloudflare Tunnel]
       ↓
  [외부 접속 가능]
```

## 폴더 구조

```
Bess_Project/
├── .env                    # API 키 (Git 제외)
├── .env.example            # API 키 템플릿
├── .gitignore
├── README.md
├── requirements_web.txt
├── web_app_realtime.py     # Flask 서버
├── realtime_engine.py      # BESS 제어 엔진
├── bess_controller.py      # 제어 알고리즘 (산업 표준)
├── api_client.py           # API 호출
├── diagnose_bess.py        # 진단 도구
├── check_data.py           # 데이터 확인 도구
├── DL_LSTM/                # LSTM 시뮬레이션
│   ├── config.py
│   ├── data_loader.py
│   └── ...
└── realtime_data/          # SQLite DB (Git 제외)
    ├── realtime.db
    └── comparison.db
```

## 설치 방법

### 1. 코드 다운로드

```bash
git clone https://github.com/onyx4519/bess_project.git
cd bess_project
```

### 2. Python 패키지 설치

```bash
pip install -r requirements_web.txt
```

### 3. API 키 설정

`.env.example`을 복사해서 `.env`로 만들고 실제 키 입력:

```bash
cp .env.example .env
# .env 파일 열어서 API 키 입력
```

필요한 API:
- 한전 ODcloud API (https://www.data.go.kr)
- 기상청 ASOS API (https://apihub.kma.go.kr)
- 전력거래소 SMP API (https://www.data.go.kr)

### 4. 실행

터미널 2개를 사용:

```bash
# 터미널 1: BESS 제어 엔진
python realtime_engine.py

# 터미널 2: 웹 서버
python web_app_realtime.py
```

브라우저에서 접속: http://localhost:5000

## 제어 알고리즘

국내 BESS 산업 표준 (피크 컷 + 재생에너지 연계 하이브리드)

### 시간대별 SOC 목표

| 시간대 | 시간 | 요금 | SOC 목표 | 출력 |
|--------|------|------|---------|------|
| 경부하 | 23-08시 | 60원/kWh | 90% | 80% |
| 중부하 | 9, 12, 17-22시 | 110원/kWh | 60% | 50% |
| 최대부하 | 10-11, 13-16시 | 180원/kWh | 20% | 100% |

### 제어 우선순위

1. **P0 비상 보호**: SOC 임계 시 (15% 미만/85% 초과)
2. **P1 태양광 잉여**: 자가발전 우선 충전
3. **P2 피크 컷**: 부하 임계값 초과 시 즉시 방전
4. **P3 SOC 목표 추종**: 시간대별 목표값 추종

## Pi4 배포

자세한 설치는 [PI4_SETUP_GUIDE.md](PI4_SETUP_GUIDE.md) 참조

## 관련 파일

- `REALTIME_SYSTEM_GUIDE.md`: 실시간 시스템 가이드
- `PI4_SETUP_GUIDE.md`: Pi4 배포 가이드

## 라이선스

개인/학술 연구용

## 작성자

- 학부 졸업논문 프로젝트
- GitHub: [@onyx4519](https://github.com/onyx4519)
