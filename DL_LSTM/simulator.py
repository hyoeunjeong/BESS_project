"""
simulator.py  ─  LSTM 기반 BESS 시뮬레이션 엔진
================================================
LSTM 모델의 예측값을 실시간으로 받아 LSTMBESSController 를 구동합니다.
Rule-Based simulator.py 와 완전히 독립된 파일입니다.
"""

import numpy as np
import pandas as pd

import config
from bess_controller import LSTMBESSController
from data_loader     import FEATURE_COLS, TARGET_COL, inverse_target


def run_lstm_simulation(merged_df      : pd.DataFrame,
                        predicted_nl   : np.ndarray,
                        test_start_idx : int,
                        controller     : LSTMBESSController = None) -> pd.DataFrame:
    """
    LSTM 예측값 기반 BESS 시뮬레이션

    Parameters
    ----------
    merged_df      : add_features() 가 적용된 전체 DataFrame
    predicted_nl   : LSTM 예측 순부하 (역정규화 kW, 테스트셋 길이)
    test_start_idx : 테스트셋이 시작하는 merged_df 행 인덱스
    controller     : LSTMBESSController (None 이면 기본값)

    Returns
    -------
    DataFrame : 시뮬레이션 결과
        timestamp, hour, load_kw, solar_kw, smp,
        tariff_period, tariff_rate,
        predicted_net_load_kw,
        bess_power_kw, charge_kw, discharge_kw,
        grid_power_kw, soc, action
    """
    if controller is None:
        controller = LSTMBESSController()

    # 피크 임계값: 테스트 기간 실제 부하 기준
    test_df = merged_df.iloc[test_start_idx: test_start_idx + len(predicted_nl)]
    controller.set_peak_threshold(test_df['load_kw'].values)

    records = []
    for i, (_, row) in enumerate(test_df.iterrows()):
        if i >= len(predicted_nl):
            break

        res = controller.control(
            predicted_net_load = float(predicted_nl[i]),
            actual_load_kw     = float(row['load_kw']),
            actual_solar_kw    = float(row['solar_kw']),
            hour               = int(row['hour']),
            time_step          = config.TIME_STEP_HOURS,
        )

        records.append({
            'timestamp'            : row['timestamp'],
            'hour'                 : row['hour'],
            'load_kw'              : row['load_kw'],
            'solar_kw'             : row['solar_kw'],
            'smp'                  : row['smp'],
            'tariff_period'        : res['tariff_period'],
            'predicted_net_load_kw': predicted_nl[i],
            'bess_power_kw'        : res['bess_power_kw'],
            'grid_power_kw'        : res['grid_power_kw'],
            'soc'                  : res['soc'],
            'action'               : res['action'],
        })

    df = pd.DataFrame(records)
    df['charge_kw']    = df['bess_power_kw'].apply(lambda x: -x if x < 0 else 0.0)
    df['discharge_kw'] = df['bess_power_kw'].apply(lambda x:  x if x > 0 else 0.0)
    df['tariff_rate']  = df['tariff_period'].map(config.TOU_TARIFF)
    return df


def run_baseline_simulation(test_df: pd.DataFrame) -> pd.DataFrame:
    """
    BESS 없는 기준 시나리오 (Deep Learning 비교용)
    evaluator.py 에서 Rule-Based baseline 과 동일한 조건으로 사용합니다.
    """
    df = test_df.copy()
    df['bess_power_kw'] = 0.0
    df['charge_kw']     = 0.0
    df['discharge_kw']  = 0.0
    df['soc']           = 0.0
    df['action']        = 'none'
    df['grid_power_kw'] = df['load_kw'] - df['solar_kw']
    df['tariff_rate']   = df['tariff_period'].map(config.TOU_TARIFF)
    return df
