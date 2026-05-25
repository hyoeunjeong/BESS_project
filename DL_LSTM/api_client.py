"""
api_client.py  ─  공공 데이터 API 클라이언트
==============================================
3개 공공 API를 호출하여 부하/SMP/기상 데이터를 가져옵니다.

1. ODcloud 부하 데이터 API
2. 공공데이터포털 SMP API
3. 기상청 ASOS 시간자료 API (옵션)

캐시 전략
---------
- 동일 기간 데이터는 data/cache/ 폴더에 CSV로 저장 후 재사용
- 캐시 hit 시 API를 호출하지 않아 속도와 할당량 절약
"""

import os
import time
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from urllib.parse import unquote

import config


# ─────────────────────────────────────────────────────────────────────
# 공통 유틸
# ─────────────────────────────────────────────────────────────────────
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


# =====================================================================
# 1. ODcloud 부하 데이터 API
# =====================================================================
def fetch_load_data(start_date: str = None,
                    end_date  : str = None,
                    use_cache : bool = True) -> pd.DataFrame:
    """
    ODcloud API로부터 전력 부하 데이터 조회

    ※ ODcloud API는 기간 파라미터를 받지 않고 전체 데이터를 페이지네이션으로 반환합니다.
      따라서 캐시는 'all' 키로 한 번만 저장하고, 기간 필터링은 메모리에서 수행합니다.

    Parameters
    ----------
    start_date : 'YYYY-MM-DD' (None이면 전체)
    end_date   : 'YYYY-MM-DD' (None이면 전체)
    use_cache  : True면 캐시 우선 사용

    Returns
    -------
    DataFrame : columns = [timestamp, load_mw]
    """
    cache = _cache_path('load', 'all', 'all')   # 항상 전체 데이터 캐시

    df = None
    if use_cache:
        df = _load_cache(cache)

    if df is None:
        print(f"   [API 호출] ODcloud 부하 데이터 (전체)")
        df = _fetch_load_all_pages()
        if df.empty:
            raise RuntimeError("ODcloud API: 응답 데이터 없음 (파싱 실패 또는 응답 형식 변경)")
        if use_cache:
            _save_cache(df, cache)

    # 기간 필터 (메모리)
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
    """
    ODcloud 응답 파싱 (wide format)

    실제 응답 예시:
        {
            '날짜': '2025-01-01',
            '1시': 62256, '2시': 59663, ..., '24시': 61871
        }

    단위는 MW (예: 62256 MW = 62.256 GW, 전국 부하)
    """
    if not rows:
        return pd.DataFrame(columns=['timestamp', 'load_mw'])

    records = []
    for row in rows:
        date_val = pd.to_datetime(row.get('날짜'), errors='coerce')
        if pd.isna(date_val):
            continue

        for h in range(1, 25):  # 1시 ~ 24시
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

            # '1시' = 00:00 ~ 01:00 구간 → timestamp는 (h-1)시로 정규화
            # (Rule-based 코드와 동일 컨벤션 유지)
            ts = date_val + pd.Timedelta(hours=(h - 1))
            records.append({'timestamp': ts, 'load_mw': val})

    return pd.DataFrame(records)


# =====================================================================
# 2. SMP API (공공데이터포털)
# =====================================================================
def fetch_smp_data(start_date: str,
                   end_date  : str,
                   use_cache : bool = True,
                   retry_max : int = 2,
                   call_interval: float = 0.3,
                   partial_save : bool = True) -> pd.DataFrame:
    """
    SMP(계통한계가격) 데이터 조회

    Parameters
    ----------
    start_date    : 'YYYY-MM-DD'
    end_date      : 'YYYY-MM-DD'
    retry_max     : 일반 오류 재시도 횟수 (quota 오류는 재시도 안 함)
    call_interval : 일별 호출 간 대기 시간(초)
    partial_save  : True면 quota 도달 시 그때까지 받은 데이터를 캐시에 저장

    Returns
    -------
    DataFrame : columns = [timestamp, smp]
    """
    cache = _cache_path('smp', start_date, end_date)
    if use_cache:
        # 1) 정식 캐시 확인
        cached = _load_cache(cache)
        if cached is not None:
            return cached

        # 2) 부분 캐시 확인 (이전 실행에서 quota로 중단된 경우)
        partial = _find_partial_smp_cache(start_date, end_date)
        if partial is not None:
            print(f"   [부분 캐시 발견] {len(partial):,}행 → 나머지만 추가 호출")
            # 부분 캐시의 마지막 timestamp 다음날부터 호출
            last_ts = partial['timestamp'].max()
            resume_date = (last_ts + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
            if pd.Timestamp(resume_date) > pd.Timestamp(end_date):
                # 이미 다 받은 상태
                return partial
            # 재귀 호출로 나머지만 받기
            try:
                rest = _fetch_smp_range(resume_date, end_date,
                                         retry_max, call_interval, partial_save)
                combined = pd.concat([partial, rest], ignore_index=True)
                combined = combined.drop_duplicates('timestamp').sort_values('timestamp').reset_index(drop=True)
                _save_cache(combined, cache)
                # 부분 캐시는 삭제
                _remove_partial_smp_caches()
                return combined
            except RuntimeError as ex:
                if 'quota' in str(ex).lower():
                    # 또 quota → 부분 캐시 유지하고 받은 만큼만 반환
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
            # API token quota exceeded → 더 이상 호출해도 무의미
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
        # 진행률 표시 (30일마다 또는 마지막)
        if day_idx % 30 == 0 or day_idx == total_days:
            pct = day_idx / total_days * 100
            print(f"   [진행] {day_idx}/{total_days}일 ({pct:.0f}%) — 성공 {success_count}, 실패 {len(failed_dates)}")

        cur += pd.Timedelta(days=1)
        time.sleep(call_interval)

    # 결과 정리
    if all_rows:
        df = pd.DataFrame(all_rows).drop_duplicates('timestamp')
        df = df.sort_values('timestamp').reset_index(drop=True)
        print(f"   [성공] SMP {success_count}일 / {len(df):,}행 수신")

        if failed_dates:
            print(f"   [경고] 일반 실패 {len(failed_dates)}일")

        # 부분 저장 (quota 도달 시에도 받은 만큼은 저장)
        if use_cache and partial_save:
            if quota_exceeded:
                # 부분 캐시는 별도 이름으로 (다음번 호출 시 이어받기 가능)
                partial_path = _cache_path('smp_partial',
                                            start_date,
                                            df['timestamp'].max().strftime('%Y-%m-%d'))
                _save_cache(df, partial_path)
                print(f"   [안내] 부분 캐시 저장됨. 내일 한도 회복 후 재실행하면 이어받기 가능")
            else:
                _save_cache(df, cache)
        return df

    # 데이터 0건 → 예외
    if quota_exceeded:
        raise RuntimeError(
            f"SMP API: 일일 토큰 한도 도달 (token quota exceeded). "
            f"한도가 회복되는 내일 00:00(KST) 이후 재실행하세요."
        )
    raise RuntimeError(
        f"SMP API: {start_date}~{end_date} 데이터 없음 "
        f"(실패한 날짜: {len(failed_dates)}개)"
    )


def _fetch_smp_single_day(url: str, date_str: str,
                          base_date: pd.Timestamp,
                          retry_max: int) -> tuple:
    """
    SMP 1일치 조회

    Returns
    -------
    (rows, status) where status ∈ {'ok', 'failed', 'quota_exceeded'}
    """
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

        # 429 처리 — 응답 본문으로 quota vs rate-limit 구분
        if r.status_code == 429:
            body = (r.text or '').lower()

            # API token quota exceeded → 재시도 무의미 (일일 한도 소진)
            if 'quota' in body or 'token' in body or 'exceeded' in body:
                return ([], 'quota_exceeded')

            # 순간 rate-limit → 지수 백오프 재시도
            if attempt < retry_max:
                wait = (2 ** attempt) * 2.0
                print(f"   [재시도] SMP {date_str} 429 → {wait:.0f}초 대기 "
                      f"({attempt + 1}/{retry_max})")
                time.sleep(wait)
                continue
            else:
                print(f"   [경고] SMP {date_str} 429 한도 초과")
                return ([], 'failed')

        # 정상 응답 외 HTTP 오류
        if not r.ok:
            print(f"   [경고] SMP {date_str} HTTP {r.status_code}")
            return ([], 'failed')

        # JSON 파싱
        try:
            j = r.json()
        except Exception as ex:
            print(f"   [경고] SMP {date_str} JSON 파싱 실패: {ex}")
            return ([], 'failed')

        # 응답 코드 확인 (공공데이터포털 표준)
        try:
            header = j.get('response', {}).get('header', {})
            result_code = str(header.get('resultCode', ''))
            if result_code and result_code != '00':
                msg = header.get('resultMsg', '')
                # 키 미인증/한도 초과 코드들
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
    """SMP item 에서 timestamp 추출"""
    # 가능한 시간 키: 'hh', 'hour', 'tm', 'baseTime'
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
    """SMP item 에서 가격 추출"""
    for key in ('smp', 'smpPrice', 'price', 'val', 'value'):
        if key in item:
            try:
                return float(item[key])
            except (ValueError, TypeError):
                continue
    return None


def _find_partial_smp_cache(start_date: str, end_date: str) -> pd.DataFrame | None:
    """
    부분 캐시(smp_partial_*) 중 시작일이 일치하는 가장 큰 캐시 찾기
    """
    if not os.path.exists(config.API_CACHE_DIR):
        return None
    candidates = []
    prefix = f'smp_partial_{start_date}_'
    for f in os.listdir(config.API_CACHE_DIR):
        if f.startswith(prefix) and f.endswith('.csv'):
            candidates.append(f)
    if not candidates:
        return None
    # 가장 끝 날짜가 큰 파일 선택
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
    """
    SMP 일별 호출 루프 (캐시 무시, 호출만 수행)
    fetch_smp_data의 내부 호출용
    """
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


# =====================================================================
# 3. 기상청 ASOS API (옵션 — 태양광 정확도 향상용)
# =====================================================================
def fetch_weather_data(start_date: str,
                       end_date  : str,
                       station_id: int = 108,   # 108 = 서울
                       use_cache : bool = True) -> pd.DataFrame:
    """
    기상청 ASOS 시간자료 조회

    Parameters
    ----------
    station_id : 지점번호 (108=서울, 159=부산, 133=대전, 143=대구, 156=광주)

    Returns
    -------
    DataFrame : columns = [timestamp, temp_c, cloud_amount, solar_radiation]
    """
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

    # ASOS는 한 번에 최대 999행 → 한 달씩 끊어서 호출
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
                    'cloud_amount'   : _safe_float(it.get('dc10Tca')),  # 전운량 (0~10)
                    'solar_radiation': _safe_float(it.get('icsr')),     # 일사량 (MJ/m²)
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


# =====================================================================
# 캐시 관리
# =====================================================================
def clear_cache():
    """모든 API 캐시 삭제"""
    if not os.path.exists(config.API_CACHE_DIR):
        return
    for f in os.listdir(config.API_CACHE_DIR):
        os.remove(os.path.join(config.API_CACHE_DIR, f))
    print(f"[캐시 삭제] {config.API_CACHE_DIR}")


# =====================================================================
if __name__ == '__main__':
    # 단독 실행 테스트 (짧은 기간으로 빠르게 검증)
    # ※ 1년치 본 실행은 main.py 를 통해 수행하세요.
    print("=" * 60)
    print("  API 클라이언트 단독 테스트 (1주일 샘플)")
    print(f"  config 기간 설정: {config.API_START_DATE} ~ {config.API_END_DATE}")
    print("=" * 60)

    start = config.API_START_DATE
    end   = '2025-01-07'   # 단독 테스트는 7일만 (SMP 호출 7회)

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
