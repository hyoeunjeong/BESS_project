# 딥러닝 순부하 예측 기반 BESS 운영 성능 분석

태양광 연계 **BESS(배터리 에너지 저장장치)**를 세 가지 제어 방식으로 시뮬레이션하고,
경제성·에너지 효율·운영 안정성·예측 정확도를 비교하는 학사학위논문 코드베이스입니다.

- **Rule-Based** — 규칙 기반 TOU 차익거래 제어
- **LSTM** — LSTM 순부하 예측 기반 제어
- **GRU** — GRU 순부하 예측 기반 제어

한국전력거래소·기상청 **2025년 실측 1년치 데이터**(부하·SMP·일사량 기반 태양광)를 사용합니다.

---

## 프로젝트 구조

```
Bess_Project/
├── compare.py              # 세 방식 동일 구간 비교 (교집합 정렬 + 공유 baseline)
├── rule_based/             # 규칙 기반 제어
│   ├── main.py             #   실행 진입점
│   ├── config.py           #   BESS·요금·경로 설정 (TARIFF_MODE)
│   ├── bess_controller.py  #   규칙 기반 제어기
│   ├── evaluator.py        #   성능 지표 계산
│   └── verify_reproduction.py  # 논문 수치 재현 검증(Acceptance Test)
├── DL_LSTM/                # LSTM 예측 기반 (동일 구성 + models/lstm_model.py)
├── DL_GRU/                 # GRU  예측 기반 (동일 구성 + models/gru_model.py)
└── web_dashboard/          # Flask 실시간 모니터링 대시보드
```

> 데이터(`data/`)·학습 모델(`models/saved/*.pt`)·결과(`results/`, `comparison_results/`)와
> API 키(`.env`)는 `.gitignore`로 제외됩니다. 최초 실행 시 데이터 수집/모델 학습이 필요합니다.

---

## 실행 방법

### 1. 환경 설정 (Python 3.12)
```bash
pip install numpy pandas matplotlib scikit-learn torch requests python-dotenv flask flask-socketio
```
`.env` 파일에 공공데이터포털 인증키를 넣습니다:
```
COMMON_API_KEY=발급받은_키
```

### 2. 각 방식 시뮬레이션
```bash
cd rule_based && python main.py
cd ../DL_LSTM && python main.py     # main.py 의 SKIP_TRAINING=False 로 최초 학습
cd ../DL_GRU  && python main.py
```

### 3. 세 방식 비교
```bash
python compare.py                   # comparison_results/comparison_metrics.csv 생성
```

### 4. 논문 수치 재현 검증
```bash
cd rule_based && python verify_reproduction.py --data-dir data/api_cache
# 종료 코드 0 = 논문 21개 지표 재현 통과
```

---

## 요금 체계 (TARIFF_MODE)

`config.py`의 `TARIFF_MODE` 한 줄로 두 요금 체계를 전환합니다.

| 모드 | 구조 | 용도 |
|---|---|---|
| `'paper'` | 연중 단일 60 / 110 / 180 원 (최대부하 10–16시) | 논문 본문 재현 (기본값) |
| `'seasonal'` | 계절별(여름/봄가을/겨울) 단가·시간대 차등 | 계절 요금 민감도 분석 |

---

## 주요 방법론 (제어기 정정 사항)

예측 기반 제어기(LSTM·GRU)는 **동일한 제어 로직**을 사용하고 예측기만 다릅니다(공정 비교).

- **역송 방지** — 방전 상한을 실측 순부하 `max(부하−태양광, 0)`로 제한
- **SOC 진동 제거** — 경부하 목표 0.80, 충전 오버슈트 클램프
- **데이터 누수 차단** — 피크 임계값을 학습 구간에서만 산출
- **요금 폭탄 제거** — 계통 충전을 경부하 시간대에만 허용
- **태양광 우선 흡수** — 실측 잉여 기준 충전으로 커튼일먼트 최소화
- **재현성** — 난수 시드 42 고정 (random/numpy/torch)

---

## 핵심 결과 (paper 모드, 8,736시점)

| 방면 | 우세 | 요약 |
|---|---|---|
| 경제적 효율 | Rule-Based | 요금 절감 6.72% (LSTM·GRU 5.6%로 근접) |
| 에너지 효율 | 예측 기반 | 태양광 이용률·커튼일먼트·손실효율 우위 |
| 운영 안정성 | 예측 기반 | SOC 권장구간 체류 87% vs 21%, 사이클·심방전 압도 |
| 예측 정확도 | LSTM | MAE 1.58 < GRU 1.75 |

> 규칙기반은 단기 요금 절감, 예측 기반은 배터리 수명·안정성에서 우위 — 장기 총소유비용 관점에선 예측 기반(특히 GRU)이 지속가능합니다.
