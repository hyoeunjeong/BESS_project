import pandas as pd
import config
from bess_controller import RuleBasedBESSController


def run_simulation(merged: pd.DataFrame,
                   controller: RuleBasedBESSController = None) -> pd.DataFrame:
    """
    Rule-Based BESS 시뮬레이션 실행

    Parameters
    ----------
    merged     : data_loader.merge_real_data() 가 반환한 DataFrame
                 필수 컬럼: timestamp, load_kw, solar_kw, smp, hour
    controller : RuleBasedBESSController 인스턴스
                 (None 이면 기본값으로 생성)

    Returns
    -------
    DataFrame : 입력 + 제어 결과 통합
        timestamp, hour, load_kw, solar_kw, smp,
        tariff_period, tariff_rate,
        bess_power_kw, charge_kw, discharge_kw,
        grid_power_kw, soc, action, blocked
    """
    if controller is None:
        controller = RuleBasedBESSController()

    records = []
    for _, row in merged.iterrows():
        res = controller.control(
            load_kw  = row['load_kw'],
            solar_kw = row['solar_kw'],
            hour     = int(row['hour']),
            time_step= config.TIME_STEP_HOURS,
            month    = int(pd.Timestamp(row['timestamp']).month),
            weekday  = int(pd.Timestamp(row['timestamp']).weekday()),
            date     = pd.Timestamp(row['timestamp']).date(),
        )
        records.append({
            'timestamp'     : row['timestamp'],
            'hour'          : row['hour'],
            'load_kw'       : row['load_kw'],
            'solar_kw'      : row['solar_kw'],
            'smp'           : row['smp'],
            'tariff_period' : res['tariff_period'],
            'bess_power_kw' : res['bess_power_kw'],
            'grid_power_kw' : res['grid_power_kw'],
            'soc'           : res['soc'],
            'action'        : res['action'],
            'blocked'       : res['blocked'],
        })

    df = pd.DataFrame(records)

    # 충전/방전 분리 컬럼
    df['charge_kw']    = df['bess_power_kw'].apply(lambda x: -x if x < 0 else 0.0)
    df['discharge_kw'] = df['bess_power_kw'].apply(lambda x:  x if x > 0 else 0.0)

    # 시간대별 TOU 요금 컬럼 (계절·시간대별, 요일/공휴일 반영)
    _ts = pd.to_datetime(df['timestamp'])
    df['tariff_rate']  = [config.get_tariff_rate(int(h), int(m), int(wd), d)
                          for h, m, wd, d in zip(df['hour'], _ts.dt.month, _ts.dt.weekday, _ts.dt.date)]

    return df


def run_baseline_simulation(merged: pd.DataFrame) -> pd.DataFrame:
    """
    BESS 없는 기준 시나리오 (비교용)
    모든 전력을 계통 + 태양광으로만 충당한다.

    Returns
    -------
    DataFrame : run_simulation() 과 동일한 컬럼 구조
    """
    df = merged.copy()
    df['bess_power_kw'] = 0.0
    df['charge_kw']     = 0.0
    df['discharge_kw']  = 0.0
    df['soc']           = 0.0
    df['action']        = 'none'
    df['blocked']       = None

    # 계통 전력 = 부하 - 태양광 (음수 = 잉여 판매)
    df['grid_power_kw'] = df['load_kw'] - df['solar_kw']
    _ts = pd.to_datetime(df['timestamp'])
    df['tariff_rate']   = [config.get_tariff_rate(int(h), int(m), int(wd), d)
                           for h, m, wd, d in zip(df['hour'], _ts.dt.month, _ts.dt.weekday, _ts.dt.date)]

    return df


if __name__ == '__main__':
    from data_loader import generate_sample_load_data, generate_sample_smp_data
    from data_loader import merge_real_data
    from solar_model import simulate_solar_generation

    days     = 7
    load_df  = generate_sample_load_data(days=days)
    smp_df   = generate_sample_smp_data(days=days)
    solar_df = simulate_solar_generation(days=days)
    merged   = merge_real_data(load_df, smp_df, solar_df)

    result = run_simulation(merged)
    print(f"시뮬레이션 완료: {len(result)}행")
    print(result[['timestamp','load_kw','solar_kw',
                  'bess_power_kw','soc','action']].head(24).to_string(index=False))
