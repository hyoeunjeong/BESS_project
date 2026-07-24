"""
5-시드 반복 학습 (§7 강건성 검증)
=====================================================================
시드 42/1/7/13/2024 로 재학습하여 예측·운영 성능의 평균±표준편차를 산출한다.
LSTM/GRU 차이가 표준편차 범위 안이면 "통계적으로 유의한 차이라 보기 어렵다".

  실행:  cd DL_LSTM && python seed_sweep.py lstm
         cd DL_GRU  && python seed_sweep.py gru
  출력:  results/seed_sweep_{lstm|gru}.csv

[§5 정합] 스케일러를 학습 구간(앞 70%)만으로 fit → 누수 없음.
[예측지표] train/val/test 구간 분리(§3-3). 운영지표는 test 아닌 전체 시뮬레이션.
[캐논 보호] 스윕이 .pt 를 덮어쓰므로 시작 시 백업하고 종료 시 복원한다.
"""
import os
import sys
import shutil
import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler

import config
import evaluator
from data_loader import (load_data, make_sequences, split_sequences,
                         inverse_target, FEATURE_COLS)

KIND = (sys.argv[1] if len(sys.argv) > 1 else 'lstm').lower()
if KIND == 'gru':
    from models.gru_model import train, predict
    from bess_controller import GRUBESSController as Controller
    from simulator import run_gru_simulation as run_sim, run_baseline_simulation
    SAVE_PATH = config.MODEL_SAVE_PATH.replace('lstm', 'gru')
else:
    from models.lstm_model import train, predict
    from bess_controller import LSTMBESSController as Controller
    from simulator import run_lstm_simulation as run_sim, run_baseline_simulation
    SAVE_PATH = config.MODEL_SAVE_PATH

SEEDS = [42, 1, 7, 13, 2024]
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(REPO, 'results')
SEQ = config.SEQ_LEN


def main():
    bak = SAVE_PATH + '.canonical_bak'
    if os.path.exists(SAVE_PATH):
        shutil.copy(SAVE_PATH, bak)

    df = load_data(config.LOAD_DATA_PATH, config.SMP_DATA_PATH)
    i_tr = int(len(df) * config.TRAIN_RATIO)

    rows = []
    for s in SEEDS:
        # [§5-1] 학습 구간만으로 스케일러 fit (누수 차단)
        scaler = MinMaxScaler().fit(df[FEATURE_COLS].values[:i_tr].astype(np.float32))
        X, y, scaler = make_sequences(df, scaler=scaler, fit_scaler=False)
        (X_tr, y_tr), (X_val, y_val), _ = split_sequences(X, y)
        model, _ = train(X_tr, y_tr, X_val, y_val, X.shape[2], seed=s)

        y_pred_kw = inverse_target(predict(model, X), scaler)
        y_true_kw = inverse_target(y, scaler)
        n = len(y); t1 = int(n * config.TRAIN_RATIO)
        t2 = int(n * (config.TRAIN_RATIO + config.VAL_RATIO))
        p_tr = evaluator.calc_prediction_metrics(y_true_kw[:t1], y_pred_kw[:t1])
        p_va = evaluator.calc_prediction_metrics(y_true_kw[t1:t2], y_pred_kw[t1:t2])
        p_te = evaluator.calc_prediction_metrics(y_true_kw[t2:], y_pred_kw[t2:])

        # 운영 지표 (전체 시뮬레이션)
        res = run_sim(df, y_pred_kw, SEQ, Controller())
        bl  = run_baseline_simulation(df.iloc[SEQ:SEQ + len(y_pred_kw)].reset_index(drop=True))
        m = evaluator.evaluate_all(res, bl)
        e, st = m['economic'], m['stability']

        rows.append({
            'seed': s,
            'mae_train': p_tr['mae_kw'], 'mae_val': p_va['mae_kw'],
            'mae_test': p_te['mae_kw'], 'rmse_test': p_te['rmse_kw'],
            'cost_saving_rate_pct': e['cost_saving_rate_pct'],
            'net_saving_won': e['net_saving_won'],
            'cycle_count': st['cycle_count'],
        })
        print(f"[{KIND}] seed {s:5d}: test MAE {p_te['mae_kw']:.3f}  "
              f"요금절감 {e['cost_saving_rate_pct']:.2f}%  순절감 {e['net_saving_won']:,.0f}  "
              f"사이클 {st['cycle_count']:.1f}")

    if os.path.exists(bak):
        shutil.move(bak, SAVE_PATH)
        print(f"[캐논 모델 복원] {SAVE_PATH}")

    d = pd.DataFrame(rows)
    os.makedirs(RESULTS, exist_ok=True)
    out = os.path.join(RESULTS, f'seed_sweep_{KIND}.csv')
    d.to_csv(out, index=False, encoding='utf-8-sig')

    print(f"\n=========== {KIND.upper()} 5-seed 요약 (n={len(SEEDS)}) ===========")
    for c in ['mae_train', 'mae_val', 'mae_test', 'rmse_test',
              'cost_saving_rate_pct', 'net_saving_won', 'cycle_count']:
        mu, sd = d[c].mean(), d[c].std(ddof=1)
        unit = 'kW' if 'mae' in c or 'rmse' in c else ('%' if 'pct' in c else ('원' if 'won' in c else ''))
        print(f"  {c:22s}: {mu:12,.3f} ± {sd:10,.3f} {unit}")
    print(f"저장: {out}\n" + "=" * 52)


if __name__ == '__main__':
    main()
