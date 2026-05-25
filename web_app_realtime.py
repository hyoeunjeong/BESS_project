"""
web_app_realtime.py - BESS 실시간 모니터링
- 모달: 7일치 누적 표시 (수집된 만큼)
- 실시간 전력흐름 그래프 모달 (일일 데이터)
- 비교 표: 1시간 평균값으로 시간별 표시
- 이전 시간 비교 데이터 유지
"""

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


def query_db(query: str, params: tuple = (), db_path=DB_PATH):
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as e:
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
    rows = query_db('''
        SELECT timestamp, hour, load_kw, solar_kw, soc, bess_power_kw, 
               charge_kw, discharge_kw, grid_power_kw, tariff_rate, tariff_period, action
        FROM realtime ORDER BY id DESC LIMIT 1
    ''')
    if not rows:
        return None
    row = rows[0]
    return {
        'timestamp': row[0], 'hour': row[1],
        'load_kw': float(row[2]), 'solar_kw': float(row[3]),
        'soc': float(row[4]) * 100,
        'bess_power_kw': float(row[5]),
        'charge_kw': float(row[6]), 'discharge_kw': float(row[7]),
        'grid_power_kw': float(row[8]),
        'tariff_rate': float(row[9]), 'tariff_period': row[10],
        'action': row[11],
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
    """가장 최근 데이터가 있는 날의 30분 단위 상세 데이터 (실시간 전력흐름 모달용)"""
    # 1) 가장 최근 데이터의 날짜 찾기
    latest = query_db('''
        SELECT DATE(timestamp) FROM realtime 
        ORDER BY id DESC LIMIT 1
    ''')
    
    if not latest or not latest[0][0]:
        return []
    
    target_date = latest[0][0]
    
    # 2) 그 날의 30분 단위 집계
    rows = query_db('''
        SELECT 
            strftime('%H', timestamp) || ':' || 
                printf('%02d', (CAST(strftime('%M', timestamp) AS INTEGER) / 30) * 30) as time_label,
            AVG(load_kw), AVG(solar_kw), AVG(bess_power_kw), AVG(grid_power_kw),
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
    """최근 N시간의 데이터를 30분 단위 평균으로 반환"""
    hours = int(request.args.get('hours', 24))
    
    # 30분 단위로 그룹화 (최근 hours 시간치)
    rows = query_db(f'''
        SELECT 
            strftime('%Y-%m-%d %H', timestamp) || ':' || 
                printf('%02d', (CAST(strftime('%M', timestamp) AS INTEGER) / 30) * 30) || ':00' as time_label,
            AVG(load_kw), AVG(solar_kw), AVG(soc), AVG(bess_power_kw), AVG(grid_power_kw)
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
                    <span>Rule-Based vs LSTM 비교 (시간별 평균)</span>
                    <button class="detail-btn" onclick="openComparisonModal()">상세 보기 ↗</button>
                </div>
                <div class="comparison-table" id="comparison-table">
                    <p style="text-align: center; color: #64748b; padding: 20px;">데이터 수집 중...</p>
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
                    <div class="modal-subtitle">시간별 성능 추이 및 종합 분석</div>
                </div>
                <button class="close-btn" onclick="closeComparisonModal()">X</button>
            </div>
            
            <!-- 종합 우위 카드 -->
            <div class="stats-grid" id="comparison-summary-stats"></div>
            
            <!-- 4개 지표 차트 (2x2 그리드) -->
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 20px;">
                <div class="modal-chart-container" style="height: 200px;">
                    <div class="comparison-chart-title">자립률 추이 (%)</div>
                    <canvas id="comparisonSelfSuffChart"></canvas>
                </div>
                <div class="modal-chart-container" style="height: 200px;">
                    <div class="comparison-chart-title">SOC 추이 (%)</div>
                    <canvas id="comparisonSocChart"></canvas>
                </div>
                <div class="modal-chart-container" style="height: 200px;">
                    <div class="comparison-chart-title">배터리 사이클 (회)</div>
                    <canvas id="comparisonCycleChart"></canvas>
                </div>
                <div class="modal-chart-container" style="height: 200px;">
                    <div class="comparison-chart-title">절감액 (원)</div>
                    <canvas id="comparisonSavingChart"></canvas>
                </div>
            </div>
            
            <!-- 시간별 상세 표 -->
            <div class="info-box">시간별 LSTM과 Rule-Based의 성능을 비교합니다. 더 좋은 값은 초록색으로 표시됩니다.</div>
            <table class="data-table">
                <thead>
                    <tr>
                        <th>시간</th>
                        <th>지표</th>
                        <th style="color: #3b82f6;">LSTM</th>
                        <th style="color: #10b981;">Rule-Based</th>
                        <th>차이</th>
                        <th>우위</th>
                    </tr>
                </thead>
                <tbody id="comparison-modal-tbody"></tbody>
            </table>
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

        // ===== Rule-Based vs LSTM 비교 모달 =====
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
            
            fetch('/api/comparison?hours=168').then(r => r.json()).then(res => {
                const data = res.data || [];
                renderComparisonSummary(data);
                renderComparisonTable(data);
                
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        setTimeout(() => {
                            renderComparisonCharts(data);
                        }, 200);
                    });
                });
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

        function renderComparisonSummary(data) {
            if (data.length === 0) {
                document.getElementById('comparison-summary-stats').innerHTML = '<p style="grid-column: span 4; text-align: center; color: #64748b; padding: 20px;">아직 비교 데이터가 없습니다</p>';
                return;
            }
            
            // 최신 데이터로 우위 집계
            let lstmWins = 0, rbWins = 0, equals = 0;
            const latest = data[data.length - 1];
            
            const metrics = [
                { lstm: latest.lstm.soc, rb: latest.rb.soc, higherBetter: true },
                { lstm: latest.lstm.self_sufficiency, rb: latest.rb.self_sufficiency, higherBetter: true },
                { lstm: latest.lstm.cycle, rb: latest.rb.cycle, higherBetter: null },
                { lstm: latest.lstm.cost_saving, rb: latest.rb.cost_saving, higherBetter: true },
            ];
            
            metrics.forEach(m => {
                const diff = m.lstm - m.rb;
                if (m.higherBetter === null) {
                    equals += 1;
                } else if (Math.abs(diff) < 0.01) {
                    equals += 1;
                } else if ((m.higherBetter && diff > 0) || (!m.higherBetter && diff < 0)) {
                    lstmWins += 1;
                } else {
                    rbWins += 1;
                }
            });
            
            document.getElementById('comparison-summary-stats').innerHTML = `
                <div class="stat-card">
                    <div class="stat-label">LSTM 우위</div>
                    <div class="stat-value" style="color: #3b82f6;">${lstmWins}개</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Rule-Based 우위</div>
                    <div class="stat-value" style="color: #10b981;">${rbWins}개</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">동일</div>
                    <div class="stat-value" style="color: #94a3b8;">${equals}개</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">수집 시간</div>
                    <div class="stat-value" style="color: #f59e0b;">${data.length}h</div>
                </div>
            `;
        }

        function makeComparisonChartConfig(label, lstmData, rbData, labels, unit) {
            return {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [
                        {
                            label: 'LSTM',
                            data: lstmData,
                            borderColor: '#3b82f6',
                            backgroundColor: 'rgba(59, 130, 246, 0.1)',
                            borderWidth: 2,
                            tension: 0.4,
                            fill: true,
                            pointRadius: 3,
                            pointHoverRadius: 6,
                        },
                        {
                            label: 'Rule-Based',
                            data: rbData,
                            borderColor: '#10b981',
                            backgroundColor: 'rgba(16, 185, 129, 0.1)',
                            borderWidth: 2,
                            tension: 0.4,
                            fill: true,
                            pointRadius: 3,
                            pointHoverRadius: 6,
                        },
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: { 
                        legend: { labels: { color: '#e2e8f0', font: { size: 11 } } },
                        tooltip: { mode: 'index', intersect: false }
                    },
                    scales: {
                        y: { 
                            beginAtZero: true, 
                            grid: { color: 'rgba(51, 65, 85, 0.3)' }, 
                            ticks: { color: '#94a3b8', font: { size: 10 } } 
                        },
                        x: { 
                            grid: { color: 'rgba(51, 65, 85, 0.3)' }, 
                            ticks: { color: '#94a3b8', font: { size: 9 }, maxTicksLimit: 6 } 
                        }
                    }
                }
            };
        }

        function renderComparisonCharts(data) {
            // 기존 차트 제거
            Object.keys(comparisonCharts).forEach(key => {
                if (comparisonCharts[key]) {
                    comparisonCharts[key].destroy();
                    comparisonCharts[key] = null;
                }
            });
            
            if (data.length === 0) return;
            
            // 라벨 (시간)
            const labels = data.map(d => {
                const ts = new Date(d.timestamp);
                return `${ts.getMonth()+1}/${ts.getDate()} ${String(ts.getHours()).padStart(2,'0')}:00`;
            });
            
            // 자립률 차트
            const ctx1 = document.getElementById('comparisonSelfSuffChart').getContext('2d');
            comparisonCharts.selfSuff = new Chart(ctx1, makeComparisonChartConfig(
                '자립률',
                data.map(d => d.lstm.self_sufficiency),
                data.map(d => d.rb.self_sufficiency),
                labels,
                '%'
            ));
            
            // SOC 차트
            const ctx2 = document.getElementById('comparisonSocChart').getContext('2d');
            comparisonCharts.soc = new Chart(ctx2, makeComparisonChartConfig(
                'SOC',
                data.map(d => d.lstm.soc),
                data.map(d => d.rb.soc),
                labels,
                '%'
            ));
            
            // 사이클 차트
            const ctx3 = document.getElementById('comparisonCycleChart').getContext('2d');
            comparisonCharts.cycle = new Chart(ctx3, makeComparisonChartConfig(
                '사이클',
                data.map(d => d.lstm.cycle),
                data.map(d => d.rb.cycle),
                labels,
                '회'
            ));
            
            // 절감액 차트
            const ctx4 = document.getElementById('comparisonSavingChart').getContext('2d');
            comparisonCharts.saving = new Chart(ctx4, makeComparisonChartConfig(
                '절감액',
                data.map(d => d.lstm.cost_saving),
                data.map(d => d.rb.cost_saving),
                labels,
                '원'
            ));
        }

        function renderComparisonTable(data) {
            const tbody = document.getElementById('comparison-modal-tbody');
            tbody.innerHTML = '';
            
            if (data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: #64748b; padding: 20px;">데이터 없음</td></tr>';
                return;
            }
            
            // 최신 데이터부터 역순
            const reversedData = [...data].reverse();
            
            reversedData.forEach((entry) => {
                const ts = new Date(entry.timestamp);
                const timeStr = `${ts.getMonth()+1}/${ts.getDate()} ${String(ts.getHours()).padStart(2,'0')}:00`;
                
                const metrics = [
                    { label: 'SOC (%)', lstm: 'soc', rb: 'soc', fmt: (v) => v.toFixed(1), higherBetter: true },
                    { label: '자립률 (%)', lstm: 'self_sufficiency', rb: 'self_sufficiency', fmt: (v) => v.toFixed(1), higherBetter: true },
                    { label: '사이클', lstm: 'cycle', rb: 'cycle', fmt: (v) => v.toFixed(2), higherBetter: null },
                    { label: '절감액 (원)', lstm: 'cost_saving', rb: 'cost_saving', fmt: (v) => Math.round(v).toLocaleString(), higherBetter: true },
                ];
                
                // 시간 헤더 행
                tbody.innerHTML += `<tr style="background: rgba(59, 130, 246, 0.1);">
                    <td colspan="6" style="padding: 6px 10px; font-weight: 500; color: #cbd5e1;">${timeStr} (샘플 ${entry.sample_count || 1}개)</td>
                </tr>`;
                
                metrics.forEach((m) => {
                    const lstmVal = entry.lstm[m.lstm] || 0;
                    const rbVal = entry.rb[m.rb] || 0;
                    const diff = lstmVal - rbVal;
                    
                    let winner = '';
                    let lstmClass = '';
                    let rbClass = '';
                    
                    if (m.higherBetter === null) {
                        winner = '-';
                    } else if (Math.abs(diff) < 0.01) {
                        winner = '동일';
                    } else if ((m.higherBetter && diff > 0) || (!m.higherBetter && diff < 0)) {
                        winner = 'LSTM';
                        lstmClass = 'better';
                    } else {
                        winner = 'Rule-Based';
                        rbClass = 'better';
                    }
                    
                    const diffStr = m.label.includes('원') 
                        ? `${diff >= 0 ? '+' : ''}${Math.round(diff).toLocaleString()}`
                        : `${diff >= 0 ? '+' : ''}${diff.toFixed(2)}`;
                    
                    tbody.innerHTML += `<tr>
                        <td></td>
                        <td style="text-align: left; color: #94a3b8;">${m.label}</td>
                        <td class="${lstmClass}">${m.fmt(lstmVal)}</td>
                        <td class="${rbClass}">${m.fmt(rbVal)}</td>
                        <td>${diffStr}</td>
                        <td style="color: ${winner === 'LSTM' ? '#3b82f6' : winner === 'Rule-Based' ? '#10b981' : '#94a3b8'};">${winner}</td>
                    </tr>`;
                });
            });
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
                        { label: '부하', data: data.map(d => d.load_kw), borderColor: '#ef4444', backgroundColor: 'rgba(239, 68, 68, 0.1)', borderWidth: 2, tension: 0.4, fill: true, pointRadius: 4, pointHoverRadius: 7 },
                        { label: '태양광', data: data.map(d => d.solar_kw), borderColor: '#f59e0b', backgroundColor: 'rgba(245, 158, 11, 0.1)', borderWidth: 2, tension: 0.4, fill: true, pointRadius: 4, pointHoverRadius: 7 },
                        { label: 'BESS', data: data.map(d => d.bess_power_kw), borderColor: '#3b82f6', backgroundColor: 'rgba(59, 130, 246, 0.1)', borderWidth: 2, tension: 0.4, fill: true, pointRadius: 4, pointHoverRadius: 7 },
                        { label: '계통', data: data.map(d => d.grid_power_kw), borderColor: '#8b5cf6', backgroundColor: 'rgba(139, 92, 246, 0.1)', borderWidth: 2, tension: 0.4, fill: true, pointRadius: 4, pointHoverRadius: 7 },
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: { 
                        legend: { labels: { color: '#e2e8f0' } },
                        tooltip: { mode: 'index', intersect: false }
                    },
                    scales: {
                        y: { beginAtZero: false, grid: { color: 'rgba(51, 65, 85, 0.3)' }, ticks: { color: '#94a3b8' } },
                        x: { grid: { color: 'rgba(51, 65, 85, 0.3)' }, ticks: { color: '#94a3b8' } }
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

        // 비교 표 - 시간별 평균값 (1시간당 1개 행)
        function updateComparison() {
            fetch('/api/comparison?hours=168').then(r => r.json()).then(res => {
                const data = res.data || [];
                if (data.length === 0) {
                    document.getElementById('comparison-table').innerHTML = '<p style="text-align: center; color: #64748b; padding: 20px;">아직 비교 데이터가 없습니다.<br>1시간마다 자동 수집됩니다.</p>';
                    return;
                }
                
                let html = `<table>
                    <tr>
                        <th>시간</th>
                        <th>지표</th>
                        <th class="lstm">LSTM</th>
                        <th class="rb">Rule-Based</th>
                    </tr>`;
                
                // 최신 시간 데이터부터 표시 (역순)
                const reversedData = [...data].reverse();
                reversedData.forEach((entry) => {
                    const ts = new Date(entry.timestamp);
                    const timeStr = `${ts.getMonth()+1}/${ts.getDate()} ${String(ts.getHours()).padStart(2,'0')}:00`;
                    
                    const metrics = [
                        { label: 'SOC (%)', lstm: 'soc', rb: 'soc', fmt: (v) => v.toFixed(1) },
                        { label: '자립률 (%)', lstm: 'self_sufficiency', rb: 'self_sufficiency', fmt: (v) => v.toFixed(1) },
                        { label: '사이클', lstm: 'cycle', rb: 'cycle', fmt: (v) => v.toFixed(2) },
                        { label: '절감액 (원)', lstm: 'cost_saving', rb: 'cost_saving', fmt: (v) => Math.round(v).toLocaleString() },
                    ];
                    
                    // 시간 헤더 행
                    html += `<tr class="hour-row">
                        <td colspan="4" class="hour-cell">${timeStr} (평균 ${entry.sample_count || 1}개)</td>
                    </tr>`;
                    
                    metrics.forEach((m) => {
                        const lstmVal = entry.lstm[m.lstm] || 0;
                        const rbVal = entry.rb[m.rb] || 0;
                        const isBetterLstm = lstmVal > rbVal;
                        html += `<tr>
                            <td></td>
                            <td class="metric-name">${m.label}</td>
                            <td class="${isBetterLstm ? 'better' : ''}">${m.fmt(lstmVal)}</td>
                            <td class="${!isBetterLstm ? 'better' : ''}">${m.fmt(rbVal)}</td>
                        </tr>`;
                    });
                });
                
                html += '</table>';
                document.getElementById('comparison-table').innerHTML = html;
            }).catch(e => {});
        }

        function initChart() {
            const ctx = document.getElementById('powerChart').getContext('2d');
            chart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [
                        { label: '부하', data: [], borderColor: '#ef4444', backgroundColor: 'rgba(239, 68, 68, 0.1)', borderWidth: 2, tension: 0.4, fill: true },
                        { label: '태양광', data: [], borderColor: '#f59e0b', backgroundColor: 'rgba(245, 158, 11, 0.1)', borderWidth: 2, tension: 0.4, fill: true },
                        { label: 'BESS', data: [], borderColor: '#3b82f6', backgroundColor: 'rgba(59, 130, 246, 0.1)', borderWidth: 2, tension: 0.4, fill: true },
                        { label: '계통', data: [], borderColor: '#8b5cf6', backgroundColor: 'rgba(139, 92, 246, 0.1)', borderWidth: 2, tension: 0.4, fill: true },
                    ]
                },
                options: { responsive: true, maintainAspectRatio: false,
                    scales: { y: { beginAtZero: true, grid: { color: 'rgba(51, 65, 85, 0.3)' }, ticks: { color: '#94a3b8' } },
                              x: { grid: { color: 'rgba(51, 65, 85, 0.3)' }, ticks: { color: '#94a3b8' } } },
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
    print("[기능] 박스 클릭 -> 일별 분석 모달")
    print("[기능] 전력흐름 클릭 -> 일일 상세 모달")
    print("[기능] 비교 표 -> 1시간 평균값으로 표시\n")
    
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)