"""
realtime_engine.py - BESS 실시간 제어 엔진 (하이브리드 + 3단계 fallback)
========================================================================
실제 API 데이터 + 분 단위 시뮬레이션

작동 원리
---------
1. [1시간 주기] 실제 API에서 데이터 fetch (실측값)
   - ODcloud: 부하 데이터
   - SMP API: 전력가격
   - 태양광 추정 — 3단계 fallback:
       1순위: 기상청 단기예보 API → solar_estimator → 실시간 발전량
       2순위: 기상청 ASOS API (2025년 같은 날짜) → 일사량 → 발전량
       3순위: 학습 데이터(fallback_data) 시간대별 패턴

2. [1초 주기] 실측값 기반 분 단위 시뮬레이션
   - 부하: 실측값 ± 5% 변동
   - 태양광: 단기예보 기준값 + 변동
   - BESS 제어: LSTM이 위 데이터로 충방전 결정
   - timestamp는 항상 현재 시간으로 저장

3. API 호출 실패 시 자동 단계 강하
   - 1순위 → 2순위 → 3순위 자동 전환
   - 각 단계의 사용 여부를 data_source 필드로 추적
"""

import os
import sys
import sqlite3
import threading
import time
import random
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

# DL_LSTM 폴더 경로 추가
sys.path.insert(0, str(Path(__file__).parent / 'DL_LSTM'))

import config
from bess_controller import LSTMBESSController

# api_client import
try:
    from api_client import (
        fetch_load_data,
        fetch_smp_data,
        fetch_weather_data,
        fetch_short_forecast,   # [신규] 단기예보
    )
    API_AVAILABLE = True
except ImportError as e:
    print(f"[경고] api_client import 실패: {e}")
    print("       fallback 모드로 작동합니다.")
    API_AVAILABLE = False

# solar_estimator import (신규)
try:
    from solar_estimator import forecast_to_solar
    SOLAR_ESTIMATOR_AVAILABLE = True
except ImportError as e:
    print(f"[경고] solar_estimator import 실패: {e}")
    print("       단기예보 → 태양광 변환 비활성화")
    SOLAR_ESTIMATOR_AVAILABLE = False


def irradiance_to_solar_kw(solar_radiation_mj: float,
                           capacity_kw: float = None,
                           efficiency: float = None) -> float:
    """
    일사량(MJ/m²/h) -> 태양광 발전량(kW) 변환 (ASOS 백업용)
    """
    if pd.isna(solar_radiation_mj) or solar_radiation_mj <= 0:
        return 0.0

    cap = capacity_kw if capacity_kw is not None else config.PV_CAPACITY_KW
    eff = efficiency if efficiency is not None else config.PV_EFFICIENCY

    irradiance_w_per_m2 = solar_radiation_mj * 1000.0 / 3.6
    ratio = irradiance_w_per_m2 / 1000.0
    return float(cap * ratio * eff)


class RealtimeEngine:
    """API 실측값 + 분 단위 시뮬레이션 하이브리드 엔진 (3단계 fallback)"""

    def __init__(self, db_path: str = 'realtime_data/realtime.db'):
        self.db_path = db_path
        self.controller = LSTMBESSController()

        # API 데이터 캐시 (24시간 평균 기준값)
        self.hourly_load_kw = None
        self.hourly_solar_kw = None     # 백업용 (tier 2, 3에서 사용)
        self.hourly_smp = None
        self.last_api_fetch = None
        self.api_status = 'pending'

        # [신규] 단기예보 기반 태양광 발전량 (tier 1)
        # 시간별 정확한 kW 값을 가진 DataFrame
        # columns = [timestamp, solar_kw]
        self.solar_forecast_df = None
        self.forecast_base_time = None    # 발표시각 (예: "20260529 14:00")
        self.solar_data_source = 'pending'  # 'forecast', 'asos_backup', 'pattern'

        # 시간대별 프로파일 (tier 2, 3에서만 사용)
        self.load_profile = None
        self.solar_profile = None

        # Fallback 데이터
        self.fallback_data = None

        self.running = False
        self.iteration_count = 0

        self._init_database()
        self._init_fallback_data()
        self._build_hourly_profiles()

        # 피크 임계값 초기값 (API 호출 후 자동 재조정됨)
        self.controller.peak_threshold = 50.0

        print("[실시간 엔진] 초기화 완료 (3단계 fallback 하이브리드 모드)")

    def _init_database(self):
        """SQLite 데이터베이스 초기화"""
        os.makedirs(Path(self.db_path).parent, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS realtime (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                hour INTEGER,
                load_kw REAL,
                solar_kw REAL,
                soc REAL,
                bess_power_kw REAL,
                charge_kw REAL,
                discharge_kw REAL,
                grid_power_kw REAL,
                tariff_rate REAL,
                tariff_period TEXT,
                action TEXT,
                smp REAL,
                data_source TEXT,
                solar_source TEXT,
                forecast_base TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 기존 테이블 마이그레이션 (신규 컬럼 추가)
        for col_def in [
            'ALTER TABLE realtime ADD COLUMN smp REAL',
            'ALTER TABLE realtime ADD COLUMN data_source TEXT',
            'ALTER TABLE realtime ADD COLUMN solar_source TEXT',
            'ALTER TABLE realtime ADD COLUMN forecast_base TEXT',
        ]:
            try:
                cursor.execute(col_def)
            except sqlite3.OperationalError:
                pass

        conn.commit()
        conn.close()

        print(f"[DB] 초기화 완료: {self.db_path}")

    def _init_fallback_data(self):
        """API 실패 시 사용할 학습 데이터 로드"""
        try:
            from data_loader import load_data
            self.fallback_data = load_data()
            print(f"[Fallback] 백업 데이터 로드: {len(self.fallback_data)} 행")
        except Exception as e:
            print(f"[Fallback] 백업 데이터 로드 실패: {e}")
            self.fallback_data = None

    def _build_hourly_profiles(self):
        """시간대별 프로파일 생성 (학습 데이터 기반)"""
        default_load = [
            0.55, 0.50, 0.48, 0.47, 0.48, 0.52,
            0.65, 0.80, 0.95, 1.10, 1.20, 1.25,
            1.30, 1.28, 1.25, 1.22, 1.18, 1.12,
            1.05, 0.95, 0.85, 0.75, 0.68, 0.60,
        ]
        default_solar = [
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
            0.10, 0.30, 0.55, 0.78, 0.93, 1.00,
            1.00, 0.95, 0.85, 0.70, 0.50, 0.28,
            0.10, 0.0, 0.0, 0.0, 0.0, 0.0,
        ]

        if self.fallback_data is not None and len(self.fallback_data) > 0:
            try:
                df = self.fallback_data
                load_by_hour = df.groupby('hour')['load_kw'].mean()
                solar_by_hour = df.groupby('hour')['solar_kw'].mean()

                load_mean = load_by_hour.mean()
                solar_mean = solar_by_hour.mean()

                if load_mean > 0:
                    self.load_profile = [
                        float(load_by_hour.get(h, load_mean) / load_mean)
                        for h in range(24)
                    ]
                else:
                    self.load_profile = default_load

                if solar_mean > 0:
                    daytime_max = max(
                        float(solar_by_hour.get(h, 0)) for h in range(6, 19)
                    )
                    if daytime_max > 0:
                        self.solar_profile = [
                            float(solar_by_hour.get(h, 0) / daytime_max)
                            for h in range(24)
                        ]
                    else:
                        self.solar_profile = default_solar
                else:
                    self.solar_profile = default_solar

                print(f"[프로파일] 학습 데이터 기반 시간대별 패턴 생성 완료")
                print(f"  부하 프로파일: 최저 {min(self.load_profile):.2f} ~ 최고 {max(self.load_profile):.2f}")
                print(f"  태양광 프로파일: 정오 피크 {self.solar_profile[12]:.2f}")
                return
            except Exception as e:
                print(f"[프로파일] 학습 데이터 분석 실패, 기본 패턴 사용: {e}")

        self.load_profile = default_load
        self.solar_profile = default_solar
        print(f"[프로파일] 기본 시간대별 패턴 사용")

    def _needs_api_refresh(self) -> bool:
        """API 갱신 필요 여부 (1시간 주기)"""
        if self.last_api_fetch is None:
            return True
        elapsed = (datetime.now() - self.last_api_fetch).total_seconds()
        return elapsed >= 3600

    # =================================================================
    # 태양광 데이터 — 3단계 fallback [신규]
    # =================================================================

    def _try_forecast_solar(self) -> bool:
        """
        [1순위] 기상청 단기예보 API → solar_estimator → 시간별 발전량

        Returns
        -------
        bool : True면 성공 (self.solar_forecast_df에 저장됨)
        """
        if not (API_AVAILABLE and SOLAR_ESTIMATOR_AVAILABLE):
            return False

        try:
            print(f"  [태양광 1순위] 기상청 단기예보 시도...")
            forecast_df = fetch_short_forecast()
            if forecast_df is None or len(forecast_df) == 0:
                raise RuntimeError("예보 데이터 비어있음")

            # 단기예보 → 태양광 변환 (간단 모드: [timestamp, solar_kw])
            solar_df = forecast_to_solar(forecast_df, detail=False)
            if solar_df is None or len(solar_df) == 0:
                raise RuntimeError("태양광 변환 결과 비어있음")

            self.solar_forecast_df = solar_df
            # 발표 시각 추출 (메타 정보)
            self.forecast_base_time = forecast_df['timestamp'].min().strftime(
                '%Y-%m-%d %H:%M'
            )
            self.solar_data_source = 'forecast'

            daytime_max = solar_df['solar_kw'].max()
            print(f"  [태양광 1순위 성공] 단기예보 {len(solar_df)}시간치 — "
                  f"피크 {daytime_max:.1f}kW")
            return True

        except Exception as e:
            print(f"  [태양광 1순위 실패] {e}")
            return False

    def _try_asos_backup_solar(self) -> bool:
        """
        [2순위] 기상청 ASOS API (2025년 같은 날짜) → 일사량 → 발전량

        실시간 데이터는 ASOS에 없으므로 작년 동일 날짜를 백업으로 사용.
        계절성 반영 가능.

        Returns
        -------
        bool : True면 성공 (self.hourly_solar_kw에 저장됨)
        """
        if not API_AVAILABLE:
            return False

        try:
            print(f"  [태양광 2순위] ASOS 백업 (작년 같은 날짜) 시도...")
            now = datetime.now()
            # 작년 같은 기간 (7일치)
            past_end = now - timedelta(days=365)
            past_start = past_end - timedelta(days=7)

            weather_df = fetch_weather_data(
                start_date=past_start.strftime('%Y-%m-%d'),
                end_date=past_end.strftime('%Y-%m-%d'),
                station_id=config.WEATHER_STATION_ID,
                use_cache=True,
            )
            if len(weather_df) == 0:
                raise RuntimeError("ASOS 응답 비어있음")

            # 낮 시간(6~18시) 일사량 평균 → 낮 피크 기준값
            weather_df['hour'] = pd.to_datetime(weather_df['timestamp']).dt.hour
            daytime = weather_df[(weather_df['hour'] >= 6) & (weather_df['hour'] <= 18)]
            avg_irradiance = daytime['solar_radiation'].mean()

            self.hourly_solar_kw = irradiance_to_solar_kw(avg_irradiance)
            self.solar_data_source = 'asos_backup'
            self.solar_forecast_df = None  # 단기예보 모드 해제
            self.forecast_base_time = past_end.strftime('%Y-%m-%d') + ' (작년 기준)'

            print(f"  [태양광 2순위 성공] ASOS 백업 — 낮 기준 {self.hourly_solar_kw:.1f}kW")
            return True

        except Exception as e:
            print(f"  [태양광 2순위 실패] {e}")
            return False

    def _use_pattern_solar(self):
        """
        [3순위] 학습 데이터(fallback_data) 시간대별 패턴 → 발전량

        가장 안전한 최후 fallback. 항상 성공.
        """
        print(f"  [태양광 3순위] 학습 데이터 패턴 사용")
        if self.fallback_data is not None:
            solar_by_hour = self.fallback_data.groupby('hour')['solar_kw'].mean()
            daytime_max = max(
                float(solar_by_hour.get(hh, 0)) for hh in range(6, 19)
            )
            self.hourly_solar_kw = daytime_max if daytime_max > 0 else 15.0
        else:
            self.hourly_solar_kw = 15.0

        self.solar_data_source = 'pattern'
        self.solar_forecast_df = None
        self.forecast_base_time = None
        print(f"  [태양광 3순위] 낮 기준 {self.hourly_solar_kw:.1f}kW (학습 패턴)")

    def _fetch_solar_3tier(self):
        """태양광 데이터 3단계 fallback 수행"""
        print(f"\n[태양광 fetch] 3단계 fallback 시작")

        if self._try_forecast_solar():
            return  # 1순위 성공

        if self._try_asos_backup_solar():
            return  # 2순위 성공

        self._use_pattern_solar()  # 3순위 (항상 성공)

    # =================================================================
    # 메인 API 호출
    # =================================================================

    def _fetch_api_data(self):
        """실제 API에서 데이터 가져오기 (부하/SMP/태양광)"""
        if not API_AVAILABLE:
            self.api_status = 'fallback'
            self._use_fallback_baseline()
            return

        print(f"\n[API 호출] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        now = datetime.now()

        end_date = now.strftime('%Y-%m-%d')
        start_date = (now - timedelta(days=7)).strftime('%Y-%m-%d')

        try:
            # 1. 부하 데이터 (기존 그대로)
            load_df = fetch_load_data(use_cache=True)
            if len(load_df) > 0:
                recent = load_df.tail(24)
                avg_mw = recent['load_mw'].mean()
                scale = config.TARGET_AVG_LOAD_KW / (avg_mw * 1000)
                self.hourly_load_kw = float(avg_mw * 1000 * scale)
                print(f"  부하(일평균): {self.hourly_load_kw:.2f} kW")

            # 2. 태양광 — 3단계 fallback
            self._fetch_solar_3tier()

            # 3. SMP 데이터 (기존 그대로)
            try:
                smp_df = fetch_smp_data(
                    start_date=start_date,
                    end_date=end_date,
                    use_cache=True
                )
                if len(smp_df) > 0:
                    self.hourly_smp = float(smp_df.tail(24)['smp'].mean())
                    print(f"  SMP: {self.hourly_smp:.2f} 원/kWh")
            except Exception as e:
                print(f"  SMP 실패 (계속 진행): {e}")
                self.hourly_smp = self.hourly_smp or 100.0

            self.last_api_fetch = datetime.now()
            self.api_status = 'live'

            # 피크 임계값 동적 재조정
            if self.hourly_load_kw is not None and self.hourly_load_kw > 0:
                new_threshold = self.hourly_load_kw * 1.2
                old_threshold = self.controller.peak_threshold
                self.controller.peak_threshold = new_threshold
                print(f"  피크 임계값: {old_threshold:.2f} -> {new_threshold:.2f} kW (현재 부하 x 1.2)")

            print(f"[API] 갱신 완료\n")

        except Exception as e:
            print(f"[API 실패] {e}")
            if self.hourly_load_kw is not None:
                self.api_status = 'cached'
            else:
                self.api_status = 'fallback'
                self._use_fallback_baseline()

    def _use_fallback_baseline(self):
        """API 실패 시 학습 데이터에서 baseline 추출 (전체 fallback)"""
        if self.fallback_data is None:
            self.hourly_load_kw = 35.0
            self.hourly_solar_kw = 15.0
            self.hourly_smp = 100.0
            self.solar_data_source = 'pattern'
        else:
            self.hourly_load_kw = float(self.fallback_data['load_kw'].mean())
            self._use_pattern_solar()  # 태양광은 3순위로 처리
            if 'smp' in self.fallback_data.columns:
                self.hourly_smp = float(self.fallback_data['smp'].mean())
            else:
                self.hourly_smp = 100.0

        if self.hourly_load_kw and self.hourly_load_kw > 0:
            self.controller.peak_threshold = self.hourly_load_kw * 1.2

    # =================================================================
    # 실시간 데이터 생성 (분 단위 시뮬레이션)
    # =================================================================

    def _get_solar_kw_now(self, now: datetime) -> float:
        """
        현재 시각의 태양광 발전량 결정 (3단계에 따라 다른 방식)

        1순위 (forecast): solar_forecast_df에서 현재 시각 lookup → 정확한 값
        2,3순위 (asos_backup, pattern): hourly_solar_kw × solar_profile[h]
        """
        h = now.hour

        # 1순위: 단기예보 → 시간별 정확한 값 lookup
        if self.solar_data_source == 'forecast' and self.solar_forecast_df is not None:
            df = self.solar_forecast_df
            # 현재 시각과 같은 hour의 예보값 찾기
            target_hour = now.replace(minute=0, second=0, microsecond=0)
            mask = df['timestamp'] == target_hour
            if mask.any():
                base_kw = float(df.loc[mask, 'solar_kw'].iloc[0])
            else:
                # 정확한 시각이 없으면 가장 가까운 시각
                df_ts = pd.to_datetime(df['timestamp'])
                idx = (df_ts - pd.Timestamp(target_hour)).abs().idxmin()
                base_kw = float(df.loc[idx, 'solar_kw'])

            # 분 단위 변동 (±10%)
            if base_kw > 0:
                variation = random.uniform(-0.10, 0.10)
                return max(0.0, base_kw * (1 + variation))
            return 0.0

        # 2,3순위: 기준값 × 시간대 프로파일
        solar_ratio = self.solar_profile[h] if self.solar_profile else (1.0 if 6 <= h <= 18 else 0.0)
        if solar_ratio > 0 and self.hourly_solar_kw:
            variation = random.uniform(-0.10, 0.10)
            return max(0.0, self.hourly_solar_kw * solar_ratio * (1 + variation))
        return 0.0

    def _get_realtime_data(self) -> dict:
        """현재 시점의 실시간 데이터 생성"""
        now = datetime.now()
        h = now.hour

        if self.hourly_load_kw is None:
            self._use_fallback_baseline()

        # 부하: 일평균 × 시간대 비율 × 분단위 변동(±5%) — 기존 로직 유지
        load_ratio = self.load_profile[h] if self.load_profile else 1.0
        load_variation = random.uniform(-0.05, 0.05)
        load_kw = self.hourly_load_kw * load_ratio * (1 + load_variation)
        load_kw = max(0, load_kw)

        # 태양광: 3단계 방식별 처리
        solar_kw = self._get_solar_kw_now(now)

        predicted_net_load = load_kw - solar_kw

        # 데이터 소스 표시용 라벨 (한글)
        solar_source_label = {
            'forecast'    : '단기예보',
            'asos_backup' : 'ASOS백업',
            'pattern'     : '패턴추정',
            'pending'     : '대기중',
        }.get(self.solar_data_source, '알수없음')

        return {
            'timestamp': now.strftime('%Y-%m-%d %H:%M:%S'),
            'hour': now.hour,
            'load_kw': load_kw,
            'solar_kw': solar_kw,
            'predicted_net_load_kw': predicted_net_load,
            'smp': self.hourly_smp or 100.0,
            'data_source': self.api_status,
            'solar_source': solar_source_label,
            'forecast_base': self.forecast_base_time or '',
        }

    # DB 저장 & 메인 루프
    def _save_to_db(self, state: dict):
        """상태를 DB에 저장"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('''
                INSERT INTO realtime
                (timestamp, hour, load_kw, solar_kw, soc, bess_power_kw,
                 charge_kw, discharge_kw, grid_power_kw, tariff_rate, tariff_period, action,
                 smp, data_source, solar_source, forecast_base)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                state['timestamp'], state['hour'],
                state['load_kw'], state['solar_kw'], state['soc'],
                state['bess_power_kw'], state['charge_kw'], state['discharge_kw'],
                state['grid_power_kw'], state['tariff_rate'], state['tariff_period'],
                state['action'], state.get('smp', 0.0),
                state.get('data_source', 'unknown'),
                state.get('solar_source', 'unknown'),
                state.get('forecast_base', ''),
            ))

            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[DB 저장 에러] {e}")

    def run(self):
        """메인 실시간 제어 루프"""
        print("[실시간 엔진] 시작")
        self.running = True

        self._fetch_api_data()

        while self.running:
            try:
                if self._needs_api_refresh():
                    self._fetch_api_data()

                data = self._get_realtime_data()

                result = self.controller.control(
                    predicted_net_load=data['predicted_net_load_kw'],
                    actual_load_kw=data['load_kw'],
                    actual_solar_kw=data['solar_kw'],
                    hour=data['hour'],
                    time_step=config.TIME_STEP_HOURS,
                    month=datetime.now().month,
                )

                state = {
                    **data,
                    'soc': self.controller.soc,
                    'bess_power_kw': result['bess_power_kw'],
                    'charge_kw': max(0, -result['bess_power_kw']),
                    'discharge_kw': max(0, result['bess_power_kw']),
                    'grid_power_kw': result['grid_power_kw'],
                    'tariff_rate': config.get_tariff_rate(int(data['hour']), datetime.now().month),
                    'tariff_period': result['tariff_period'],
                    'action': result['action'],
                }
                self._save_to_db(state)

                self.iteration_count += 1
                if self.iteration_count % 30 == 0:
                    status_tag = {
                        'live': '[LIVE]',
                        'cached': '[CACHED]',
                        'fallback': '[FALLBACK]',
                        'pending': '[PENDING]',
                    }.get(self.api_status, '[?]')

                    solar_tag = data.get('solar_source', '?')

                    print(f"{status_tag}[{solar_tag}] {data['timestamp']} - "
                          f"부하: {data['load_kw']:.1f}kW | "
                          f"태양광: {data['solar_kw']:.1f}kW | "
                          f"SOC: {self.controller.soc*100:.1f}% | "
                          f"BESS: {result['bess_power_kw']:.1f}kW")

                time.sleep(1)

            except KeyboardInterrupt:
                print("\n[실시간 엔진] 종료...")
                self.running = False
                break
            except Exception as e:
                print(f"[에러] 제어 루프: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(5)

    def stop(self):
        self.running = False
        print("[실시간 엔진] 정지 신호")


if __name__ == '__main__':
    print("=" * 70)
    print("  BESS 실시간 제어 엔진 (3단계 fallback 하이브리드 모드)")
    print("=" * 70)
    print()
    print("작동 방식:")
    print("  1시간 주기 -> 실제 API 데이터 fetch")
    print("    - 부하: ODcloud")
    print("    - SMP:  공공데이터포털")
    print("    - 태양광: 3단계 fallback")
    print("        1순위: 기상청 단기예보 (실시간 예보)")
    print("        2순위: 기상청 ASOS  (작년 같은 날짜)")
    print("        3순위: 학습 데이터 패턴")
    print("  1초 주기   -> 실측값 +/- 미세 변동으로 시뮬레이션")
    print("  현재 시간으로 timestamp 저장 -> 대시보드 정상 작동")
    print()
    print("=" * 70)

    engine = RealtimeEngine('realtime_data/realtime.db')

    try:
        engine.run()
    except KeyboardInterrupt:
        print("\n[종료]")
