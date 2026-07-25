# 딥러닝 기반 순부하 예측을 활용한 BESS 운영 성능 분석

학위논문 부속 코드 저장소. 태양광 연계 BESS(배터리 에너지저장장치)의 순부하(net load)를
**LSTM·GRU**로 예측하고, 그 예측을 충·방전 제어에 결합했을 때의 운영 성능을
**규칙 기반(Rule-Based)** 방식과 **동일한 데이터·동일한 BESS 사양·동일한 제약** 아래에서
정량 비교한다. 세 방식(Rule-Based / LSTM / GRU)을 2025년 1년치 시계열에 대해 시뮬레이션하고,
경제성·에너지 효율·운영 안정성 세 관점으로 평가한다.

> 이 문서는 심사 시 코드를 확인하기 위한 **방어 자료**다. 모든 서술은 저장소의 코드·파일로
> 확인한 내용이며, 확인하지 못한 항목은 "미검증/미특정"으로 명시한다.

---

## 1. 저장소 구조

```
Bess_Project/
├── rule_based/            # 규칙 기반 운영 (기준선)
│   ├── main.py            #   실행 진입점 → results/rb_simulation_result.csv (8,760행)
│   ├── config.py          #   BESS·요금·데이터 플래그
│   ├── data_loader.py     #   KPX/ASOS 실측 로드 + _validate_series 진위 검사
│   ├── api_client.py      #   공공 API 호출·캐시 (fetch_load/smp/kma)
│   ├── bess_controller.py #   규칙 기반 제어(경부하 충전·피크 방전 등)
│   ├── simulator.py · evaluator.py · solar_model.py
│   ├── verify_reproduction.py  # 정본 재현 자동 검증(8,736 정렬, 13지표)
│   └── data/              #   실측 캐시·CSV (재현 패키지, §5)
├── DL_LSTM/               # LSTM 예측 기반 운영
│   ├── main.py            #   예측→시뮬 → results/lstm_simulation_result.csv (8,736행)
│   ├── config.py · data_loader.py · api_client.py
│   ├── bess_controller.py #   예측 결합 제어(P0′·P0·P1·P2·P3 계층)
│   ├── models/lstm_model.py · models/gru_model.py · models/saved/*.pt
│   ├── stage_run.py       #   표2.11·2.15 산출(정정 단계·κ 민감도)
│   ├── seed_sweep.py      #   5시드 반복 학습 → results/seed_sweep_lstm.csv
│   ├── _build_base_data.py#   results/base_data.csv, pred_lstm.npy 생성
│   └── _ablation_run.py   #   표2.14 예측 기여도 → prediction_contribution.csv
├── DL_GRU/                # GRU 예측 기반 운영 (구조 동일)
│   ├── main.py → results/gru_simulation_result.csv (8,736행)
│   ├── seed_sweep.py → results/seed_sweep_gru.csv
│   └── _ablation_dump.py  #   results/pred_gru.npy 생성
├── compare.py             # 세 방식 3자 정렬 비교 → 표2.8·2.9 (comparison_results/)
├── make_table_2_13.py     # 표2.13 계절별 절감률 (검산 assert 포함)
├── make_tables.py         # 표2.11·2.12·2.15 → results/table_2_1*.csv
├── make_figures.py        # 그림2.3~2.12 → figures/*.png
├── stats_test.py          # 표2.7 5시드 통계(평균±σ·Welch t) — seed_sweep CSV 읽기
├── results/               # 논문 근거 CSV (커밋 포함)·중간 산출
├── figures/               # 논문 그림 PNG (커밋 포함)
└── web_dashboard/         # Flask 모니터링 대시보드 — 논문에 사용되지 않음(별도 데모)
```

> `web_dashboard/` 는 실시간 모니터링 데모이며 **논문의 어떤 표·그림도 이 경로에서 산출되지 않는다**
> (제약은 §9 참조).

---

## 2. 데이터

| 계열 | 출처 | 비고 |
|---|---|---|
| 시간별 전력 부하 | 한국전력거래소(KPX) 시간별 전국 전력수요 | 연평균 50kW로 스케일 |
| 계통한계가격(SMP) | KPX SMP(**육지**) | DL 예측 입력 feature로만 사용 |
| 태양광 발전량 | 기상청 ASOS 시간별 **일사량(icsr)** 기반 **추정** | 실측 발전량 아님 |

- 기간: **2025-01-01 ~ 2025-12-31**, 세 계열 각 **8,760시점, 결측 0**.
- 태양광은 **실측 발전량이 아니라 ASOS 일사량으로부터 추정**한 값이다:
  `solar_kw = icsr × 50kW × 0.2778` (0~50kW 클립). **성능비(PR)·인버터 효율·모듈 온도 보정은
  미반영**이므로 발전량이 과대평가되어 있다(논문 §2.4.2와 동일).
- SMP는 요금·제어 계산에 쓰이지 않고(evaluator·controller 미참조) DL 예측의 입력 feature로만 쓰인다.

---

## 3. 평가구간 (8,760 → 8,736)

- 원자료는 8,760시점이나, LSTM/GRU는 **입력 시퀀스 24시간**을 소모하므로 예측·시뮬은 첫 24시각을
  제외한 **8,736시점**에서 이뤄진다.
- **Rule-Based 단독 실행은 8,760시점**이다. 세 방식 비교(`compare.py`)는
  `align_frames`로 timestamp **교집합(8,736)** 에 정렬하며, **표2.8 이하 모든 비교·표2.13은
  8,736 기준**이다. `verify_reproduction.py`도 8,736 정렬 기준으로 검증한다.

---

## 4. 재현 패키지 (저장소 단독 재현)

논문 재현에 필요한 입력 데이터·모델을 저장소에 포함한다(합계 **약 3.74MB**). 대용량 캐시를 통째로
올리지 않고 `.gitignore` 예외로 **필요한 파일만** 연다. **네트워크·API 키 불필요.**

| 파일 | 용도 | 크기 | 대안 |
|---|---|---|---|
| `rule_based/data/api_cache/kma_st108_*.csv` | **일사량 실측(태양광)** | 344KB | **없음** — 합성 태양광은 `STRICT_DATA`가 차단 |
| `rule_based/data/api_cache/{load,smp_육지}_*.csv` | 부하·SMP 캐시 | 566KB | 아래 CSV |
| `rule_based/data/{load_data.csv, smp_data.csv}` | RB 자립 CSV 경로 | 125KB | — |
| `DL_{LSTM,GRU}/data/cache/{load_all_all,smp_2025-*}.csv` | DL 실측 입력 캐시 | 1.13MB | — |
| `DL_{LSTM,GRU}/models/saved/{lstm_best,gru_best}.pt`, `scaler.pkl` | 예측 모델·정규화 | 2.9MB | 시드 42 재학습 |

- **`.pt` 를 포함한 이유**: 시드 42 재학습의 비트동일성은 미검증(§9)이므로, `.pt`로 정확 재현을 보장한다.
- 일사량(kma) 캐시는 **CSV 대안이 없는 유일 병목**이다 — 합성 태양광은 논문과 다르고 `STRICT_DATA`가 차단한다.

---

## 5. 재현 절차

**클린 클론 실측 검증**: 저장소를 새 경로에 `git clone`한 뒤(`.env` 없음=API 키 없음) 아래 절차만으로
표2.7·2.8·2.9·2.11·2.12·2.13·2.14·2.15·2.16 및 그림2.3~2.12, `verify_reproduction` 13/13이
**네트워크 호출 0회, 약 2분 이내**에 재현됨을 확인하였다.

### 5.1 고정 설정 (기본값 그대로)
```
config.py: STRICT_DATA=True, TARIFF_MODE='seasonal', DATA_SOURCE='auto'
           USE_LOAD_API=USE_SMP_API=USE_KMA_API=True (캐시 자동 사용)
main.py  : SKIP_TRAINING=True, FULL_YEAR=True   (저장된 .pt 로드)
```

> **[필수] UTF-8 콘솔**: 모든 파이프라인 스크립트(`main.py`×3·`compare.py`·`make_*.py`·
> `verify_reproduction.py`·`evaluator.py` 등)가 한국어를 출력하므로, 한국어 Windows(cp949) 기본
> 콘솔에서는 `UnicodeEncodeError`가 발생한다. 실행 전 `PYTHONUTF8=1`을 설정하라(리눅스/UTF-8 콘솔은
> 불필요). 아래 세 형태 모두 실제로 `utf8_mode=1`로 동작함을 확인하였다.
> ```
> PowerShell : $env:PYTHONUTF8=1
> cmd        : set PYTHONUTF8=1      (한 줄로 먼저 실행; 또는 chcp 65001)
> bash       : export PYTHONUTF8=1
> ```

### 5.2 핵심 지표·그림 (표2.7 test·2.8·2.9·2.13, 그림2.3~2.12)
```bash
# 1) 세 방식 시뮬레이션 (캐시 자동 사용, 네트워크 불필요)
cd rule_based && python main.py && cd ..     # → rb_simulation_result.csv (8,760)  [약 10초]
cd DL_LSTM    && python main.py && cd ..      # → lstm_..._result.csv (8,736), test MAE 1.290  [약 16초]
cd DL_GRU     && python main.py && cd ..      # → gru_..._result.csv  (8,736), test MAE 1.306  [약 17초]

# 2) 표
python compare.py                             # 표2.8·2.9 → comparison_results/comparison_metrics.csv
python make_table_2_13.py                     # 표2.13 → results/table_2_13_seasonal.csv (검산 assert)

# 3) 그림용 공통 입력(pred_*.npy) 빌드 → 그림
#    base_data.csv·표 CSV는 저장소에 포함되나, 중간 바이너리 pred_*.npy 는 재생성 대상이다.
cd DL_LSTM && python _build_base_data.py && cd ..   # results/base_data.csv, pred_lstm.npy
cd DL_GRU  && python _ablation_dump.py  && cd ..     # results/pred_gru.npy
python make_figures.py                        # 그림2.3~2.12 → figures/*.png

# 4) 규칙 기반 정본 검증
cd rule_based && python verify_reproduction.py  # 종료코드 0, 13/13 (8,736 정렬)
```

**기대 출력값(클린 클론 검증됨)**:
```
verify_reproduction : 13/13 일치, 종료코드 0
표2.8 순 절감액     : RB 285,497 / LSTM 398,281 / GRU 400,442 원
표2.13 (여름/봄가을/겨울)
  Rule-Based : 0.996 / 0.071 / 1.189 %
  LSTM       : 1.292 / 0.557 / 0.842 %
  GRU        : 1.292 / 0.560 / 0.855 %
표2.7 test MAE      : LSTM 1.290 / GRU 1.306 kW
그림               : figures/fig_2_03.png ~ fig_2_12.png (10개)
```

### 5.3 나머지 표 (2.7 5시드·2.11·2.12·2.14·2.15·2.16)
```bash
# 표2.7 5시드 통계 (seed_sweep_*.csv 는 커밋 포함 → 재학습 없이 통계만 재현)
python stats_test.py
#   → LSTM 1.203±0.066 / GRU 1.283±0.142, Welch t=-1.14 p=0.299

# 표2.11(정정 단계)·2.12(계층별 발동)·2.15(κ 민감도)
python make_tables.py
#   → results/table_2_11_stages.csv, table_2_12_reasons.csv, table_2_15_kappa.csv
#   표2.15: κ=0.90(기준) 순절감 398,281 / κ=0.95 587,790

# 표2.14 예측 소스별 기여도 (persistence·무예측·완전예측)
cd DL_GRU  && python _ablation_dump.py && cd ..    # pred_gru.npy (5.2에서 이미 생성됐으면 생략 가능)
cd DL_LSTM && python _ablation_run.py  && cd ..     # → results/prediction_contribution.csv

# 표2.16 요금 체계별(2026 개편) — 전용 스크립트 없음. 3개 config 의 TARIFF_MODE 를
#   'seasonal' → '2026' 으로 바꾼 뒤 위 5.2 의 (1)·(2)를 다시 실행하는 수동 절차다.
#   (표2.15 의 κ 민감도는 make_tables/stage_run 이 내부에서 κ=0.85/0.90/0.95 를 스윕하므로
#    DEMAND_SHAVE 를 바꿀 필요가 없다. TARIFF_MODE 변경이 필요한 것은 표2.16 뿐이다.)

# 2026 로 전환 (bash 예; 원복 명령도 함께 제시)
for c in rule_based/config.py DL_LSTM/config.py DL_GRU/config.py; do
  sed -i "s/^TARIFF_MODE = 'seasonal'/TARIFF_MODE = '2026'/" "$c"
done
cd rule_based && python main.py && cd ..
cd DL_LSTM    && python main.py && cd ..
cd DL_GRU     && python main.py && cd ..
python compare.py                              # 표2.16 (2026)
# 원복 (반드시 실행)
for c in rule_based/config.py DL_LSTM/config.py DL_GRU/config.py; do
  sed -i "s/^TARIFF_MODE = '2026'/TARIFF_MODE = 'seasonal'/" "$c"
done
#   클린 클론 검증(2026): 기본요금 증감 +0(RB·LSTM·GRU 전부), 규칙 대비 순절감 LSTM +19.3% / GRU +20.8%.
```

> **재현 범위 명시**: 위 명령은 표2.7·2.8·2.9·2.11·2.12·2.13·2.14·2.15·2.16 및 그림2.3~2.12를
> 산출한다. 표2.1~2.6·2.10·2.17은 전용 산출 스크립트가 없거나 미특정이다(§6 매핑표).

### 5.4 세 방식 예측 기여도 분석(선택)
```bash
python compare.py                 # 3자 비교 요약
```

**소요 시간(클린 클론, 참고)**: clone 2초 + main×3 약 43초 + compare/표 약 9초 +
pred 빌드·그림 약 35초 = **약 90초(1.5분)**. 심사장 라이브 시연 가능.

---

## 6. 논문 표·그림 → 산출 스크립트 매핑

| 논문 표/그림 | 산출 스크립트 | 출력 파일 | 필요 플래그 | 검증 상태 |
|---|---|---|---|---|
| 표2.1 선행연구 성능 | — (문헌 정리) | — | — | 산출 스크립트 불필요(문헌 표) |
| 표2.2 요금 체계 | config `_BANDS_2025`·`SEASONAL_TARIFF` | — | `TARIFF_MODE='seasonal'` | config 값(전용 스크립트 없음) |
| 표2.3 입력 피처 | config·data_loader `FEATURE_COLS` | — | — | config 값(전용 스크립트 없음) |
| 표2.4 학습설정·모델규모 | config(hidden 128·2층·dropout 0.2 등)+모델 파라미터 수 | — | — | config 값(전용 스크립트 없음) |
| 표2.5 시간대별 SOC 목표·출력비율 | config(SOC 목표·출력 비율) | — | — | config 값(전용 스크립트 없음) |
| 표2.6 평가 관점·지표 | — (개념 정의) | — | — | 산출 스크립트 불필요(개념 표) |
| 표2.7 예측성능(test) | `DL_{LSTM,GRU}/main.py` | 콘솔 | `SKIP_TRAINING=True,FULL_YEAR=True` | 클린 클론 검증됨 |
| 표2.7 예측성능(5시드) | `stats_test.py`(seed_sweep CSV 읽기) | seed_sweep_{lstm,gru}.csv | — | 클린 클론 검증됨(*재학습 미실행, CSV는 커밋본) |
| 표2.8 운영 성능 | `compare.py` | comparison_metrics.csv | seasonal | 클린 클론 검증됨 |
| 표2.9 시간대별 절감률 | `compare.py` | comparison_metrics.csv | seasonal | 클린 클론 검증됨 |
| 표2.10 시간대별 충·방전량 | (전용 스크립트 없음) sim 결과 `tariff_period`별 방전량 집계 | `*_simulation_result.csv` | seasonal | 값 확인됨·전용 스크립트 없음(주) |
| 표2.11 정정 단계별 | `make_tables.py`→`stage_run.py` | table_2_11_stages.csv | seasonal | 클린 클론 검증됨 |
| 표2.12 동작·계층별 발동 | `make_tables.py` | table_2_12_reasons.csv | seasonal | 클린 클론 검증됨 |
| 표2.13 계절별 절감률 | `make_table_2_13.py` | table_2_13_seasonal.csv | seasonal | 클린 클론 검증됨 |
| 표2.14 예측 소스별 기여도 | `_ablation_dump.py`+`_ablation_run.py` | prediction_contribution.csv | — | 클린 클론 검증됨 |
| 표2.15 κ 민감도 | `make_tables.py`→`stage_run.py` | table_2_15_kappa.csv | — | 클린 클론 검증됨 |
| 표2.16 요금 체계별(2026) | (수동) `TARIFF_MODE='2026'`+main×3+compare | comparison_metrics.csv | `TARIFF_MODE='2026'` | 클린 클론 검증됨(수동 플래그, 규칙대비 19.3/20.8%) |
| 표2.17 세 평가축 우열 종합 | — (표2.8 기반 정성 종합, 그림2.12) | — | — | 산출 스크립트 불필요(정성 종합) |
| 그림2.3~2.12 | `make_figures.py` | figures/fig_2_0*.png | pred 빌드 선행 | 클린 클론 검증됨 |

> 검증 상태 구분: **클린 클론 검증됨**(새 클론에서 실행하고 논문값과 대조) / **config·설정값 표**(전용
> 스크립트 없이 config에서 확인) / **산출 스크립트 불필요**(문헌·개념·정성 종합 표).
>
> **(주) 표2.10**: 전용 산출 스크립트는 없다. 값은 `*_simulation_result.csv`의 `bess_power_kw>0`을
> `tariff_period`별로 합산하면 도출되며, 감사에서 논문값과 일치함을 확인하였다
> (RB 중간부하 방전 77.6%·최대부하 22.4%, LSTM 최대부하 방전 65.0%). `compare.py`는 이 표를
> 출력하지 않는다.

---

## 7. config 플래그 레퍼런스

3개 `config.py`(rule_based / DL_LSTM / DL_GRU) 공통. 기본값이 논문 재현 조건이다.

| 플래그 | 위치 | 허용값 | 기본값 | 효과 |
|---|---|---|---|---|
| `STRICT_DATA` | 3 config | True/False | **True** | True면 **합성 데이터 폴백을 명시적 `raise`로 차단**(출처 기반 게이트) |
| `TARIFF_MODE` | 3 config | `'seasonal'`·`'2026'`·`'paper'` | **`'seasonal'`** | 요금 시간대. seasonal=논문 기준, 2026=표2.16 전용, paper=가상 |
| `DATA_SOURCE` | DL config | `'auto'`·`'api'`·`'csv'` | **`'auto'`** | 데이터 소스. **`'csv'`는 태양광을 합성으로 강제** → STRICT면 차단 |
| `USE_LOAD_API` | rule_based | True/False | True | True=캐시/API, False=`load_data.csv` |
| `USE_SMP_API` | rule_based | True/False | True | True=캐시/API, False=`smp_data.csv`(RB 자립 경로) |
| `USE_KMA_API` | rule_based | True/False | True | True=ASOS 실측, **False=합성(STRICT면 raise)** |
| `SKIP_TRAINING` | DL main.py | True/False | **True** | True=저장 `.pt` 로드, False=시드 42 재학습 |
| `FULL_YEAR` | DL main.py | True/False | **True** | True=1년 전체 시뮬. True+SKIP_TRAINING면 저장 scaler 재사용 |
| `DEMAND_SHAVE` | DL config | 실수 | **0.90** | P0′ 수요저감 계수 κ(표2.15 민감도 대상) |

**핵심 주의**:
- `STRICT_DATA=True`의 차단은 `_validate_series`(다양성 검사)가 아니라 **출처 기반 명시 `raise`**다.
  물리적으로 사실적인 합성 태양광은 `_validate_series`를 통과하므로(§9), 실측 강제는 이 게이트가 담당한다.
- `DATA_SOURCE='csv'`는 SMP·부하만 CSV로 읽고 **태양광을 `simulate_solar`(합성)로 대체**한다.
  따라서 STRICT에서 차단되며, **DL은 실측 kma 캐시가 필수**다(자립 CSV 경로 없음).
- `FULL_YEAR=True`+`SKIP_TRAINING=True`: 저장된 scaler를 재사용해 학습 구간 정규화를 유지한다
  (`fit_scaler=False`, 미래 정보 누수 차단).

---

## 8. 알려진 제약

각 항목을 **논문 결과 영향 유무**로 구분한다.

| 제약 | 논문 영향 |
|---|---|
| `api_client.fetch_smp_data`가 쓰는 엔드포인트 **`SmpWithForecastDemand`는 하루전 발전계획용(day-ahead)**이며 과거 실측 조회용이 아니다. 과거 실측은 공공데이터포털 **"한국전력거래소_계통한계가격조회"**(data.go.kr/data/15076302, 육지·제주 1시간 단위)가 별도로 존재한다. | **영향 없음** — 동봉 캐시·`smp_data.csv`로 재현하므로 이 엔드포인트를 쓰지 않는다. |
| `_validate_series`의 고유율 임계가 **solar에만 2%로 완화**되어(load/smp는 5%) **2~5% 구간의 구조적 합성 데이터를 걸러내지 못한다**(고유율 3.56% 가짜로 실증). | **영향 없음** — 실제 태양광은 ASOS 실측 기반(고유율 4.10%)이며, 합성 차단은 §7의 출처 게이트가 담당. |
| `web_dashboard` 실시간 경로는 `STRICT_DATA`를 우회하며, 예보 엔드포인트에서 저다양성 SMP 캐시를 생성한다. | **영향 없음** — 논문 배치 파이프라인과 무관(대시보드 데모 전용). |
| DL의 `DATA_SOURCE='csv'` 자립 경로가 없다(태양광 합성 강제). | 재현은 실측 kma 캐시로 수행되므로 **영향 없음**. RB만 CSV 자립 가능. |

**검증되지 않은 항목**: 동일 시드(42) **2회 재학습의 비트동일성**은 실행하지 않았다(미검증).
이 때문에 재현 패키지에 `.pt`를 동봉하여 정확 재현을 보장한다.

> **제출 후 개선(제안, 미적용)**: §5.1의 UTF-8 콘솔 요구는 각 진입 스크립트 상단에
> `sys.stdout.reconfigure(encoding='utf-8')`(Python 3.7+)를 두면 환경변수 없이 제거할 수 있다.
> 결과 수치에는 영향이 없으나 지금은 코드를 변경하지 않는다.

---

## 9. 폐기된 결과 세대 (삭제됨)

`lstm_result_final.csv` / `gru_result_final.csv`(각 8,371행)는 2026-07 감사에서 폐기 확인 후 삭제되었다.
식별 지문: **8,371행**(=364일×23시간−1), **hour 0 전량 누락**, **smp 고유값 16개**(연중 동일 16값
분포가 매월 반복되어 월평균이 전부 109.85원 — 가짜 SMP), **reason 컬럼 없음**, **14열**.
`STRICT_DATA` 도입(commit `3841a1a`) 이전 세대이며, 논문의 어떤 수치도 이 파일에서 산출되지 않았다.
논문 근거는 `*_simulation_result.csv`(**8,736행 · 실측 SMP 4,120 고유**)이다.

교차 폴더에 남아 있던 동일 파일명 구세대 사본 6종도 삭제되었고, `compare.py`·`make_figures.py`·
`make_table_2_13.py`에 **행수 assert**(정본 RB 8,760 / LSTM·GRU 8,736)를 두어 세대 오독을 차단한다.

---

## 10. 감사 이력

2026-07 논문–코드 정합성 감사(§1~§7 수치·방법론·데이터 건전성·재현성) 수행. 주요 커밋:

| 커밋 | 내용 |
|---|---|
| `aa60ded` | 표2.13 산출 스크립트(`make_table_2_13.py`) 신설, 정렬 8,736 통일, 세대 가드 |
| `bb9418b` | 재현 패키지 15파일(데이터·모델) `.gitignore` 예외로 커밋 |
| `e6aefc9` | README 재현 절차 확정, `verify_reproduction` 8,736 표기 정정 |
| `6ee15f0` | README 재현 절차에 `pred_*.npy` 빌드 단계 추가(클린 클론 검증서 발견) |

추가된 방어 장치:
- **행수 assert**(`compare.py`·`make_figures.py`·`make_table_2_13.py`): 로드한 결과가 8,760/8,736이
  아니면 즉시 중단 — 8,372행 구세대 파일 오독 방지.
- **계절↔전체 검산 assert**(`make_table_2_13.py`): 계절별 절감액 합산이 전체 절감률(표2.9)과 일치하고,
  계절별 시점 수 합이 8,736임을 매 실행 검증 — 표2.13이 표2.8·2.9와 정합함을 보장.

---

## 11. 설치와 실행 환경

```bash
pip install -r rule_based/requirements.txt          # 규칙 기반
pip install -r DL_LSTM/requirements.txt             # 예측 기반(torch·scipy 포함, DL_GRU 동일)
# (선택) 웹 대시보드 — 논문 무관
pip install -r web_dashboard/requirements_web.txt
```

- Python 3.12 기준. 재현 패키지가 동봉되어 있어 **`.env`·공공데이터 API 키 없이** §5 절차가 동작한다
  (API 키는 최초 데이터 수집 시에만 필요).
- 웹 대시보드(선택): `cd web_dashboard && python web_app.py` (http://localhost:5000).
