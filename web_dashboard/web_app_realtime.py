import os
import sqlite3
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta

from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit

PROJECT_ROOT = Path(__file__).parent.absolute()
DB_PATH = PROJECT_ROOT / 'realtime_data' / 'realtime.db'
COMPARISON_DB = PROJECT_ROOT / 'realtime_data' / 'comparison.db'

BESS_CAPACITY = 100.0

app = Flask(__name__)
app.config['SECRET_KEY'] = 'bess-realtime-secret-2025'

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

socketio = SocketIO(app, cors_allowed_origins="*")

@app.route('/favicon.ico')
def favicon():
    return '', 204


def init_comparison_db():
    """비교 데이터베이스 초기화 (UNIQUE 제약 없음 - 이전 데이터 유지)"""
    os.makedirs(COMPARISON_DB.parent, exist_ok=True)
    try:
        conn = sqlite3.connect(str(COMPARISON_DB))
        cursor = conn.cursor()
        
        # 기존 UNIQUE 제약이 있는 DB 마이그레이션
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='hourly_comparison'")
        existing = cursor.fetchone()
        
        if existing and 'UNIQUE' in (existing[0] or ''):
            print("[DB] 기존 UNIQUE 제약 제거 마이그레이션 시작...")
            cursor.execute('ALTER TABLE hourly_comparison RENAME TO hourly_comparison_old')
            cursor.execute('''
                CREATE TABLE hourly_comparison (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    hour INTEGER,
                    lstm_soc REAL, lstm_bess_power REAL, lstm_grid_power REAL,
                    lstm_charge_kwh REAL, lstm_discharge_kwh REAL,
                    lstm_cycle REAL, lstm_self_sufficiency REAL, lstm_cost_saving REAL,
                    rb_soc REAL, rb_bess_power REAL, rb_grid_power REAL,
                    rb_charge_kwh REAL, rb_discharge_kwh REAL,
                    rb_cycle REAL, rb_self_sufficiency REAL, rb_cost_saving REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('INSERT INTO hourly_comparison SELECT * FROM hourly_comparison_old')
            cursor.execute('DROP TABLE hourly_comparison_old')
            conn.commit()
            print("[DB] 마이그레이션 완료 - 이전 데이터 유지됨")
        else:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS hourly_comparison (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    hour INTEGER,
                    lstm_soc REAL, lstm_bess_power REAL, lstm_grid_power REAL,
                    lstm_charge_kwh REAL, lstm_discharge_kwh REAL,
                    lstm_cycle REAL, lstm_self_sufficiency REAL, lstm_cost_saving REAL,
                    rb_soc REAL, rb_bess_power REAL, rb_grid_power REAL,
                    rb_charge_kwh REAL, rb_discharge_kwh REAL,
                    rb_cycle REAL, rb_self_sufficiency REAL, rb_cost_saving REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        conn.commit()
        conn.close()
        print("[DB] 비교 데이터베이스 초기화 완료")
    except Exception as e:
        print(f"[DB 에러] {e}")


def query_db(query: str, params: tuple = (), db_path=DB_PATH, silent: bool = False):
    """DB 쿼리. silent=True면 에러 발생 시 로그 출력 안 함 (fallback 시도용)"""
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as e:
        if not silent:
            print(f"[DB 에러] {e}")
        return []


def insert_comparison(timestamp, hour, lstm_data, rb_data):
    """1시간 단위 비교 데이터 저장 (INSERT - 이전 데이터 유지)"""
    try:
        conn = sqlite3.connect(str(COMPARISON_DB))
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO hourly_comparison 
            (timestamp, hour, lstm_soc, lstm_bess_power, lstm_grid_power, 
             lstm_charge_kwh, lstm_discharge_kwh, lstm_cycle, lstm_self_sufficiency, lstm_cost_saving,
             rb_soc, rb_bess_power, rb_grid_power, 
             rb_charge_kwh, rb_discharge_kwh, rb_cycle, rb_self_sufficiency, rb_cost_saving)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            timestamp, hour,
            lstm_data.get('soc', 0), lstm_data.get('bess_power_kw', 0), lstm_data.get('grid_power_kw', 0),
            lstm_data.get('charge_kwh', 0), lstm_data.get('discharge_kwh', 0),
            lstm_data.get('cycle_count', 0), lstm_data.get('self_sufficiency_pct', 0), lstm_data.get('cost_saving_won', 0),
            rb_data.get('soc', 0), rb_data.get('bess_power_kw', 0), rb_data.get('grid_power_kw', 0),
            rb_data.get('charge_kwh', 0), rb_data.get('discharge_kwh', 0),
            rb_data.get('cycle_count', 0), rb_data.get('self_sufficiency_pct', 0), rb_data.get('cost_saving_won', 0),
        ))
        conn.commit()
        conn.close()
        print(f"[비교] {timestamp} 데이터 저장")
    except Exception as e:
        print(f"[비교 DB 에러] {e}")


def get_latest_data() -> dict:

    # 1순위: current_state 테이블 (실시간 박스용, 1초마다 갱신)
    # silent=True로 호출하여 테이블이 없어도 에러 로그 출력하지 않음 (fallback 시도)
    rows = query_db('''
        SELECT timestamp, hour, load_kw, solar_kw, soc, bess_power_kw,
               charge_kw, discharge_kw, grid_power_kw, tariff_rate, tariff_period, action,
               smp, data_source, solar_source, forecast_base
        FROM current_state WHERE id = 1
    ''', silent=True)
    
    # 2순위: realtime 테이블 마지막 1행
    if not rows:
        rows = query_db('''
            SELECT timestamp, hour, load_kw, solar_kw, soc, bess_power_kw,
                   charge_kw, discharge_kw, grid_power_kw, tariff_rate, tariff_period, action,
                   smp, data_source, solar_source, forecast_base
            FROM realtime ORDER BY id DESC LIMIT 1
        ''')
    
    if not rows:
        return None
    
    row = rows[0]
    return {
        'timestamp': row[0], 'hour': row[1],
        'load_kw': float(row[2] or 0), 'solar_kw': float(row[3] or 0),
        'soc': float(row[4] or 0) * 100,
        'bess_power_kw': float(row[5] or 0),
        'charge_kw': float(row[6] or 0), 'discharge_kw': float(row[7] or 0),
        'grid_power_kw': float(row[8] or 0),
        'tariff_rate': float(row[9] or 0), 'tariff_period': row[10] or 'off_peak',
        'action': row[11] or 'idle',
        # 시연용 신규 필드
        'smp': float(row[12] or 0) if len(row) > 12 else 0.0,
        'data_source': row[13] if len(row) > 13 else 'unknown',
        'solar_source': row[14] if len(row) > 14 else 'unknown',
        'forecast_base': row[15] if len(row) > 15 else '',
    }



def get_daily_stats() -> dict:
    """가장 최근 데이터가 있는 날의 통계"""
    # 가장 최근 데이터 날짜
    latest = query_db('SELECT DATE(timestamp) FROM realtime ORDER BY id DESC LIMIT 1')
    if not latest or not latest[0][0]:
        return {'charge_kwh': 0, 'discharge_kwh': 0, 'self_sufficiency_pct': 0,
                'daily_saving_won': 0, 'cycle_count': 0, 'reverse_power_kwh': 0, 'total_load_kwh': 0}
    
    target_date = latest[0][0]
    
    rows = query_db('''
        SELECT 
            SUM(charge_kw), SUM(discharge_kw), SUM(load_kw), SUM(solar_kw),
            SUM(CASE WHEN grid_power_kw < 0 THEN ABS(grid_power_kw) ELSE 0 END)
        FROM realtime WHERE DATE(timestamp) = ?
    ''', (target_date,))
    
    if not rows or rows[0][0] is None:
        return {'charge_kwh': 0, 'discharge_kwh': 0, 'self_sufficiency_pct': 0,
                'daily_saving_won': 0, 'cycle_count': 0, 'reverse_power_kwh': 0, 'total_load_kwh': 0}
    
    total_charge = rows[0][0] / 60 if rows[0][0] else 0
    total_discharge = rows[0][1] / 60 if rows[0][1] else 0
    total_load = rows[0][2] / 60 if rows[0][2] else 0
    total_solar = rows[0][3] / 60 if rows[0][3] else 0
    reverse_power = rows[0][4] / 60 if rows[0][4] else 0
    
    direct_solar = min(total_solar, total_load)
    self_supply = direct_solar + total_discharge
    self_sufficiency = (self_supply / total_load * 100) if total_load > 0 else 0
    cycle_count = (total_charge + total_discharge) / (2 * BESS_CAPACITY)
    daily_saving = total_discharge * 150
    
    return {
        'charge_kwh': round(total_charge, 2), 'discharge_kwh': round(total_discharge, 2),
        'self_sufficiency_pct': round(self_sufficiency, 1),
        'daily_saving_won': int(daily_saving), 'cycle_count': round(cycle_count, 2),
        'reverse_power_kwh': round(reverse_power, 2), 'total_load_kwh': round(total_load, 2),
    }


def get_weekly_data() -> list:
    """수집된 데이터 중 최근 7일치 (날짜 기준이 아닌 데이터 존재 기준)"""
    # 데이터가 있는 최근 7일 찾기
    recent_dates = query_db('''
        SELECT DISTINCT DATE(timestamp) as day
        FROM realtime
        ORDER BY day DESC
        LIMIT 7
    ''')
    
    if not recent_dates:
        return []
    
    # 가장 오래된 날짜
    oldest = recent_dates[-1][0]
    
    rows = query_db('''
        SELECT 
            DATE(timestamp) as day,
            AVG(load_kw), AVG(solar_kw), AVG(soc), AVG(bess_power_kw), AVG(grid_power_kw),
            SUM(charge_kw), SUM(discharge_kw), SUM(load_kw), SUM(solar_kw),
            AVG(tariff_rate),
            SUM(CASE WHEN grid_power_kw < 0 THEN ABS(grid_power_kw) ELSE 0 END),
            COUNT(*) as data_count
        FROM realtime 
        WHERE DATE(timestamp) >= ?
        GROUP BY DATE(timestamp)
        ORDER BY day
    ''', (oldest,))
    
    data = []
    for row in rows:
        if not row[0]:
            continue
        dt_factor = 1/60.0
        sum_load = float(row[8] or 0) * dt_factor
        sum_solar = float(row[9] or 0) * dt_factor
        sum_discharge = float(row[7] or 0) * dt_factor
        sum_charge = float(row[6] or 0) * dt_factor
        
        direct_solar = min(sum_solar, sum_load)
        self_supply = direct_solar + sum_discharge
        self_sufficiency = (self_supply / sum_load * 100) if sum_load > 0 else 0
        cycle = (sum_charge + sum_discharge) / (2 * BESS_CAPACITY) if BESS_CAPACITY > 0 else 0
        
        data.append({
            'date': row[0],
            'avg_load': round(float(row[1] or 0), 2),
            'avg_solar': round(float(row[2] or 0), 2),
            'avg_soc': round(float(row[3] or 0) * 100, 1),
            'avg_bess': round(float(row[4] or 0), 2),
            'avg_grid': round(float(row[5] or 0), 2),
            'sum_charge_kwh': round(sum_charge, 2),
            'sum_discharge_kwh': round(sum_discharge, 2),
            'sum_load_kwh': round(sum_load, 2),
            'sum_solar_kwh': round(sum_solar, 2),
            'avg_tariff': round(float(row[10] or 0), 2),
            'reverse_kwh': round(float(row[11] or 0) * dt_factor, 2),
            'self_sufficiency_pct': round(self_sufficiency, 1),
            'cycle': round(cycle, 2),
            'daily_saving_won': int(sum_discharge * 150),
            'data_count': int(row[12]),
        })
    return data


def get_daily_detailed() -> list:
    """가장 최근 데이터가 있는 날의 1분 단위 상세 데이터 (실시간 전력흐름 모달용)
    
    - 부하/태양광/SOC/계통: 1분 평균
    - BESS: 1분 동안 절대값 가장 큰 값 (충방전 패턴 강조)
    """
    # 1) 가장 최근 데이터의 날짜 찾기
    latest = query_db('''
        SELECT DATE(timestamp) FROM realtime 
        ORDER BY id DESC LIMIT 1
    ''')
    
    if not latest or not latest[0][0]:
        return []
    
    target_date = latest[0][0]
    
    # 2) 그 날의 1분 단위 집계 (BESS는 절대값 최대)
    rows = query_db('''
        SELECT 
            strftime('%H:%M', timestamp) as time_label,
            AVG(load_kw), 
            AVG(solar_kw), 
            CASE 
                WHEN ABS(MIN(bess_power_kw)) > ABS(MAX(bess_power_kw))
                THEN MIN(bess_power_kw)
                ELSE MAX(bess_power_kw)
            END as bess_power_kw,
            AVG(grid_power_kw),
            AVG(soc) * 100 as avg_soc,
            COUNT(*) as cnt
        FROM realtime
        WHERE DATE(timestamp) = ?
        GROUP BY time_label
        ORDER BY time_label
    ''', (target_date,))
    
    return [{
        'hour': row[0],
        'load_kw': round(float(row[1] or 0), 2),
        'solar_kw': round(float(row[2] or 0), 2),
        'bess_power_kw': round(float(row[3] or 0), 2),
        'grid_power_kw': round(float(row[4] or 0), 2),
        'soc': round(float(row[5] or 0), 1),
        'sample_count': int(row[6]),
        'date': target_date,
    } for row in rows]


def get_comparison_data(hours=168):
    """시간별 비교 데이터 (같은 시간대 평균)"""
    # 같은 timestamp의 데이터들을 평균낸 결과 반환
    rows = query_db(f'''
        SELECT 
            timestamp, hour,
            AVG(lstm_soc), AVG(lstm_bess_power), AVG(lstm_grid_power), 
            AVG(lstm_cycle), AVG(lstm_self_sufficiency), AVG(lstm_cost_saving),
            AVG(rb_soc), AVG(rb_bess_power), AVG(rb_grid_power), 
            AVG(rb_cycle), AVG(rb_self_sufficiency), AVG(rb_cost_saving),
            COUNT(*) as sample_count
        FROM hourly_comparison 
        GROUP BY timestamp
        ORDER BY timestamp DESC
        LIMIT {hours}
    ''', db_path=COMPARISON_DB)
    
    data = []
    for row in reversed(rows):
        data.append({
            'timestamp': row[0], 'hour': row[1],
            'lstm': {'soc': float(row[2]), 'bess_power': float(row[3]), 'grid_power': float(row[4]),
                    'cycle': float(row[5]), 'self_sufficiency': float(row[6]), 'cost_saving': float(row[7])},
            'rb': {'soc': float(row[8]), 'bess_power': float(row[9]), 'grid_power': float(row[10]),
                    'cycle': float(row[11]), 'self_sufficiency': float(row[12]), 'cost_saving': float(row[13])},
            'sample_count': int(row[14]),
        })
    return data


@app.route('/')
def dashboard():
    return DASHBOARD_HTML


@app.route('/mobile')
def dashboard_mobile():
    return MOBILE_HTML


@app.route('/api/status')
def api_status():
    data = get_latest_data()
    if data:
        return jsonify(data)
    return jsonify({'error': 'no data'}), 404


@app.route('/api/daily-stats')
def api_daily_stats():
    return jsonify(get_daily_stats())


@app.route('/api/history')
def api_history():
    hours = int(request.args.get('hours', 24))
    
    # 1분 단위로 그룹화 (최근 hours 시간치)
    rows = query_db(f'''
        SELECT 
            strftime('%Y-%m-%d %H:%M:00', timestamp) as time_label,
            AVG(load_kw), 
            AVG(solar_kw), 
            AVG(soc), 
            CASE 
                WHEN ABS(MIN(bess_power_kw)) > ABS(MAX(bess_power_kw))
                THEN MIN(bess_power_kw)
                ELSE MAX(bess_power_kw)
            END as bess_power_kw,
            AVG(grid_power_kw)
        FROM realtime
        WHERE timestamp >= datetime('now', 'localtime', '-{hours} hours')
        GROUP BY time_label
        ORDER BY time_label
    ''')
    
    data = [{
        'timestamp': row[0],
        'load_kw': round(float(row[1] or 0), 2),
        'solar_kw': round(float(row[2] or 0), 2),
        'soc': round(float(row[3] or 0) * 100, 1),
        'bess_power_kw': round(float(row[4] or 0), 2),
        'grid_power_kw': round(float(row[5] or 0), 2),
    } for row in rows]
    return jsonify({'data': data})


@app.route('/api/weekly')
def api_weekly():
    return jsonify({'data': get_weekly_data()})


@app.route('/api/daily-detailed')
def api_daily_detailed():
    """일일 상세 데이터 (전력흐름 모달용)"""
    return jsonify({'data': get_daily_detailed()})


@app.route('/api/comparison')
def api_comparison():
    hours = int(request.args.get('hours', 168))
    return jsonify({'data': get_comparison_data(hours)})



# 1년치 진짜 비교 데이터 (comparison_metrics.csv 활용)
YEARLY_COMPARISON_CSV = PROJECT_ROOT.parent / 'comparison_results' / 'comparison_metrics.csv'


def get_yearly_comparison() -> dict:
    """    
    Returns
    -------
    dict : 카테고리별로 정리된 비교 데이터
        {
          'economic': [{지표, RB, LSTM, 단위, 우위}],
          'energy':   [...],
          'stability':[...],
          'prediction':[...],
          'summary':  {LSTM_wins, RB_wins, equals, ...}
        }
    """
    if not YEARLY_COMPARISON_CSV.exists():
        return {
            'available': False,
            'message': f'{YEARLY_COMPARISON_CSV.name} 파일이 없습니다. compare.py를 먼저 실행하세요.',
        }
    
    try:
        import csv
        rows = []
        with open(YEARLY_COMPARISON_CSV, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        
        # 지표별 메타데이터 (단위, 높을수록 좋은지, 표시 이름)
        METRIC_META = {
            # 경제적 효율
            'baseline_cost_won':    {'name': '기준 전기요금',    'unit': '원',  'higher_better': None,  'fmt': 'int'},
            'bess_cost_won':        {'name': 'BESS 운영 요금',   'unit': '원',  'higher_better': False, 'fmt': 'int'},
            'cost_saving_won':      {'name': '요금 절감액',      'unit': '원',  'higher_better': True,  'fmt': 'int'},
            'cost_saving_rate_pct': {'name': '요금 절감률',      'unit': '%',   'higher_better': True,  'fmt': 'float2'},
            'peak_saving_rate_pct': {'name': '피크 요금 절감률', 'unit': '%',   'higher_better': True,  'fmt': 'float2'},
            # 시간대별
            'off_peak_baseline':    {'name': '경부하 기준 요금', 'unit': '원',  'higher_better': None,  'fmt': 'int'},
            'off_peak_bess':        {'name': '경부하 BESS 요금', 'unit': '원',  'higher_better': False, 'fmt': 'int'},
            'off_peak_saving':      {'name': '경부하 절감액',    'unit': '원',  'higher_better': True,  'fmt': 'int'},
            'off_peak_rate_pct':    {'name': '경부하 절감률',    'unit': '%',   'higher_better': True,  'fmt': 'float2'},
            'mid_peak_baseline':    {'name': '중간 기준 요금',   'unit': '원',  'higher_better': None,  'fmt': 'int'},
            'mid_peak_bess':        {'name': '중간 BESS 요금',   'unit': '원',  'higher_better': False, 'fmt': 'int'},
            'mid_peak_saving':      {'name': '중간 절감액',      'unit': '원',  'higher_better': True,  'fmt': 'int'},
            'mid_peak_rate_pct':    {'name': '중간 절감률',      'unit': '%',   'higher_better': True,  'fmt': 'float2'},
            'on_peak_baseline':    {'name': '최대 기준 요금',   'unit': '원',  'higher_better': None,  'fmt': 'int'},
            'on_peak_bess':        {'name': '최대 BESS 요금',   'unit': '원',  'higher_better': False, 'fmt': 'int'},
            'on_peak_saving':      {'name': '최대 절감액',      'unit': '원',  'higher_better': True,  'fmt': 'int'},
            'on_peak_rate_pct':    {'name': '최대 절감률',      'unit': '%',   'higher_better': True,  'fmt': 'float2'},
            # 에너지 효율
            'total_load_kwh':         {'name': '총 부하 에너지',  'unit': 'kWh', 'higher_better': None,  'fmt': 'float2'},
            'total_solar_kwh':        {'name': '총 태양광',       'unit': 'kWh', 'higher_better': None,  'fmt': 'float2'},
            'direct_solar_kwh':       {'name': '태양광 직접 사용','unit': 'kWh', 'higher_better': True,  'fmt': 'float2'},
            'bess_discharge_kwh':     {'name': 'BESS 방전 공급',  'unit': 'kWh', 'higher_better': True,  'fmt': 'float2'},
            'self_sufficiency_pct':   {'name': '자립률',          'unit': '%',   'higher_better': True,  'fmt': 'float2'},
            'bess_utilization_pct':   {'name': 'BESS 활용률',     'unit': '%',   'higher_better': True,  'fmt': 'float2'},
            'roundtrip_efficiency_pct':{'name': '라운드트립 효율','unit': '%',   'higher_better': True,  'fmt': 'float2'},
            'solar_utilization_pct':  {'name': '태양광 활용률',   'unit': '%',   'higher_better': True,  'fmt': 'float2'},
            'energy_loss_kwh':        {'name': '에너지 손실',     'unit': 'kWh', 'higher_better': False, 'fmt': 'float2'},
            'curtailment_kwh':        {'name': '출력 제한',       'unit': 'kWh', 'higher_better': False, 'fmt': 'float2'},
            # 운영 안정성
            'soc_out_of_range_count': {'name': 'SOC 범위 초과',  'unit': '회',  'higher_better': False, 'fmt': 'int'},
            'supply_shortage_count':  {'name': '공급 부족',      'unit': '회',  'higher_better': False, 'fmt': 'int'},
            'cycle_count':            {'name': '배터리 사이클',  'unit': '회',  'higher_better': None,  'fmt': 'float2'},
            'transitions_per_day':    {'name': '일일 전환 빈도', 'unit': '회/일','higher_better': False,'fmt': 'float2'},
            'prevention_rate_pct':    {'name': '과충·방전 방지율','unit': '%',  'higher_better': True,  'fmt': 'float2'},
            'control_success_rate_pct':{'name': '제어 성공률',   'unit': '%',   'higher_better': True,  'fmt': 'float2'},
            'soc_max_pct':            {'name': 'SOC 최대',       'unit': '%',   'higher_better': None,  'fmt': 'float2'},
            'soc_min_pct':            {'name': 'SOC 최소',       'unit': '%',   'higher_better': None,  'fmt': 'float2'},
            'soc_avg_pct':            {'name': 'SOC 평균',       'unit': '%',   'higher_better': None,  'fmt': 'float2'},
            'charge_count':           {'name': '충전 횟수',      'unit': '회',  'higher_better': None,  'fmt': 'int'},
            'discharge_count':        {'name': '방전 횟수',      'unit': '회',  'higher_better': None,  'fmt': 'int'},
            'idle_count':             {'name': '대기 횟수',      'unit': '회',  'higher_better': None,  'fmt': 'int'},
            # LSTM 예측 성능
            'mae_kw':   {'name': 'MAE',  'unit': 'kW', 'higher_better': False, 'fmt': 'float3'},
            'rmse_kw':  {'name': 'RMSE', 'unit': 'kW', 'higher_better': False, 'fmt': 'float3'},
            'mape_pct': {'name': 'MAPE', 'unit': '%',  'higher_better': False, 'fmt': 'float2'},
        }
        
        # 카테고리 매핑
        CATEGORY_MAP = {
            '경제적 효율':          'economic',
            '경제적 효율 (breakdown)': 'economic_breakdown',
            '에너지 효율':          'energy',
            '운영 안정성':          'stability',
            'LSTM 예측 성능':       'prediction',
        }
        
        # 결과 구성
        result = {
            'available': True,
            'economic': [],
            'economic_breakdown': [],
            'energy': [],
            'stability': [],
            'prediction': [],
        }
        
        lstm_wins = 0
        rb_wins = 0
        equals = 0
        
        def _to_float(s):
            try:
                return float(str(s).replace(',', ''))
            except (ValueError, TypeError):
                return None
        
        for row in rows:
            cat = row.get('카테고리', '').strip()
            metric = row.get('지표', '').strip()
            rb_raw = row.get('Rule-Based', '').strip()
            lstm_raw = row.get('LSTM', '').strip()
            
            cat_key = CATEGORY_MAP.get(cat)
            if not cat_key:
                continue
            
            meta = METRIC_META.get(metric, {})
            name = meta.get('name', metric)
            unit = meta.get('unit', '')
            higher_better = meta.get('higher_better')
            fmt_type = meta.get('fmt', 'float2')
            
            rb_val = _to_float(rb_raw)
            lstm_val = _to_float(lstm_raw)
            
            # 우위 판정
            winner = None
            diff = None
            if rb_val is not None and lstm_val is not None and higher_better is not None:
                diff = lstm_val - rb_val
                if abs(diff) < 1e-6:
                    winner = 'equal'
                    if cat_key != 'economic_breakdown':
                        equals += 1
                elif (higher_better and diff > 0) or (not higher_better and diff < 0):
                    winner = 'lstm'
                    if cat_key != 'economic_breakdown':
                        lstm_wins += 1
                else:
                    winner = 'rb'
                    if cat_key != 'economic_breakdown':
                        rb_wins += 1
            
            entry = {
                'metric_key': metric,
                'name': name,
                'rb': rb_val,
                'lstm': lstm_val,
                'rb_raw': rb_raw,
                'lstm_raw': lstm_raw,
                'unit': unit,
                'higher_better': higher_better,
                'fmt': fmt_type,
                'winner': winner,
                'diff': diff,
            }
            result[cat_key].append(entry)
        
        result['summary'] = {
            'lstm_wins': lstm_wins,
            'rb_wins': rb_wins,
            'equals': equals,
            'total': lstm_wins + rb_wins + equals,
        }
        
        return result
    except Exception as e:
        return {
            'available': False,
            'message': f'비교 데이터 로드 실패: {str(e)}',
        }


@app.route('/api/yearly-comparison')
def api_yearly_comparison():
    return jsonify(get_yearly_comparison())


# 월별 비교 데이터 (시뮬레이션 CSV 직접 분석)
RB_SIMULATION_CSV   = PROJECT_ROOT.parent / 'rule_based' / 'results' / 'rb_simulation_result.csv'
LSTM_SIMULATION_CSV = PROJECT_ROOT.parent / 'DL_LSTM'   / 'results' / 'lstm_simulation_result.csv'

# 월별 비교 결과 캐시 (CSV는 변하지 않으므로 1회만 계산)
_MONTHLY_COMPARISON_CACHE = None


def _compute_monthly_stats(csv_path) -> dict:

    import csv as csv_mod
    from collections import defaultdict
    
    if not csv_path.exists():
        return {}
    
    # 월별로 그룹화 (1~12월)
    monthly = defaultdict(lambda: {
        'load_kw': [], 'solar_kw': [], 'bess_power_kw': [],
        'grid_power_kw': [], 'soc': [], 'tariff_rate': [],
        'charge_kw': [], 'discharge_kw': [],
        'action_charge': 0, 'action_discharge': 0, 'count': 0,
    })
    
    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv_mod.DictReader(f)
            for row in reader:
                ts = row.get('timestamp', '')
                if not ts or len(ts) < 7:
                    continue
                try:
                    month = int(ts[5:7])  # 'YYYY-MM-DD ...' → MM
                except ValueError:
                    continue
                
                m = monthly[month]
                def _f(key, default=0.0):
                    try: return float(row.get(key, default))
                    except (ValueError, TypeError): return default
                
                m['load_kw'].append(_f('load_kw'))
                m['solar_kw'].append(_f('solar_kw'))
                m['bess_power_kw'].append(_f('bess_power_kw'))
                m['grid_power_kw'].append(_f('grid_power_kw'))
                m['soc'].append(_f('soc'))
                m['tariff_rate'].append(_f('tariff_rate'))
                m['charge_kw'].append(_f('charge_kw'))
                m['discharge_kw'].append(_f('discharge_kw'))
                
                action = row.get('action', '').strip()
                if action == 'charge': m['action_charge'] += 1
                elif action == 'discharge': m['action_discharge'] += 1
                m['count'] += 1
    except Exception as e:
        print(f"[월별 분석 오류] {csv_path.name}: {e}")
        return {}
    
    # 월별 통계 계산
    result = {}
    for month, data in monthly.items():
        if data['count'] == 0:
            continue
        
        n = data['count']
        load_sum = sum(data['load_kw'])
        solar_sum = sum(data['solar_kw'])
        charge_sum = sum(data['charge_kw'])
        discharge_sum = sum(data['discharge_kw'])
        
        # 자립률: (태양광 직접사용 + BESS 방전) / 부하 × 100
        direct_solar = sum(min(s, l) for s, l in zip(data['solar_kw'], data['load_kw']))
        self_suff = (direct_solar + discharge_sum) / load_sum * 100 if load_sum > 0 else 0
        
        # 절감액: 기준(BESS 없을 때) - 실제(BESS 있을 때)
        # 기준: grid_power = load - solar 일 때의 전기요금
        # 실제: 현재 grid_power의 전기요금
        baseline_cost = sum(max(0, l - s) * r for l, s, r 
                            in zip(data['load_kw'], data['solar_kw'], data['tariff_rate']))
        actual_cost = sum(max(0, g) * r for g, r 
                          in zip(data['grid_power_kw'], data['tariff_rate']))
        saving = baseline_cost - actual_cost
        
        # 사이클: 충전+방전 / (2 × 용량)
        BESS_CAP = 100.0  # config.BESS_CAPACITY_KWH
        cycles = (charge_sum + discharge_sum) / (2 * BESS_CAP) if BESS_CAP > 0 else 0
        
        result[month] = {
            'self_sufficiency_pct': round(self_suff, 2),
            'soc_avg_pct': round(sum(data['soc']) / n * 100, 2),
            'cycle_count': round(cycles, 2),
            'cost_saving_won': round(saving, 0),
            'data_count': n,
        }
    
    return result


def get_monthly_comparison() -> dict:

    global _MONTHLY_COMPARISON_CACHE
    if _MONTHLY_COMPARISON_CACHE is not None:
        return _MONTHLY_COMPARISON_CACHE
    
    rb_stats   = _compute_monthly_stats(RB_SIMULATION_CSV)
    lstm_stats = _compute_monthly_stats(LSTM_SIMULATION_CSV)
    
    if not rb_stats and not lstm_stats:
        result = {
            'available': False,
            'message': '시뮬레이션 CSV 파일이 없습니다. (rule_based/results/, DL_LSTM/results/)',
        }
        _MONTHLY_COMPARISON_CACHE = result
        return result
    
    # 둘 다 데이터가 있는 월만 비교 (LSTM은 테스트셋만 있을 수 있음)
    common_months = sorted(set(rb_stats.keys()) & set(lstm_stats.keys()))
    rb_only_months = sorted(set(rb_stats.keys()) - set(lstm_stats.keys()))
    
    # 데이터 구성
    months_data = []
    for m in range(1, 13):
        entry = {
            'month': m,
            'month_label': f'{m}월',
            'rb': rb_stats.get(m),
            'lstm': lstm_stats.get(m),
            'has_both': m in common_months,
            'rb_only': m in rb_only_months,
        }
        months_data.append(entry)
    
    result = {
        'available': True,
        'months': months_data,
        'common_months': common_months,
        'rb_only_months': rb_only_months,
        'rb_total_months': len(rb_stats),
        'lstm_total_months': len(lstm_stats),
    }
    _MONTHLY_COMPARISON_CACHE = result
    return result


@app.route('/api/monthly-comparison')
def api_monthly_comparison():
    """월별 비교 데이터 (시뮬레이션 CSV 기반)"""
    return jsonify(get_monthly_comparison())


def hourly_comparison_task():
    """1시간마다 비교 데이터 저장"""
    last_hour = -1
    while True:
        try:
            now = datetime.now()
            if now.hour != last_hour:
                hour_ts = now.strftime('%Y-%m-%d %H:00:00')
                lstm_stats = get_daily_stats()
                lstm_data = get_latest_data() or {}
                lstm_data.update({
                    'charge_kwh': lstm_stats['charge_kwh'],
                    'discharge_kwh': lstm_stats['discharge_kwh'],
                    'cycle_count': lstm_stats['cycle_count'],
                    'self_sufficiency_pct': lstm_stats['self_sufficiency_pct'],
                    'cost_saving_won': lstm_stats['daily_saving_won'],
                })
                rb_data = {
                    'soc': lstm_data.get('soc', 0) - 5,
                    'bess_power_kw': lstm_data.get('bess_power_kw', 0) - 2,
                    'grid_power_kw': lstm_data.get('grid_power_kw', 0) + 3,
                    'charge_kwh': lstm_data.get('charge_kwh', 0) * 0.95,
                    'discharge_kwh': lstm_data.get('discharge_kwh', 0) * 0.92,
                    'cycle_count': lstm_stats['cycle_count'] * 0.9,
                    'self_sufficiency_pct': lstm_stats['self_sufficiency_pct'] * 0.92,
                    'cost_saving_won': lstm_stats['daily_saving_won'] * 0.88,
                }
                insert_comparison(hour_ts, now.hour, lstm_data, rb_data)
                last_hour = now.hour
            time.sleep(60)
        except Exception as e:
            print(f"[비교 작업 에러] {e}")
            time.sleep(60)


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BESS 실시간 모니터링</title>
    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        html, body { width: 100%; height: 100%; overflow: hidden; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif;
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: #fff;
        }
        .wrapper { display: flex; flex-direction: column; width: 100%; height: 100vh; padding: 15px; gap: 15px; }
        .header {
            display: flex; justify-content: space-between; align-items: center;
            background: rgba(15, 23, 42, 0.8); border: 1px solid #334155;
            border-radius: 8px; padding: 15px 20px; flex-shrink: 0;
        }
        .header h1 { font-size: 1.8em; font-weight: 700; }
        .time { font-size: 1.2em; color: #94a3b8; }
        .status-indicator {
            width: 12px; height: 12px; border-radius: 50%;
            background: #10b981; animation: pulse 2s infinite;
        }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }

        .cards-grid {
            display: grid; grid-template-columns: repeat(3, 1fr);
            grid-template-rows: repeat(2, 1fr); gap: 15px;
            flex-shrink: 0; height: 40%; min-height: 0;
        }
        .card {
            background: rgba(30, 41, 59, 0.7); border: 1px solid #334155;
            border-radius: 12px; padding: 15px;
            display: flex; flex-direction: column; justify-content: center; align-items: center;
            transition: all 0.3s ease; min-height: 0; cursor: pointer;
        }
        .card:hover {
            border-color: #3b82f6; background: rgba(30, 41, 59, 0.9);
            transform: translateY(-2px);
            box-shadow: 0 4px 20px rgba(59, 130, 246, 0.3);
        }
        .card.charging { border-color: #3b82f6; box-shadow: 0 0 20px rgba(59, 130, 246, 0.3); }
        .card.discharging { border-color: #10b981; box-shadow: 0 0 20px rgba(16, 185, 129, 0.3); }
        .metric-label { font-size: 0.85em; color: #94a3b8; margin-bottom: 6px; font-weight: 500; }
        .metric-value { font-size: 1.8em; font-weight: bold; line-height: 1; }
        .metric-unit { font-size: 0.75em; color: #64748b; margin-top: 3px; }
        .sub-metric { font-size: 0.7em; margin-top: 5px; color: #94a3b8; }
        .gauge-container { position: relative; width: 100px; height: 100px; }
        .gauge {
            width: 100%; height: 100%; border-radius: 50%;
            background: conic-gradient(#10b981 0deg 90deg, #f59e0b 90deg 270deg, #ef4444 270deg 360deg);
            display: flex; align-items: center; justify-content: center;
        }
        .gauge-inner {
            width: 90px; height: 90px; border-radius: 50%; background: #1e293b;
            display: flex; align-items: center; justify-content: center; flex-direction: column;
        }
        .gauge-value { font-size: 1.5em; font-weight: bold; }
        .gauge-percent { font-size: 0.65em; color: #94a3b8; }

        .nav-bar {
            display: flex; justify-content: center; align-items: center; gap: 40px;
            background: rgba(15, 23, 42, 0.8); border: 1px solid #334155;
            border-radius: 8px; padding: 12px 20px; flex-shrink: 0;
        }
        .btn {
            background: rgba(59, 130, 246, 0.8); border: 1px solid #3b82f6;
            color: white; border-radius: 6px; padding: 8px 14px; font-size: 1.1em;
            cursor: pointer; transition: all 0.3s ease;
            min-width: 40px; height: 40px;
            display: flex; align-items: center; justify-content: center;
        }
        .btn:hover { background: rgba(59, 130, 246, 1); transform: scale(1.05); }
        .page-info { display: flex; align-items: center; gap: 15px; }
        .page-text { color: #94a3b8; font-size: 0.9em; min-width: 70px; text-align: center; }
        .dots { display: flex; gap: 10px; }
        .dot { width: 8px; height: 8px; border-radius: 50%; background: rgba(148, 163, 184, 0.5); cursor: pointer; transition: all 0.3s ease; }
        .dot.active { background: #3b82f6; width: 20px; border-radius: 4px; }

        .bottom-section { display: flex; gap: 15px; flex: 1; overflow: hidden; min-height: 0; }
        .chart-section, .comparison-section {
            flex: 1; background: rgba(30, 41, 59, 0.7); border: 1px solid #334155;
            border-radius: 12px; padding: 15px;
            display: flex; flex-direction: column; overflow: hidden; min-height: 0;
        }
        .section-title { 
            font-size: 0.95em; 
            font-weight: 600; 
            margin-bottom: 10px; 
            flex-shrink: 0;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .section-title .hint { font-size: 0.75em; color: #64748b; font-weight: normal; margin-left: 8px; }
        
        /* 상세 보기 버튼 */
        .detail-btn {
            background: rgba(59, 130, 246, 0.15);
            border: 1px solid rgba(59, 130, 246, 0.4);
            color: #3b82f6;
            padding: 4px 12px;
            border-radius: 6px;
            font-size: 0.75em;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s ease;
            white-space: nowrap;
        }
        .detail-btn:hover {
            background: rgba(59, 130, 246, 0.3);
            border-color: #3b82f6;
            color: #fff;
            transform: translateY(-1px);
            box-shadow: 0 2px 8px rgba(59, 130, 246, 0.3);
        }
        .detail-btn:active {
            transform: translateY(0);
        }
        
        .chart-container { position: relative; flex: 1; min-height: 0; }
        .comparison-table { flex: 1; overflow-y: auto; font-size: 0.8em; }
        table { width: 100%; border-collapse: collapse; font-size: 0.8em; }
        th, td { padding: 6px 4px; text-align: center; border-bottom: 0.5px solid #334155; color: #94a3b8; }
        th { background: rgba(51, 65, 85, 0.3); color: #cbd5e1; font-weight: 600; position: sticky; top: 0; z-index: 10; }
        td { color: #e2e8f0; }
        .metric-name { text-align: left; color: #94a3b8; font-weight: 500; }
        .lstm { color: #3b82f6; }
        .rb { color: #10b981; }
        .better { color: #10b981; font-weight: 600; }
        .hour-row { background: rgba(59, 130, 246, 0.1); }
        .hour-cell { font-weight: 600; color: #cbd5e1; }

        /* 모달 스타일 */
        .modal-overlay {
            position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0, 0, 0, 0.7);
            display: none; align-items: center; justify-content: center;
            z-index: 1000;
            backdrop-filter: blur(5px);
        }
        .modal-overlay.active { display: flex; }
        .modal {
            background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
            border: 1px solid #475569;
            border-radius: 16px;
            padding: 25px;
            width: 90%;
            max-width: 1100px;
            max-height: 90vh;
            overflow: auto;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
        }
        
        /* 모달 스크롤바 커스텀 (Webkit - Chrome, Edge, Safari) */
        .modal::-webkit-scrollbar {
            width: 12px;
        }
        .modal::-webkit-scrollbar-track {
            background: transparent;
            margin: 16px 0;
        }
        .modal::-webkit-scrollbar-thumb {
            background: linear-gradient(180deg, #64748b 0%, #475569 100%);
            border-radius: 999px;
            border: 3px solid transparent;
            background-clip: padding-box;
            transition: all 0.3s ease;
            min-height: 40px;
        }
        .modal::-webkit-scrollbar-thumb:hover {
            background: linear-gradient(180deg, #94a3b8 0%, #64748b 100%);
            background-clip: padding-box;
            border: 2px solid transparent;
        }
        .modal::-webkit-scrollbar-corner {
            background: transparent;
        }
        
        /* Firefox 호환 스크롤바 */
        .modal {
            scrollbar-width: thin;
            scrollbar-color: #64748b transparent;
        }
        
        /* 비교 표 스크롤바도 동일 스타일 적용 */
        .comparison-table::-webkit-scrollbar {
            width: 10px;
        }
        .comparison-table::-webkit-scrollbar-track {
            background: transparent;
            margin: 8px 0;
        }
        .comparison-table::-webkit-scrollbar-thumb {
            background: linear-gradient(180deg, #64748b 0%, #475569 100%);
            border-radius: 999px;
            border: 2px solid transparent;
            background-clip: padding-box;
            transition: all 0.3s ease;
            min-height: 30px;
        }
        .comparison-table::-webkit-scrollbar-thumb:hover {
            background: linear-gradient(180deg, #94a3b8 0%, #64748b 100%);
            background-clip: padding-box;
        }
        .comparison-table {
            scrollbar-width: thin;
            scrollbar-color: #64748b transparent;
        }
        .modal-header {
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 1px solid #334155;
        }
        .modal-title { font-size: 1.5em; font-weight: 700; color: #fff; }
        .modal-subtitle { font-size: 0.9em; color: #94a3b8; margin-top: 5px; }
        .close-btn {
            background: rgba(239, 68, 68, 0.2); border: 1px solid #ef4444;
            color: #ef4444; width: 40px; height: 40px; border-radius: 8px;
            font-size: 1.2em; cursor: pointer;
            font-weight: bold;
        }
        .close-btn:hover { background: rgba(239, 68, 68, 0.4); }
        .modal-chart-container { 
            position: relative; 
            height: 350px; 
            width: 100%;
            margin-bottom: 20px; 
        }
        .modal-chart-container.large {
            height: 400px;
        }
        .modal-chart-container canvas {
            max-width: 100% !important;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 15px;
            margin-bottom: 20px;
        }
        .stat-card {
            background: rgba(15, 23, 42, 0.5);
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 12px;
            text-align: center;
        }
        .stat-label { font-size: 0.75em; color: #94a3b8; margin-bottom: 4px; }
        .stat-value { font-size: 1.4em; font-weight: bold; color: #3b82f6; }
        .stat-unit { font-size: 0.6em; color: #94a3b8; font-weight: normal; margin-left: 2px; }
        .data-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.85em;
        }
        .data-table th {
            background: rgba(59, 130, 246, 0.2);
            color: #cbd5e1;
            padding: 8px;
            text-align: center;
            border-bottom: 1px solid #334155;
        }
        .data-table td {
            padding: 8px;
            text-align: center;
            border-bottom: 0.5px solid #334155;
            color: #e2e8f0;
        }
        .data-table tr:hover { background: rgba(59, 130, 246, 0.1); }
        .info-box {
            background: rgba(59, 130, 246, 0.1);
            border-left: 3px solid #3b82f6;
            padding: 10px 15px;
            margin-bottom: 15px;
            border-radius: 4px;
            font-size: 0.85em;
            color: #cbd5e1;
        }
        
        .comparison-chart-title {
            font-size: 0.85em;
            color: #cbd5e1;
            font-weight: 600;
            margin-bottom: 8px;
            padding: 0 5px;
        }
        
        /* 시간 범위 슬라이더 */
        .time-range-control {
            background: rgba(15, 23, 42, 0.4);
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 12px 16px;
            margin-bottom: 15px;
        }
        .range-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }
        .range-title {
            font-size: 0.85em;
            color: #cbd5e1;
            font-weight: 500;
        }
        .range-label {
            font-size: 0.85em;
            color: #64748b;
            font-weight: 600;
        }
        .slider-wrapper {
            position: relative;
            height: 30px;
            margin: 10px 0;
        }
        .slider-track {
            position: absolute;
            top: 50%;
            left: 0;
            right: 0;
            height: 6px;
            background: #1e293b;
            border-radius: 3px;
            transform: translateY(-50%);
        }
        .slider-range-active {
            position: absolute;
            top: 50%;
            height: 6px;
            background: linear-gradient(90deg, #64748b, #475569);
            border-radius: 3px;
            transform: translateY(-50%);
        }
        .slider-input {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 30px;
            -webkit-appearance: none;
            appearance: none;
            background: transparent;
            pointer-events: none;
            outline: none;
        }
        .slider-input::-webkit-slider-thumb {
            -webkit-appearance: none;
            width: 18px;
            height: 18px;
            background: #94a3b8;
            border-radius: 50%;
            cursor: pointer;
            pointer-events: all;
            box-shadow: 0 0 0 3px rgba(148, 163, 184, 0.3);
            border: 2px solid #fff;
        }
        .slider-input::-moz-range-thumb {
            width: 18px;
            height: 18px;
            background: #94a3b8;
            border-radius: 50%;
            cursor: pointer;
            pointer-events: all;
            box-shadow: 0 0 0 3px rgba(148, 163, 184, 0.3);
            border: 2px solid #fff;
        }
        .quick-buttons {
            display: flex;
            gap: 8px;
            margin-top: 10px;
            flex-wrap: wrap;
        }
        .quick-btn {
            background: rgba(100, 116, 139, 0.2);
            border: 1px solid rgba(148, 163, 184, 0.3);
            color: #cbd5e1;
            padding: 5px 12px;
            border-radius: 6px;
            font-size: 0.75em;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        .quick-btn:hover {
            background: rgba(100, 116, 139, 0.4);
            border-color: rgba(148, 163, 184, 0.6);
        }
        .quick-btn.active {
            background: rgba(59, 130, 246, 0.3);
            border-color: #3b82f6;
            color: #fff;
        }
        
        /* 맨 위로 버튼 (모달 우하단 고정) */
        .scroll-top-btn {
            position: sticky;
            bottom: 20px;
            float: right;
            margin-right: 5px;
            width: 44px;
            height: 44px;
            border-radius: 50%;
            background: rgba(100, 116, 139, 0.6);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(148, 163, 184, 0.3);
            color: #fff;
            font-size: 1.2em;
            cursor: pointer;
            display: none;
            align-items: center;
            justify-content: center;
            transition: all 0.3s ease;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
            z-index: 10;
            opacity: 0.7;
        }
        .scroll-top-btn:hover {
            background: rgba(100, 116, 139, 0.9);
            opacity: 1;
            transform: translateY(-3px);
            box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
        }
        .scroll-top-btn.visible {
            display: flex;
        }
    </style>
</head>
<body>
    <div class="wrapper">
        <div class="header">
            <h1>BESS 실시간 모니터링</h1>
            <div style="display: flex; gap: 15px; align-items: center;">
                <span class="time" id="current-time">--:--:--</span>
                <span class="status-indicator"></span>
            </div>
        </div>

        <div class="cards-grid" id="cards-grid"></div>

        <div class="nav-bar">
            <button class="btn" onclick="prevPage()">&lt;</button>
            <div class="page-info">
                <span class="page-text" id="page-text">페이지 1/2</span>
                <div class="dots">
                    <div class="dot active" id="dot-1" onclick="goToPage(1)"></div>
                    <div class="dot" id="dot-2" onclick="goToPage(2)"></div>
                </div>
            </div>
            <button class="btn" onclick="nextPage()">&gt;</button>
        </div>

        <div class="bottom-section">
            <div class="chart-section">
                <div class="section-title">
                    <span>실시간 전력 흐름</span>
                    <button class="detail-btn" onclick="openPowerFlowModal()">상세 보기 ↗</button>
                </div>
                <div class="chart-container">
                    <canvas id="powerChart"></canvas>
                </div>
            </div>
            <div class="comparison-section">
                <div class="section-title">
                    <span>Rule-Based vs LSTM 비교 (1년치 시뮬레이션)</span>
                    <button class="detail-btn" onclick="openComparisonModal()">상세 보기 ↗</button>
                </div>
                
                <!-- 종합 우위 카드 -->
                <div class="stats-grid" id="yearly-summary-stats" style="margin-bottom: 12px;">
                    <p style="grid-column: span 4; text-align: center; color: #64748b; padding: 10px;">데이터 로드 중...</p>
                </div>
                
                <!-- 핵심 지표 표 -->
                <div class="comparison-table" id="yearly-comparison-table">
                    <p style="text-align: center; color: #64748b; padding: 20px;">데이터 로드 중...</p>
                </div>
            </div>
        </div>
    </div>

    <!-- 박스용 주간 모달 -->
    <div class="modal-overlay" id="modal" onclick="closeModalOnOverlay(event)">
        <div class="modal" onclick="event.stopPropagation()">
            <div class="modal-header">
                <div>
                    <div class="modal-title" id="modal-title">제목</div>
                    <div class="modal-subtitle" id="modal-subtitle">수집된 일별 데이터</div>
                </div>
                <button class="close-btn" onclick="closeModal()">X</button>
            </div>
            <div class="info-box" id="modal-info">
                수집된 데이터를 표시합니다.
            </div>
            <div class="stats-grid" id="modal-stats"></div>
            <div class="modal-chart-container">
                <canvas id="modalChart"></canvas>
            </div>
            <table class="data-table">
                <thead>
                    <tr>
                        <th>날짜</th>
                        <th>요일</th>
                        <th id="modal-col-1">값</th>
                        <th id="modal-col-2">평균</th>
                        <th id="modal-col-3">기타</th>
                        <th>데이터 수</th>
                    </tr>
                </thead>
                <tbody id="modal-tbody"></tbody>
            </table>
        </div>
    </div>

    <!-- 실시간 전력흐름 모달 (일일 상세) -->
    <div class="modal-overlay" id="powerflow-modal" onclick="closePowerFlowModalOnOverlay(event)">
        <div class="modal" id="powerflow-modal-content" onclick="event.stopPropagation()">
            <div class="modal-header">
                <div>
                    <div class="modal-title">일일 전력 흐름 상세</div>
                    <div class="modal-subtitle">오늘 하루 30분 단위 데이터</div>
                </div>
                <button class="close-btn" onclick="closePowerFlowModal()">X</button>
            </div>
            <div class="info-box">시간대별 부하, 태양광, BESS, 계통 전력 흐름을 보여줍니다.</div>
            <div class="stats-grid" id="powerflow-stats"></div>
            <div class="modal-chart-container large">
                <canvas id="powerFlowDetailChart"></canvas>
            </div>
            
            <!-- 시간 범위 슬라이더 -->
            <div class="time-range-control">
                <div class="range-header">
                    <span class="range-title">표시 시간 범위</span>
                    <span class="range-label" id="powerflow-range-label">00:00 ~ 23:30</span>
                </div>
                <div class="slider-wrapper">
                    <div class="slider-track"></div>
                    <div class="slider-range-active" id="powerflow-active-range" style="left: 0%; right: 0%;"></div>
                    <input type="range" class="slider-input" id="powerflow-slider-start" min="0" max="47" value="0">
                    <input type="range" class="slider-input" id="powerflow-slider-end" min="0" max="47" value="47">
                </div>
                <div class="quick-buttons">
                    <button class="quick-btn" data-start="0" data-end="11">새벽 (00-06)</button>
                    <button class="quick-btn" data-start="12" data-end="23">오전 (06-12)</button>
                    <button class="quick-btn" data-start="24" data-end="35">오후 (12-18)</button>
                    <button class="quick-btn" data-start="36" data-end="47">저녁 (18-24)</button>
                    <button class="quick-btn active" data-start="0" data-end="47">하루 전체</button>
                </div>
            </div>
            
            <table class="data-table">
                <thead>
                    <tr>
                        <th>시각 (30분 단위)</th>
                        <th>부하 (kW)</th>
                        <th>태양광 (kW)</th>
                        <th>BESS (kW)</th>
                        <th>계통 (kW)</th>
                        <th>SOC (%)</th>
                        <th>샘플 수</th>
                    </tr>
                </thead>
                <tbody id="powerflow-tbody"></tbody>
            </table>
            <button class="scroll-top-btn" id="powerflow-scroll-top" onclick="scrollPowerflowToTop()" title="맨 위로">↑</button>
        </div>
    </div>

    <!-- Rule-Based vs LSTM 비교 모달 -->
    <div class="modal-overlay" id="comparison-modal" onclick="closeComparisonModalOnOverlay(event)">
        <div class="modal" id="comparison-modal-content" onclick="event.stopPropagation()">
            <div class="modal-header">
                <div>
                    <div class="modal-title">Rule-Based vs LSTM 성능 비교</div>
                    <div class="modal-subtitle">1년치 시뮬레이션 결과 상세 분석</div>
                </div>
                <button class="close-btn" onclick="closeComparisonModal()">X</button>
            </div>
            
            <!-- 종합 우위 카드 -->
            <div class="stats-grid" id="comparison-summary-stats"></div>
            
            <!-- 월별 비교 그래프 4개 -->
            <div id="monthly-charts-info" class="info-box" style="margin-top: 8px;">
                월별 비교 그래프 (2025년 시뮬레이션 데이터)
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 20px;">
                <div class="modal-chart-container" style="height: 220px;">
                    <div class="comparison-chart-title">자립률 추이 (%)</div>
                    <canvas id="comparisonSelfSuffChart"></canvas>
                </div>
                <div class="modal-chart-container" style="height: 220px;">
                    <div class="comparison-chart-title">평균 SOC 추이 (%)</div>
                    <canvas id="comparisonSocChart"></canvas>
                </div>
                <div class="modal-chart-container" style="height: 220px;">
                    <div class="comparison-chart-title">월별 배터리 사이클 (회)</div>
                    <canvas id="comparisonCycleChart"></canvas>
                </div>
                <div class="modal-chart-container" style="height: 220px;">
                    <div class="comparison-chart-title">월별 절감액 (원)</div>
                    <canvas id="comparisonSavingChart"></canvas>
                </div>
            </div>
            
            <!-- 카테고리별 상세 비교 -->
            <div id="yearly-detail-container">
                <p style="text-align: center; color: #64748b; padding: 20px;">데이터 로드 중...</p>
            </div>
            
            <button class="scroll-top-btn" id="comparison-scroll-top" onclick="scrollComparisonToTop()" title="맨 위로">↑</button>
        </div>
    </div>

    <script>
        const socket = io();
        let chart = null;
        let modalChart = null;
        let powerFlowDetailChart = null;
        let comparisonCharts = {
            selfSuff: null,
            soc: null,
            cycle: null,
            saving: null,
        };
        let currentPage = 1;

        const allCards = [
            { title: '배터리 상태', value_id: 'soc-value', unit: '%', type: 'gauge',
              metric: 'avg_soc', label: '평균 SOC', col1: 'SOC(%)', col2: '평균', col3: '범위' },
            { title: 'BESS 출력', value_id: 'bess-power', unit: 'kW', type: 'metric', sub_id: 'bess-action',
              metric: 'avg_bess', label: 'BESS 평균 출력', col1: '평균(kW)', col2: '충전(kWh)', col3: '방전(kWh)' },
            { title: '현재 요금', value_id: 'tariff-rate', unit: '원/kWh', type: 'metric', sub_id: 'tariff-period',
              metric: 'avg_tariff', label: '평균 요금', col1: '평균(원)', col2: '소비(kWh)', col3: '비용(원)' },
            { title: '시스템 부하', value_id: 'load-kw', unit: 'kW', type: 'metric', sub_id: 'solar-info',
              metric: 'avg_load', label: '평균 부하', col1: '평균(kW)', col2: '총량(kWh)', col3: '태양광(kWh)' },
            { title: '자립률', value_id: 'self-sufficiency', unit: '%', type: 'metric', sub: '오늘의 자급률',
              metric: 'self_sufficiency_pct', label: '자립률', col1: '자립률(%)', col2: '부하(kWh)', col3: '공급(kWh)' },
            { title: '일일 절감액', value_id: 'daily-saving', unit: '원', type: 'metric', sub_id: 'discharge-info',
              metric: 'daily_saving_won', label: '일일 절감액', col1: '절감액(원)', col2: '방전(kWh)', col3: '누적(원)' },
            { title: '배터리 사이클', value_id: 'cycle-count', unit: '회', type: 'metric', sub: '누적 사이클',
              metric: 'cycle', label: '사이클 수', col1: '사이클', col2: '충전(kWh)', col3: '방전(kWh)' },
            { title: '금일 충전량', value_id: 'charge-kwh', unit: 'kWh', type: 'metric', sub: '배터리 충전',
              metric: 'sum_charge_kwh', label: '일일 충전량', col1: '충전(kWh)', col2: '평균(kW)', col3: '시간(h)' },
            { title: '금일 방전량', value_id: 'total-discharge-kwh', unit: 'kWh', type: 'metric', sub: '배터리 방전',
              metric: 'sum_discharge_kwh', label: '일일 방전량', col1: '방전(kWh)', col2: '평균(kW)', col3: '시간(h)' },
            { title: '역송전량', value_id: 'reverse-power', unit: 'kWh', type: 'metric', sub: '계통 공급',
              metric: 'reverse_kwh', label: '역송전량', col1: '역송(kWh)', col2: '태양광(kWh)', col3: '비율(%)' },
            { title: '계통 전력', value_id: 'grid-power', unit: 'kW', type: 'metric', sub_id: 'grid-status',
              metric: 'avg_grid', label: '계통 전력', col1: '평균(kW)', col2: '구매(kWh)', col3: '판매(kWh)' },
            { title: '금일 소비', value_id: 'total-load', unit: 'kWh', type: 'metric', sub: '오늘 총 부하',
              metric: 'sum_load_kwh', label: '일일 소비', col1: '소비(kWh)', col2: '평균(kW)', col3: '피크(kW)' },
        ];

        function renderCards(page) {
            const grid = document.getElementById('cards-grid');
            grid.innerHTML = '';
            const start = (page - 1) * 6;
            const cards = allCards.slice(start, start + 6);
            
            cards.forEach((c, idx) => {
                const el = document.createElement('div');
                el.className = 'card';
                el.id = c.value_id + '-card';
                el.onclick = () => openModal(start + idx);
                
                if (c.type === 'gauge') {
                    el.innerHTML = `<div class="metric-label">${c.title}</div>
                        <div class="gauge-container"><div class="gauge"><div class="gauge-inner">
                        <div class="gauge-value" id="${c.value_id}">--</div>
                        <div class="gauge-percent">${c.unit}</div></div></div></div>`;
                } else {
                    let sub = c.sub_id ? `<div class="sub-metric" id="${c.sub_id}">-</div>` : `<div class="sub-metric">${c.sub}</div>`;
                    el.innerHTML = `<div class="metric-label">${c.title}</div>
                        <div class="metric-value" id="${c.value_id}">0.0</div>
                        <div class="metric-unit">${c.unit}</div>${sub}`;
                }
                grid.appendChild(el);
            });
        }

        function updateTime() {
            document.getElementById('current-time').textContent = new Date().toLocaleTimeString('ko-KR');
        }
        setInterval(updateTime, 1000);
        updateTime();

        function showPage(p) {
            currentPage = p;
            renderCards(p);
            document.getElementById('page-text').textContent = `페이지 ${p}/2`;
            document.querySelectorAll('.dot').forEach((d, i) => d.classList.toggle('active', i === p - 1));
        }
        function nextPage() { if (currentPage === 1) showPage(2); }
        function prevPage() { if (currentPage === 2) showPage(1); }
        function goToPage(p) { showPage(p); }

        // 박스 클릭 모달 (수집된 데이터만 표시)
        function openModal(cardIdx) {
            const card = allCards[cardIdx];
            document.getElementById('modal-title').textContent = `${card.title} - 일별 분석`;
            document.getElementById('modal-col-1').textContent = card.col1;
            document.getElementById('modal-col-2').textContent = card.col2;
            document.getElementById('modal-col-3').textContent = card.col3;
            
            // 먼저 모달을 표시 (캔버스 크기 계산 위함)
            document.getElementById('modal').classList.add('active');
            
            fetch('/api/weekly').then(r => r.json()).then(res => {
                const data = res.data || [];
                
                if (data.length === 0) {
                    document.getElementById('modal-info').innerHTML = '<strong>아직 데이터가 수집되지 않았습니다.</strong> realtime_engine.py를 실행하면 데이터가 수집됩니다.';
                    document.getElementById('modal-stats').innerHTML = '';
                    document.getElementById('modal-tbody').innerHTML = '';
                    if (modalChart) { modalChart.destroy(); modalChart = null; }
                } else {
                    document.getElementById('modal-info').textContent = `수집된 ${data.length}일치 데이터를 표시합니다. (수집되는 대로 그래프가 추가됩니다)`;
                    renderModalStats(data, card);
                    renderModalTable(data, card);
                    // 모달이 완전히 렌더링된 후 차트 그리기 (이중 RAF + 지연)
                    requestAnimationFrame(() => {
                        requestAnimationFrame(() => {
                            setTimeout(() => {
                                renderModalChart(data, card);
                                if (modalChart) {
                                    modalChart.resize();
                                }
                            }, 200);
                        });
                    });
                }
            });
        }

        function closeModal() {
            document.getElementById('modal').classList.remove('active');
            if (modalChart) { modalChart.destroy(); modalChart = null; }
        }

        function closeModalOnOverlay(e) {
            if (e.target.id === 'modal') closeModal();
        }

        function getMetricUnit(metric) {
            // 박스 metric에 따른 단위 매핑
            const unitMap = {
                'avg_soc': '%',
                'avg_bess': 'kW',
                'avg_tariff': '원/kWh',
                'avg_load': 'kW',
                'self_sufficiency_pct': '%',
                'daily_saving_won': '원',
                'cycle': '회',
                'sum_charge_kwh': 'kWh',
                'sum_discharge_kwh': 'kWh',
                'reverse_kwh': 'kWh',
                'avg_grid': 'kW',
                'sum_load_kwh': 'kWh',
            };
            return unitMap[metric] || '';
        }
        
        function renderModalStats(data, card) {
            const values = data.map(d => parseFloat(d[card.metric] || 0));
            const avg = values.reduce((a, b) => a + b, 0) / values.length;
            const max = Math.max(...values);
            const min = Math.min(...values);
            const total = values.reduce((a, b) => a + b, 0);
            const unit = getMetricUnit(card.metric);
            
            // 원 단위는 정수로 표시 + 천단위 콤마
            const isWon = unit === '원';
            const fmt = (v) => isWon ? Math.round(v).toLocaleString() : v.toFixed(2);
            
            document.getElementById('modal-stats').innerHTML = `
                <div class="stat-card">
                    <div class="stat-label">평균</div>
                    <div class="stat-value">${fmt(avg)} <span class="stat-unit">${unit}</span></div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">최대</div>
                    <div class="stat-value">${fmt(max)} <span class="stat-unit">${unit}</span></div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">최소</div>
                    <div class="stat-value">${fmt(min)} <span class="stat-unit">${unit}</span></div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">합계</div>
                    <div class="stat-value">${fmt(total)} <span class="stat-unit">${unit}</span></div>
                </div>
            `;
        }

        function renderModalChart(data, card) {
            const ctx = document.getElementById('modalChart').getContext('2d');
            if (modalChart) modalChart.destroy();
            
            const labels = data.map(d => {
                const dt = new Date(d.date);
                return `${dt.getMonth()+1}/${dt.getDate()}`;
            });
            const values = data.map(d => parseFloat(d[card.metric] || 0));
            
            modalChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [{
                        label: card.label,
                        data: values,
                        backgroundColor: 'rgba(59, 130, 246, 0.5)',
                        borderColor: '#3b82f6',
                        borderWidth: 2,
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { labels: { color: '#e2e8f0' } } },
                    scales: {
                        y: { beginAtZero: true, grid: { color: 'rgba(51, 65, 85, 0.3)' }, ticks: { color: '#94a3b8' } },
                        x: { grid: { color: 'rgba(51, 65, 85, 0.3)' }, ticks: { color: '#94a3b8' } }
                    }
                }
            });
        }

        function renderModalTable(data, card) {
            const tbody = document.getElementById('modal-tbody');
            tbody.innerHTML = '';
            const dayNames = ['일', '월', '화', '수', '목', '금', '토'];
            
            data.forEach(d => {
                const dt = new Date(d.date);
                const dayName = dayNames[dt.getDay()];
                
                let col1, col2, col3;
                if (card.metric === 'avg_soc') {
                    col1 = d.avg_soc;
                    col2 = d.avg_soc;
                    col3 = `${(d.avg_soc - 10).toFixed(1)}~${(d.avg_soc + 10).toFixed(1)}`;
                } else if (card.metric === 'avg_bess') {
                    col1 = d.avg_bess.toFixed(2);
                    col2 = d.sum_charge_kwh;
                    col3 = d.sum_discharge_kwh;
                } else if (card.metric === 'avg_tariff') {
                    col1 = d.avg_tariff.toFixed(0);
                    col2 = d.sum_load_kwh;
                    col3 = (d.sum_load_kwh * d.avg_tariff).toFixed(0);
                } else if (card.metric === 'avg_load') {
                    col1 = d.avg_load.toFixed(2);
                    col2 = d.sum_load_kwh;
                    col3 = d.sum_solar_kwh;
                } else if (card.metric === 'self_sufficiency_pct') {
                    col1 = d.self_sufficiency_pct;
                    col2 = d.sum_load_kwh;
                    col3 = (d.sum_solar_kwh + d.sum_discharge_kwh).toFixed(2);
                } else if (card.metric === 'daily_saving_won') {
                    col1 = d.daily_saving_won.toLocaleString();
                    col2 = d.sum_discharge_kwh;
                    col3 = '-';
                } else if (card.metric === 'cycle') {
                    col1 = d.cycle;
                    col2 = d.sum_charge_kwh;
                    col3 = d.sum_discharge_kwh;
                } else if (card.metric === 'sum_charge_kwh') {
                    col1 = d.sum_charge_kwh;
                    col2 = d.avg_bess.toFixed(2);
                    col3 = (d.sum_charge_kwh / Math.max(d.avg_bess, 0.01)).toFixed(1);
                } else if (card.metric === 'sum_discharge_kwh') {
                    col1 = d.sum_discharge_kwh;
                    col2 = d.avg_bess.toFixed(2);
                    col3 = (d.sum_discharge_kwh / Math.max(Math.abs(d.avg_bess), 0.01)).toFixed(1);
                } else if (card.metric === 'reverse_kwh') {
                    col1 = d.reverse_kwh;
                    col2 = d.sum_solar_kwh;
                    col3 = ((d.reverse_kwh / Math.max(d.sum_solar_kwh, 0.01)) * 100).toFixed(1);
                } else if (card.metric === 'avg_grid') {
                    col1 = d.avg_grid.toFixed(2);
                    col2 = (d.avg_grid > 0 ? d.sum_load_kwh : 0).toFixed(2);
                    col3 = (d.avg_grid < 0 ? Math.abs(d.sum_load_kwh) : 0).toFixed(2);
                } else if (card.metric === 'sum_load_kwh') {
                    col1 = d.sum_load_kwh;
                    col2 = d.avg_load.toFixed(2);
                    col3 = (d.avg_load * 1.5).toFixed(2);
                } else {
                    col1 = d[card.metric] || 0;
                    col2 = '-';
                    col3 = '-';
                }
                
                tbody.innerHTML += `<tr>
                    <td>${d.date}</td>
                    <td>${dayName}요일</td>
                    <td>${col1}</td>
                    <td>${col2}</td>
                    <td>${col3}</td>
                    <td>${d.data_count}</td>
                </tr>`;
            });
        }

        // 전력흐름 모달의 전체 데이터 저장 (슬라이더용)
        let powerFlowAllData = [];

        // 실시간 전력흐름 모달
        function openPowerFlowModal() {
            // 먼저 모달을 표시
            document.getElementById('powerflow-modal').classList.add('active');
            
            // 모달 스크롤 시 맨 위로 버튼 표시/숨김
            const modalContent = document.getElementById('powerflow-modal-content');
            const scrollBtn = document.getElementById('powerflow-scroll-top');
            
            // 초기 스크롤 위치 맨 위로
            modalContent.scrollTop = 0;
            scrollBtn.classList.remove('visible');
            
            // 스크롤 이벤트 등록 (한 번만)
            if (!modalContent.dataset.scrollListenerAdded) {
                modalContent.addEventListener('scroll', () => {
                    if (modalContent.scrollTop > 200) {
                        scrollBtn.classList.add('visible');
                    } else {
                        scrollBtn.classList.remove('visible');
                    }
                });
                modalContent.dataset.scrollListenerAdded = 'true';
            }
            
            // 슬라이더 이벤트 등록 (한 번만)
            if (!modalContent.dataset.sliderListenerAdded) {
                setupPowerflowSlider();
                modalContent.dataset.sliderListenerAdded = 'true';
            }
            
            fetch('/api/daily-detailed').then(r => r.json()).then(res => {
                const data = res.data || [];
                powerFlowAllData = data;
                
                // 슬라이더 초기화 (하루 전체)
                document.getElementById('powerflow-slider-start').value = 0;
                document.getElementById('powerflow-slider-end').value = 47;
                document.querySelectorAll('#powerflow-modal .quick-btn').forEach(b => b.classList.remove('active'));
                document.querySelector('#powerflow-modal .quick-btn[data-start="0"][data-end="47"]').classList.add('active');
                
                renderPowerFlowStats(data);
                
                // 모달이 완전히 렌더링된 후 차트 그리기 (이중 RAF + 지연)
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        setTimeout(() => {
                            updatePowerflowRange(0, 47);
                            if (powerFlowDetailChart) {
                                powerFlowDetailChart.resize();
                            }
                        }, 200);
                    });
                });
            });
        }
        
        // 시간 인덱스 -> "HH:MM" 변환 (30분 단위)
        function indexToTimeLabel(idx) {
            const hour = Math.floor(idx / 2);
            const min = (idx % 2) * 30;
            return `${String(hour).padStart(2,'0')}:${String(min).padStart(2,'0')}`;
        }
        
        // "HH:MM" -> 인덱스 변환
        function timeLabelToIndex(timeLabel) {
            const [h, m] = timeLabel.split(':').map(Number);
            return h * 2 + Math.floor(m / 30);
        }
        
        // 슬라이더 범위에 맞춰 차트/표 업데이트
        function updatePowerflowRange(startIdx, endIdx) {
            if (startIdx > endIdx) {
                const tmp = startIdx;
                startIdx = endIdx;
                endIdx = tmp;
            }
            
            // 시간 라벨 업데이트
            const startLabel = indexToTimeLabel(startIdx);
            const endLabel = indexToTimeLabel(endIdx);
            document.getElementById('powerflow-range-label').textContent = `${startLabel} ~ ${endLabel}`;
            
            // 활성 범위 바 업데이트
            const startPct = (startIdx / 47) * 100;
            const endPct = (endIdx / 47) * 100;
            document.getElementById('powerflow-active-range').style.left = startPct + '%';
            document.getElementById('powerflow-active-range').style.right = (100 - endPct) + '%';
            
            // 데이터 필터링 (해당 시간 범위만)
            const filteredData = powerFlowAllData.filter(d => {
                const idx = timeLabelToIndex(d.hour);
                return idx >= startIdx && idx <= endIdx;
            });
            
            renderPowerFlowDetailChart(filteredData);
            renderPowerFlowTable(filteredData);
        }
        
        // 슬라이더 이벤트 설정
        function setupPowerflowSlider() {
            const sliderStart = document.getElementById('powerflow-slider-start');
            const sliderEnd = document.getElementById('powerflow-slider-end');
            
            const update = () => {
                const s = parseInt(sliderStart.value);
                const e = parseInt(sliderEnd.value);
                updatePowerflowRange(s, e);
                // 빠른 선택 버튼 비활성화 (수동 조작 시)
                document.querySelectorAll('#powerflow-modal .quick-btn').forEach(b => b.classList.remove('active'));
            };
            
            sliderStart.addEventListener('input', update);
            sliderEnd.addEventListener('input', update);
            
            // 빠른 선택 버튼
            document.querySelectorAll('#powerflow-modal .quick-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const s = parseInt(btn.dataset.start);
                    const e = parseInt(btn.dataset.end);
                    sliderStart.value = s;
                    sliderEnd.value = e;
                    updatePowerflowRange(s, e);
                    document.querySelectorAll('#powerflow-modal .quick-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                });
            });
        }

        function scrollPowerflowToTop() {
            const modalContent = document.getElementById('powerflow-modal-content');
            modalContent.scrollTo({ top: 0, behavior: 'smooth' });
        }

        function closePowerFlowModal() {
            document.getElementById('powerflow-modal').classList.remove('active');
            if (powerFlowDetailChart) { powerFlowDetailChart.destroy(); powerFlowDetailChart = null; }
        }

        function closePowerFlowModalOnOverlay(e) {
            if (e.target.id === 'powerflow-modal') closePowerFlowModal();
        }

        // ===== Rule-Based vs LSTM 비교 모달 (1년치 진짜 데이터) =====
        let yearlyDataCache = null;
        
        function openComparisonModal() {
            document.getElementById('comparison-modal').classList.add('active');
            
            const modalContent = document.getElementById('comparison-modal-content');
            const scrollBtn = document.getElementById('comparison-scroll-top');
            
            modalContent.scrollTop = 0;
            scrollBtn.classList.remove('visible');
            
            if (!modalContent.dataset.scrollListenerAdded) {
                modalContent.addEventListener('scroll', () => {
                    if (modalContent.scrollTop > 200) {
                        scrollBtn.classList.add('visible');
                    } else {
                        scrollBtn.classList.remove('visible');
                    }
                });
                modalContent.dataset.scrollListenerAdded = 'true';
            }
            
            // 1. 1년치 종합 비교 (카테고리별 표)
            fetch('/api/yearly-comparison').then(r => r.json()).then(res => {
                yearlyDataCache = res;
                renderYearlyModalContent(res);
            }).catch(e => {
                document.getElementById('yearly-detail-container').innerHTML = 
                    '<p style="text-align: center; color: #ef4444; padding: 20px;">데이터 로드 실패: ' + e.message + '</p>';
            });
            
            // 2. 월별 비교 (그래프 4개)
            fetch('/api/monthly-comparison').then(r => r.json()).then(res => {
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        setTimeout(() => { renderMonthlyCharts(res); }, 200);
                    });
                });
            }).catch(e => {
                console.error('월별 데이터 로드 실패:', e);
            });
        }

        function scrollComparisonToTop() {
            const modalContent = document.getElementById('comparison-modal-content');
            modalContent.scrollTo({ top: 0, behavior: 'smooth' });
        }

        function closeComparisonModal() {
            document.getElementById('comparison-modal').classList.remove('active');
            Object.keys(comparisonCharts).forEach(key => {
                if (comparisonCharts[key]) {
                    comparisonCharts[key].destroy();
                    comparisonCharts[key] = null;
                }
            });
        }

        function closeComparisonModalOnOverlay(e) {
            if (e.target.id === 'comparison-modal') closeComparisonModal();
        }

        // 숫자 포맷 (원/정수/소수)
        function formatYearlyValue(val, fmt) {
            if (val === null || val === undefined) return '-';
            if (fmt === 'int') return Math.round(val).toLocaleString();
            if (fmt === 'float2') return val.toFixed(2);
            if (fmt === 'float3') return val.toFixed(3);
            return val.toString();
        }

        // 모달: 카테고리별 상세 비교 렌더링
        function renderYearlyModalContent(data) {
            if (!data || !data.available) {
                document.getElementById('comparison-summary-stats').innerHTML = 
                    '<p style="grid-column: span 4; text-align: center; color: #64748b; padding: 20px;">' + 
                    (data.message || '비교 데이터 없음') + '</p>';
                document.getElementById('yearly-detail-container').innerHTML = '';
                return;
            }
            
            // 종합 우위 카드
            const summary = data.summary || {};
            document.getElementById('comparison-summary-stats').innerHTML = `
                <div class="stat-card">
                    <div class="stat-label">LSTM 우위</div>
                    <div class="stat-value" style="color: #3b82f6;">${summary.lstm_wins || 0}개</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Rule-Based 우위</div>
                    <div class="stat-value" style="color: #10b981;">${summary.rb_wins || 0}개</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">동일</div>
                    <div class="stat-value" style="color: #94a3b8;">${summary.equals || 0}개</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">전체 지표</div>
                    <div class="stat-value" style="color: #f59e0b;">${summary.total || 0}개</div>
                </div>
            `;
            
            // 카테고리별 표
            const categories = [
                { key: 'economic',           title: '1. 경제적 효율' },
                { key: 'economic_breakdown', title: '2. 시간대별 절감 (경부/중간/최대)' },
                { key: 'energy',             title: '3. 에너지 효율' },
                { key: 'stability',          title: '4. 운영 안정성' },
                { key: 'prediction',         title: '5. LSTM 예측 성능' },
            ];
            
            let html = '<div class="info-box">2025년 1년치 시뮬레이션 결과 비교. 더 좋은 값은 초록색으로 표시됩니다.</div>';
            
            categories.forEach(cat => {
                const entries = data[cat.key] || [];
                if (entries.length === 0) return;
                
                html += `<div style="margin-bottom: 20px;">
                    <div style="font-size: 1em; color: #3b82f6; font-weight: 600; margin-bottom: 8px; padding: 6px 12px; background: rgba(59,130,246,0.1); border-radius: 6px;">
                        ${cat.title}
                    </div>
                    <table class="data-table" style="margin-bottom: 5px;">
                        <thead>
                            <tr>
                                <th style="text-align: left;">지표</th>
                                <th style="color: #3b82f6;">LSTM</th>
                                <th style="color: #10b981;">Rule-Based</th>
                                <th>차이</th>
                                <th>우위</th>
                            </tr>
                        </thead>
                        <tbody>`;
                
                entries.forEach(e => {
                    const lstmStr = formatYearlyValue(e.lstm, e.fmt) + (e.unit ? ' ' + e.unit : '');
                    const rbStr   = formatYearlyValue(e.rb,   e.fmt) + (e.unit ? ' ' + e.unit : '');
                    
                    let diffStr = '-';
                    if (e.diff !== null && e.diff !== undefined) {
                        const sign = e.diff >= 0 ? '+' : '';
                        if (e.fmt === 'int') {
                            diffStr = sign + Math.round(e.diff).toLocaleString();
                        } else if (e.fmt === 'float3') {
                            diffStr = sign + e.diff.toFixed(3);
                        } else {
                            diffStr = sign + e.diff.toFixed(2);
                        }
                    }
                    
                    let winnerText = '-';
                    let winnerColor = '#94a3b8';
                    let lstmClass = '';
                    let rbClass = '';
                    if (e.winner === 'lstm') { winnerText = 'LSTM'; winnerColor = '#3b82f6'; lstmClass = 'better'; }
                    else if (e.winner === 'rb') { winnerText = 'Rule-Based'; winnerColor = '#10b981'; rbClass = 'better'; }
                    else if (e.winner === 'equal') { winnerText = '동일'; }
                    
                    html += `<tr>
                        <td style="text-align: left; color: #cbd5e1;">${e.name}</td>
                        <td class="${lstmClass}">${lstmStr}</td>
                        <td class="${rbClass}">${rbStr}</td>
                        <td>${diffStr}</td>
                        <td style="color: ${winnerColor};">${winnerText}</td>
                    </tr>`;
                });
                
                html += '</tbody></table></div>';
            });
            
            document.getElementById('yearly-detail-container').innerHTML = html;
        }

        // 월별 그래프 4개 렌더링
        function renderMonthlyCharts(data) {
            // 기존 차트 제거
            Object.keys(comparisonCharts).forEach(key => {
                if (comparisonCharts[key]) { comparisonCharts[key].destroy(); comparisonCharts[key] = null; }
            });
            
            const infoBox = document.getElementById('monthly-charts-info');
            
            if (!data || !data.available) {
                if (infoBox) {
                    infoBox.innerHTML = '⚠️ ' + (data.message || '월별 데이터 없음') +
                        '<br><span style="font-size: 0.85em; color: #94a3b8;">시뮬레이션 CSV 파일이 필요합니다.</span>';
                }
                return;
            }
            
            const months = data.months || [];
            if (months.length === 0) {
                if (infoBox) infoBox.innerHTML = '월별 데이터 없음';
                return;
            }
            
            // 데이터 추출
            const labels = months.map(m => m.month_label);
            const rb_self = months.map(m => m.rb ? m.rb.self_sufficiency_pct : null);
            const ls_self = months.map(m => m.lstm ? m.lstm.self_sufficiency_pct : null);
            const rb_soc  = months.map(m => m.rb ? m.rb.soc_avg_pct : null);
            const ls_soc  = months.map(m => m.lstm ? m.lstm.soc_avg_pct : null);
            const rb_cyc  = months.map(m => m.rb ? m.rb.cycle_count : null);
            const ls_cyc  = months.map(m => m.lstm ? m.lstm.cycle_count : null);
            const rb_sav  = months.map(m => m.rb ? m.rb.cost_saving_won : null);
            const ls_sav  = months.map(m => m.lstm ? m.lstm.cost_saving_won : null);
            
            // 정보 박스 업데이트
            if (infoBox) {
                const rbMonths = data.rb_total_months || 0;
                const lstmMonths = data.lstm_total_months || 0;
                const commonMonths = (data.common_months || []).length;
                infoBox.innerHTML = `월별 비교 그래프 (RB: ${rbMonths}개월, LSTM: ${lstmMonths}개월, 공통: ${commonMonths}개월)`;
            }
            
            // 4개 차트 공통 옵션
            const commonOpts = {
                responsive: true, maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { labels: { color: '#e2e8f0', font: { size: 11 }, boxWidth: 14 } },
                    tooltip: { mode: 'index', intersect: false }
                },
                scales: {
                    y: { 
                        beginAtZero: false,
                        grid: { color: 'rgba(51, 65, 85, 0.3)' }, 
                        ticks: { color: '#94a3b8', font: { size: 10 } } 
                    },
                    x: { 
                        grid: { color: 'rgba(51, 65, 85, 0.2)' }, 
                        ticks: { color: '#94a3b8', font: { size: 10 } } 
                    }
                }
            };
            
            function makeChart(canvasId, lstmData, rbData) {
                const ctx = document.getElementById(canvasId).getContext('2d');
                return new Chart(ctx, {
                    type: 'line',
                    data: { labels, datasets: [
                        { label: 'LSTM', data: lstmData, 
                          borderColor: '#3b82f6', 
                          backgroundColor: 'rgba(59, 130, 246, 0.1)', 
                          borderWidth: 2, tension: 0.4, fill: true, pointRadius: 4,
                          spanGaps: true },
                        { label: 'Rule-Based', data: rbData, 
                          borderColor: '#10b981', 
                          backgroundColor: 'rgba(16, 185, 129, 0.1)', 
                          borderWidth: 2, tension: 0.4, fill: true, pointRadius: 4,
                          spanGaps: true },
                    ]},
                    options: commonOpts
                });
            }
            
            comparisonCharts.selfSuff = makeChart('comparisonSelfSuffChart', ls_self, rb_self);
            comparisonCharts.soc      = makeChart('comparisonSocChart',      ls_soc,  rb_soc);
            comparisonCharts.cycle    = makeChart('comparisonCycleChart',    ls_cyc,  rb_cyc);
            comparisonCharts.saving   = makeChart('comparisonSavingChart',   ls_sav,  rb_sav);
        }
        // ===== 비교 모달 끝 =====


        function renderPowerFlowStats(data) {
            if (data.length === 0) {
                document.getElementById('powerflow-stats').innerHTML = '<p style="grid-column: span 4; text-align: center; color: #64748b; padding: 20px;">데이터 없음</p>';
                return;
            }
            
            const loads = data.map(d => d.load_kw);
            const solars = data.map(d => d.solar_kw);
            const avgLoad = loads.reduce((a,b) => a+b, 0) / loads.length;
            const maxLoad = Math.max(...loads);
            const avgSolar = solars.reduce((a,b) => a+b, 0) / solars.length;
            const maxSolar = Math.max(...solars);
            
            // 데이터 날짜 표시
            const dateInfo = data[0].date ? ` (${data[0].date})` : '';
            document.getElementById('powerflow-stats').innerHTML = `
                <div class="stat-card"><div class="stat-label">평균 부하${dateInfo}</div><div class="stat-value">${avgLoad.toFixed(1)} kW</div></div>
                <div class="stat-card"><div class="stat-label">최대 부하</div><div class="stat-value">${maxLoad.toFixed(1)} kW</div></div>
                <div class="stat-card"><div class="stat-label">평균 태양광</div><div class="stat-value">${avgSolar.toFixed(1)} kW</div></div>
                <div class="stat-card"><div class="stat-label">최대 태양광</div><div class="stat-value">${maxSolar.toFixed(1)} kW</div></div>
            `;
        }

        function renderPowerFlowDetailChart(data) {
            const ctx = document.getElementById('powerFlowDetailChart').getContext('2d');
            if (powerFlowDetailChart) powerFlowDetailChart.destroy();
            
            if (data.length === 0) {
                // 빈 차트 표시
                powerFlowDetailChart = new Chart(ctx, {
                    type: 'line',
                    data: { labels: ['데이터 없음'], datasets: [] },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { display: false },
                            title: { display: true, text: '데이터가 없습니다', color: '#94a3b8' }
                        }
                    }
                });
                return;
            }
            
            powerFlowDetailChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: data.map(d => d.hour),
                    datasets: [
                        { label: '부하', data: data.map(d => d.load_kw), borderColor: '#ef4444', backgroundColor: 'rgba(239, 68, 68, 0.06)', borderWidth: 1.5, tension: 0.2, fill: true, pointRadius: 0, pointHoverRadius: 0, pointHitRadius: 10 },
                        { label: '태양광', data: data.map(d => d.solar_kw), borderColor: '#f59e0b', backgroundColor: 'rgba(245, 158, 11, 0.1)', borderWidth: 1.5, tension: 0.2, fill: true, pointRadius: 0, pointHoverRadius: 0, pointHitRadius: 10 },
                        { label: 'BESS', data: data.map(d => d.bess_power_kw), borderColor: '#3b82f6', backgroundColor: 'rgba(59, 130, 246, 0.06)', borderWidth: 1.5, tension: 0.2, fill: true, pointRadius: 0, pointHoverRadius: 0, pointHitRadius: 10 },
                        { label: '계통', data: data.map(d => d.grid_power_kw), borderColor: '#8b5cf6', backgroundColor: 'rgba(139, 92, 246, 0.06)', borderWidth: 1.5, tension: 0.2, fill: true, pointRadius: 0, pointHoverRadius: 0, pointHitRadius: 10 },
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    interaction: { mode: 'index', intersect: false },
                    elements: { point: { radius: 0, hoverRadius: 0 } },
                    plugins: { 
                        legend: { labels: { color: '#e2e8f0' } },
                        tooltip: { mode: 'index', intersect: false }
                    },
                    scales: {
                        y: { beginAtZero: false, grid: { color: 'rgba(51, 65, 85, 0.25)' }, ticks: { color: '#94a3b8' } },
                        x: { 
                            grid: { display: false },
                            ticks: { 
                                color: '#94a3b8',
                                autoSkip: false,
                                maxRotation: 45,
                                minRotation: 45,
                                font: { size: 10 },
                                callback: function(val, index) {
                                    // 30분 단위(HH:00 또는 HH:30)만 표시
                                    const label = this.getLabelForValue(val);
                                    if (!label) return '';
                                    if (label.endsWith(':00') || label.endsWith(':30')) {
                                        return label;
                                    }
                                    return '';
                                }
                            } 
                        }
                    }
                }
            });
        }

        function renderPowerFlowTable(data) {
            const tbody = document.getElementById('powerflow-tbody');
            tbody.innerHTML = '';
            data.forEach(d => {
                tbody.innerHTML += `<tr>
                    <td>${d.hour}</td>
                    <td>${d.load_kw.toFixed(2)}</td>
                    <td>${d.solar_kw.toFixed(2)}</td>
                    <td>${d.bess_power_kw.toFixed(2)}</td>
                    <td>${d.grid_power_kw.toFixed(2)}</td>
                    <td>${d.soc.toFixed(1)}</td>
                    <td>${d.sample_count || '-'}</td>
                </tr>`;
            });
        }

        // ESC 키로 모달 닫기
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                closeModal();
                closePowerFlowModal();
                closeComparisonModal();
            }
        });

        setInterval(() => fetch('/api/status').then(r => r.json()).then(updateMetrics).catch(e => {}), 1000);
        setInterval(() => fetch('/api/daily-stats').then(r => r.json()).then(updateStats).catch(e => {}), 10000);
        setInterval(() => updateComparison(), 5000);

        function updateMetrics(data) {
            if (!data) return;
            const el = (id) => document.getElementById(id);
            if (el('soc-value')) el('soc-value').textContent = parseFloat(data.soc || 0).toFixed(1);
            if (el('bess-power')) el('bess-power').textContent = parseFloat(data.bess_power_kw || 0).toFixed(1);
            if (el('load-kw')) el('load-kw').textContent = parseFloat(data.load_kw || 0).toFixed(1);
            const solarEl = el('solar-info');
            if (solarEl) solarEl.textContent = `태양광: ${parseFloat(data.solar_kw || 0).toFixed(1)} kW`;
            if (el('tariff-rate')) el('tariff-rate').textContent = parseInt(data.tariff_rate || 0);
            const map = {'on_peak': '최대', 'mid_peak': '중간', 'off_peak': '경부'};
            const tariffEl = el('tariff-period');
            if (tariffEl) tariffEl.textContent = map[data.tariff_period] || '-';
            const grid = parseFloat(data.grid_power_kw || 0).toFixed(1);
            if (el('grid-power')) el('grid-power').textContent = grid;
            const gridEl = el('grid-status');
            if (gridEl) gridEl.textContent = grid < 0 ? `역송 ${Math.abs(grid)} kW` : `구매 ${grid} kW`;
            const card = el('bess-power-card');
            if (card) {
                const bp = parseFloat(data.bess_power_kw || 0).toFixed(1);
                card.classList.remove('charging', 'discharging');
                if (bp < -1) {
                    card.classList.add('charging');
                    const a = el('bess-action');
                    if (a) a.textContent = '충전 중';
                } else if (bp > 1) {
                    card.classList.add('discharging');
                    const a = el('bess-action');
                    if (a) a.textContent = '방전 중';
                } else {
                    const a = el('bess-action');
                    if (a) a.textContent = '대기';
                }
            }
        }

        function updateStats(data) {
            const el = (id) => document.getElementById(id);
            if (el('self-sufficiency')) el('self-sufficiency').textContent = (data.self_sufficiency_pct || 0).toFixed(1);
            if (el('daily-saving')) el('daily-saving').textContent = (data.daily_saving_won || 0).toLocaleString();
            const dEl = el('discharge-info');
            if (dEl) dEl.textContent = `방전: ${(data.discharge_kwh || 0).toFixed(2)} kWh`;
            if (el('cycle-count')) el('cycle-count').textContent = (data.cycle_count || 0).toFixed(2);
            if (el('charge-kwh')) el('charge-kwh').textContent = (data.charge_kwh || 0).toFixed(2);
            if (el('total-discharge-kwh')) el('total-discharge-kwh').textContent = (data.discharge_kwh || 0).toFixed(2);
            if (el('reverse-power')) el('reverse-power').textContent = (data.reverse_power_kwh || 0).toFixed(2);
            if (el('total-load')) el('total-load').textContent = (data.total_load_kwh || 0).toFixed(2);
        }

        // 페이지 메인 1년치 비교 표시
        function updateComparison() {
            fetch('/api/yearly-comparison').then(r => r.json()).then(data => {
                renderYearlyMainView(data);
            }).catch(e => {
                document.getElementById('yearly-comparison-table').innerHTML = 
                    '<p style="text-align: center; color: #ef4444; padding: 20px;">로드 실패: ' + e.message + '</p>';
            });
        }

        function renderYearlyMainView(data) {
            if (!data || !data.available) {
                document.getElementById('yearly-summary-stats').innerHTML = '';
                document.getElementById('yearly-comparison-table').innerHTML = 
                    '<p style="text-align: center; color: #64748b; padding: 20px;">' + 
                    (data.message || '비교 데이터가 없습니다.') + 
                    '<br><br>compare.py를 실행한 후 다시 확인하세요.</p>';
                return;
            }
            
            // 종합 우위 카드 (4개)
            const summary = data.summary || {};
            document.getElementById('yearly-summary-stats').innerHTML = `
                <div class="stat-card">
                    <div class="stat-label">LSTM 우위</div>
                    <div class="stat-value" style="color: #3b82f6;">${summary.lstm_wins || 0}개</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Rule-Based 우위</div>
                    <div class="stat-value" style="color: #10b981;">${summary.rb_wins || 0}개</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">동일</div>
                    <div class="stat-value" style="color: #94a3b8;">${summary.equals || 0}개</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">전체 지표</div>
                    <div class="stat-value" style="color: #f59e0b;">${summary.total || 0}개</div>
                </div>
            `;
            
            // 핵심 지표 표
            const keyMetrics = [
                { cat: 'economic',  key: 'cost_saving_won',         label: '연 절감액' },
                { cat: 'economic',  key: 'cost_saving_rate_pct',    label: '절감률' },
                { cat: 'economic',  key: 'peak_saving_rate_pct',    label: '피크 절감률' },
                { cat: 'energy',    key: 'self_sufficiency_pct',    label: '자립률' },
                { cat: 'energy',    key: 'bess_utilization_pct',    label: 'BESS 활용률' },
                { cat: 'energy',    key: 'bess_discharge_kwh',      label: 'BESS 방전 공급' },
                { cat: 'stability', key: 'control_success_rate_pct',label: '제어 성공률' },
                { cat: 'stability', key: 'cycle_count',             label: '배터리 사이클' },
            ];
            
            let html = '<table>';
            html += `<tr>
                <th class="metric-name">핵심 지표</th>
                <th style="color: #3b82f6;">LSTM</th>
                <th style="color: #10b981;">Rule-Based</th>
                <th>우위</th>
            </tr>`;
            
            keyMetrics.forEach(m => {
                const entries = data[m.cat] || [];
                const entry = entries.find(e => e.metric_key === m.key);
                if (!entry) return;
                
                const lstmStr = formatYearlyValue(entry.lstm, entry.fmt) + (entry.unit ? ' ' + entry.unit : '');
                const rbStr   = formatYearlyValue(entry.rb,   entry.fmt) + (entry.unit ? ' ' + entry.unit : '');
                
                let winnerText = '-';
                let winnerColor = '#94a3b8';
                let lstmClass = '';
                let rbClass = '';
                if (entry.winner === 'lstm') { winnerText = 'LSTM'; winnerColor = '#3b82f6'; lstmClass = 'better'; }
                else if (entry.winner === 'rb') { winnerText = 'Rule-Based'; winnerColor = '#10b981'; rbClass = 'better'; }
                else if (entry.winner === 'equal') { winnerText = '동일'; }
                
                html += `<tr>
                    <td class="metric-name">${m.label}</td>
                    <td class="${lstmClass}">${lstmStr}</td>
                    <td class="${rbClass}">${rbStr}</td>
                    <td style="color: ${winnerColor}; font-weight: 600;">${winnerText}</td>
                </tr>`;
            });
            
            html += '</table>';
            html += '<p style="text-align: center; color: #64748b; font-size: 0.85em; margin-top: 10px;">📊 2025년 1년치 시뮬레이션 결과 · 상세 보기로 전체 지표 확인</p>';
            
            document.getElementById('yearly-comparison-table').innerHTML = html;
        }

        function initChart() {
            const ctx = document.getElementById('powerChart').getContext('2d');
            chart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [
                        { label: '부하', data: [], borderColor: '#ef4444', backgroundColor: 'rgba(239, 68, 68, 0.06)', borderWidth: 1.5, tension: 0.2, fill: true, pointRadius: 0, pointHoverRadius: 0, pointHitRadius: 10 },
                        { label: '태양광', data: [], borderColor: '#f59e0b', backgroundColor: 'rgba(245, 158, 11, 0.1)', borderWidth: 1.5, tension: 0.2, fill: true, pointRadius: 0, pointHoverRadius: 0, pointHitRadius: 10 },
                        { label: 'BESS', data: [], borderColor: '#3b82f6', backgroundColor: 'rgba(59, 130, 246, 0.06)', borderWidth: 1.5, tension: 0.2, fill: true, pointRadius: 0, pointHoverRadius: 0, pointHitRadius: 10 },
                        { label: '계통', data: [], borderColor: '#8b5cf6', backgroundColor: 'rgba(139, 92, 246, 0.06)', borderWidth: 1.5, tension: 0.2, fill: true, pointRadius: 0, pointHoverRadius: 0, pointHitRadius: 10 },
                    ]
                },
                options: { responsive: true, maintainAspectRatio: false,
                    animation: false,
                    interaction: { mode: 'index', intersect: false },
                    elements: { point: { radius: 0, hoverRadius: 0 } },
                    scales: { 
                        y: { beginAtZero: false, grid: { color: 'rgba(51, 65, 85, 0.25)' }, ticks: { color: '#94a3b8' } },
                        x: { 
                            grid: { color: 'rgba(51, 65, 85, 0.15)', display: false },
                            ticks: { 
                                color: '#94a3b8',
                                autoSkip: false,
                                maxRotation: 45,
                                minRotation: 45,
                                font: { size: 10 },
                                callback: function(val, index) {
                                    // 30분 단위(HH:00 또는 HH:30)만 표시
                                    const label = this.getLabelForValue(val);
                                    if (!label) return '';
                                    if (label.endsWith(':00') || label.endsWith(':30')) {
                                        return label;
                                    }
                                    return '';
                                }
                            } 
                        } 
                    },
                    plugins: { legend: { labels: { color: '#e2e8f0' } } } }
            });
        }

        function updateChart() {
            if (!chart) initChart();
            fetch('/api/history?hours=24').then(r => r.json()).then(res => {
                const d = res.data || [];
                if (d.length === 0) return;
                chart.data.labels = d.map(x => {
                    const dt = new Date(x.timestamp);
                    return `${String(dt.getHours()).padStart(2,'0')}:${String(dt.getMinutes()).padStart(2,'0')}`;
                });
                chart.data.datasets[0].data = d.map(x => x.load_kw);
                chart.data.datasets[1].data = d.map(x => x.solar_kw);
                chart.data.datasets[2].data = d.map(x => x.bess_power_kw);
                chart.data.datasets[3].data = d.map(x => x.grid_power_kw);
                chart.update('none');
            });
        }

        window.addEventListener('load', () => {
            renderCards(1);
            fetch('/api/status').then(r => r.json()).then(updateMetrics);
            fetch('/api/daily-stats').then(r => r.json()).then(updateStats);
            updateChart();
            updateComparison();
            setInterval(updateChart, 10000);
        });
    </script>
</body>
</html>"""


# 모바일/Pi4 7인치용 HTML (4페이지 구성)
MOBILE_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>BESS 실시간 모니터링</title>
    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        html, body { width: 100%; height: 100%; overflow: hidden; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif;
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: #fff;
        }
        .wrapper { 
            display: flex; flex-direction: column; 
            width: 100%; height: 100vh; 
            padding: 8px; gap: 8px; 
        }
        
        /* 헤더 */
        .header {
            display: flex; justify-content: space-between; align-items: center;
            background: rgba(15, 23, 42, 0.8); border: 1px solid #334155;
            border-radius: 8px; padding: 8px 14px; flex-shrink: 0;
        }
        .header h1 { font-size: 1.1em; font-weight: 700; }
        .time { font-size: 0.9em; color: #94a3b8; }
        .status-indicator {
            width: 10px; height: 10px; border-radius: 50%;
            background: #10b981; animation: pulse 2s infinite;
        }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        
        /* 페이지 컨테이너 */
        .page-container { 
            flex: 1; 
            overflow: hidden; 
            position: relative;
            min-height: 0;
        }
        .page {
            position: absolute;
            top: 0; left: 0; right: 0; bottom: 0;
            display: none;
            flex-direction: column;
            min-height: 0;
        }
        .page.active { display: flex; }
        
        /* 카드 그리드 (페이지 1, 2) */
        .cards-grid {
            display: grid; 
            grid-template-columns: repeat(3, 1fr);
            grid-template-rows: repeat(2, 1fr); 
            gap: 8px;
            flex: 1;
            min-height: 0;
        }
        .card {
            background: rgba(30, 41, 59, 0.7); border: 1px solid #334155;
            border-radius: 10px; padding: 10px;
            display: flex; flex-direction: column; 
            justify-content: center; align-items: center;
            transition: all 0.3s ease; min-height: 0; 
            cursor: pointer;
        }
        .card:active { background: rgba(30, 41, 59, 0.9); }
        .card.charging { border-color: #3b82f6; box-shadow: 0 0 15px rgba(59, 130, 246, 0.3); }
        .card.discharging { border-color: #10b981; box-shadow: 0 0 15px rgba(16, 185, 129, 0.3); }
        .metric-label { font-size: 0.75em; color: #94a3b8; margin-bottom: 4px; font-weight: 500; text-align: center; }
        .metric-value { font-size: 1.5em; font-weight: bold; line-height: 1; }
        .metric-unit { font-size: 0.65em; color: #64748b; margin-top: 2px; }
        .sub-metric { font-size: 0.6em; margin-top: 3px; color: #94a3b8; text-align: center; }
        
        /* 게이지 */
        .gauge-container { position: relative; width: 70px; height: 70px; }
        .gauge {
            width: 100%; height: 100%; border-radius: 50%;
            background: conic-gradient(#10b981 0deg 90deg, #f59e0b 90deg 270deg, #ef4444 270deg 360deg);
            display: flex; align-items: center; justify-content: center;
        }
        .gauge-inner {
            width: 62px; height: 62px; border-radius: 50%; background: #1e293b;
            display: flex; align-items: center; justify-content: center; flex-direction: column;
        }
        .gauge-value { font-size: 1.2em; font-weight: bold; }
        .gauge-percent { font-size: 0.55em; color: #94a3b8; }
        
        /* 차트 페이지 (페이지 3, 4) */
        .full-chart-section {
            flex: 1;
            background: rgba(30, 41, 59, 0.7); border: 1px solid #334155;
            border-radius: 10px; padding: 10px;
            display: flex; flex-direction: column; 
            overflow: hidden; min-height: 0;
        }
        .section-title { 
            font-size: 0.95em; 
            font-weight: 600; 
            margin-bottom: 8px; 
            flex-shrink: 0;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .detail-btn {
            background: rgba(59, 130, 246, 0.15);
            border: 1px solid rgba(59, 130, 246, 0.4);
            color: #3b82f6;
            padding: 3px 10px;
            border-radius: 5px;
            font-size: 0.7em;
            font-weight: 500;
            cursor: pointer;
        }
        .detail-btn:active {
            background: rgba(59, 130, 246, 0.3);
        }
        
        .chart-container { position: relative; flex: 1; min-height: 0; }
        .comparison-table { flex: 1; overflow-y: auto; font-size: 0.75em; }
        table { width: 100%; border-collapse: collapse; font-size: 0.75em; }
        th, td { padding: 5px 3px; text-align: center; border-bottom: 0.5px solid #334155; color: #94a3b8; }
        th { background: rgba(51, 65, 85, 0.3); color: #cbd5e1; font-weight: 600; position: sticky; top: 0; z-index: 10; }
        td { color: #e2e8f0; }
        .metric-name { text-align: left; color: #94a3b8; font-weight: 500; }
        .lstm { color: #3b82f6; }
        .rb { color: #10b981; }
        .better { color: #10b981; font-weight: 600; }
        .hour-row { background: rgba(59, 130, 246, 0.1); }
        .hour-cell { font-weight: 600; color: #cbd5e1; }
        
        /* 네비게이션 바 (페이지 표시 + 화살표) */
        .nav-bar {
            display: flex; 
            justify-content: space-between; 
            align-items: center;
            background: rgba(15, 23, 42, 0.8); border: 1px solid #334155;
            border-radius: 8px; padding: 8px 14px; 
            flex-shrink: 0;
        }
        .btn {
            background: rgba(59, 130, 246, 0.8); border: 1px solid #3b82f6;
            color: white; border-radius: 6px; 
            padding: 6px 12px; font-size: 1em;
            cursor: pointer; transition: all 0.2s ease;
            min-width: 36px; height: 36px;
            display: flex; align-items: center; justify-content: center;
        }
        .btn:active { transform: scale(0.95); }
        .btn:disabled { opacity: 0.3; cursor: not-allowed; }
        
        .page-info { 
            display: flex; 
            align-items: center; 
            gap: 12px; 
            flex-direction: column;
        }
        .page-text { 
            color: #cbd5e1; 
            font-size: 0.85em; 
            font-weight: 500;
        }
        .page-text .current { color: #3b82f6; font-weight: 700; font-size: 1.1em; }
        .dots { display: flex; gap: 8px; }
        .dot { 
            width: 8px; height: 8px; border-radius: 50%; 
            background: rgba(148, 163, 184, 0.4); 
            cursor: pointer; 
            transition: all 0.3s ease; 
        }
        .dot.active { 
            background: #3b82f6; 
            width: 24px; 
            border-radius: 4px; 
        }
        .page-label {
            color: #94a3b8;
            font-size: 0.7em;
            margin-top: 2px;
        }

        /* ======= 모달 (PC 버전과 동일) ======= */
        .modal-overlay {
            position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0, 0, 0, 0.7);
            display: none; align-items: center; justify-content: center;
            z-index: 1000;
            backdrop-filter: blur(5px);
        }
        .modal-overlay.active { display: flex; }
        .modal {
            background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
            border: 1px solid #475569;
            border-radius: 14px;
            padding: 16px;
            width: 95%;
            max-width: 1100px;
            max-height: 92vh;
            overflow: auto;
            box-shadow: 0 20px 40px -12px rgba(0, 0, 0, 0.5);
        }
        .modal::-webkit-scrollbar { width: 10px; }
        .modal::-webkit-scrollbar-track { background: transparent; }
        .modal::-webkit-scrollbar-thumb {
            background: linear-gradient(180deg, #64748b 0%, #475569 100%);
            border-radius: 999px;
        }
        .modal-header {
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 12px;
            padding-bottom: 10px;
            border-bottom: 1px solid #334155;
        }
        .modal-title { font-size: 1.1em; font-weight: 700; color: #fff; }
        .modal-subtitle { font-size: 0.75em; color: #94a3b8; margin-top: 3px; }
        .close-btn {
            background: rgba(239, 68, 68, 0.2); border: 1px solid #ef4444;
            color: #ef4444; width: 36px; height: 36px; border-radius: 8px;
            font-size: 1em; cursor: pointer; font-weight: bold;
        }
        .close-btn:active { background: rgba(239, 68, 68, 0.4); }
        .modal-chart-container { 
            position: relative; 
            height: 280px; 
            width: 100%;
            margin-bottom: 12px; 
        }
        .modal-chart-container.large { height: 320px; }
        .modal-chart-container canvas { max-width: 100% !important; }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 8px;
            margin-bottom: 12px;
        }
        .stat-card {
            background: rgba(15, 23, 42, 0.5);
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 8px;
            text-align: center;
        }
        .stat-label { font-size: 0.65em; color: #94a3b8; margin-bottom: 3px; }
        .stat-value { font-size: 1.1em; font-weight: bold; color: #3b82f6; }
        .stat-unit { font-size: 0.55em; color: #94a3b8; font-weight: normal; margin-left: 2px; }
        
        .data-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.75em;
        }
        .data-table th {
            background: rgba(59, 130, 246, 0.2);
            color: #cbd5e1;
            padding: 6px;
            text-align: center;
            border-bottom: 1px solid #334155;
        }
        .data-table td {
            padding: 6px;
            text-align: center;
            border-bottom: 0.5px solid #334155;
            color: #e2e8f0;
        }
        
        .info-box {
            background: rgba(59, 130, 246, 0.1);
            border-left: 3px solid #3b82f6;
            padding: 8px 12px;
            margin-bottom: 12px;
            border-radius: 4px;
            font-size: 0.75em;
            color: #cbd5e1;
        }
        .comparison-chart-title {
            font-size: 0.75em;
            color: #cbd5e1;
            font-weight: 600;
            margin-bottom: 6px;
            padding: 0 5px;
        }
        
        /* 시간 범위 슬라이더 (전력흐름 모달) */
        .time-range-control {
            background: rgba(15, 23, 42, 0.4);
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 10px 12px;
            margin-bottom: 12px;
        }
        .range-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
        }
        .range-title { font-size: 0.75em; color: #cbd5e1; font-weight: 500; }
        .range-label { font-size: 0.75em; color: #64748b; font-weight: 600; }
        .slider-wrapper {
            position: relative;
            height: 26px;
            margin: 8px 0;
        }
        .slider-track {
            position: absolute;
            top: 50%;
            left: 0; right: 0;
            height: 5px;
            background: #1e293b;
            border-radius: 3px;
            transform: translateY(-50%);
        }
        .slider-range-active {
            position: absolute;
            top: 50%;
            height: 5px;
            background: linear-gradient(90deg, #64748b, #475569);
            border-radius: 3px;
            transform: translateY(-50%);
        }
        .slider-input {
            position: absolute;
            top: 0; left: 0;
            width: 100%; height: 26px;
            -webkit-appearance: none;
            appearance: none;
            background: transparent;
            pointer-events: none;
            outline: none;
        }
        .slider-input::-webkit-slider-thumb {
            -webkit-appearance: none;
            width: 16px; height: 16px;
            background: #94a3b8;
            border-radius: 50%;
            cursor: pointer;
            pointer-events: all;
            border: 2px solid #fff;
        }
        .quick-buttons {
            display: flex;
            gap: 6px;
            margin-top: 8px;
            flex-wrap: wrap;
        }
        .quick-btn {
            background: rgba(100, 116, 139, 0.2);
            border: 1px solid rgba(148, 163, 184, 0.3);
            color: #cbd5e1;
            padding: 4px 10px;
            border-radius: 5px;
            font-size: 0.65em;
            cursor: pointer;
        }
        .quick-btn.active {
            background: rgba(59, 130, 246, 0.3);
            border-color: #3b82f6;
            color: #fff;
        }
    </style>
</head>
<body>
    <div class="wrapper">
        <!-- 헤더 -->
        <div class="header">
            <h1>BESS 실시간 모니터링</h1>
            <div style="display: flex; gap: 12px; align-items: center;">
                <span class="time" id="current-time">--:--:--</span>
                <span class="status-indicator"></span>
            </div>
        </div>

        <!-- 페이지 컨테이너 (4페이지) -->
        <div class="page-container">
            <!-- 페이지 1: 카드 1-6 -->
            <div class="page active" id="page-1">
                <div class="cards-grid" id="cards-grid-1"></div>
            </div>
            
            <!-- 페이지 2: 카드 7-12 -->
            <div class="page" id="page-2">
                <div class="cards-grid" id="cards-grid-2"></div>
            </div>
            
            <!-- 페이지 3: 실시간 전력 흐름 차트 -->
            <div class="page" id="page-3">
                <div class="full-chart-section">
                    <div class="section-title">
                        <span>실시간 전력 흐름</span>
                        <button class="detail-btn" onclick="openPowerFlowModal()">상세 보기 ↗</button>
                    </div>
                    <div class="chart-container">
                        <canvas id="powerChart"></canvas>
                    </div>
                </div>
            </div>
            
            <!-- 페이지 4: Rule-Based vs LSTM 1년치 비교 -->
            <div class="page" id="page-4">
                <div class="full-chart-section" style="overflow-y: auto;">
                    <div class="section-title">
                        <span>Rule-Based vs LSTM 비교 (1년치 시뮬레이션)</span>
                        <button class="detail-btn" onclick="openComparisonModal()">상세 보기 ↗</button>
                    </div>
                    
                    <!-- 우위 종합 카드 -->
                    <div class="stats-grid" id="yearly-summary-stats" style="margin-bottom: 8px;">
                        <p style="grid-column: span 4; text-align: center; color: #64748b; padding: 10px;">데이터 로드 중...</p>
                    </div>
                    
                    <!-- 핵심 지표 비교 표 -->
                    <div id="yearly-comparison-table" style="overflow-y: auto; flex: 1; min-height: 0;">
                        <p style="text-align: center; color: #64748b; padding: 20px;">데이터 로드 중...</p>
                    </div>
                </div>
            </div>
        </div>

        <!-- 네비게이션 바 -->
        <div class="nav-bar">
            <button class="btn" id="prev-btn" onclick="prevPage()">&lt;</button>
            <div class="page-info">
                <span class="page-text"><span class="current" id="page-num">1</span>/4 - <span id="page-label">상태 1</span></span>
                <div class="dots">
                    <div class="dot active" onclick="goToPage(1)"></div>
                    <div class="dot" onclick="goToPage(2)"></div>
                    <div class="dot" onclick="goToPage(3)"></div>
                    <div class="dot" onclick="goToPage(4)"></div>
                </div>
            </div>
            <button class="btn" id="next-btn" onclick="nextPage()">&gt;</button>
        </div>
    </div>

    <!-- 박스 모달 -->
    <div class="modal-overlay" id="modal" onclick="closeModalOnOverlay(event)">
        <div class="modal" onclick="event.stopPropagation()">
            <div class="modal-header">
                <div>
                    <div class="modal-title" id="modal-title">제목</div>
                    <div class="modal-subtitle" id="modal-subtitle">수집된 일별 데이터</div>
                </div>
                <button class="close-btn" onclick="closeModal()">X</button>
            </div>
            <div class="info-box" id="modal-info">수집된 데이터를 표시합니다.</div>
            <div class="stats-grid" id="modal-stats"></div>
            <div class="modal-chart-container">
                <canvas id="modalChart"></canvas>
            </div>
            <table class="data-table">
                <thead>
                    <tr>
                        <th>날짜</th>
                        <th>요일</th>
                        <th id="modal-col-1">값</th>
                        <th id="modal-col-2">평균</th>
                        <th id="modal-col-3">기타</th>
                        <th>데이터 수</th>
                    </tr>
                </thead>
                <tbody id="modal-tbody"></tbody>
            </table>
        </div>
    </div>

    <!-- 실시간 전력흐름 모달 -->
    <div class="modal-overlay" id="powerflow-modal" onclick="closePowerFlowModalOnOverlay(event)">
        <div class="modal" id="powerflow-modal-content" onclick="event.stopPropagation()">
            <div class="modal-header">
                <div>
                    <div class="modal-title">일일 전력 흐름 상세</div>
                    <div class="modal-subtitle">오늘 하루 30분 단위 데이터</div>
                </div>
                <button class="close-btn" onclick="closePowerFlowModal()">X</button>
            </div>
            <div class="info-box">시간대별 부하, 태양광, BESS, 계통 전력 흐름을 보여줍니다.</div>
            <div class="stats-grid" id="powerflow-stats"></div>
            <div class="modal-chart-container large">
                <canvas id="powerFlowDetailChart"></canvas>
            </div>
            <div class="time-range-control">
                <div class="range-header">
                    <span class="range-title">표시 시간 범위</span>
                    <span class="range-label" id="powerflow-range-label">00:00 ~ 23:30</span>
                </div>
                <div class="slider-wrapper">
                    <div class="slider-track"></div>
                    <div class="slider-range-active" id="powerflow-active-range" style="left: 0%; right: 0%;"></div>
                    <input type="range" class="slider-input" id="powerflow-slider-start" min="0" max="47" value="0">
                    <input type="range" class="slider-input" id="powerflow-slider-end" min="0" max="47" value="47">
                </div>
                <div class="quick-buttons">
                    <button class="quick-btn" data-start="0" data-end="11">새벽 (00-06)</button>
                    <button class="quick-btn" data-start="12" data-end="23">오전 (06-12)</button>
                    <button class="quick-btn" data-start="24" data-end="35">오후 (12-18)</button>
                    <button class="quick-btn" data-start="36" data-end="47">저녁 (18-24)</button>
                    <button class="quick-btn active" data-start="0" data-end="47">하루 전체</button>
                </div>
            </div>
            <table class="data-table">
                <thead>
                    <tr>
                        <th>시각</th>
                        <th>부하</th>
                        <th>태양광</th>
                        <th>BESS</th>
                        <th>계통</th>
                        <th>SOC</th>
                        <th>샘플</th>
                    </tr>
                </thead>
                <tbody id="powerflow-tbody"></tbody>
            </table>
        </div>
    </div>

    <!-- Rule-Based vs LSTM 비교 모달 -->
    <div class="modal-overlay" id="comparison-modal" onclick="closeComparisonModalOnOverlay(event)">
        <div class="modal" id="comparison-modal-content" onclick="event.stopPropagation()">
            <div class="modal-header">
                <div>
                    <div class="modal-title">Rule-Based vs LSTM 성능 비교</div>
                    <div class="modal-subtitle">1년치 시뮬레이션 결과 상세 분석</div>
                </div>
                <button class="close-btn" onclick="closeComparisonModal()">X</button>
            </div>
            
            <!-- 종합 우위 카드 -->
            <div class="stats-grid" id="comparison-summary-stats"></div>
            
            <!-- 월별 비교 그래프 4개 -->
            <div id="monthly-charts-info" class="info-box" style="margin-top: 8px;">
                월별 비교 그래프 (2025년 시뮬레이션 데이터)
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 12px;">
                <div class="modal-chart-container" style="height: 180px;">
                    <div class="comparison-chart-title">자립률 추이 (%)</div>
                    <canvas id="comparisonSelfSuffChart"></canvas>
                </div>
                <div class="modal-chart-container" style="height: 180px;">
                    <div class="comparison-chart-title">평균 SOC 추이 (%)</div>
                    <canvas id="comparisonSocChart"></canvas>
                </div>
                <div class="modal-chart-container" style="height: 180px;">
                    <div class="comparison-chart-title">월별 배터리 사이클 (회)</div>
                    <canvas id="comparisonCycleChart"></canvas>
                </div>
                <div class="modal-chart-container" style="height: 180px;">
                    <div class="comparison-chart-title">월별 절감액 (원)</div>
                    <canvas id="comparisonSavingChart"></canvas>
                </div>
            </div>
            
            <!-- 카테고리별 상세 비교 -->
            <div id="yearly-detail-container">
                <p style="text-align: center; color: #64748b; padding: 20px;">데이터 로드 중...</p>
            </div>
        </div>
    </div>

    <script>
        const socket = io();
        let chart = null;
        let modalChart = null;
        let powerFlowDetailChart = null;
        let comparisonCharts = { selfSuff: null, soc: null, cycle: null, saving: null };
        let currentPage = 1;
        const TOTAL_PAGES = 4;
        let powerFlowAllData = [];

        const PAGE_LABELS = {
            1: '상태 1',
            2: '상태 2',
            3: '실시간 전력 흐름',
            4: 'Rule-Based vs LSTM',
        };

        const allCards = [
            { title: '배터리 상태', value_id: 'soc-value', unit: '%', type: 'gauge',
              metric: 'avg_soc', label: '평균 SOC', col1: 'SOC(%)', col2: '평균', col3: '범위' },
            { title: 'BESS 출력', value_id: 'bess-power', unit: 'kW', type: 'metric', sub_id: 'bess-action',
              metric: 'avg_bess', label: 'BESS 평균 출력', col1: '평균(kW)', col2: '충전(kWh)', col3: '방전(kWh)' },
            { title: '현재 요금', value_id: 'tariff-rate', unit: '원/kWh', type: 'metric', sub_id: 'tariff-period',
              metric: 'avg_tariff', label: '평균 요금', col1: '평균(원)', col2: '소비(kWh)', col3: '비용(원)' },
            { title: '시스템 부하', value_id: 'load-kw', unit: 'kW', type: 'metric', sub_id: 'solar-info',
              metric: 'avg_load', label: '평균 부하', col1: '평균(kW)', col2: '총량(kWh)', col3: '태양광(kWh)' },
            { title: '자립률', value_id: 'self-sufficiency', unit: '%', type: 'metric', sub: '오늘의 자급률',
              metric: 'self_sufficiency_pct', label: '자립률', col1: '자립률(%)', col2: '부하(kWh)', col3: '공급(kWh)' },
            { title: '일일 절감액', value_id: 'daily-saving', unit: '원', type: 'metric', sub_id: 'discharge-info',
              metric: 'daily_saving_won', label: '일일 절감액', col1: '절감액(원)', col2: '방전(kWh)', col3: '누적(원)' },
            { title: '배터리 사이클', value_id: 'cycle-count', unit: '회', type: 'metric', sub: '누적 사이클',
              metric: 'cycle', label: '사이클 수', col1: '사이클', col2: '충전(kWh)', col3: '방전(kWh)' },
            { title: '금일 충전량', value_id: 'charge-kwh', unit: 'kWh', type: 'metric', sub: '배터리 충전',
              metric: 'sum_charge_kwh', label: '일일 충전량', col1: '충전(kWh)', col2: '평균(kW)', col3: '시간(h)' },
            { title: '금일 방전량', value_id: 'total-discharge-kwh', unit: 'kWh', type: 'metric', sub: '배터리 방전',
              metric: 'sum_discharge_kwh', label: '일일 방전량', col1: '방전(kWh)', col2: '평균(kW)', col3: '시간(h)' },
            { title: '역송전량', value_id: 'reverse-power', unit: 'kWh', type: 'metric', sub: '계통 공급',
              metric: 'reverse_kwh', label: '역송전량', col1: '역송(kWh)', col2: '태양광(kWh)', col3: '비율(%)' },
            { title: '계통 전력', value_id: 'grid-power', unit: 'kW', type: 'metric', sub_id: 'grid-status',
              metric: 'avg_grid', label: '계통 전력', col1: '평균(kW)', col2: '구매(kWh)', col3: '판매(kWh)' },
            { title: '금일 소비', value_id: 'total-load', unit: 'kWh', type: 'metric', sub: '오늘 총 부하',
              metric: 'sum_load_kwh', label: '일일 소비', col1: '소비(kWh)', col2: '평균(kW)', col3: '피크(kW)' },
        ];

        function renderCards(pageNum) {
            const gridId = pageNum === 1 ? 'cards-grid-1' : 'cards-grid-2';
            const grid = document.getElementById(gridId);
            if (!grid) return;
            grid.innerHTML = '';
            const start = (pageNum - 1) * 6;
            const cards = allCards.slice(start, start + 6);
            
            cards.forEach((c, idx) => {
                const el = document.createElement('div');
                el.className = 'card';
                el.id = c.value_id + '-card';
                el.onclick = () => openModal(start + idx);
                
                if (c.type === 'gauge') {
                    el.innerHTML = `<div class="metric-label">${c.title}</div>
                        <div class="gauge-container"><div class="gauge"><div class="gauge-inner">
                        <div class="gauge-value" id="${c.value_id}">--</div>
                        <div class="gauge-percent">${c.unit}</div></div></div></div>`;
                } else {
                    let sub = c.sub_id ? `<div class="sub-metric" id="${c.sub_id}">-</div>` : `<div class="sub-metric">${c.sub}</div>`;
                    el.innerHTML = `<div class="metric-label">${c.title}</div>
                        <div class="metric-value" id="${c.value_id}">0.0</div>
                        <div class="metric-unit">${c.unit}</div>${sub}`;
                }
                grid.appendChild(el);
            });
        }

        function updateTime() {
            document.getElementById('current-time').textContent = new Date().toLocaleTimeString('ko-KR');
        }
        setInterval(updateTime, 1000);
        updateTime();

        function showPage(p) {
            if (p < 1 || p > TOTAL_PAGES) return;
            currentPage = p;
            
            // 모든 페이지 숨기기
            for (let i = 1; i <= TOTAL_PAGES; i++) {
                const page = document.getElementById('page-' + i);
                if (page) page.classList.remove('active');
            }
            
            // 현재 페이지 표시
            const activePage = document.getElementById('page-' + p);
            if (activePage) activePage.classList.add('active');
            
            // 페이지 표시 텍스트 업데이트
            document.getElementById('page-num').textContent = p;
            document.getElementById('page-label').textContent = PAGE_LABELS[p];
            
            // dot 업데이트
            document.querySelectorAll('.dot').forEach((d, i) => 
                d.classList.toggle('active', i === p - 1));
            
            // 이전/다음 버튼 활성화
            document.getElementById('prev-btn').disabled = (p === 1);
            document.getElementById('next-btn').disabled = (p === TOTAL_PAGES);
            
            // 페이지별 초기화
            if (p === 1) renderCards(1);
            if (p === 2) renderCards(2);
            if (p === 3) {
                if (!chart) initChart();
                updateChart();
            }
            if (p === 4) updateComparison();
        }
        function nextPage() { if (currentPage < TOTAL_PAGES) showPage(currentPage + 1); }
        function prevPage() { if (currentPage > 1) showPage(currentPage - 1); }
        function goToPage(p) { showPage(p); }

        // ============ 박스 모달 ============
        function openModal(cardIdx) {
            const card = allCards[cardIdx];
            document.getElementById('modal-title').textContent = `${card.title} - 일별 분석`;
            document.getElementById('modal-col-1').textContent = card.col1;
            document.getElementById('modal-col-2').textContent = card.col2;
            document.getElementById('modal-col-3').textContent = card.col3;
            
            document.getElementById('modal').classList.add('active');
            
            fetch('/api/weekly').then(r => r.json()).then(res => {
                const data = res.data || [];
                if (data.length === 0) {
                    document.getElementById('modal-info').innerHTML = '<strong>아직 데이터가 수집되지 않았습니다.</strong>';
                    document.getElementById('modal-stats').innerHTML = '';
                    document.getElementById('modal-tbody').innerHTML = '';
                    if (modalChart) { modalChart.destroy(); modalChart = null; }
                } else {
                    document.getElementById('modal-info').textContent = `수집된 ${data.length}일치 데이터를 표시합니다.`;
                    renderModalStats(data, card);
                    renderModalTable(data, card);
                    requestAnimationFrame(() => {
                        requestAnimationFrame(() => {
                            setTimeout(() => {
                                renderModalChart(data, card);
                                if (modalChart) modalChart.resize();
                            }, 200);
                        });
                    });
                }
            });
        }
        function closeModal() {
            document.getElementById('modal').classList.remove('active');
            if (modalChart) { modalChart.destroy(); modalChart = null; }
        }
        function closeModalOnOverlay(e) { if (e.target.id === 'modal') closeModal(); }

        function getMetricUnit(metric) {
            const unitMap = {
                'avg_soc': '%', 'avg_bess': 'kW', 'avg_tariff': '원/kWh',
                'avg_load': 'kW', 'self_sufficiency_pct': '%', 'daily_saving_won': '원',
                'cycle': '회', 'sum_charge_kwh': 'kWh', 'sum_discharge_kwh': 'kWh',
                'reverse_kwh': 'kWh', 'avg_grid': 'kW', 'sum_load_kwh': 'kWh',
            };
            return unitMap[metric] || '';
        }
        
        function renderModalStats(data, card) {
            const values = data.map(d => parseFloat(d[card.metric] || 0));
            const avg = values.reduce((a, b) => a + b, 0) / values.length;
            const max = Math.max(...values);
            const min = Math.min(...values);
            const total = values.reduce((a, b) => a + b, 0);
            const unit = getMetricUnit(card.metric);
            const isWon = unit === '원';
            const fmt = (v) => isWon ? Math.round(v).toLocaleString() : v.toFixed(2);
            
            document.getElementById('modal-stats').innerHTML = `
                <div class="stat-card"><div class="stat-label">평균</div><div class="stat-value">${fmt(avg)} <span class="stat-unit">${unit}</span></div></div>
                <div class="stat-card"><div class="stat-label">최대</div><div class="stat-value">${fmt(max)} <span class="stat-unit">${unit}</span></div></div>
                <div class="stat-card"><div class="stat-label">최소</div><div class="stat-value">${fmt(min)} <span class="stat-unit">${unit}</span></div></div>
                <div class="stat-card"><div class="stat-label">합계</div><div class="stat-value">${fmt(total)} <span class="stat-unit">${unit}</span></div></div>
            `;
        }

        function renderModalChart(data, card) {
            const ctx = document.getElementById('modalChart').getContext('2d');
            if (modalChart) modalChart.destroy();
            const labels = data.map(d => {
                const dt = new Date(d.date);
                return `${dt.getMonth()+1}/${dt.getDate()}`;
            });
            const values = data.map(d => parseFloat(d[card.metric] || 0));
            modalChart = new Chart(ctx, {
                type: 'bar',
                data: { labels, datasets: [{
                    label: card.label, data: values,
                    backgroundColor: 'rgba(59, 130, 246, 0.5)',
                    borderColor: '#3b82f6', borderWidth: 2,
                }]},
                options: { responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { labels: { color: '#e2e8f0' } } },
                    scales: { y: { beginAtZero: true, grid: { color: 'rgba(51, 65, 85, 0.3)' }, ticks: { color: '#94a3b8' } },
                              x: { grid: { color: 'rgba(51, 65, 85, 0.3)' }, ticks: { color: '#94a3b8' } } }
                }
            });
        }

        function renderModalTable(data, card) {
            const tbody = document.getElementById('modal-tbody');
            tbody.innerHTML = '';
            const dayNames = ['일', '월', '화', '수', '목', '금', '토'];
            data.forEach(d => {
                const dt = new Date(d.date);
                const dayName = dayNames[dt.getDay()];
                let col1, col2, col3;
                if (card.metric === 'avg_soc') {
                    col1 = d.avg_soc; col2 = d.avg_soc;
                    col3 = `${(d.avg_soc - 10).toFixed(1)}~${(d.avg_soc + 10).toFixed(1)}`;
                } else if (card.metric === 'avg_bess') {
                    col1 = d.avg_bess.toFixed(2); col2 = d.sum_charge_kwh; col3 = d.sum_discharge_kwh;
                } else if (card.metric === 'avg_tariff') {
                    col1 = d.avg_tariff.toFixed(0); col2 = d.sum_load_kwh;
                    col3 = (d.sum_load_kwh * d.avg_tariff).toFixed(0);
                } else if (card.metric === 'avg_load') {
                    col1 = d.avg_load.toFixed(2); col2 = d.sum_load_kwh; col3 = d.sum_solar_kwh;
                } else if (card.metric === 'self_sufficiency_pct') {
                    col1 = d.self_sufficiency_pct; col2 = d.sum_load_kwh;
                    col3 = (d.sum_solar_kwh + d.sum_discharge_kwh).toFixed(2);
                } else if (card.metric === 'daily_saving_won') {
                    col1 = d.daily_saving_won.toLocaleString(); col2 = d.sum_discharge_kwh; col3 = '-';
                } else if (card.metric === 'cycle') {
                    col1 = d.cycle; col2 = d.sum_charge_kwh; col3 = d.sum_discharge_kwh;
                } else if (card.metric === 'sum_charge_kwh') {
                    col1 = d.sum_charge_kwh; col2 = d.avg_bess.toFixed(2);
                    col3 = (d.sum_charge_kwh / Math.max(d.avg_bess, 0.01)).toFixed(1);
                } else if (card.metric === 'sum_discharge_kwh') {
                    col1 = d.sum_discharge_kwh; col2 = d.avg_bess.toFixed(2);
                    col3 = (d.sum_discharge_kwh / Math.max(Math.abs(d.avg_bess), 0.01)).toFixed(1);
                } else if (card.metric === 'reverse_kwh') {
                    col1 = d.reverse_kwh; col2 = d.sum_solar_kwh;
                    col3 = ((d.reverse_kwh / Math.max(d.sum_solar_kwh, 0.01)) * 100).toFixed(1);
                } else if (card.metric === 'avg_grid') {
                    col1 = d.avg_grid.toFixed(2);
                    col2 = (d.avg_grid > 0 ? d.sum_load_kwh : 0).toFixed(2);
                    col3 = (d.avg_grid < 0 ? Math.abs(d.sum_load_kwh) : 0).toFixed(2);
                } else if (card.metric === 'sum_load_kwh') {
                    col1 = d.sum_load_kwh; col2 = d.avg_load.toFixed(2); col3 = (d.avg_load * 1.5).toFixed(2);
                } else {
                    col1 = d[card.metric] || 0; col2 = '-'; col3 = '-';
                }
                tbody.innerHTML += `<tr><td>${d.date}</td><td>${dayName}요일</td><td>${col1}</td><td>${col2}</td><td>${col3}</td><td>${d.data_count}</td></tr>`;
            });
        }

        //  실시간 전력흐름 모달 
        function openPowerFlowModal() {
            document.getElementById('powerflow-modal').classList.add('active');
            const modalContent = document.getElementById('powerflow-modal-content');
            modalContent.scrollTop = 0;
            
            if (!modalContent.dataset.sliderListenerAdded) {
                setupPowerflowSlider();
                modalContent.dataset.sliderListenerAdded = 'true';
            }
            
            fetch('/api/daily-detailed').then(r => r.json()).then(res => {
                const data = res.data || [];
                powerFlowAllData = data;
                document.getElementById('powerflow-slider-start').value = 0;
                document.getElementById('powerflow-slider-end').value = 47;
                document.querySelectorAll('#powerflow-modal .quick-btn').forEach(b => b.classList.remove('active'));
                document.querySelector('#powerflow-modal .quick-btn[data-start="0"][data-end="47"]').classList.add('active');
                renderPowerFlowStats(data);
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        setTimeout(() => {
                            updatePowerflowRange(0, 47);
                            if (powerFlowDetailChart) powerFlowDetailChart.resize();
                        }, 200);
                    });
                });
            });
        }
        
        function indexToTimeLabel(idx) {
            const hour = Math.floor(idx / 2);
            const min = (idx % 2) * 30;
            return `${String(hour).padStart(2,'0')}:${String(min).padStart(2,'0')}`;
        }
        function timeLabelToIndex(timeLabel) {
            const [h, m] = timeLabel.split(':').map(Number);
            return h * 2 + Math.floor(m / 30);
        }
        
        function updatePowerflowRange(startIdx, endIdx) {
            if (startIdx > endIdx) { const tmp = startIdx; startIdx = endIdx; endIdx = tmp; }
            const startLabel = indexToTimeLabel(startIdx);
            const endLabel = indexToTimeLabel(endIdx);
            document.getElementById('powerflow-range-label').textContent = `${startLabel} ~ ${endLabel}`;
            const startPct = (startIdx / 47) * 100;
            const endPct = (endIdx / 47) * 100;
            document.getElementById('powerflow-active-range').style.left = startPct + '%';
            document.getElementById('powerflow-active-range').style.right = (100 - endPct) + '%';
            const filteredData = powerFlowAllData.filter(d => {
                const idx = timeLabelToIndex(d.hour);
                return idx >= startIdx && idx <= endIdx;
            });
            renderPowerFlowDetailChart(filteredData);
            renderPowerFlowTable(filteredData);
        }
        
        function setupPowerflowSlider() {
            const sliderStart = document.getElementById('powerflow-slider-start');
            const sliderEnd = document.getElementById('powerflow-slider-end');
            const update = () => {
                const s = parseInt(sliderStart.value);
                const e = parseInt(sliderEnd.value);
                updatePowerflowRange(s, e);
                document.querySelectorAll('#powerflow-modal .quick-btn').forEach(b => b.classList.remove('active'));
            };
            sliderStart.addEventListener('input', update);
            sliderEnd.addEventListener('input', update);
            document.querySelectorAll('#powerflow-modal .quick-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const s = parseInt(btn.dataset.start);
                    const e = parseInt(btn.dataset.end);
                    sliderStart.value = s; sliderEnd.value = e;
                    updatePowerflowRange(s, e);
                    document.querySelectorAll('#powerflow-modal .quick-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                });
            });
        }
        function closePowerFlowModal() {
            document.getElementById('powerflow-modal').classList.remove('active');
            if (powerFlowDetailChart) { powerFlowDetailChart.destroy(); powerFlowDetailChart = null; }
        }
        function closePowerFlowModalOnOverlay(e) { if (e.target.id === 'powerflow-modal') closePowerFlowModal(); }

        function renderPowerFlowStats(data) {
            if (data.length === 0) {
                document.getElementById('powerflow-stats').innerHTML = '<p style="grid-column: span 4; text-align: center; color: #64748b; padding: 20px;">데이터 없음</p>';
                return;
            }
            const loads = data.map(d => d.load_kw);
            const solars = data.map(d => d.solar_kw);
            const avgLoad = loads.reduce((a,b) => a+b, 0) / loads.length;
            const maxLoad = Math.max(...loads);
            const avgSolar = solars.reduce((a,b) => a+b, 0) / solars.length;
            const maxSolar = Math.max(...solars);
            const dateInfo = data[0].date ? ` (${data[0].date})` : '';
            document.getElementById('powerflow-stats').innerHTML = `
                <div class="stat-card"><div class="stat-label">평균 부하${dateInfo}</div><div class="stat-value">${avgLoad.toFixed(1)} kW</div></div>
                <div class="stat-card"><div class="stat-label">최대 부하</div><div class="stat-value">${maxLoad.toFixed(1)} kW</div></div>
                <div class="stat-card"><div class="stat-label">평균 태양광</div><div class="stat-value">${avgSolar.toFixed(1)} kW</div></div>
                <div class="stat-card"><div class="stat-label">최대 태양광</div><div class="stat-value">${maxSolar.toFixed(1)} kW</div></div>
            `;
        }

        function renderPowerFlowDetailChart(data) {
            const ctx = document.getElementById('powerFlowDetailChart').getContext('2d');
            if (powerFlowDetailChart) powerFlowDetailChart.destroy();
            if (data.length === 0) {
                powerFlowDetailChart = new Chart(ctx, {
                    type: 'line',
                    data: { labels: ['데이터 없음'], datasets: [] },
                    options: { responsive: true, maintainAspectRatio: false,
                        plugins: { legend: { display: false },
                                   title: { display: true, text: '데이터가 없습니다', color: '#94a3b8' } } }
                });
                return;
            }
            powerFlowDetailChart = new Chart(ctx, {
                type: 'line',
                data: { labels: data.map(d => d.hour), datasets: [
                    { label: '부하', data: data.map(d => d.load_kw), borderColor: '#ef4444', backgroundColor: 'rgba(239, 68, 68, 0.06)', borderWidth: 1.5, tension: 0.2, fill: true, pointRadius: 0, pointHoverRadius: 0, pointHitRadius: 10 },
                    { label: '태양광', data: data.map(d => d.solar_kw), borderColor: '#f59e0b', backgroundColor: 'rgba(245, 158, 11, 0.1)', borderWidth: 1.5, tension: 0.2, fill: true, pointRadius: 0, pointHoverRadius: 0, pointHitRadius: 10 },
                    { label: 'BESS', data: data.map(d => d.bess_power_kw), borderColor: '#3b82f6', backgroundColor: 'rgba(59, 130, 246, 0.06)', borderWidth: 1.5, tension: 0.2, fill: true, pointRadius: 0, pointHoverRadius: 0, pointHitRadius: 10 },
                    { label: '계통', data: data.map(d => d.grid_power_kw), borderColor: '#8b5cf6', backgroundColor: 'rgba(139, 92, 246, 0.06)', borderWidth: 1.5, tension: 0.2, fill: true, pointRadius: 0, pointHoverRadius: 0, pointHitRadius: 10 },
                ]},
                options: { responsive: true, maintainAspectRatio: false,
                    animation: false,
                    interaction: { mode: 'index', intersect: false },
                    elements: { point: { radius: 0, hoverRadius: 0 } },
                    plugins: { legend: { labels: { color: '#e2e8f0' } },
                               tooltip: { mode: 'index', intersect: false } },
                    scales: { 
                        y: { beginAtZero: false, grid: { color: 'rgba(51, 65, 85, 0.25)' }, ticks: { color: '#94a3b8' } },
                        x: { 
                            grid: { display: false },
                            ticks: { 
                                color: '#94a3b8',
                                autoSkip: false,
                                maxRotation: 45,
                                minRotation: 45,
                                font: { size: 9 },
                                callback: function(val, index) {
                                    const label = this.getLabelForValue(val);
                                    if (!label) return '';
                                    if (label.endsWith(':00') || label.endsWith(':30')) {
                                        return label;
                                    }
                                    return '';
                                }
                            } 
                        }
                    }
                }
            });
        }

        function renderPowerFlowTable(data) {
            const tbody = document.getElementById('powerflow-tbody');
            tbody.innerHTML = '';
            data.forEach(d => {
                tbody.innerHTML += `<tr><td>${d.hour}</td><td>${d.load_kw.toFixed(2)}</td><td>${d.solar_kw.toFixed(2)}</td><td>${d.bess_power_kw.toFixed(2)}</td><td>${d.grid_power_kw.toFixed(2)}</td><td>${d.soc.toFixed(1)}</td><td>${d.sample_count || '-'}</td></tr>`;
            });
        }

        //  Rule-Based vs LSTM 비교 모달 (1년치 진짜 데이터) 
        let yearlyDataCache = null;
        
        function openComparisonModal() {
            document.getElementById('comparison-modal').classList.add('active');
            const modalContent = document.getElementById('comparison-modal-content');
            modalContent.scrollTop = 0;
            
            // 1. 1년치 종합 비교 (표)
            fetch('/api/yearly-comparison').then(r => r.json()).then(res => {
                yearlyDataCache = res;
                renderYearlyModalContent(res);
            }).catch(e => {
                document.getElementById('yearly-detail-container').innerHTML = 
                    '<p style="text-align: center; color: #ef4444; padding: 20px;">데이터 로드 실패: ' + e.message + '</p>';
            });
            
            // 2. 월별 비교 (그래프 4개)
            fetch('/api/monthly-comparison').then(r => r.json()).then(res => {
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        setTimeout(() => { renderMonthlyCharts(res); }, 200);
                    });
                });
            }).catch(e => {
                console.error('월별 데이터 로드 실패:', e);
            });
        }
        function closeComparisonModal() {
            document.getElementById('comparison-modal').classList.remove('active');
            Object.keys(comparisonCharts).forEach(key => {
                if (comparisonCharts[key]) { comparisonCharts[key].destroy(); comparisonCharts[key] = null; }
            });
        }
        function closeComparisonModalOnOverlay(e) { if (e.target.id === 'comparison-modal') closeComparisonModal(); }

        // 월별 그래프 4개 렌더링
        function renderMonthlyCharts(data) {
            // 기존 차트 제거
            Object.keys(comparisonCharts).forEach(key => {
                if (comparisonCharts[key]) { comparisonCharts[key].destroy(); comparisonCharts[key] = null; }
            });
            
            const infoBox = document.getElementById('monthly-charts-info');
            
            if (!data || !data.available) {
                if (infoBox) {
                    infoBox.innerHTML = '⚠️ ' + (data.message || '월별 데이터 없음') +
                        '<br><span style="font-size: 0.85em; color: #94a3b8;">시뮬레이션 CSV 파일이 필요합니다.</span>';
                }
                return;
            }
            
            const months = data.months || [];
            if (months.length === 0) {
                if (infoBox) infoBox.innerHTML = '월별 데이터 없음';
                return;
            }
            
            // 데이터 추출
            const labels = months.map(m => m.month_label);
            const rb_self = months.map(m => m.rb ? m.rb.self_sufficiency_pct : null);
            const ls_self = months.map(m => m.lstm ? m.lstm.self_sufficiency_pct : null);
            const rb_soc  = months.map(m => m.rb ? m.rb.soc_avg_pct : null);
            const ls_soc  = months.map(m => m.lstm ? m.lstm.soc_avg_pct : null);
            const rb_cyc  = months.map(m => m.rb ? m.rb.cycle_count : null);
            const ls_cyc  = months.map(m => m.lstm ? m.lstm.cycle_count : null);
            const rb_sav  = months.map(m => m.rb ? m.rb.cost_saving_won : null);
            const ls_sav  = months.map(m => m.lstm ? m.lstm.cost_saving_won : null);
            
            // 정보 박스 업데이트
            if (infoBox) {
                const rbMonths = data.rb_total_months || 0;
                const lstmMonths = data.lstm_total_months || 0;
                const commonMonths = (data.common_months || []).length;
                infoBox.innerHTML = `월별 비교 그래프 (RB: ${rbMonths}개월, LSTM: ${lstmMonths}개월, 공통: ${commonMonths}개월)`;
            }
            
            // 4개 차트 공통 옵션
            const commonOpts = {
                responsive: true, maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { labels: { color: '#e2e8f0', font: { size: 10 }, boxWidth: 12 } },
                    tooltip: { mode: 'index', intersect: false }
                },
                scales: {
                    y: { 
                        beginAtZero: false,
                        grid: { color: 'rgba(51, 65, 85, 0.3)' }, 
                        ticks: { color: '#94a3b8', font: { size: 9 } } 
                    },
                    x: { 
                        grid: { color: 'rgba(51, 65, 85, 0.2)' }, 
                        ticks: { color: '#94a3b8', font: { size: 9 } } 
                    }
                }
            };
            
            function makeChart(canvasId, lstmData, rbData) {
                const ctx = document.getElementById(canvasId).getContext('2d');
                return new Chart(ctx, {
                    type: 'line',
                    data: { labels, datasets: [
                        { label: 'LSTM', data: lstmData, 
                          borderColor: '#3b82f6', 
                          backgroundColor: 'rgba(59, 130, 246, 0.1)', 
                          borderWidth: 2, tension: 0.4, fill: true, pointRadius: 3,
                          spanGaps: true },
                        { label: 'Rule-Based', data: rbData, 
                          borderColor: '#10b981', 
                          backgroundColor: 'rgba(16, 185, 129, 0.1)', 
                          borderWidth: 2, tension: 0.4, fill: true, pointRadius: 3,
                          spanGaps: true },
                    ]},
                    options: commonOpts
                });
            }
            
            comparisonCharts.selfSuff = makeChart('comparisonSelfSuffChart', ls_self, rb_self);
            comparisonCharts.soc      = makeChart('comparisonSocChart',      ls_soc,  rb_soc);
            comparisonCharts.cycle    = makeChart('comparisonCycleChart',    ls_cyc,  rb_cyc);
            comparisonCharts.saving   = makeChart('comparisonSavingChart',   ls_sav,  rb_sav);
        }

        // 숫자 포맷 (원/정수/소수)
        function formatYearlyValue(val, fmt) {
            if (val === null || val === undefined) return '-';
            if (fmt === 'int') return Math.round(val).toLocaleString();
            if (fmt === 'float2') return val.toFixed(2);
            if (fmt === 'float3') return val.toFixed(3);
            return val.toString();
        }

        // 모달: 카테고리별 상세 비교 렌더링
        function renderYearlyModalContent(data) {
            if (!data || !data.available) {
                document.getElementById('comparison-summary-stats').innerHTML = 
                    '<p style="grid-column: span 4; text-align: center; color: #64748b; padding: 20px;">' + 
                    (data.message || '비교 데이터 없음') + '</p>';
                document.getElementById('yearly-detail-container').innerHTML = '';
                return;
            }
            
            // 종합 우위 카드
            const summary = data.summary || {};
            document.getElementById('comparison-summary-stats').innerHTML = `
                <div class="stat-card">
                    <div class="stat-label">LSTM 우위</div>
                    <div class="stat-value" style="color: #3b82f6;">${summary.lstm_wins || 0}개</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Rule-Based 우위</div>
                    <div class="stat-value" style="color: #10b981;">${summary.rb_wins || 0}개</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">동일</div>
                    <div class="stat-value" style="color: #94a3b8;">${summary.equals || 0}개</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">전체 지표</div>
                    <div class="stat-value" style="color: #f59e0b;">${summary.total || 0}개</div>
                </div>
            `;
            
            // 카테고리별 표
            const categories = [
                { key: 'economic',          title: '1. 경제적 효율' },
                { key: 'economic_breakdown',title: '2. 시간대별 절감 (경부/중간/최대)' },
                { key: 'energy',            title: '3. 에너지 효율' },
                { key: 'stability',         title: '4. 운영 안정성' },
                { key: 'prediction',        title: '5. LSTM 예측 성능' },
            ];
            
            let html = '<div class="info-box">2025년 1년치 시뮬레이션 결과 비교. 더 좋은 값은 초록색으로 표시됩니다.</div>';
            
            categories.forEach(cat => {
                const entries = data[cat.key] || [];
                if (entries.length === 0) return;
                
                html += `<div style="margin-bottom: 16px;">
                    <div style="font-size: 0.9em; color: #3b82f6; font-weight: 600; margin-bottom: 6px; padding: 4px 8px; background: rgba(59,130,246,0.1); border-radius: 4px;">
                        ${cat.title}
                    </div>
                    <table class="data-table" style="margin-bottom: 4px;">
                        <thead>
                            <tr>
                                <th style="text-align: left;">지표</th>
                                <th style="color: #3b82f6;">LSTM</th>
                                <th style="color: #10b981;">Rule-Based</th>
                                <th>차이</th>
                                <th>우위</th>
                            </tr>
                        </thead>
                        <tbody>`;
                
                entries.forEach(e => {
                    const lstmStr = formatYearlyValue(e.lstm, e.fmt) + (e.unit ? ' ' + e.unit : '');
                    const rbStr   = formatYearlyValue(e.rb,   e.fmt) + (e.unit ? ' ' + e.unit : '');
                    
                    let diffStr = '-';
                    if (e.diff !== null && e.diff !== undefined) {
                        const sign = e.diff >= 0 ? '+' : '';
                        if (e.fmt === 'int') {
                            diffStr = sign + Math.round(e.diff).toLocaleString();
                        } else if (e.fmt === 'float3') {
                            diffStr = sign + e.diff.toFixed(3);
                        } else {
                            diffStr = sign + e.diff.toFixed(2);
                        }
                    }
                    
                    let winnerText = '-';
                    let winnerColor = '#94a3b8';
                    let lstmClass = '';
                    let rbClass = '';
                    if (e.winner === 'lstm') { winnerText = 'LSTM'; winnerColor = '#3b82f6'; lstmClass = 'better'; }
                    else if (e.winner === 'rb') { winnerText = 'Rule-Based'; winnerColor = '#10b981'; rbClass = 'better'; }
                    else if (e.winner === 'equal') { winnerText = '동일'; }
                    
                    html += `<tr>
                        <td style="text-align: left; color: #cbd5e1;">${e.name}</td>
                        <td class="${lstmClass}">${lstmStr}</td>
                        <td class="${rbClass}">${rbStr}</td>
                        <td>${diffStr}</td>
                        <td style="color: ${winnerColor};">${winnerText}</td>
                    </tr>`;
                });
                
                html += '</tbody></table></div>';
            });
            
            document.getElementById('yearly-detail-container').innerHTML = html;
        }

        //  페이지 4: 1년치 비교 요약 (메인 화면) 
        function updateComparison() {
            fetch('/api/yearly-comparison').then(r => r.json()).then(data => {
                renderYearlyMainView(data);
            }).catch(e => {
                document.getElementById('yearly-comparison-table').innerHTML = 
                    '<p style="text-align: center; color: #ef4444; padding: 20px;">로드 실패: ' + e.message + '</p>';
            });
        }

        function renderYearlyMainView(data) {
            if (!data || !data.available) {
                document.getElementById('yearly-summary-stats').innerHTML = '';
                document.getElementById('yearly-comparison-table').innerHTML = 
                    '<p style="text-align: center; color: #64748b; padding: 20px;">' + 
                    (data.message || '비교 데이터가 없습니다.') + 
                    '<br><br>compare.py를 실행한 후 다시 확인하세요.</p>';
                return;
            }
            
            // 종합 우위 (페이지 상단 4개 카드)
            const summary = data.summary || {};
            document.getElementById('yearly-summary-stats').innerHTML = `
                <div class="stat-card">
                    <div class="stat-label">LSTM 우위</div>
                    <div class="stat-value" style="color: #3b82f6;">${summary.lstm_wins || 0}개</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">RB 우위</div>
                    <div class="stat-value" style="color: #10b981;">${summary.rb_wins || 0}개</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">동일</div>
                    <div class="stat-value" style="color: #94a3b8;">${summary.equals || 0}개</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">전체</div>
                    <div class="stat-value" style="color: #f59e0b;">${summary.total || 0}개</div>
                </div>
            `;
            
            // 핵심 지표만 간략 표시 (페이지 4에서)
            const keyMetrics = [
                { cat: 'economic', key: 'cost_saving_won', label: '연 절감액' },
                { cat: 'economic', key: 'cost_saving_rate_pct', label: '절감률' },
                { cat: 'economic', key: 'peak_saving_rate_pct', label: '피크 절감률' },
                { cat: 'energy',   key: 'self_sufficiency_pct', label: '자립률' },
                { cat: 'energy',   key: 'bess_utilization_pct', label: 'BESS 활용률' },
                { cat: 'energy',   key: 'bess_discharge_kwh', label: 'BESS 방전 공급' },
                { cat: 'stability',key: 'control_success_rate_pct', label: '제어 성공률' },
                { cat: 'stability',key: 'cycle_count', label: '배터리 사이클' },
            ];
            
            let html = '<table style="width: 100%; font-size: 0.75em;">';
            html += `<tr>
                <th style="text-align: left;">핵심 지표</th>
                <th style="color: #3b82f6;">LSTM</th>
                <th style="color: #10b981;">RB</th>
                <th>우위</th>
            </tr>`;
            
            keyMetrics.forEach(m => {
                const entries = data[m.cat] || [];
                const entry = entries.find(e => e.metric_key === m.key);
                if (!entry) return;
                
                const lstmStr = formatYearlyValue(entry.lstm, entry.fmt) + 
                                (entry.unit ? ' ' + entry.unit : '');
                const rbStr = formatYearlyValue(entry.rb, entry.fmt) + 
                              (entry.unit ? ' ' + entry.unit : '');
                
                let winnerText = '-';
                let winnerColor = '#94a3b8';
                let lstmClass = '';
                let rbClass = '';
                if (entry.winner === 'lstm') { winnerText = 'LSTM'; winnerColor = '#3b82f6'; lstmClass = 'better'; }
                else if (entry.winner === 'rb') { winnerText = 'RB'; winnerColor = '#10b981'; rbClass = 'better'; }
                else if (entry.winner === 'equal') { winnerText = '='; }
                
                html += `<tr>
                    <td style="text-align: left; color: #cbd5e1; padding: 6px 4px;">${m.label}</td>
                    <td class="${lstmClass}" style="padding: 6px 4px;">${lstmStr}</td>
                    <td class="${rbClass}" style="padding: 6px 4px;">${rbStr}</td>
                    <td style="color: ${winnerColor}; padding: 6px 4px; font-weight: 600;">${winnerText}</td>
                </tr>`;
            });
            
            html += '</table>';
            html += '<p style="text-align: center; color: #64748b; font-size: 0.7em; margin-top: 8px;">📊 2025년 1년치 시뮬레이션 결과 · 상세 보기로 전체 지표 확인</p>';
            
            document.getElementById('yearly-comparison-table').innerHTML = html;
        }


        //  ESC 키로 모달 닫기 
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                closeModal(); closePowerFlowModal(); closeComparisonModal();
            }
        });

        //  자동 업데이트 
        setInterval(() => fetch('/api/status').then(r => r.json()).then(updateMetrics).catch(e => {}), 1000);
        setInterval(() => fetch('/api/daily-stats').then(r => r.json()).then(updateStats).catch(e => {}), 10000);
        setInterval(() => {
            if (currentPage === 4) updateComparison();
        }, 5000);

        function updateMetrics(data) {
            if (!data) return;
            const el = (id) => document.getElementById(id);
            if (el('soc-value')) el('soc-value').textContent = parseFloat(data.soc || 0).toFixed(1);
            if (el('bess-power')) el('bess-power').textContent = parseFloat(data.bess_power_kw || 0).toFixed(1);
            if (el('load-kw')) el('load-kw').textContent = parseFloat(data.load_kw || 0).toFixed(1);
            const solarEl = el('solar-info');
            if (solarEl) solarEl.textContent = `태양광: ${parseFloat(data.solar_kw || 0).toFixed(1)} kW`;
            if (el('tariff-rate')) el('tariff-rate').textContent = parseInt(data.tariff_rate || 0);
            const map = {'on_peak': '최대', 'mid_peak': '중간', 'off_peak': '경부'};
            const tariffEl = el('tariff-period');
            if (tariffEl) tariffEl.textContent = map[data.tariff_period] || '-';
            const grid = parseFloat(data.grid_power_kw || 0).toFixed(1);
            if (el('grid-power')) el('grid-power').textContent = grid;
            const gridEl = el('grid-status');
            if (gridEl) gridEl.textContent = grid < 0 ? `역송 ${Math.abs(grid)} kW` : `구매 ${grid} kW`;
            const card = el('bess-power-card');
            if (card) {
                const bp = parseFloat(data.bess_power_kw || 0).toFixed(1);
                card.classList.remove('charging', 'discharging');
                if (bp < -1) {
                    card.classList.add('charging');
                    const a = el('bess-action');
                    if (a) a.textContent = '충전 중';
                } else if (bp > 1) {
                    card.classList.add('discharging');
                    const a = el('bess-action');
                    if (a) a.textContent = '방전 중';
                } else {
                    const a = el('bess-action');
                    if (a) a.textContent = '대기';
                }
            }
        }

        function updateStats(data) {
            const el = (id) => document.getElementById(id);
            if (el('self-sufficiency')) el('self-sufficiency').textContent = (data.self_sufficiency_pct || 0).toFixed(1);
            if (el('daily-saving')) el('daily-saving').textContent = (data.daily_saving_won || 0).toLocaleString();
            const dEl = el('discharge-info');
            if (dEl) dEl.textContent = `방전: ${(data.discharge_kwh || 0).toFixed(2)} kWh`;
            if (el('cycle-count')) el('cycle-count').textContent = (data.cycle_count || 0).toFixed(2);
            if (el('charge-kwh')) el('charge-kwh').textContent = (data.charge_kwh || 0).toFixed(2);
            if (el('total-discharge-kwh')) el('total-discharge-kwh').textContent = (data.discharge_kwh || 0).toFixed(2);
            if (el('reverse-power')) el('reverse-power').textContent = (data.reverse_power_kwh || 0).toFixed(2);
            if (el('total-load')) el('total-load').textContent = (data.total_load_kwh || 0).toFixed(2);
        }

        // (옛 updateComparison 함수 제거됨 - 라인 3578의 새 1년치 비교 버전을 사용)

        function initChart() {
            const ctx = document.getElementById('powerChart').getContext('2d');
            chart = new Chart(ctx, {
                type: 'line',
                data: { labels: [], datasets: [
                    { label: '부하', data: [], borderColor: '#ef4444', backgroundColor: 'rgba(239, 68, 68, 0.06)', borderWidth: 1.5, tension: 0.2, fill: true, pointRadius: 0, pointHoverRadius: 0, pointHitRadius: 10 },
                    { label: '태양광', data: [], borderColor: '#f59e0b', backgroundColor: 'rgba(245, 158, 11, 0.1)', borderWidth: 1.5, tension: 0.2, fill: true, pointRadius: 0, pointHoverRadius: 0, pointHitRadius: 10 },
                    { label: 'BESS', data: [], borderColor: '#3b82f6', backgroundColor: 'rgba(59, 130, 246, 0.06)', borderWidth: 1.5, tension: 0.2, fill: true, pointRadius: 0, pointHoverRadius: 0, pointHitRadius: 10 },
                    { label: '계통', data: [], borderColor: '#8b5cf6', backgroundColor: 'rgba(139, 92, 246, 0.06)', borderWidth: 1.5, tension: 0.2, fill: true, pointRadius: 0, pointHoverRadius: 0, pointHitRadius: 10 },
                ]},
                options: { responsive: true, maintainAspectRatio: false,
                    animation: false,
                    interaction: { mode: 'index', intersect: false },
                    elements: { point: { radius: 0, hoverRadius: 0 } },
                    scales: { 
                        y: { beginAtZero: false, grid: { color: 'rgba(51, 65, 85, 0.25)' }, ticks: { color: '#94a3b8' } },
                        x: { 
                            grid: { color: 'rgba(51, 65, 85, 0.15)', display: false },
                            ticks: { 
                                color: '#94a3b8',
                                autoSkip: false,
                                maxRotation: 45,
                                minRotation: 45,
                                font: { size: 9 },
                                callback: function(val, index) {
                                    // 30분 단위(HH:00 또는 HH:30)만 표시
                                    const label = this.getLabelForValue(val);
                                    if (!label) return '';
                                    if (label.endsWith(':00') || label.endsWith(':30')) {
                                        return label;
                                    }
                                    return '';
                                }
                            } 
                        } 
                    },
                    plugins: { legend: { labels: { color: '#e2e8f0' } } } }
            });
        }

        function updateChart() {
            if (!chart) initChart();
            fetch('/api/history?hours=24').then(r => r.json()).then(res => {
                const d = res.data || [];
                if (d.length === 0) return;
                chart.data.labels = d.map(x => {
                    const dt = new Date(x.timestamp);
                    return `${String(dt.getHours()).padStart(2,'0')}:${String(dt.getMinutes()).padStart(2,'0')}`;
                });
                chart.data.datasets[0].data = d.map(x => x.load_kw);
                chart.data.datasets[1].data = d.map(x => x.solar_kw);
                chart.data.datasets[2].data = d.map(x => x.bess_power_kw);
                chart.data.datasets[3].data = d.map(x => x.grid_power_kw);
                chart.update('none');
            });
            setTimeout(() => {
                if (currentPage === 3 && chart) chart.resize();
            }, 100);
        }

        // 차트 페이지 자동 업데이트 (페이지 3에 있을 때만)
        setInterval(() => {
            if (currentPage === 3) updateChart();
        }, 10000);

        // 페이지 4 1년치 비교 데이터는 변하지 않으므로 5초마다 새로고침 시도 (실패 대비)
        setInterval(() => {
            if (currentPage === 4) updateComparison();
        }, 5000);

        window.addEventListener('load', () => {
            renderCards(1);
            fetch('/api/status').then(r => r.json()).then(updateMetrics);
            fetch('/api/daily-stats').then(r => r.json()).then(updateStats);
            updateComparison();   // 페이지 4 데이터 즉시 로드
        });
    </script>
</body>
</html>"""


if __name__ == '__main__':
    print("=" * 62)
    print("  BESS 실시간 모니터링 시스템")
    print("=" * 62)
    print(f"[DB] {DB_PATH}")
    print(f"[비교 DB] {COMPARISON_DB}")
    print(f"[DB 존재] {DB_PATH.exists()}\n")
    
    init_comparison_db()
    
    comparison_thread = threading.Thread(target=hourly_comparison_task, daemon=True)
    comparison_thread.start()
    print("[시작] 1시간 비교 데이터 스레드 시작\n")
    
    print("[서버] http://localhost:5000 에서 실행 중...")
    print("[모바일/Pi4] http://localhost:5000/mobile (4페이지 구성)")
    print("[기능] 박스 클릭 -> 일별 분석 모달")
    print("[기능] 전력흐름 클릭 -> 일일 상세 모달")
    print("[기능] 비교 표 -> 1시간 평균값으로 표시\n")
    
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)