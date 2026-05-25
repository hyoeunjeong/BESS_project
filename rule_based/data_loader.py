"""
모든 데이터를 API로 수집:
- 부하 → ODcloud API
- SMP → Public Data Portal API
- 태양광 → 기상청 ASOS API
"""

import numpy as np
import pandas as pd
import os
import config


# ─────────────────────────────────────────────────────────────────────
# 내부 유틸 (CSV 백업용)
# ─────────────────────────────────────────────────────────────────────
def _read_csv_auto(filepath: str) -> pd.DataFrame:
    """인코딩 자동 감지"""
    for enc in ('cp949', 'utf-8', 'euc-kr'):
        try:
            return pd.read_csv(filepath, encoding=enc)
        except (UnicodeDecodeError, FileNotFoundError):
            continue
    raise ValueError(f"파일을 읽을 수 없습니다: {filepath}")


def _wide_to_long(df: pd.DataFrame, date_col: str, value_col: str) -> pd.DataFrame:
    """가로 형식 → 세로 형식"""
    df = df.dropna(subset=[date_col])
    df = df[df[date_col].astype(str).str.strip() != '']
    df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
    df = df.dropna(subset=[date_col])

    hour_cols = [c for c in df.columns if '시' in str(c)
                 and c not in ('최대', '최소', '가중평균')]

    records = []
    for _, row in df.iterrows():
        date = row[date_col]
        for col in hour_cols:
            h = int(''.join(filter(str.isdigit, str(col))))
            actual_hour = h - 1 if h <= 24 else h
            ts = date + pd.Timedelta(hours=actual_hour)
            try:
                val = float(row[col])
            except (ValueError, TypeError):
                val = np.nan
            records.append({'timestamp': ts, value_col: val})

    result = pd.DataFrame(records).dropna(subset=[value_col])
    return result.sort_values('timestamp').reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────
# CSV 백업 로더 (API 실패 시)
# ─────────────────────────────────────────────────────────────────────
def load_kpx_demand_data(filepath: str) -> pd.DataFrame:
    """[백업] 한국전력거래소 시간별 전국 전력수요량 CSV"""
    raw = _read_csv_auto(filepath)
    return _wide_to_long(raw, date_col='날짜', value_col='load_mw')


def load_kpx_smp_data(filepath: str) -> pd.DataFrame:
    """[백업] 한국전력거래소 시간별 SMP CSV"""
    raw = _read_csv_auto(filepath)
    return _wide_to_long(raw, date_col='기간', value_col='smp')


def scale_load_data(load_df: pd.DataFrame,
                    target_kw: float = config.TARGET_AVG_LOAD_KW) -> pd.DataFrame:
    """전국 MW → 소규모 시설 kW 스케일링"""
    df = load_df.copy()
    avg_kw = df['load_mw'].mean() * 1000
    scale = target_kw / avg_kw
    df['load_kw'] = df['load_mw'] * 1000 * scale
    return df[['timestamp', 'load_kw']]


def merge_real_data(load_df: pd.DataFrame, smp_df: pd.DataFrame,
                    solar_df: pd.DataFrame) -> pd.DataFrame:
    """부하 + SMP + 태양광 병합"""
    df = load_df.merge(smp_df,   on='timestamp', how='inner')
    df = df.merge(solar_df,      on='timestamp', how='inner')
    df['hour']          = df['timestamp'].dt.hour
    df['date']          = df['timestamp'].dt.date
    df['tariff_period'] = df['hour'].apply(config.get_tariff_period)
    return df.sort_values('timestamp').reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────
# 가상 데이터 (백업용)
# ─────────────────────────────────────────────────────────────────────
def generate_sample_load_data(days: int = 30, seed: int = 42) -> pd.DataFrame:
    """가상 부하 데이터"""
    np.random.seed(seed)
    timestamps = pd.date_range('2025-01-01', periods=days * 24, freq='h')
    loads = []
    base_map = {range(0,6):15, range(6,9):25, range(9,12):35,
                range(12,14):40, range(14,18):38, range(18,22):30,
                range(22,24):20}
    for ts in timestamps:
        base = next((v for k,v in base_map.items() if ts.hour in k), 20)
        if ts.weekday() >= 5:
            base *= 0.8
        loads.append(max(5.0, base + np.random.normal(0, base * 0.1)))
    return pd.DataFrame({'timestamp': timestamps, 'load_kw': loads})


def generate_sample_smp_data(days: int = 30, seed: int = 43) -> pd.DataFrame:
    """가상 SMP 데이터"""
    np.random.seed(seed)
    timestamps = pd.date_range('2025-01-01', periods=days * 24, freq='h')
    base_map = {'on_peak': 160.0, 'mid_peak': 120.0, 'off_peak': 80.0}
    smps = [max(40.0, base_map[config.get_tariff_period(ts.hour)]
                + np.random.normal(0, 15)) for ts in timestamps]
    return pd.DataFrame({'timestamp': timestamps, 'smp': smps})


# ─────────────────────────────────────────────────────────────────────
# 통합 데이터 로드 (완전 API)
# ─────────────────────────────────────────────────────────────────────
def load_all_data(use_load_api: bool = True,
                  use_smp_api: bool = True,
                  use_kma_api: bool = True) -> pd.DataFrame:
    """
    모든 데이터를 API로 수집하여 시뮬레이션용으로 가공

    Parameters
    ----------
    use_load_api : True = 부하 API, False = 부하 CSV
    use_smp_api  : True = SMP API, False = SMP CSV
    use_kma_api  : True = 기상청 API, False = 태양광 시뮬레이션
    """
    # 1. 부하 데이터
    if use_load_api:
        print("[데이터] 부하 API 호출 (ODcloud)")
        from api_client import fetch_load_data
        load_raw = fetch_load_data()
        if len(load_raw) == 0:
            raise RuntimeError("부하 API 데이터 수집 실패")
    else:
        if not os.path.exists(config.LOAD_DATA_PATH):
            raise FileNotFoundError(f"부하 CSV 없음: {config.LOAD_DATA_PATH}")
        print(f"[데이터] 부하 CSV 로드: {config.LOAD_DATA_PATH}")
        load_raw = load_kpx_demand_data(config.LOAD_DATA_PATH)

    load_df = scale_load_data(load_raw, config.TARGET_AVG_LOAD_KW)
    print(f"   - 부하: {len(load_df):,}시간 "
          f"({load_df['timestamp'].min().date()} ~ {load_df['timestamp'].max().date()})")

    # 2. SMP 데이터
    if use_smp_api:
        print("[데이터] SMP API 호출")
        from api_client import fetch_smp_data
        smp_full = fetch_smp_data(area='육지')
        smp_df = smp_full[['timestamp', 'smp']]
    else:
        if not os.path.exists(config.SMP_DATA_PATH):
            raise FileNotFoundError(f"SMP CSV 없음: {config.SMP_DATA_PATH}")
        print(f"[데이터] SMP CSV 로드: {config.SMP_DATA_PATH}")
        smp_df = load_kpx_smp_data(config.SMP_DATA_PATH)
    print(f"   - SMP: {len(smp_df):,}시간")

    # 3. 태양광 데이터
    if use_kma_api:
        print("[데이터] 기상청 API 호출 → 태양광 변환")
        from api_client import fetch_kma_data, convert_irradiance_to_solar
        kma_df   = fetch_kma_data()
        solar_df = convert_irradiance_to_solar(kma_df)
    else:
        print("[데이터] 태양광 시뮬레이션 사용")
        from solar_model import simulate_solar_generation
        sim_days = (load_df['timestamp'].max() - load_df['timestamp'].min()).days + 1
        solar_df = simulate_solar_generation(days=sim_days,
                                              capacity_kw=config.PV_CAPACITY_KW)
        solar_df['timestamp'] = pd.date_range(
            start=load_df['timestamp'].min(),
            periods=len(solar_df), freq='h'
        )
    print(f"   - 태양광: {len(solar_df):,}시간")

    # 4. 병합
    merged = merge_real_data(load_df, smp_df, solar_df)
    print(f"[데이터] 병합 완료: 총 {len(merged):,}시간 ({len(merged)/24:.0f}일)")
    return merged
