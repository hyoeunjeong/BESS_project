"""
realtime_engine.py - BESS 실시간 제어 엔진 (하이브리드 방식)
================================================================
실제 API 데이터 + 분 단위 시뮬레이션

작동 원리
---------
1. [1시간 주기] 실제 API에서 데이터 fetch (실측값)
   - ODcloud: 부하 데이터
   - 기상청 ASOS: 일사량 → 태양광 변환
   - SMP API: 전력가격
   
2. [1초 주기] 실측값 기반 분 단위 시뮬레이션
   - 부하: 실측값 ± 5% 변동
   - 태양광: 일사량 기반 + 변동
   - BESS 제어: LSTM이 위 데이터로 충방전 결정
   - timestamp는 항상 현재 시간으로 저장

3. API 호출 실패 시 fallback
   - 마지막 성공 데이터 유지
   - 그 이상 실패 시 학습 데이터 패턴 사용
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
    )
    API_AVAILABLE = True
except ImportError as e:
    print(f"[경고] api_client import 실패: {e}")
    print("       fallback 모드로 작동합니다.")
    API_AVAILABLE = False


def irradiance_to_solar_kw(solar_radiation_mj: float,
                           capacity_kw: float = None,
                           efficiency: float = None) -> float:
    """
    일사량(MJ/m²/h) -> 태양광 발전량(kW) 변환
    
    Parameters
    ----------
    solar_radiation_mj : 시간당 일사량 (MJ/m²)
    capacity_kw        : 태양광 설비 용량 (kW), 기본값 config.PV_CAPACITY_KW
    efficiency         : 시스템 효율, 기본값 config.PV_EFFICIENCY
    
    Returns
    -------
    float : 태양광 출력 (kW)
    
    변환 공식:
      MJ/m²/h × (1000/3600) = W/m²
      → 표준 일사량 1000 W/m² 대비 비율 × 설비용량 × 효율 = 출력
    """
    if pd.isna(solar_radiation_mj) or solar_radiation_mj <= 0:
        return 0.0
    
    cap = capacity_kw if capacity_kw is not None else config.PV_CAPACITY_KW
    eff = efficiency if efficiency is not None else config.PV_EFFICIENCY
    
    # MJ/m²/h -> W/m² (1 MJ/h = 1000000/3600 W ≈ 277.78 W)
    irradiance_w_per_m2 = solar_radiation_mj * 1000.0 / 3.6
    
    # 표준 조건(1000 W/m²) 대비 비율
    ratio = irradiance_w_per_m2 / 1000.0
    
    # 출력 = 설비용량 × 일사량 비율 × 효율
    return float(cap * ratio * eff)


class RealtimeEngine:
    """API 실측값 + 분 단위 시뮬레이션 하이브리드 엔진"""
    
    def __init__(self, db_path: str = 'realtime_data/realtime.db'):
        self.db_path = db_path
        self.controller = LSTMBESSController()
        
        # API 데이터 캐시
        self.hourly_load_kw = None
        self.hourly_solar_kw = None
        self.hourly_smp = None
        self.last_api_fetch = None
        self.api_status = 'pending'
        
        # Fallback 데이터
        self.fallback_data = None
        
        self.running = False
        self.iteration_count = 0
        
        self._init_database()
        self._init_fallback_data()
        
        # 피크 임계값 초기값 (API 호출 후 자동 재조정됨)
        self.controller.peak_threshold = 50.0
        
        print("[실시간 엔진] 초기화 완료 (하이브리드 모드)")
    
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 기존 테이블에 새 컬럼 추가 (마이그레이션)
        try:
            cursor.execute('ALTER TABLE realtime ADD COLUMN smp REAL')
        except sqlite3.OperationalError:
            pass  # 이미 존재
        try:
            cursor.execute('ALTER TABLE realtime ADD COLUMN data_source TEXT')
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
    
    def _needs_api_refresh(self) -> bool:
        """API 갱신 필요 여부 (1시간 주기)"""
        if self.last_api_fetch is None:
            return True
        elapsed = (datetime.now() - self.last_api_fetch).total_seconds()
        return elapsed >= 3600
    
    def _fetch_api_data(self):
        """실제 API에서 데이터 가져오기"""
        if not API_AVAILABLE:
            self.api_status = 'fallback'
            self._use_fallback_baseline()
            return
        
        print(f"\n[API 호출] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        now = datetime.now()
        
        # 날짜 범위 계산 (최근 7일)
        end_date = now.strftime('%Y-%m-%d')
        start_date = (now - timedelta(days=7)).strftime('%Y-%m-%d')
        
        try:
            # 1. 부하 데이터 (전체 캐시 사용)
            load_df = fetch_load_data(use_cache=True)
            if len(load_df) > 0:
                recent = load_df.tail(24)
                avg_mw = recent['load_mw'].mean()
                scale = config.TARGET_AVG_LOAD_KW / (avg_mw * 1000)
                self.hourly_load_kw = float(avg_mw * 1000 * scale)
                print(f"  부하: {self.hourly_load_kw:.2f} kW")
            
            # 2. 기상청 일사량 -> 태양광 변환
            try:
                weather_df = fetch_weather_data(
                    start_date=start_date,
                    end_date=end_date,
                    station_id=config.WEATHER_STATION_ID,
                    use_cache=True
                )
                if len(weather_df) > 0:
                    recent_weather = weather_df.tail(24)
                    if 6 <= now.hour <= 18:
                        # 일사량 평균 -> 태양광 변환
                        avg_irradiance = recent_weather['solar_radiation'].mean()
                        self.hourly_solar_kw = irradiance_to_solar_kw(avg_irradiance)
                    else:
                        self.hourly_solar_kw = 0.0
                    print(f"  태양광: {self.hourly_solar_kw:.2f} kW (일사량 기반)")
            except Exception as e:
                print(f"  태양광 실패 (계속 진행): {e}")
                # 시간대 기반 추정
                if 6 <= now.hour <= 18:
                    self.hourly_solar_kw = 15.0  # 기본값
                else:
                    self.hourly_solar_kw = 0.0
            
            # 3. SMP 데이터
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
            
            # 피크 임계값 동적 재조정 (현재 실시간 부하의 1.2배)
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
        """API 실패 시 학습 데이터에서 baseline 추출"""
        if self.fallback_data is None:
            self.hourly_load_kw = 35.0
            self.hourly_solar_kw = 10.0 if 6 <= datetime.now().hour <= 18 else 0.0
            self.hourly_smp = 100.0
        else:
            current_hour = datetime.now().hour
            same_hour_data = self.fallback_data[
                self.fallback_data['hour'] == current_hour
            ]
            
            if len(same_hour_data) > 0:
                self.hourly_load_kw = float(same_hour_data['load_kw'].mean())
                self.hourly_solar_kw = float(same_hour_data['solar_kw'].mean())
                if 'smp' in same_hour_data.columns:
                    self.hourly_smp = float(same_hour_data['smp'].mean())
                else:
                    self.hourly_smp = 100.0
        
        # 피크 임계값 재조정
        if self.hourly_load_kw and self.hourly_load_kw > 0:
            self.controller.peak_threshold = self.hourly_load_kw * 1.2
    
    def _get_realtime_data(self) -> dict:
        """현재 시점의 실시간 데이터 생성"""
        now = datetime.now()
        
        if self.hourly_load_kw is None:
            self._use_fallback_baseline()
        
        # 분 단위 변동 (±5%)
        load_variation = random.uniform(-0.05, 0.05)
        load_kw = self.hourly_load_kw * (1 + load_variation)
        load_kw = max(0, load_kw)
        
        # 태양광 (시간대 + 변동)
        if 6 <= now.hour <= 18:
            solar_variation = random.uniform(-0.10, 0.10)
            solar_kw = self.hourly_solar_kw * (1 + solar_variation)
            solar_kw = max(0, solar_kw)
        else:
            solar_kw = 0.0
        
        predicted_net_load = load_kw - solar_kw
        
        return {
            'timestamp': now.strftime('%Y-%m-%d %H:%M:%S'),
            'hour': now.hour,
            'load_kw': load_kw,
            'solar_kw': solar_kw,
            'predicted_net_load_kw': predicted_net_load,
            'smp': self.hourly_smp or 100.0,
            'data_source': self.api_status,
        }
    
    def _save_to_db(self, state: dict):
        """상태를 DB에 저장"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO realtime 
                (timestamp, hour, load_kw, solar_kw, soc, bess_power_kw, 
                 charge_kw, discharge_kw, grid_power_kw, tariff_rate, tariff_period, action,
                 smp, data_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                state['timestamp'], state['hour'],
                state['load_kw'], state['solar_kw'], state['soc'],
                state['bess_power_kw'], state['charge_kw'], state['discharge_kw'],
                state['grid_power_kw'], state['tariff_rate'], state['tariff_period'],
                state['action'], state.get('smp', 0.0),
                state.get('data_source', 'unknown'),
            ))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[DB 저장 에러] {e}")
    
    def run(self):
        """메인 실시간 제어 루프"""
        print("[실시간 엔진] 시작")
        self.running = True
        
        # 시작 시 즉시 API 호출
        self._fetch_api_data()
        
        while self.running:
            try:
                # 1) 1시간 주기로 API 갱신
                if self._needs_api_refresh():
                    self._fetch_api_data()
                
                # 2) 실시간 데이터 생성
                data = self._get_realtime_data()
                
                # 3) BESS 제어
                result = self.controller.control(
                    predicted_net_load=data['predicted_net_load_kw'],
                    actual_load_kw=data['load_kw'],
                    actual_solar_kw=data['solar_kw'],
                    hour=data['hour'],
                    time_step=config.TIME_STEP_HOURS,
                )
                
                # 4) 상태 저장
                state = {
                    **data,
                    'soc': self.controller.soc,
                    'bess_power_kw': result['bess_power_kw'],
                    'charge_kw': max(0, -result['bess_power_kw']),
                    'discharge_kw': max(0, result['bess_power_kw']),
                    'grid_power_kw': result['grid_power_kw'],
                    'tariff_rate': config.TOU_TARIFF.get(result['tariff_period'], 0),
                    'tariff_period': result['tariff_period'],
                    'action': result['action'],
                }
                self._save_to_db(state)
                
                # 5) 진행 상황 출력
                self.iteration_count += 1
                if self.iteration_count % 30 == 0:
                    status_tag = {
                        'live': '[LIVE]',
                        'cached': '[CACHED]',
                        'fallback': '[FALLBACK]',
                        'pending': '[PENDING]',
                    }.get(self.api_status, '[?]')
                    
                    print(f"{status_tag} {data['timestamp']} - "
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
    print("  BESS 실시간 제어 엔진 (하이브리드 모드)")
    print("=" * 70)
    print()
    print("작동 방식:")
    print("  1시간 주기 -> 실제 API 데이터 fetch (ODcloud, 기상청, SMP)")
    print("  1초 주기   -> 실측값 +/- 미세 변동으로 시뮬레이션")
    print("  현재 시간으로 timestamp 저장 -> 대시보드 정상 작동")
    print()
    print("=" * 70)
    
    engine = RealtimeEngine('realtime_data/realtime.db')
    
    try:
        engine.run()
    except KeyboardInterrupt:
        print("\n[종료]")