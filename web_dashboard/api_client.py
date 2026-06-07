import os
import time
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from urllib.parse import unquote

import config

# 공통 유틸

def _cache_path(name: str, start: str, end: str) -> str:
    """캐시 파일 경로 생성"""
    os.makedirs(config.API_CACHE_DIR, exist_ok=True)
    return os.path.join(config.API_CACHE_DIR,
                        f"{name}_{start}_{end}.csv")


def _load_cache(path: str) -> pd.DataFrame | None:
    """캐시가 존재하면 DataFrame 로드, 없으면 None"""
    if os.path.exists(path):
        try:
            df = pd.read_csv(path, parse_dates=['timestamp'])
            print(f"   [캐시 HIT] {os.path.basename(path)} ({len(df):,}행)")
            return df
        except Exception as e:
            print(f"   [캐시 손상] {e} → API 재호출")
    return None


def _save_cache(df: pd.DataFrame, path: str):
    """DataFrame을 캐시에 저장"""
    df.to_csv(path, index=False, encoding='utf-8-sig')
    print(f"   [캐시 저장] {os.path.basename(path)}")



# 1. ODcloud 부하 데이터 API
def fetch_load_data(start_date: str = None,
                    end_date  : str = None,
                    use_cache : bool = True) -> pd.DataFrame:
    """ODcloud API로부터 전력 부하 데이터 조회 (전체 데이터를 캐시 후 메모리 필터)"""
    cache = _cache_path('load', 'all', 'all')

    df = None
    if use_cache:
        df = _load_cache(cache)

    if df is None:
        print(f"   [API 호출] ODcloud 부하 데이터 (전체)")
        df = _fetch_load_all_pages()
        if df.empty:
            raise RuntimeError("ODcloud API: 응답 데이터 없음")
        if use_cache:
            _save_cache(df, cache)

    if start_date:
        df = df[df['timestamp'] >= pd.Timestamp(start_date)]
    if end_date:
        df = df[df['timestamp'] <= pd.Timestamp(end_date) + pd.Timedelta(days=1)]

    df = df.sort_values('timestamp').reset_index(drop=True)
    print(f"   [성공] 부하 데이터 {len(df):,}행 ({start_date or '전체'} ~ {end_date or '전체'})")
    return df


def _fetch_load_all_pages() -> pd.DataFrame:
    """ODcloud 부하 API에서 모든 페이지 수집"""
    url = config.API_LOAD_URL
    all_rows = []
    page = 1
    per_page = 1000

    while True:
        params = {
            'page'      : page,
            'perPage'   : per_page,
            'serviceKey': config.COMMON_API_KEY,
        }
        try:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            j = r.json()
        except Exception as ex:
            raise RuntimeError(f"ODcloud API 호출 실패 (page={page}): {ex}")

        data = j.get('data', [])
        if not data:
            break

        all_rows.extend(data)

        total = j.get('totalCount', 0)
        if page * per_page >= total:
            break
        page += 1
        time.sleep(0.1)

    return _parse_odcloud_load(all_rows)


def _parse_odcloud_load(rows: list) -> pd.DataFrame:
    """ODcloud 응답 파싱 (wide format)"""
    if not rows:
        return pd.DataFrame(columns=['timestamp', 'load_mw'])

    records = []
    for row in rows:
        date_val = pd.to_datetime(row.get('날짜'), errors='coerce')
        if pd.isna(date_val):
            continue

        for h in range(1, 25):
            key = f'{h}시'
            if key not in row:
                continue
            raw = row[key]
            try:
                val = float(raw)
            except (ValueError, TypeError):
                continue
            if np.isnan(val):
                continue

            ts = date_val + pd.Timedelta(hours=(h - 1))
            records.append({'timestamp': ts, 'load_mw': val})

    return pd.DataFrame(records)


# 2. SMP API (공공데이터포털)
def fetch_smp_data(start_date: str,
                   end_date  : str,
                   use_cache : bool = True,
                   retry_max : int = 2,
                   call_interval: float = 0.3,
                   partial_save : bool = True) -> pd.DataFrame:
    """SMP(계통한계가격) 데이터 조회"""
    cache = _cache_path('smp', start_date, end_date)
    if use_cache:
        cached = _load_cache(cache)
        if cached is not None:
            return cached

        partial = _find_partial_smp_cache(start_date, end_date)
        if partial is not None:
            print(f"   [부분 캐시 발견] {len(partial):,}행 → 나머지만 추가 호출")
            last_ts = partial['timestamp'].max()
            resume_date = (last_ts + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
            if pd.Timestamp(resume_date) > pd.Timestamp(end_date):
                return partial
            try:
                rest = _fetch_smp_range(resume_date, end_date,
                                        retry_max, call_interval, partial_save)
                combined = pd.concat([partial, rest], ignore_index=True)
                combined = combined.drop_duplicates('timestamp').sort_values('timestamp').reset_index(drop=True)
                _save_cache(combined, cache)
                _remove_partial_smp_caches()
                return combined
            except RuntimeError as ex:
                if 'quota' in str(ex).lower():
                    print(f"   [안내] 추가 호출 중 quota 도달 → 부분 캐시({len(partial)}행) 반환")
                    return partial
                raise

    print(f"   [API 호출] SMP ({start_date} ~ {end_date})")
    url = config.API_SMP_URL

    all_rows = []
    cur = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    total_days = (end - cur).days + 1

    failed_dates = []
    quota_exceeded = False
    success_count = 0
    day_idx = 0
    while cur <= end:
        date_str = cur.strftime('%Y%m%d')
        rows, status = _fetch_smp_single_day(url, date_str, cur, retry_max)

        if status == 'quota_exceeded':
            quota_exceeded = True
            remaining_days = (end - cur).days + 1
            print(f"   [중단] API 일일 한도 도달 → 남은 {remaining_days}일 호출 스킵")
            break
        elif status == 'failed':
            failed_dates.append(date_str)
        else:
            all_rows.extend(rows)
            success_count += 1

        day_idx += 1
        if day_idx % 30 == 0 or day_idx == total_days:
            pct = day_idx / total_days * 100
            print(f"   [진행] {day_idx}/{total_days}일 ({pct:.0f}%) — 성공 {success_count}, 실패 {len(failed_dates)}")

        cur += pd.Timedelta(days=1)
        time.sleep(call_interval)

    if all_rows:
        df = pd.DataFrame(all_rows).drop_duplicates('timestamp')
        df = df.sort_values('timestamp').reset_index(drop=True)
        print(f"   [성공] SMP {success_count}일 / {len(df):,}행 수신")

        if failed_dates:
            print(f"   [경고] 일반 실패 {len(failed_dates)}일")

        if use_cache and partial_save:
            if quota_exceeded:
                partial_path = _cache_path('smp_partial',
                                           start_date,
                                           df['timestamp'].max().strftime('%Y-%m-%d'))
                _save_cache(df, partial_path)
                print(f"   [안내] 부분 캐시 저장됨. 내일 한도 회복 후 재실행하면 이어받기 가능")
            else:
                _save_cache(df, cache)
        return df

    if quota_exceeded:
        raise RuntimeError(
            "SMP API: 일일 토큰 한도 도달 (token quota exceeded). "
            "한도가 회복되는 내일 00:00(KST) 이후 재실행하세요."
        )
    raise RuntimeError(
        f"SMP API: {start_date}~{end_date} 데이터 없음 "
        f"(실패한 날짜: {len(failed_dates)}개)"
    )


def _fetch_smp_single_day(url: str, date_str: str,
                          base_date: pd.Timestamp,
                          retry_max: int) -> tuple:
    """SMP 1일치 조회"""
    for attempt in range(retry_max + 1):
        params = {
            'serviceKey': config.COMMON_API_KEY,
            'pageNo'    : 1,
            'numOfRows' : 100,
            'dataType'  : 'JSON',
            'baseDate'  : date_str,
        }
        try:
            r = requests.get(url, params=params, timeout=30)
        except Exception as ex:
            if attempt < retry_max:
                time.sleep(1.0)
                continue
            print(f"   [경고] SMP {date_str} 네트워크 오류: {ex}")
            return ([], 'failed')

        if r.status_code == 429:
            body = (r.text or '').lower()
            if 'quota' in body or 'token' in body or 'exceeded' in body:
                return ([], 'quota_exceeded')
            if attempt < retry_max:
                wait = (2 ** attempt) * 2.0
                print(f"   [재시도] SMP {date_str} 429 → {wait:.0f}초 대기 "
                      f"({attempt + 1}/{retry_max})")
                time.sleep(wait)
                continue
            else:
                print(f"   [경고] SMP {date_str} 429 한도 초과")
                return ([], 'failed')

        if not r.ok:
            print(f"   [경고] SMP {date_str} HTTP {r.status_code}")
            return ([], 'failed')

        try:
            j = r.json()
        except Exception as ex:
            print(f"   [경고] SMP {date_str} JSON 파싱 실패: {ex}")
            return ([], 'failed')

        try:
            header = j.get('response', {}).get('header', {})
            result_code = str(header.get('resultCode', ''))
            if result_code and result_code != '00':
                msg = header.get('resultMsg', '')
                if result_code in ('22', '99', '03'):
                    return ([], 'quota_exceeded')
                print(f"   [경고] SMP {date_str} 응답 오류 [{result_code}] {msg}")
                return ([], 'failed')

            items = j.get('response', {}).get('body', {}).get('items', {})
            if isinstance(items, dict):
                items = items.get('item', [])
            if not isinstance(items, list):
                items = [items] if items else []

            rows = []
            for it in items:
                ts = _parse_smp_timestamp(it, base_date)
                if ts is None:
                    continue
                smp_val = _extract_smp_value(it)
                if smp_val is not None:
                    rows.append({'timestamp': ts, 'smp': smp_val})
            return (rows, 'ok')

        except Exception as ex:
            print(f"   [경고] SMP {date_str} 파싱 실패: {ex}")
            return ([], 'failed')

    return ([], 'failed')


def _parse_smp_timestamp(item: dict, base_date: pd.Timestamp) -> pd.Timestamp | None:
    """SMP item에서 timestamp 추출"""
    for key in ('hh', 'hour', 'tm', 'baseTime', 'time'):
        if key in item:
            try:
                h = int(float(str(item[key])[:2]))
                if 0 <= h <= 23:
                    return base_date + pd.Timedelta(hours=h)
                elif 1 <= h <= 24:
                    return base_date + pd.Timedelta(hours=h - 1)
            except (ValueError, TypeError):
                continue
    return None


def _extract_smp_value(item: dict) -> float | None:
    """SMP item에서 가격 추출"""
    for key in ('smp', 'smpPrice', 'price', 'val', 'value'):
        if key in item:
            try:
                return float(item[key])
            except (ValueError, TypeError):
                continue
    return None


def _find_partial_smp_cache(start_date: str, end_date: str) -> pd.DataFrame | None:
    """부분 캐시 찾기"""
    if not os.path.exists(config.API_CACHE_DIR):
        return None
    candidates = []
    prefix = f'smp_partial_{start_date}_'
    for f in os.listdir(config.API_CACHE_DIR):
        if f.startswith(prefix) and f.endswith('.csv'):
            candidates.append(f)
    if not candidates:
        return None
    candidates.sort()
    best = candidates[-1]
    try:
        path = os.path.join(config.API_CACHE_DIR, best)
        df = pd.read_csv(path, parse_dates=['timestamp'])
        print(f"   [부분 캐시 HIT] {best} ({len(df):,}행)")
        return df
    except Exception:
        return None


def _remove_partial_smp_caches():
    """모든 SMP 부분 캐시 삭제"""
    if not os.path.exists(config.API_CACHE_DIR):
        return
    for f in os.listdir(config.API_CACHE_DIR):
        if f.startswith('smp_partial_') and f.endswith('.csv'):
            try:
                os.remove(os.path.join(config.API_CACHE_DIR, f))
            except OSError:
                pass


def _fetch_smp_range(start_date: str, end_date: str,
                    retry_max: int, call_interval: float,
                    partial_save: bool) -> pd.DataFrame:
    """SMP 일별 호출 루프 (캐시 무시)"""
    print(f"   [API 호출] SMP 추가 ({start_date} ~ {end_date})")
    url = config.API_SMP_URL

    all_rows = []
    cur = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    quota_exceeded = False

    while cur <= end:
        date_str = cur.strftime('%Y%m%d')
        rows, status = _fetch_smp_single_day(url, date_str, cur, retry_max)
        if status == 'quota_exceeded':
            quota_exceeded = True
            break
        elif status == 'ok':
            all_rows.extend(rows)
        cur += pd.Timedelta(days=1)
        time.sleep(call_interval)

    if not all_rows:
        if quota_exceeded:
            raise RuntimeError("SMP API: token quota exceeded (이어받기 중 한도 도달)")
        raise RuntimeError(f"SMP API: {start_date}~{end_date} 데이터 없음")

    df = pd.DataFrame(all_rows).drop_duplicates('timestamp').sort_values('timestamp').reset_index(drop=True)
    if quota_exceeded:
        raise RuntimeError("SMP API: token quota exceeded")
    return df

# 3. 기상청 ASOS API (과거 관측 — 백업/학습용)
def fetch_weather_data(start_date: str,
                       end_date  : str,
                       station_id: int = 108,
                       use_cache : bool = True) -> pd.DataFrame:
    """기상청 ASOS 시간자료 조회"""
    cache = _cache_path(f'weather_{station_id}', start_date, end_date)
    if use_cache:
        cached = _load_cache(cache)
        if cached is not None:
            return cached

    print(f"   [API 호출] ASOS 기상 (지점={station_id}, {start_date} ~ {end_date})")
    url = config.API_WEATHER_URL

    all_rows = []
    cur = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)

    while cur <= end:
        chunk_end = min(cur + pd.Timedelta(days=30), end)
        params = {
            'serviceKey': config.COMMON_API_KEY,
            'pageNo'    : 1,
            'numOfRows' : 999,
            'dataType'  : 'JSON',
            'dataCd'    : 'ASOS',
            'dateCd'    : 'HR',
            'startDt'   : cur.strftime('%Y%m%d'),
            'startHh'   : '00',
            'endDt'     : chunk_end.strftime('%Y%m%d'),
            'endHh'     : '23',
            'stnIds'    : str(station_id),
        }
        try:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            j = r.json()
            items = j.get('response', {}).get('body', {}).get('items', {})
            if isinstance(items, dict):
                items = items.get('item', [])
            for it in items:
                ts = pd.to_datetime(it.get('tm'), errors='coerce')
                if pd.isna(ts):
                    continue
                all_rows.append({
                    'timestamp'      : ts,
                    'temp_c'         : _safe_float(it.get('ta')),
                    'cloud_amount'   : _safe_float(it.get('dc10Tca')),
                    'solar_radiation': _safe_float(it.get('icsr')),
                })
        except Exception as ex:
            print(f"   [경고] ASOS {cur.date()} 호출 실패: {ex}")

        cur = chunk_end + pd.Timedelta(days=1)
        time.sleep(0.2)

    if not all_rows:
        raise RuntimeError(f"ASOS API: {start_date}~{end_date} 데이터 없음")

    df = pd.DataFrame(all_rows).drop_duplicates('timestamp')
    df = df.sort_values('timestamp').reset_index(drop=True)
    print(f"   [성공] 기상 데이터 {len(df):,}행 수신")

    if use_cache:
        _save_cache(df, cache)
    return df


def _safe_float(v) -> float:
    try:
        return float(v) if v not in (None, '', ' ') else np.nan
    except (ValueError, TypeError):
        return np.nan

# 4. 기상청 단기예보 API (실시간 예보 — 운영용) [NEW]
# 발표시각: 02, 05, 08, 11, 14, 17, 20, 23시 (1일 8회)
# 예보범위: 발표시각 기준 +3일까지
# 응답 카테고리(8개):
#   SKY(하늘상태), PTY(강수형태), TMP(기온), REH(습도),
#   WSD(풍속), POP(강수확률), PCP(강수량), SNO(적설)
_FORECAST_CATEGORIES = ['SKY', 'PTY', 'TMP', 'REH', 'WSD', 'POP', 'PCP', 'SNO']


def fetch_short_forecast(nx: int = None,
                         ny: int = None,
                         base_datetime: pd.Timestamp = None,
                         use_cache : bool = True) -> pd.DataFrame:
    """
    기상청 단기예보 (getVilageFcst) 조회

    Parameters
    ----------
    nx, ny        : 격자좌표 (None이면 config.FORECAST_NX/NY 사용)
    base_datetime : 발표시각 기준 (None이면 현재 시각 기준 자동 선택)
    use_cache     : True면 발표시각 단위로 캐시 사용

    Returns
    -------
    DataFrame : columns = [timestamp, SKY, PTY, TMP, REH, WSD, POP, PCP, SNO]
                예보 시각(timestamp)별 wide format
    """
    if nx is None:
        nx = config.FORECAST_NX
    if ny is None:
        ny = config.FORECAST_NY

    # 가장 최근 발표시각 계산
    base_date, base_time = _get_latest_base_time(base_datetime)

    # 캐시 확인 (발표시각 단위로 별도 저장)
    cache = _cache_path(f'forecast_{nx}_{ny}', base_date, base_time)
    if use_cache:
        cached = _load_cache(cache)
        if cached is not None:
            return cached

    print(f"   [API 호출] 단기예보 (격자 {nx},{ny}, 발표 {base_date} {base_time})")

    all_items = []
    page = 1
    per_page = 1000

    while True:
        params = {
            'serviceKey': config.KMA_FORECAST_API_KEY,
            'pageNo'    : page,
            'numOfRows' : per_page,
            'dataType'  : 'JSON',
            'base_date' : base_date,
            'base_time' : base_time,
            'nx'        : nx,
            'ny'        : ny,
        }
        try:
            r = requests.get(config.API_FORECAST_URL, params=params, timeout=30)
            r.raise_for_status()
            j = r.json()
        except Exception as ex:
            raise RuntimeError(f"단기예보 API 호출 실패 (page={page}): {ex}")

        # 응답 코드 확인
        header = j.get('response', {}).get('header', {})
        result_code = str(header.get('resultCode', ''))
        if result_code and result_code != '00':
            msg = header.get('resultMsg', '')
            raise RuntimeError(f"단기예보 API 응답 오류 [{result_code}] {msg}")

        body = j.get('response', {}).get('body', {})
        items = body.get('items', {})
        if isinstance(items, dict):
            items = items.get('item', [])
        if not items:
            break

        all_items.extend(items)

        total = body.get('totalCount', 0)
        if page * per_page >= total:
            break
        page += 1
        time.sleep(0.2)

    if not all_items:
        raise RuntimeError(
            f"단기예보 API: 응답 데이터 없음 "
            f"(격자 {nx},{ny}, 발표 {base_date} {base_time})"
        )

    df = _parse_forecast_items(all_items)
    print(f"   [성공] 단기예보 {len(df):,}행 (격자 {nx},{ny}, "
          f"예보범위 {df['timestamp'].min()} ~ {df['timestamp'].max()})")

    if use_cache:
        _save_cache(df, cache)
    return df


def _get_latest_base_time(base_datetime: pd.Timestamp = None) -> tuple:
    """
    현재 시각 기준 가장 최근 발표시각 계산

    발표시각: 02, 05, 08, 11, 14, 17, 20, 23 (시)
    데이터 가용성: 발표 약 10분 후 → 안전을 위해 15분 버퍼 적용

    Returns
    -------
    (base_date, base_time) : ('YYYYMMDD', 'HHMM')
    """
    if base_datetime is None:
        base_datetime = pd.Timestamp.now()

    safe_time = base_datetime - pd.Timedelta(minutes=15)

    base_hours = [2, 5, 8, 11, 14, 17, 20, 23]
    cur_hour = safe_time.hour

    available = [h for h in base_hours if h <= cur_hour]
    if available:
        base_hour = max(available)
        base_date = safe_time.strftime('%Y%m%d')
    else:
        # 새벽 02시 이전이면 어제 23시 발표 사용
        base_hour = 23
        yesterday = safe_time - pd.Timedelta(days=1)
        base_date = yesterday.strftime('%Y%m%d')

    base_time = f'{base_hour:02d}00'
    return base_date, base_time


def _parse_forecast_items(items: list) -> pd.DataFrame:
    """
    단기예보 응답 (long format) → wide DataFrame 변환

    응답 1개 item 예시:
        {
          'baseDate': '20260529', 'baseTime': '1400',
          'category': 'TMP', 'fcstDate': '20260529',
          'fcstTime': '1500', 'fcstValue': '22',
          'nx': 60, 'ny': 127
        }
    """
    records = {}
    for it in items:
        category = it.get('category', '')
        if category not in _FORECAST_CATEGORIES:
            continue

        fcst_date = it.get('fcstDate', '')
        fcst_time = it.get('fcstTime', '')
        fcst_value = it.get('fcstValue', '')

        try:
            ts = pd.to_datetime(f'{fcst_date}{fcst_time}', format='%Y%m%d%H%M')
        except Exception:
            continue

        value = _parse_forecast_value(category, fcst_value)
        if value is None:
            continue

        if ts not in records:
            records[ts] = {'timestamp': ts}
        records[ts][category] = value

    if not records:
        return pd.DataFrame(columns=['timestamp'] + _FORECAST_CATEGORIES)

    df = pd.DataFrame(list(records.values()))
    df = df.sort_values('timestamp').reset_index(drop=True)

    # 누락 카테고리 컬럼 보장 (예보 시점에 따라 일부 카테고리 없을 수 있음)
    for cat in _FORECAST_CATEGORIES:
        if cat not in df.columns:
            df[cat] = np.nan

    return df[['timestamp'] + _FORECAST_CATEGORIES]


def _parse_forecast_value(category: str, value) -> float | None:
    """
    카테고리별 값 파싱 (특수 문자열 처리)

    PCP (강수량):
      '강수없음' → 0.0
      '1mm 미만' → 0.5
      '30.0~50.0mm' → 40.0 (중간값)
      '50.0mm 이상' → 50.0
      'X.X' → float

    SNO (적설):
      '적설없음' → 0.0
      '1cm 미만' → 0.5
      '5.0cm 이상' → 5.0
      'X.X' → float
    """
    if value in (None, '', '-'):
        return None
    s = str(value).strip()

    if category == 'PCP':
        if '없음' in s:
            return 0.0
        if '미만' in s:
            return 0.5
        if '이상' in s:
            try:
                return float(s.replace('mm', '').replace('이상', '').strip())
            except Exception:
                return 50.0
        if '~' in s:
            try:
                parts = s.replace('mm', '').split('~')
                return (float(parts[0]) + float(parts[1])) / 2
            except Exception:
                return None
        try:
            return float(s.replace('mm', '').strip())
        except Exception:
            return None

    if category == 'SNO':
        if '없음' in s:
            return 0.0
        if '미만' in s:
            return 0.5
        if '이상' in s:
            try:
                return float(s.replace('cm', '').replace('이상', '').strip())
            except Exception:
                return 5.0
        try:
            return float(s.replace('cm', '').strip())
        except Exception:
            return None

    # 그 외 숫자형 카테고리 (SKY, PTY, TMP, REH, WSD, POP)
    try:
        return float(s)
    except (ValueError, TypeError):
        return None

# 캐시 관리
def clear_cache():
    """모든 API 캐시 삭제"""
    if not os.path.exists(config.API_CACHE_DIR):
        return
    for f in os.listdir(config.API_CACHE_DIR):
        os.remove(os.path.join(config.API_CACHE_DIR, f))
    print(f"[캐시 삭제] {config.API_CACHE_DIR}")



if __name__ == '__main__':
    print("=" * 60)
    print("  API 클라이언트 단독 테스트")
    print(f"  config 기간 설정: {config.API_START_DATE} ~ {config.API_END_DATE}")
    print("=" * 60)

    start = config.API_START_DATE
    end   = '2025-01-07'

    try:
        load_df = fetch_load_data(start, end)
        print(load_df.head())
    except Exception as e:
        print(f"부하 API 실패: {e}")

    try:
        smp_df = fetch_smp_data(start, end)
        print(smp_df.head())
    except Exception as e:
        print(f"SMP API 실패: {e}")

    # 단기예보 테스트 (현재 시각 기준)
    print()
    print("-" * 60)
    print("  단기예보 API 테스트 (현재 시각 기준)")
    print("-" * 60)
    try:
        fcst_df = fetch_short_forecast()
        print(f"\n  컬럼: {list(fcst_df.columns)}")
        print(f"  예보 기간: {fcst_df['timestamp'].min()} ~ {fcst_df['timestamp'].max()}")
        print(f"\n  처음 5행:")
        print(fcst_df.head())
        print(f"\n  통계 요약:")
        print(fcst_df.describe())
    except Exception as e:
        print(f"단기예보 API 실패: {e}")
