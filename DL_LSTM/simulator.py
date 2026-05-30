import numpy as np
import pandas as pd

import config
from bess_controller import LSTMBESSController
from data_loader     import FEATURE_COLS, TARGET_COL, inverse_target


def run_lstm_simulation(merged_df      : pd.DataFrame,
                        predicted_nl   : np.ndarray,
                        test_start_idx : int,
                        controller     : LSTMBESSController = None) -> pd.DataFrame:
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
    df = test_df.copy()
    df['bess_power_kw'] = 0.0
    df['charge_kw']     = 0.0
    df['discharge_kw']  = 0.0
    df['soc']           = 0.0
    df['action']        = 'none'
    df['grid_power_kw'] = df['load_kw'] - df['solar_kw']
    df['tariff_rate']   = df['tariff_period'].map(config.TOU_TARIFF)
    return df
