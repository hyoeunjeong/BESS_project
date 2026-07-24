"""[§6-2] 예측 기여도 ablation (통합 러너)

동일한 제어기·데이터·기간에 대해 '예측 소스'만 바꿔가며 BESS를 시뮬레이션하고,
예측 품질이 경제 성과에 얼마나 기여하는지 표로 만든다.

예측 소스 5종:
    no_pred     : 예측 없음(0) — P2 피크컷이 예측으로는 발동하지 않음(하한선)
    persistence : 24시간 전 실제 순부하(단순 나이브 기준선)
    LSTM        : 학습된 LSTM 예측
    GRU         : 학습된 GRU 예측(DL_GRU/_ablation_dump.py 결과 로드)
    perfect     : 실제 순부하(오라클 상한선)

실행:
    (먼저) cd DL_GRU && python _ablation_dump.py
    (그다음) cd DL_LSTM && python _ablation_run.py
출력:
    <repo>/results/prediction_contribution.csv
"""
import os
import numpy as np
import pandas as pd

import config
from data_loader import (load_data, make_sequences, inverse_target,
                          load_scaler, TARGET_COL)
from models.lstm_model import predict, load_model
from bess_controller import LSTMBESSController
from simulator import run_lstm_simulation, run_baseline_simulation
from evaluator import evaluate_all, calc_prediction_metrics

REPO    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(REPO, 'results')
OUT     = os.path.join(RESULTS, 'prediction_contribution.csv')
GRU_NPY = os.path.join(RESULTS, 'pred_gru.npy')


def main():
    df = load_data(config.LOAD_DATA_PATH, config.SMP_DATA_PATH)
    scaler = load_scaler()
    X, y, scaler = make_sequences(df, scaler=scaler, fit_scaler=False)
    n_features = X.shape[2]

    # LSTM 예측
    model = load_model(n_features)
    pred_lstm = inverse_target(predict(model, X), scaler)   # (sim_len,)

    SEQ = config.SEQ_LEN
    test_start_idx = SEQ
    sim_len = len(pred_lstm)

    net_actual_full = df[TARGET_COL].values.astype(float)        # (N,) 0-clip 순부하
    net_actual_sim  = net_actual_full[SEQ: SEQ + sim_len]        # perfect
    net_persist     = net_actual_full[SEQ - 24: SEQ - 24 + sim_len]  # 24h 전(=[0:sim_len])
    no_pred         = np.zeros(sim_len, dtype=float)

    # GRU 예측 로드 (없으면 경고 후 건너뜀)
    sources = {
        'no_pred'    : no_pred,
        'persistence': net_persist,
        'LSTM'       : pred_lstm,
    }
    if os.path.exists(GRU_NPY):
        pred_gru = np.load(GRU_NPY).astype(float)
        if len(pred_gru) == sim_len:
            sources['GRU'] = pred_gru
        else:
            print(f"[경고] GRU 예측 길이 불일치({len(pred_gru)}≠{sim_len}) → GRU 행 생략")
    else:
        print(f"[경고] {GRU_NPY} 없음 → 먼저 DL_GRU/_ablation_dump.py 실행 권장")
    sources['perfect'] = net_actual_sim

    # baseline (무제어) — 한 번만
    test_df  = df.iloc[test_start_idx: test_start_idx + sim_len].reset_index(drop=True)
    baseline = run_baseline_simulation(test_df)

    # test 구간(§3-3와 동일 정의)
    t2 = int(sim_len * (config.TRAIN_RATIO + config.VAL_RATIO))

    rows = []
    for name, pred in sources.items():
        pred = np.asarray(pred, dtype=float)
        ctrl = LSTMBESSController()
        res  = run_lstm_simulation(df, pred, test_start_idx, ctrl)
        m    = evaluate_all(res, baseline, y_true=net_actual_sim, y_pred=pred)
        e    = m['economic']
        pm   = calc_prediction_metrics(net_actual_sim[t2:], pred[t2:])
        rows.append({
            'prediction_source'      : name,
            'pred_mae_test_kw'       : pm['mae_kw'],
            'pred_nmae_test_pct'     : pm['nmae_pct'],
            'energy_cost_saving_won' : e['cost_saving_won'],
            'billing_demand_kw'      : e['peak_demand_kw'],
            'peak_reduction_pct'     : e['peak_demand_reduction_pct'],
            'base_charge_delta_won'  : e['base_charge_delta_won'],
            'net_saving_won'         : e['net_saving_won'],
            'net_saving_rate_pct'    : e['net_saving_rate_pct'],
        })

    out_df = pd.DataFrame(rows)
    os.makedirs(RESULTS, exist_ok=True)
    out_df.to_csv(OUT, index=False, encoding='utf-8-sig')
    print(f"\n[§6-2] 예측 기여도 표 저장: {OUT}")
    print(out_df.to_string(index=False))


if __name__ == '__main__':
    main()
