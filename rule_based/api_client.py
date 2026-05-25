"""
api_client.py  ─  공공데이터포털 API 클라이언트
==========================================================
1. 부하 데이터 (ODcloud API)
   URL: https://api.odcloud.kr/api/15065266/v1/uddi:6ade08d2-...
   응답: data 배열, {날짜, 1시~24시}

2. SMP 데이터 (Public Data Portal API)
   URL: https://apis.data.go.kr/B552115/SmpWithForecastDemand/...
   응답: items.item 배열, {date, hour, areaName, smp, slfd, mlfd}

3. 기상청 ASOS (Public Data Portal API)
   URL: http://apis.data.go.kr/1360000/AsosHourlyInfoService/...
   응답: items.item 배열, {tm, icsr, ta, hm, ws}
"""

import os
import time
import pandas as pd
import requests
from urllib.parse import unquote
from datetime import datetime, timedelta
import config


# =====================================================================
# 내부 유틸
# =====================================================================
def _ensure_cache_dir():
    os.makedirs(config.API_CACHE_DIR, exist_ok=True)


def _cache_path(name: str) -> str:
    return os.path.join(
        config.API_CACHE_DIR,
        f"{name}_{config.API_START_DATE}_{config.API_END_DATE}.csv"
    )


def _date_range(start: str, end: str):
    s = datetime.strptime(start, '%Y%m%d')
    e = datetime.strptime(end,   '%Y%m%d')
    days = []
    cur = s
    while cur <= e:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def _validate_key(key: str, name: str):
    if not key or '여기에' in key or len(key) < 20:
        raise ValueError(
            f"\n[ERROR] {name} API 인증키가 설정되지 않았습니다.\n"
            f"  config.py 의 API_KEYS['{name.lower()}'] 에 인증키를 입력하세요.\n"
        )


# =====================================================================
# 1. 부하 데이터 API (ODcloud)
# =====================================================================
def fetch_load_data(use_cache: bool = True) -> pd.DataFrame:
    _ensure_cache_dir()
    cache = _cache_path('load')

    if use_cache and os.path.exists(cache):
        print(f"[API] 부하 캐시 사용: {cache}")
        return pd.read_csv(cache, parse_dates=['timestamp'])

    _validate_key(config.API_KEYS['load'], 'LOAD')

    # 2025년 데이터 엔드포인트
    url = config.API_LOAD_URL

    print(f"[API] 부하 데이터 수집 시작 (ODcloud)")

    # 1년치를 한 번에 받기 (365행)
    params = {
        'serviceKey': unquote(config.API_KEYS['load']),
        'page'      : 1,
        'perPage'   : 400,    # 1년 = 365일
        'returnType': 'JSON',
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        data = resp.json()
        items = data.get('data', [])

        print(f"   - 수집된 일자: {len(items)}일")

        records = []
        for item in items:
            date_str = item.get('날짜', '')
            try:
                date = pd.to_datetime(date_str)
            except Exception:
                continue

            # 1시~24시 컬럼 처리
            for h in range(1, 25):
                key = f'{h}시'
                if key in item:
                    val = item[key]
                    if val is None or val == '':
                        continue
                    # 1시 → 0시, 24시 → 23시 변환
                    actual_h = h - 1
                    ts = date + timedelta(hours=actual_h)
                    records.append({
                        'timestamp': ts,
                        'load_mw'  : float(val),
                    })

        df = pd.DataFrame(records).sort_values('timestamp').reset_index(drop=True)

        # 시작/종료 날짜 필터링 (config 설정)
        start_dt = pd.to_datetime(config.API_START_DATE, format='%Y%m%d')
        end_dt   = pd.to_datetime(config.API_END_DATE,   format='%Y%m%d') + pd.Timedelta(hours=23)
        df = df[(df['timestamp'] >= start_dt) & (df['timestamp'] <= end_dt)]

        if len(df) > 0:
            df.to_csv(cache, index=False, encoding='utf-8-sig')
            print(f"[API] 부하 캐시 저장: {cache}")
            print(f"   - 평균 부하: {df['load_mw'].mean():.0f} MWh")
            print(f"   - 최대 부하: {df['load_mw'].max():.0f} MWh")
            print(f"   - 데이터 수: {len(df):,}시간")

        return df

    except Exception as exc:
        print(f"[API] 부하 데이터 수집 실패: {exc}")
        if 'resp' in dir():
            print(f"   응답 일부: {resp.text[:300]}")
        return pd.DataFrame()


# =====================================================================
# 2. SMP API (한국전력거래소_계통한계가격 및 수요예측)
# =====================================================================
def fetch_smp_data(start_date: str = None,
                   end_date: str = None,
                   area: str = '육지',
                   use_cache: bool = True) -> pd.DataFrame:
    """
    한국전력거래소_계통한계가격 및 수요예측 API

    응답 형식 (확인됨):
        date, hour (1~24), areaName, smp, slfd, mlfd
    """
    start_date = start_date or config.API_START_DATE
    end_date   = end_date   or config.API_END_DATE

    _ensure_cache_dir()
    cache = _cache_path(f'smp_{area}')

    if use_cache and os.path.exists(cache):
        print(f"[API] SMP 캐시 사용: {cache}")
        return pd.read_csv(cache, parse_dates=['timestamp'])

    _validate_key(config.API_KEYS['smp'], 'SMP')

    url = "https://apis.data.go.kr/B552115/SmpWithForecastDemand/getSmpWithForecastDemand"

    print(f"[API] SMP 데이터 수집 시작: {start_date} ~ {end_date} (지역: {area})")

    days = _date_range(start_date, end_date)
    records = []

    for i, day in enumerate(days, 1):
        params = {
            'serviceKey': unquote(config.API_KEYS['smp']),
            'pageNo'    : 1,
            'numOfRows' : 48,
            'dataType'  : 'JSON',
            'date'      : day.strftime('%Y%m%d'),
        }

        try:
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            items = data['response']['body']['items']['item']
            if isinstance(items, dict):
                items = [items]

            for item in items:
                if item.get('areaName', '').strip() != area:
                    continue

                hour = int(item.get('hour', 0))
                smp  = float(item.get('smp', 0))
                slfd = float(item.get('slfd', 0))

                actual_h = hour - 1 if 1 <= hour <= 24 else hour
                ts = day + timedelta(hours=actual_h)

                records.append({
                    'timestamp': ts,
                    'smp': smp,
                    'forecast_demand': slfd,
                })

        except KeyError:
            print(f"  [경고] {day.strftime('%Y-%m-%d')} 데이터 없음")
        except Exception as exc:
            print(f"  [경고] {day.strftime('%Y-%m-%d')} 수집 실패: {exc}")

        if i % 30 == 0 or i == len(days):
            print(f"  진행률: {i}/{len(days)}일 ({i/len(days)*100:.1f}%)")
        time.sleep(0.1)

    df = pd.DataFrame(records).sort_values('timestamp').reset_index(drop=True)

    if len(df) > 0:
        df.to_csv(cache, index=False, encoding='utf-8-sig')
        print(f"[API] SMP 캐시 저장: {cache}")
        print(f"   - 평균 SMP: {df['smp'].mean():.2f} 원/kWh")
        print(f"   - 최대 SMP: {df['smp'].max():.2f} 원/kWh")
        print(f"   - 최소 SMP: {df['smp'].min():.2f} 원/kWh")

    return df


# =====================================================================
# 3. 기상청 ASOS API
# =====================================================================
def fetch_kma_data(start_date: str = None,
                   end_date: str = None,
                   station_id: int = None,
                   use_cache: bool = True) -> pd.DataFrame:
    """기상청 지상 ASOS 시간자료 API"""
    start_date = start_date or config.API_START_DATE
    end_date   = end_date   or config.API_END_DATE
    station_id = station_id or config.KMA_STATION_ID

    _ensure_cache_dir()
    cache = _cache_path(f'kma_st{station_id}')

    if use_cache and os.path.exists(cache):
        print(f"[API] 기상 캐시 사용: {cache}")
        return pd.read_csv(cache, parse_dates=['timestamp'])

    _validate_key(config.API_KEYS['kma'], 'KMA')

    url = "http://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList"

    print(f"[API] 기상 데이터 수집: 지점 {station_id}, {start_date} ~ {end_date}")

    days = _date_range(start_date, end_date)
    records = []
    chunk_size = 30

    for chunk_start in range(0, len(days), chunk_size):
        chunk_end_idx = min(chunk_start + chunk_size - 1, len(days) - 1)
        s_day = days[chunk_start]
        e_day = days[chunk_end_idx]

        params = {
            'serviceKey': unquote(config.API_KEYS['kma']),
            'pageNo'    : 1,
            'numOfRows' : 999,
            'dataType'  : 'JSON',
            'dataCd'    : 'ASOS',
            'dateCd'    : 'HR',
            'startDt'   : s_day.strftime('%Y%m%d'),
            'startHh'   : '00',
            'endDt'     : e_day.strftime('%Y%m%d'),
            'endHh'     : '23',
            'stnIds'    : station_id,
        }

        try:
            resp = requests.get(url, params=params, timeout=15)
            data = resp.json()
            items = data['response']['body']['items']['item']
            if isinstance(items, dict):
                items = [items]

            for item in items:
                ts_str = item.get('tm', '')
                ts = pd.to_datetime(ts_str, errors='coerce')
                if pd.isna(ts):
                    continue

                def _f(key):
                    v = item.get(key, '')
                    try:
                        return float(v) if v not in ('', None) else 0.0
                    except (ValueError, TypeError):
                        return 0.0

                records.append({
                    'timestamp': ts,
                    'icsr': _f('icsr'),
                    'ta'  : _f('ta'),
                    'hm'  : _f('hm'),
                    'ws'  : _f('ws'),
                })

        except Exception as exc:
            print(f"  [경고] {s_day.strftime('%Y-%m-%d')} 청크 실패: {exc}")

        print(f"  진행률: {min(chunk_end_idx+1, len(days))}/{len(days)}일")
        time.sleep(0.2)

    df = pd.DataFrame(records).sort_values('timestamp').reset_index(drop=True)

    if len(df) > 0:
        df.to_csv(cache, index=False, encoding='utf-8-sig')
        print(f"[API] 기상 캐시 저장: {cache}")

    return df


# =====================================================================
# 일사량 → 태양광 발전량 변환
# =====================================================================
def convert_irradiance_to_solar(kma_df: pd.DataFrame,
                                 capacity_kw: float = None,
                                 efficiency: float = None) -> pd.DataFrame:
    """기상청 일사량 → 태양광 발전량 변환"""
    cap = capacity_kw if capacity_kw is not None else config.PV_CAPACITY_KW
    eta = efficiency  if efficiency  is not None else config.PV_EFFICIENCY

    panel_area = cap / eta

    df = kma_df.copy()
    df['solar_kw'] = df['icsr'] * panel_area * eta * 0.2778
    df['solar_kw'] = df['solar_kw'].clip(lower=0, upper=cap)

    print(f"   - 평균 발전량: {df['solar_kw'].mean():.2f} kW")
    print(f"   - 최대 발전량: {df['solar_kw'].max():.2f} kW")

    return df[['timestamp', 'solar_kw']]


# ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("=== 부하 API 단독 테스트 ===\n")
    try:
        load = fetch_load_data(use_cache=False)
        print(f"\n수집 완료: {len(load):,}행")
        if len(load) > 0:
            print(load.head(24))
    except ValueError as e:
        print(e)
