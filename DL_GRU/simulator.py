import numpy as np
import pandas as pd

import config
from bess_controller import GRUBESSController
from data_loader     import FEATURE_COLS, TARGET_COL, inverse_target


def run_gru_simulation(merged_df      : pd.DataFrame,
                        predicted_nl   : np.ndarray,
                        test_start_idx : int,
                        controller     : GRUBESSController = None) -> pd.DataFrame:
    if controller is None:
        controller = GRUBESSController()

    test_df = merged_df.iloc[test_start_idx: test_start_idx + len(predicted_nl)]
    # [정정(마)/§5-3] 데이터 누수 차단 — 피크 임계값·수요상한을 학습 구간(전체 데이터 앞 70%)
    #   에서만 산출한다. FULL_YEAR 값과 무관하게 동일한 값이 나오도록 merged_df 기준.
    n_tr_full = int(len(merged_df) * config.TRAIN_RATIO)
    train_slice = merged_df.iloc[:n_tr_full]
    controller.set_peak_threshold(train_slice['load_kw'].values)

    # [§4] 수요전력 상한(P_CAP) = 학습 구간 무제어 요금적용전력(중간·최대부하 순부하 최대)
    if hasattr(controller, 'set_demand_cap'):
        _pmask = train_slice['tariff_period'].isin(['mid_peak', 'on_peak'])
        _pnet = (train_slice['load_kw'] - train_slice['solar_kw']).clip(lower=0)
        controller.set_demand_cap(float(_pnet[_pmask].max()) if bool(_pmask.any()) else None)

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
            month              = int(pd.Timestamp(row['timestamp']).month),
            weekday            = int(pd.Timestamp(row['timestamp']).weekday()),
            date               = pd.Timestamp(row['timestamp']).date(),
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
            'reason'               : res['reason'],   # [§6-1] 제어 사유 관측
        })

    df = pd.DataFrame(records)
    df['charge_kw']    = df['bess_power_kw'].apply(lambda x: -x if x < 0 else 0.0)
    df['discharge_kw'] = df['bess_power_kw'].apply(lambda x:  x if x > 0 else 0.0)
    _ts = pd.to_datetime(df['timestamp'])
    df['tariff_rate']  = [config.get_tariff_rate(int(h), int(m), int(wd), d)
                          for h, m, wd, d in zip(df['hour'], _ts.dt.month, _ts.dt.weekday, _ts.dt.date)]
    return df


def run_baseline_simulation(test_df: pd.DataFrame) -> pd.DataFrame:
    df = test_df.copy()
    df['bess_power_kw'] = 0.0
    df['charge_kw']     = 0.0
    df['discharge_kw']  = 0.0
    df['soc']           = 0.0
    df['action']        = 'none'
    df['grid_power_kw'] = df['load_kw'] - df['solar_kw']
    _ts = pd.to_datetime(df['timestamp'])
    df['tariff_rate']   = [config.get_tariff_rate(int(h), int(m), int(wd), d)
                           for h, m, wd, d in zip(df['hour'], _ts.dt.month, _ts.dt.weekday, _ts.dt.date)]
    return df
