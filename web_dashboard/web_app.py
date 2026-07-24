import os
import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
import pandas as pd
import numpy as np

# 경로 설정
import os

PROJECT_ROOT = Path(__file__).parent.absolute()

possible_paths = [
    PROJECT_ROOT / 'DL_LSTM' / 'results',
    PROJECT_ROOT / 'results',
]

DATA_DIR = None
for p in possible_paths:
    if p.exists():
        DATA_DIR = p
        print(f"[경로 감지] 발견: {p}")
        break

if DATA_DIR is None:
    DATA_DIR = PROJECT_ROOT / 'DL_LSTM' / 'results'
    print(f"[경로 경고] 폴더를 찾을 수 없음. 기본값 사용: {DATA_DIR}")

CSV_PATH = DATA_DIR / 'lstm_simulation_result.csv'
DB_PATH = DATA_DIR / 'bess_data.db'

print(f"\n[경로 정보]")
print(f"  프로젝트 루트: {PROJECT_ROOT}")
print(f"  데이터 폴더: {DATA_DIR}")
print(f"  CSV 파일: {CSV_PATH}")
print(f"  CSV 존재: {CSV_PATH.exists()}")
print(f"  DB 파일: {DB_PATH}\n")

# Flask 설정
app = Flask(__name__, template_folder=str(PROJECT_ROOT / 'templates'))
app.config['SECRET_KEY'] = 'bess-monitoring-secret-2025'

# CORS 헤더 추가
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

socketio = SocketIO(app, cors_allowed_origins="*")

# Favicon 무시
@app.route('/favicon.ico')
def favicon():
    return '', 204


# 데이터베이스 초기화
def init_database():
    """CSV를 SQLite DB로 변환 (빠른 조회용)"""
    os.makedirs(DATA_DIR, exist_ok=True)
    
    if not CSV_PATH.exists():
        print(f"[경고] CSV 파일 없음: {CSV_PATH}")
        print(f"[안내] 먼저 main.py를 실행해주세요:")
        print(f"       cd {DATA_DIR.parent}")
        print(f"       python main.py")
        return False
    
    try:
        conn = sqlite3.connect(str(DB_PATH))
        df = pd.read_csv(str(CSV_PATH))
        df.to_sql('simulation', conn, if_exists='replace', index=False)
        conn.close()
        print(f"[DB] 초기화 완료: {DB_PATH}")
        return True
    except Exception as e:
        print(f"[에러] DB 초기화 실패: {e}")
        return False


def get_latest_data() -> dict:
    """최신 시뮬레이션 데이터 조회"""
    try:
        if CSV_PATH.exists():
            df = pd.read_csv(str(CSV_PATH))
            if len(df) == 0:
                return None
            latest = df.iloc[-1]
            
            def safe_get(series, key, default=None):
                try:
                    return series[key] if key in series else default
                except:
                    return default
            
            return {
                'timestamp': str(safe_get(latest, 'timestamp', '')),
                'hour': int(safe_get(latest, 'hour', 0)),
                'load_kw': float(safe_get(latest, 'load_kw', 0)),
                'solar_kw': float(safe_get(latest, 'solar_kw', 0)),
                'soc': float(safe_get(latest, 'soc', 0)) * 100,
                'bess_power_kw': float(safe_get(latest, 'bess_power_kw', 0)),
                'charge_kw': float(safe_get(latest, 'charge_kw', 0)),
                'discharge_kw': float(safe_get(latest, 'discharge_kw', 0)),
                'grid_power_kw': float(safe_get(latest, 'grid_power_kw', 0)),
                'tariff_rate': float(safe_get(latest, 'tariff_rate', 0)),
                'action': str(safe_get(latest, 'action', 'idle')),
            }
    except Exception as e:
        print(f"[에러] 데이터 조회 실패: {e}")
    return None


def get_daily_summary() -> dict:
    """일일 통계"""
    try:
        if CSV_PATH.exists():
            df = pd.read_csv(str(CSV_PATH))
            if len(df) == 0:
                return {}
            
            ts = 1.0
            total_load = (df['load_kw'] * ts).sum()
            total_solar = (df['solar_kw'] * ts).sum()
            discharge = (df['discharge_kw'] * ts).sum()
            charge = (df['charge_kw'] * ts).sum()
            
            grid_buy = df['grid_power_kw'].clip(lower=0) * ts
            cost = (grid_buy * df['tariff_rate']).sum()
            
            return {
                'total_load_kwh': round(total_load, 2),
                'total_solar_kwh': round(total_solar, 2),
                'bess_discharge_kwh': round(discharge, 2),
                'bess_charge_kwh': round(charge, 2),
                'total_cost_won': int(cost),
                'self_sufficiency_pct': round((total_solar + discharge) / total_load * 100, 2) if total_load > 0 else 0,
            }
    except Exception as e:
        print(f"[에러] 통계 계산 실패: {e}")
    return {}


def get_hourly_chart_data(hours: int = 24) -> dict:
    """시간별 차트 데이터"""
    try:
        if CSV_PATH.exists():
            df = pd.read_csv(str(CSV_PATH))
            if len(df) == 0:
                return {}
            
            n = min(hours, len(df))
            df_slice = df.tail(n).reset_index(drop=True)
            
            return {
                'timestamps': [str(t).split()[-1] if pd.notna(t) else '' for t in df_slice['timestamp']],
                'load': df_slice['load_kw'].fillna(0).round(2).tolist(),
                'solar': df_slice['solar_kw'].fillna(0).round(2).tolist(),
                'bess': df_slice['bess_power_kw'].fillna(0).round(2).tolist(),
                'grid': df_slice['grid_power_kw'].fillna(0).round(2).tolist(),
                'soc': (df_slice['soc'].fillna(0) * 100).round(1).tolist(),
            }
    except Exception as e:
        print(f"[에러] 차트 데이터 계산 실패: {e}")
    return {}

# 웹 라우트
@app.route('/')
def dashboard():
    """메인 대시보드 페이지"""
    return render_template('dashboard.html')


@app.route('/api/status')
def api_status():
    """REST API: 최신 상태"""
    data = get_latest_data()
    if data:
        return jsonify(data)
    return jsonify({'error': '데이터 없음'}), 404


@app.route('/api/daily-summary')
def api_daily_summary():
    """REST API: 일일 통계"""
    return jsonify(get_daily_summary())


@app.route('/api/chart-data')
def api_chart_data():
    """REST API: 차트 데이터"""
    hours = int(request.args.get('hours', 24))
    return jsonify(get_hourly_chart_data(hours))


# WebSocket
@socketio.on('connect')
def on_connect():
    """클라이언트 연결"""
    print(f"[연결] 클라이언트 접속")
    data = get_latest_data()
    if data:
        emit('status_update', data)


@socketio.on('disconnect')
def on_disconnect():
    """클라이언트 연결 해제"""
    print(f"[연결] 클라이언트 종료")


def broadcast_updates():
    """백그라운드에서 실시간 업데이트 브로드캐스트"""
    last_timestamp = None
    while True:
        try:
            data = get_latest_data()
            if data and data.get('timestamp') != last_timestamp:
                socketio.emit('status_update', data, to=None)
                last_timestamp = data.get('timestamp')
            time.sleep(1)
        except Exception as e:
            print(f"[에러] 브로드캐스트 실패: {e}")
            time.sleep(5)


# 실행
if __name__ == '__main__':
    print("=" * 62)
    print("  BESS 실시간 모니터링 웹 대시보드")
    print("=" * 62)
    
    csv_exists = init_database()
    
    if not csv_exists:
        print("\n[주의] CSV 파일이 없어서 데이터를 표시할 수 없습니다.")
        print("       웹서버는 실행되지만 빈 화면이 나옵니다.")
        print("       먼저 main.py를 실행해주세요.\n")
    
    update_thread = threading.Thread(target=broadcast_updates, daemon=True)
    update_thread.start()
    print("[시작] 실시간 업데이트 스레드 시작")
    
    print("\n[서버] http://127.0.0.1:5000 에서 실행 중... (로컬 전용)")
    print("[접속] http://localhost:5000 (이 PC에서만 접속 가능)\n")

    socketio.run(app, host='127.0.0.1', port=5000, debug=False, allow_unsafe_werkzeug=True)
