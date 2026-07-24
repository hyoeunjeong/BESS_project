import os
import pickle
import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler

import config

try:
    import api_client
    _API_AVAILABLE = True
except ImportError:
    _API_AVAILABLE = False
    print("[경고] api_client 모듈 없음 → CSV/가상 데이터만 사용")


# =====================================================================
# 태양광 데이터 통일 설정 (Rule-Based 와 동일한 실측 태양광 사용)
# ---------------------------------------------------------------------
# 세 모델(Rule-Based / LSTM / GRU)이 모두 동일한 태양광을 쓰도록,
# rule_based 폴더가 만들어 둔 기상청 실측 일사량(icsr) 캐시를 그대로 읽고
# rule_based 와 똑같은 변환식으로 태양광 발전량(kW)을 계산한다.
#
# 폴더 위치가 바뀌면 아래 _RB_SOLAR_CACHE 경로 한 줄만 고치면 된다.
# =====================================================================
# 이 파일(DL_LSTM/data_loader.py) 기준 상위 폴더의 rule_based 캐시 경로
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_RB_SOLAR_CACHE = os.path.join(
    _THIS_DIR, '..', 'rule_based', 'data', 'api_cache',
    'kma_st108_20250101_20251231.csv'
)
# Rule-Based 변환식과 동일한 상수
_RB_PV_CAPACITY = 50.0   # kW (rule_based config.PV_CAPACITY_KW 와 동일)


def _load_solar_from_rb_cache(start: str, end: str) -> pd.DataFrame:
    """
    rule_based 의 기상청 실측 일사량 캐시(icsr)를 읽어
    rule_based 와 동일한 변환식으로 태양광 발전량(kW)을 계산한다.

    rule_based 변환식:
        solar_kw = icsr * (cap/eta) * eta * 0.2778
                 = icsr * cap * 0.2778        (eta 가 상쇄됨)
        clip(0, cap)

    Returns
    -------
    DataFrame : [timestamp, solar_kw]
    """
    path = os.path.abspath(_RB_SOLAR_CACHE)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"rule_based 태양광 캐시 없음: {path}\n"
            f"   먼저 rule_based 폴더에서 main.py 를 실행해 캐시를 생성하세요."
        )

    df = pd.read_csv(path)
    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
    df = df.dropna(subset=['timestamp'])

    # icsr(일사량) → 태양광(kW), rule_based 와 동일
    df['solar_kw'] = (df['icsr'] * _RB_PV_CAPACITY * 0.2778).clip(
        lower=0, upper=_RB_PV_CAPACITY)

    # 요청 기간으로 자르기
    s = pd.Timestamp(start)
    e = pd.Timestamp(end) + pd.Timedelta(hours=23)
    df = df[(df['timestamp'] >= s) & (df['timestamp'] <= e)]

    print(f"   [태양광] rule_based 실측 캐시 사용: {len(df):,}행 "
          f"(평균 {df['solar_kw'].mean():.2f} kW, 최대 {df['solar_kw'].max():.2f} kW)")

    return df[['timestamp', 'solar_kw']].sort_values('timestamp').reset_index(drop=True)



# CSV 읽기 유틸
def _read_csv_auto(filepath: str) -> pd.DataFrame:
    for enc in ('cp949', 'utf-8', 'euc-kr'):
        try:
            return pd.read_csv(filepath, encoding=enc)
        except (UnicodeDecodeError, FileNotFoundError):
            continue
    raise ValueError(f"파일 읽기 실패: {filepath}")


def _wide_to_long(df: pd.DataFrame,
                  date_col: str,
                  value_col: str) -> pd.DataFrame:
    df = df.dropna(subset=[date_col])
    df = df[df[date_col].astype(str).str.strip() != '']
    df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
    df = df.dropna(subset=[date_col])
    hour_cols = [c for c in df.columns if '시' in str(c)
                 and c not in ('최대', '최소', '가중평균')]
    records = []
    for _, row in df.iterrows():
        for col in hour_cols:
            h  = int(''.join(filter(str.isdigit, str(col))))
            ts = row[date_col] + pd.Timedelta(hours=(h - 1 if h <= 24 else h))
            try:
                val = float(row[col])
            except (ValueError, TypeError):
                val = np.nan
            records.append({'timestamp': ts, value_col: val})
    result = pd.DataFrame(records).dropna(subset=[value_col])
    return result.sort_values('timestamp').reset_index(drop=True)

# 실제 데이터 로드
def load_kpx_demand_data(filepath: str) -> pd.DataFrame:
    return _wide_to_long(_read_csv_auto(filepath), '날짜', 'load_mw')


def load_kpx_smp_data(filepath: str) -> pd.DataFrame:
    return _wide_to_long(_read_csv_auto(filepath), '기간', 'smp')


def scale_load_data(load_df: pd.DataFrame,
                    target_kw: float = config.TARGET_AVG_LOAD_KW) -> pd.DataFrame:
    df     = load_df.copy()
    scale  = target_kw / (df['load_mw'].mean() * 1000)
    df['load_kw'] = df['load_mw'] * 1000 * scale
    return df[['timestamp', 'load_kw']]


# 가상 데이터 (Fallback)
def generate_sample_load_data(days: int = 30, seed: int = 42) -> pd.DataFrame:
    np.random.seed(seed)
    ts = pd.date_range('2025-01-01', periods=days * 24, freq='h')
    base_map = {range(0,6):15, range(6,9):25, range(9,12):35,
                range(12,14):40, range(14,18):38, range(18,22):30,
                range(22,24):20}
    loads = []
    for t in ts:
        base = next((v for k,v in base_map.items() if t.hour in k), 20)
        if t.weekday() >= 5:
            base *= 0.8
        loads.append(max(5.0, base + np.random.normal(0, base * 0.1)))
    return pd.DataFrame({'timestamp': ts, 'load_kw': loads})


def generate_sample_smp_data(days: int = 30, seed: int = 43) -> pd.DataFrame:
    np.random.seed(seed)
    ts   = pd.date_range('2025-01-01', periods=days * 24, freq='h')
    bmap = {'on_peak': 160.0, 'mid_peak': 120.0, 'off_peak': 80.0}
    smps = [max(40.0, bmap[config.get_tariff_period(t.hour, t.month)]
                + np.random.normal(0, 15)) for t in ts]
    return pd.DataFrame({'timestamp': ts, 'smp': smps})


# 태양광 (Deep Learning 프로젝트 내부 사용)
_MONTHLY_FACTOR = {1:0.55,2:0.65,3:0.80,4:0.90,5:0.95,6:0.85,
                   7:0.70,8:0.75,9:0.85,10:0.80,11:0.65,12:0.55}

def simulate_solar(days: int, start_date: str = '2024-01-01',
                   capacity_kw: float = config.PV_CAPACITY_KW,
                   seed: int = 42) -> pd.DataFrame:
    np.random.seed(seed)
    ts = pd.date_range(start=start_date, periods=days * 24, freq='h')
    out = []
    for t in ts:
        h = t.hour
        tf = np.exp(-((h - 12) ** 2) / 8.0) if 6 <= h <= 18 else 0.0
        wf = float(np.clip(np.random.normal(0.85, 0.15), 0.3, 1.0))
        out.append(max(0.0, capacity_kw * tf * _MONTHLY_FACTOR[t.month] * wf))
    return pd.DataFrame({'timestamp': ts, 'solar_kw': out})


# 피처 엔지니어링 + 정규화 + LSTM 시퀀스 생성
FEATURE_COLS = [
    'load_kw',          # 전력 부하
    'solar_kw',         # 태양광 발전량
    'net_load_kw',      # 순부하 = 부하 - 태양광
    'smp',              # 시장가격 (SMP)
    'tariff_rate',      # TOU 요금
    'hour_sin',         # 시간 순환 인코딩 (sin)
    'hour_cos',         # 시간 순환 인코딩 (cos)
    'dow_sin',          # 요일 순환 인코딩 (sin)
    'dow_cos',          # 요일 순환 인코딩 (cos)
    'month_sin',        # 월 순환 인코딩 (sin)
    'month_cos',        # 월 순환 인코딩 (cos)
    'is_weekend',       # 주말 여부 (0/1)
]
TARGET_COL = 'net_load_kw'   # LSTM 예측 타깃: 순부하


def add_features(merged: pd.DataFrame) -> pd.DataFrame:
    """
    시뮬레이션 데이터에 LSTM 입력 피처를 추가합니다.

    Parameters
    ----------
    merged : timestamp, load_kw, solar_kw, smp, hour, tariff_period 포함 DataFrame

    Returns
    -------
    DataFrame : 피처 컬럼 추가됨
    """
    df = merged.copy()
    dt = pd.to_datetime(df['timestamp'])

    df['net_load_kw'] = (df['load_kw'] - df['solar_kw']).clip(lower=0)
    df['tariff_rate'] = [config.get_tariff_rate(int(h), int(m), int(wd), d)
                         for h, m, wd, d in zip(dt.dt.hour, dt.dt.month, dt.dt.weekday, dt.dt.date)]

    # 순환 인코딩
    df['hour_sin']  = np.sin(2 * np.pi * dt.dt.hour      / 24)
    df['hour_cos']  = np.cos(2 * np.pi * dt.dt.hour      / 24)
    df['dow_sin']   = np.sin(2 * np.pi * dt.dt.dayofweek / 7)
    df['dow_cos']   = np.cos(2 * np.pi * dt.dt.dayofweek / 7)
    df['month_sin'] = np.sin(2 * np.pi * dt.dt.month     / 12)
    df['month_cos'] = np.cos(2 * np.pi * dt.dt.month     / 12)
    df['is_weekend'] = (dt.dt.dayofweek >= 5).astype(float)

    return df


def make_sequences(df: pd.DataFrame,
                   seq_len: int      = config.SEQ_LEN,
                   horizon: int      = config.PRED_HORIZON,
                   scaler            = None,
                   fit_scaler: bool  = True
                   ) -> tuple:
    """
    피처 정규화 후 LSTM 입력 시퀀스 생성

    Parameters
    ----------
    df         : add_features() 적용된 DataFrame
    seq_len    : 입력 시퀀스 길이 (기본 24)
    horizon    : 예측 horizon (기본 1)
    scaler     : 기존 scaler (None 이면 새로 fit)
    fit_scaler : True → scaler fit, False → transform only

    Returns
    -------
    X      : (N, seq_len, n_features)  numpy float32
    y      : (N,)                       numpy float32  (정규화된 타깃)
    scaler : 학습된 MinMaxScaler
    """
    data = df[FEATURE_COLS].values.astype(np.float32)

    if scaler is None:
        scaler = MinMaxScaler()
    if fit_scaler:
        scaler.fit(data)
    data_scaled = scaler.transform(data)

    target_idx = FEATURE_COLS.index(TARGET_COL)
    X, y = [], []
    for i in range(seq_len, len(data_scaled) - horizon + 1):
        X.append(data_scaled[i - seq_len: i])
        y.append(data_scaled[i + horizon - 1, target_idx])

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32), scaler


def inverse_target(y_scaled: np.ndarray, scaler: MinMaxScaler) -> np.ndarray:
    """
    정규화된 타깃(net_load_kw)을 원래 kW 스케일로 복원합니다.
    """
    target_idx = FEATURE_COLS.index(TARGET_COL)
    dummy = np.zeros((len(y_scaled), len(FEATURE_COLS)), dtype=np.float32)
    dummy[:, target_idx] = y_scaled
    return scaler.inverse_transform(dummy)[:, target_idx]


def split_sequences(X: np.ndarray, y: np.ndarray,
                    train_r: float = config.TRAIN_RATIO,
                    val_r  : float = config.VAL_RATIO
                    ) -> tuple:
    """
    시계열 순서를 유지한 Train / Val / Test 분할
    (shuffle=False)

    Returns
    -------
    (X_tr, y_tr), (X_val, y_val), (X_te, y_te)
    """
    n      = len(X)
    t1     = int(n * train_r)
    t2     = int(n * (train_r + val_r))
    return (X[:t1], y[:t1]), (X[t1:t2], y[t1:t2]), (X[t2:], y[t2:])


# Scaler 저장 / 불러오기
def save_scaler(scaler: MinMaxScaler, path: str = config.SCALER_SAVE_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        pickle.dump(scaler, f)
    print(f"[Scaler 저장] {path}")


def load_scaler(path: str = config.SCALER_SAVE_PATH) -> MinMaxScaler:
    with open(path, 'rb') as f:
        return pickle.load(f)


# 통합 로드 함수
def _validate_series(s: pd.Series, name: str, min_unique_ratio: float = 0.05):
    """수집 데이터가 상수/반복(하루치×N) 패턴인지 검사한다(§1-1 가짜 SMP 차단).

    - 고유값 비율이 너무 낮으면 수집 실패로 간주.
    - 24시간 배수 길이면, 모든 날짜의 24시간 프로파일이 동일한지(반복) 검사.
    """
    s = pd.to_numeric(s, errors='coerce').dropna()
    u = s.nunique()
    if u < len(s) * min_unique_ratio:
        raise ValueError(
            f"{name}: 고유값 {u}개 / {len(s)}행 — 반복 패턴 의심. 수집 실패로 간주한다.")
    if len(s) % 24 == 0 and len(s) >= 48:
        daily = s.values.reshape(-1, 24)
        if np.abs(daily - daily[0]).max() < 1e-9:
            raise ValueError(
                f"{name}: 모든 날짜의 24시간 프로파일이 동일. 수집 실패(하루치×N 반복).")


def _report_data_source(df: pd.DataFrame, src: dict):
    """사용된 데이터 출처·범위·결측 시점을 명시적으로 출력한다(재현성)."""
    print(f"[데이터 출처] 부하={src.get('load')} / SMP={src.get('smp')} / 태양광={src.get('solar')}")
    ts = pd.to_datetime(df['timestamp'])
    full = pd.date_range(ts.min(), ts.max(), freq='h')
    missing = len(full) - len(df)
    print(f"[데이터 범위] {ts.min()} ~ {ts.max()}, {len(df):,}시점 (결측 {missing}시점)")
    if missing > 0:
        from collections import Counter
        miss = sorted(set(full) - set(ts))
        by_hour = Counter(t.hour for t in miss)
        print(f"   결측 시각대 분포(시각:개수): {dict(sorted(by_hour.items()))}")


def load_data(load_path: str = config.LOAD_DATA_PATH,
              smp_path : str = config.SMP_DATA_PATH,
              fallback_days: int = config.SIMULATION_DAYS,
              source   : str = None) -> pd.DataFrame:
    """
    데이터 로드 (우선순위: API/캐시 → CSV → 가상 데이터)
    STRICT_DATA=True 면 실측(API캐시/기상청 실측 태양광)만 허용하고 폴백은 예외.

    Parameters
    ----------
    source : 'api' | 'csv' | 'auto' | None
             None 이면 config.DATA_SOURCE 사용

    Returns
    -------
    DataFrame : add_features() 적용 완료
    """
    source = source or config.DATA_SOURCE
    strict = getattr(config, 'STRICT_DATA', False)
    load_df, smp_df, solar_df = None, None, None
    src = {'load': None, 'smp': None, 'solar': None}

    # ── 1순위: API / 캐시
    if source in ('api', 'auto') and _API_AVAILABLE:
        try:
            print("[데이터] 공공 API/캐시 호출 시도")
            load_df, smp_df, solar_df, src = _load_from_api(strict=strict)
        except Exception as ex:
            if strict:
                raise RuntimeError(
                    f"[STRICT_DATA] 실측 데이터 로드 실패로 중단합니다: {ex}") from ex
            print(f"[데이터] API 실패 → CSV로 폴백: {ex}")
            load_df = smp_df = solar_df = None

    # ── 2순위: CSV (STRICT 면 금지 — 태양광이 합성이라 논문과 달라짐)
    if load_df is None and source in ('csv', 'auto'):
        if strict:
            raise RuntimeError(
                "[STRICT_DATA] CSV 폴백은 태양광이 합성(simulate_solar)이라 금지됩니다. "
                "기상청 실측 캐시(API캐시) 경로를 사용하세요.")
        if os.path.exists(load_path) and os.path.exists(smp_path):
            print("[데이터] 실제 KPX CSV 로드")
            load_raw = load_kpx_demand_data(load_path)
            load_df  = scale_load_data(load_raw)
            smp_df   = load_kpx_smp_data(smp_path)
            days     = (load_df['timestamp'].max()
                        - load_df['timestamp'].min()).days + 1
            start    = str(load_df['timestamp'].min().date())
            solar_df = simulate_solar(days=days, start_date=start)
            src = {'load': 'KPX CSV', 'smp': 'KPX CSV', 'solar': '합성(simulate_solar)'}

    # ── 3순위: 가상 데이터 (STRICT 면 금지)
    if load_df is None:
        if strict:
            raise RuntimeError(
                "[STRICT_DATA] 실측 데이터가 없어 중단합니다. 가상 데이터 대체는 금지됩니다.")
        print(f"[데이터] 모든 소스 실패 → 가상 데이터 {fallback_days}일 생성")
        load_df  = generate_sample_load_data(fallback_days)
        smp_df   = generate_sample_smp_data(fallback_days)
        solar_df = simulate_solar(fallback_days)
        src = {'load': '가상', 'smp': '가상', 'solar': '합성'}

    # ── 수집 데이터 진위 검증 (§1-1: 반복/상수 패턴 차단)
    _validate_series(smp_df['smp'],        'SMP')
    _validate_series(load_df['load_kw'],   '부하')
    _validate_series(solar_df['solar_kw'], '태양광', min_unique_ratio=0.02)

    # ── 병합
    df = load_df.merge(smp_df,   on='timestamp', how='inner')
    df = df.merge(solar_df,      on='timestamp', how='inner')
    df['hour']          = df['timestamp'].dt.hour
    df['date']          = df['timestamp'].dt.date
    df['tariff_period'] = [config.get_tariff_period(int(h), int(m), int(wd), d)
                           for h, m, wd, d in zip(df['hour'], df['timestamp'].dt.month,
                                                  df['timestamp'].dt.weekday, df['timestamp'].dt.date)]
    df = df.sort_values('timestamp').reset_index(drop=True)

    # ── 데이터 출처·범위·결측 보고 (재현성)
    _report_data_source(df, src)

    # ── 피처 추가
    df = add_features(df)
    print(f"[데이터] 총 {len(df):,}h ({len(df)//24}일) 로드 완료")
    return df


def _load_from_api(strict: bool = False) -> tuple:
    """
    API/캐시에서 부하/SMP/태양광 데이터 수집

    STRICT_DATA=True 면 SMP·태양광 합성 폴백을 예외로 차단한다.

    Returns
    -------
    (load_df, smp_df, solar_df, src)  # src = {'load','smp','solar'} 출처 딕셔너리
    """
    start = config.API_START_DATE
    end   = config.API_END_DATE

    # 부하 데이터 (필수)
    load_raw = api_client.fetch_load_data(start, end)
    load_df  = scale_load_data(load_raw)

    # SMP 데이터 (STRICT 면 실패 시 예외, 아니면 가상 폴백)
    try:
        smp_df = api_client.fetch_smp_data(start, end)
    except Exception as ex:
        if strict:
            raise RuntimeError(f"[STRICT_DATA] SMP 실측 로드 실패: {ex}") from ex
        print(f"   [경고] SMP API 실패 → 가상 SMP 데이터로 대체: {ex}")
        days = (pd.Timestamp(end) - pd.Timestamp(start)).days + 1
        smp_df = generate_sample_smp_data(days)
        # timestamp 범위를 API 기간에 맞춰 재설정
        smp_df['timestamp'] = pd.date_range(
            start=start, periods=len(smp_df), freq='h')

    # 태양광 데이터 (기상청 실측 캐시로만; STRICT 면 실패 시 예외)
    try:
        solar_df = _load_solar_from_rb_cache(start, end)
    except Exception as ex:
        if strict:
            raise RuntimeError(
                f"[STRICT_DATA] 기상청 실측 태양광(_load_solar_from_rb_cache) 로드 실패: {ex}") from ex
        print(f"   [경고] rule_based 실측 태양광 로드 실패 → 시뮬레이션 사용: {ex}")
        days = (pd.Timestamp(end) - pd.Timestamp(start)).days + 1
        solar_df = simulate_solar(days=days, start_date=start)

    # 시간 정합 (모든 DataFrame을 동일 기간으로 자르기)
    common_start = max(load_df['timestamp'].min(),
                       smp_df['timestamp'].min(),
                       solar_df['timestamp'].min())
    common_end   = min(load_df['timestamp'].max(),
                       smp_df['timestamp'].max(),
                       solar_df['timestamp'].max())

    load_df  = load_df[(load_df['timestamp']  >= common_start) & (load_df['timestamp']  <= common_end)]
    smp_df   = smp_df[(smp_df['timestamp']    >= common_start) & (smp_df['timestamp']   <= common_end)]
    solar_df = solar_df[(solar_df['timestamp']>= common_start) & (solar_df['timestamp'] <= common_end)]

    print(f"   [API 통합] 공통 기간: {common_start.date()} ~ {common_end.date()}")
    src = {'load': 'API캐시', 'smp': 'API캐시', 'solar': '기상청실측캐시(kma_st108)'}
    return load_df, smp_df, solar_df, src


def _solar_from_weather(weather_df: pd.DataFrame,
                         capacity_kw: float = config.PV_CAPACITY_KW) -> pd.DataFrame:
    """
    기상청 일사량 데이터를 태양광 발전량(kW)으로 변환

    일사량(MJ/m²/h) × 면적 × 효율 → kWh → kW
    PV_CAPACITY_KW 50kW 가정 시 약 280m² 패널 면적
    """
    df = weather_df.copy()
    # 일사량 NaN → 0 (야간/구름)
    df['solar_radiation'] = df['solar_radiation'].fillna(0.0)

    # 1 MJ/m²/h = 0.2778 kWh/m²/h
    # 50kW 시스템 ≒ 280m² × 0.18 효율로 역산
    area_m2 = capacity_kw / (1.0 * config.PV_EFFICIENCY)   # STC 1kW/m² 기준
    df['solar_kw'] = (df['solar_radiation'] * 0.2778
                      * area_m2 * config.PV_EFFICIENCY).clip(lower=0, upper=capacity_kw)

    return df[['timestamp', 'solar_kw']]


if __name__ == '__main__':
    df = load_data()
    X, y, scaler = make_sequences(df)
    (X_tr, y_tr), (X_val, y_val), (X_te, y_te) = split_sequences(X, y)
    print(f"X shape : {X.shape}")
    print(f"Train   : {X_tr.shape}  Val: {X_val.shape}  Test: {X_te.shape}")
    print(f"피처 목록: {FEATURE_COLS}")