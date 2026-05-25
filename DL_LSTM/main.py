"""
main.py  ─  LSTM 기반 BESS 시뮬레이션 실행 파일
================================================
실행 순서
---------
1.  데이터 로드 및 피처 엔지니어링
2.  LSTM 입력 시퀀스 생성 + Train/Val/Test 분할
3.  LSTM 모델 학습 (또는 저장된 모델 불러오기)
4.  테스트셋 예측 → 역정규화
5.  LSTM 기반 BESS 시뮬레이션
6.  기준 시나리오 (BESS 없음) 시뮬레이션
7.  평가 지표 계산 및 출력
8.  결과 저장 및 시각화

사용법
------
    python main.py

옵션: SKIP_TRAINING = True 로 설정하면 저장된 모델을 바로 사용합니다.
"""

import os
import numpy as np
import pandas as pd

import config
from data_loader  import (load_data, make_sequences, split_sequences,
                           inverse_target, save_scaler, load_scaler,
                           FEATURE_COLS)
from models.lstm_model  import train, predict, load_model
from bess_controller    import LSTMBESSController
from simulator          import run_lstm_simulation, run_baseline_simulation
from evaluator          import evaluate_all, print_report, print_comparison
from visualizer         import (plot_training_curve, plot_prediction,
                                 plot_daily_operation, plot_soc_trend,
                                 plot_comparison, plot_radar)


# ─────────────────────────────────────────────────────────────────────
# 옵션
# ─────────────────────────────────────────────────────────────────────
SKIP_TRAINING = False   # True → 저장된 모델(.pt) 로드


# ─────────────────────────────────────────────────────────────────────
# 극단 사례 자동 선정 (Rule-Based main.py 와 동일 로직)
# ─────────────────────────────────────────────────────────────────────
def find_extreme_days(result_df: pd.DataFrame) -> dict:
    df       = result_df.copy()
    df['date'] = df['timestamp'].dt.date
    first    = df['timestamp'].min().date()
    daily    = df.groupby('date').agg(
        load_mean  = ('load_kw',  'mean'),
        smp_mean   = ('smp',      'mean'),
        solar_mean = ('solar_kw', 'mean'),
    ).reset_index()
    daily['day_idx'] = daily['date'].apply(lambda d: (d - first).days)

    def _pick(col, largest):
        r = daily.loc[daily[col].idxmax() if largest else daily[col].idxmin()]
        return {'day_idx': int(r['day_idx']), 'date': str(r['date']),
                'value': float(r[col])}

    return {
        'peak_load'  : {**_pick('load_mean', True),  'label': '부하 최대일',    'unit': 'kW'},
        'peak_smp'   : {**_pick('smp_mean',  True),  'label': 'SMP 최대일',     'unit': '원/kWh'},
        'best_solar' : {**_pick('solar_mean',True),  'label': '태양광 최대일',  'unit': 'kW'},
        'worst_solar': {**_pick('solar_mean',False), 'label': '태양광 최소일',  'unit': 'kW'},
    }


# ─────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 62)
    print("  LSTM 기반 BESS 충·방전 제어 시뮬레이션")
    print("  (한국전력거래소 실제 데이터 기반)")
    print("=" * 62)

    os.makedirs(config.RESULT_DIR,    exist_ok=True)
    os.makedirs(config.MODEL_SAVE_DIR, exist_ok=True)

    # ── 1. 데이터 로드 ─────────────────────────────────────────────
    print("\n[1/8] 데이터 로드 및 피처 엔지니어링")
    df = load_data(config.LOAD_DATA_PATH, config.SMP_DATA_PATH)

    # ── 2. 시퀀스 생성 + 분할 ─────────────────────────────────────
    print("\n[2/8] LSTM 시퀀스 생성 및 Train/Val/Test 분할")
    X, y, scaler = make_sequences(df, fit_scaler=True)
    save_scaler(scaler)

    (X_tr, y_tr), (X_val, y_val), (X_te, y_te) = split_sequences(X, y)
    n_features = X.shape[2]
    print(f"   Train : {X_tr.shape}  Val : {X_val.shape}  Test : {X_te.shape}")
    print(f"   피처 수: {n_features}  →  {FEATURE_COLS}")

    # ── 3. LSTM 학습 ───────────────────────────────────────────────
    print("\n[3/8] LSTM 모델 학습")
    if SKIP_TRAINING and os.path.exists(config.MODEL_SAVE_PATH):
        print("   저장된 모델 로드 (SKIP_TRAINING=True)")
        model   = load_model(n_features)
        history = None
    else:
        model, history = train(X_tr, y_tr, X_val, y_val, n_features)

    # ── 4. 테스트셋 예측 ───────────────────────────────────────────
    print("\n[4/8] 테스트셋 예측")
    y_pred_scaled = predict(model, X_te)

    # 역정규화
    y_pred_kw = inverse_target(y_pred_scaled, scaler)
    y_true_kw = inverse_target(y_te,          scaler)
    print(f"   예측 완료: {len(y_pred_kw)}개 샘플")

    # 테스트 기간 원본 DataFrame 인덱스 계산
    # 시퀀스 생성 시 seq_len 만큼 앞이 잘림 → 테스트 시작 행 인덱스
    n_total   = len(X)
    n_train   = len(X_tr)
    n_val     = len(X_val)
    test_start_idx = config.SEQ_LEN + n_train + n_val   # df 기준 인덱스

    # ── 5. LSTM BESS 시뮬레이션 ────────────────────────────────────
    print("\n[5/8] LSTM 기반 BESS 시뮬레이션")
    controller  = LSTMBESSController()
    lstm_result = run_lstm_simulation(df, y_pred_kw, test_start_idx, controller)
    print("   진행상태: 완료")

    # ── 6. 기준 시나리오 ───────────────────────────────────────────
    print("\n[6/8] 기준 시나리오 (BESS 없음) 시뮬레이션")
    test_df  = df.iloc[test_start_idx: test_start_idx + len(y_pred_kw)].reset_index(drop=True)
    baseline = run_baseline_simulation(test_df)
    print("   진행상태: 완료")

    # ── 7. 평가 ────────────────────────────────────────────────────
    print("\n[7/8] 평가 지표 계산")
    metrics = evaluate_all(lstm_result, baseline,
                           y_true=y_true_kw, y_pred=y_pred_kw)
    print_report(metrics)

    # ── 8. 결과 저장 + 시각화 ─────────────────────────────────────
    print("[8/8] 결과 저장 및 시각화")
    print("-" * 60)

    csv_path = os.path.join(config.RESULT_DIR, 'lstm_simulation_result.csv')
    lstm_result.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"   CSV   : {csv_path}")

    # 학습 곡선
    if history:
        plot_training_curve(
            history,
            save_path=os.path.join(config.RESULT_DIR, 'lstm_training_curve.png'))

    # 예측 정확도
    plot_prediction(
        y_true_kw, y_pred_kw,
        save_path=os.path.join(config.RESULT_DIR, 'lstm_prediction.png'))

    print("=" * 62)
    # 극단 사례 운영 그래프
    extreme = find_extreme_days(lstm_result)
    print("\n   극단 사례 선정")
    print("-" * 60)
    for info in extreme.values():
        label = info['label']
        # 한글은 2칸, 영문/숫자는 1칸으로 계산하여 전체 18칸을 맞춤
        display_width = sum(2 if ord(c) > 127 else 1 for c in label)
        padding = " " * (15 - display_width)
    
        # 라벨 뒤에 계산된 공백(padding)을 추가하고 콜론(:)을 배치
        print(f"   - {label}{padding}: {info['date']}  "
              f"(평균 {info['value']:>8.2f} {info['unit']})")
    print("-" * 60)     
    tag_map = {
        'peak_load'  : 'extreme_peak_load',
        'peak_smp'   : 'extreme_peak_smp',
        'best_solar' : 'extreme_best_solar',
        'worst_solar': 'extreme_worst_solar',
    }
    for key, tag in tag_map.items():
        info = extreme[key]
        plot_daily_operation(
            lstm_result,
            day_idx   = info['day_idx'],
            save_path = os.path.join(config.RESULT_DIR,
                                     f'{tag}_{info["date"]}.png'))

    plot_soc_trend(lstm_result,
                   save_path=os.path.join(config.RESULT_DIR, 'lstm_soc_trend.png'))
    print("=" * 62)
    print(f"\n시뮬레이션 완료 → 결과 폴더: ./{config.RESULT_DIR}/")
    print("\n※ Rule-Based 결과와 비교하려면 아래 함수를 활용하세요:")
    print("   from evaluator import print_comparison")
    print("   from visualizer import plot_comparison, plot_radar")


if __name__ == '__main__':
    main()
