"""[논문 표·그림] §1 공통 입력 생성

results/base_data.csv (8,760행)  — 모든 표·그림의 공통 입력
results/pred_lstm.npy (8,736,)   — LSTM 역정규화 예측(kW)

DL_LSTM 디렉토리 안에서 실행: cd DL_LSTM && python _build_base_data.py
저장소 파이프라인(data_loader/model/scaler)을 그대로 써서 재현 일관성을 보장한다.
"""
import os
import numpy as np
import pandas as pd

import config
from data_loader import (load_data, make_sequences, inverse_target,
                         load_scaler, TARGET_COL)
from models.lstm_model import predict, load_model

REPO    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(REPO, 'results')


def build_base_data():
    df = load_data(config.LOAD_DATA_PATH, config.SMP_DATA_PATH)
    base = pd.DataFrame({
        'timestamp'   : df['timestamp'],
        'load_kw'     : df['load_kw'],
        'solar_kw'    : df['solar_kw'],
        'smp'         : df['smp'],
        'tp'          : df['tariff_period'],
        'rate'        : df['tariff_rate'],
        'net_load_kw' : df['net_load_kw'],
    })

    # ── 검증 (지시서 §1-1) ─────────────────────────────────────
    n        = len(base)
    missing  = int(base.isna().sum().sum())
    solar_sum= float(base['solar_kw'].sum())
    load_mean= float(base['load_kw'].mean())
    load_max = float(base['load_kw'].max())
    print(f"[base_data] 행={n:,}  결측={missing}  "
          f"solar_sum={solar_sum:,.0f} kWh  load_mean={load_mean:.2f}  load_max={load_max:.2f} kW")
    checks = {
        '행 8,760'          : n == 8760,
        '결측 0'            : missing == 0,
        'solar_sum≈71,809'  : abs(solar_sum - 71809) / 71809 < 0.02,
        'load_mean=50.0'    : abs(load_mean - 50.0) < 0.1,
        'load_max≈74.4'     : abs(load_max - 74.4) < 74.4 * 0.02,
    }
    for k, ok in checks.items():
        print(f"   {'OK ' if ok else 'FAIL'} {k}")
    if not all(checks.values()):
        print("   [경고] 검증 실패 항목이 있습니다. 데이터 소스를 점검하세요(지시서 §1-1).")

    os.makedirs(RESULTS, exist_ok=True)
    out = os.path.join(RESULTS, 'base_data.csv')
    base.to_csv(out, index=False, encoding='utf-8-sig')
    print(f"   저장: {out}")
    return df


def build_pred_lstm(df):
    scaler = load_scaler()
    X, y, scaler = make_sequences(df, scaler=scaler, fit_scaler=False)
    model = load_model(X.shape[2])
    y_pred_kw = inverse_target(predict(model, X), scaler)
    y_true_kw = df[TARGET_COL].values[config.SEQ_LEN:]
    mae = float(np.mean(np.abs(y_true_kw - y_pred_kw)))
    out = os.path.join(RESULTS, 'pred_lstm.npy')
    np.save(out, np.asarray(y_pred_kw, dtype=float))
    print(f"[pred_lstm] shape={y_pred_kw.shape}  full MAE={mae:.3f} kW  저장: {out}")


if __name__ == '__main__':
    df = build_base_data()
    build_pred_lstm(df)
