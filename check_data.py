"""
check_data.py - 데이터 수집 상태 확인 도구 (12개 박스 + 비교 데이터)
"""

import sqlite3
import os
from pathlib import Path
from datetime import datetime, timedelta
import platform

DB_PATH = Path(__file__).parent / 'realtime_data' / 'realtime.db'
COMPARISON_DB = Path(__file__).parent / 'realtime_data' / 'comparison.db'

BESS_CAPACITY = 100.0


def pad_kr(text: str, width: int, align: str = 'left') -> str:
    """한글은 2칸으로 계산해서 너비 맞추기"""
    text_w = sum(2 if ord(c) > 127 else 1 for c in str(text))
    pad = width - text_w
    if pad <= 0:
        return str(text)
    if align == 'right':
        return ' ' * pad + str(text)
    elif align == 'center':
        l = pad // 2
        r = pad - l
        return ' ' * l + str(text) + ' ' * r
    else:
        return str(text) + ' ' * pad


def check_realtime_db():
    """실시간 DB 확인"""
    print("\n" + "=" * 62)
    print("   LSTM 실시간 데이터 (realtime.db) 확인")
    print("=" * 62)
    
    if not DB_PATH.exists():
        print("[ERR] realtime.db 파일이 없습니다!")
        print(f"      경로: {DB_PATH}")
        print("      -> realtime_engine.py를 실행하면 자동 생성됩니다")
        return False
    
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM realtime')
        total = cursor.fetchone()[0]
        print(f"\n[OK] 총 데이터: {total:,}개")
        
        if total == 0:
            print("[!]  아직 데이터가 없습니다. realtime_engine.py를 실행해주세요.")
            conn.close()
            return False
        
        cursor.execute('''
            SELECT timestamp, hour, load_kw, solar_kw, soc, bess_power_kw, grid_power_kw, action
            FROM realtime ORDER BY id DESC LIMIT 1
        ''')
        row = cursor.fetchone()
        if row:
            print(f"\n[최신 데이터]")
            print(f"   {pad_kr('시간', 8)}: {row[0]} ({row[1]}시)")
            print(f"   {pad_kr('부하', 8)}: {row[2]:.1f} kW")
            print(f"   {pad_kr('태양광', 8)}: {row[3]:.1f} kW")
            print(f"   {pad_kr('SOC', 8)}: {float(row[4])*100:.1f} %")
            print(f"   {pad_kr('BESS', 8)}: {row[5]:.1f} kW ({row[7]})")
            print(f"   {pad_kr('계통', 8)}: {row[6]:.1f} kW")
        
        cursor.execute('SELECT COUNT(*) FROM realtime WHERE DATE(timestamp) = DATE("now")')
        today = cursor.fetchone()[0]
        print(f"\n[오늘 데이터] {today}개")
        
        cursor.execute('SELECT COUNT(*) FROM realtime WHERE timestamp > datetime("now", "-24 hours")')
        recent = cursor.fetchone()[0]
        print(f"[최근 24시간] {recent}개")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"[ERR] 에러: {e}")
        return False


def check_12_boxes():
    """12개 박스 각각의 데이터 수집 현황"""
    print("\n" + "=" * 62)
    print("   12개 박스 데이터 수집 현황")
    print("=" * 62)
    
    if not DB_PATH.exists():
        print("[ERR] realtime.db 없음")
        return
    
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                COUNT(*) as total,
                AVG(soc) * 100 as avg_soc,
                AVG(bess_power_kw) as avg_bess,
                AVG(tariff_rate) as avg_tariff,
                AVG(load_kw) as avg_load,
                AVG(solar_kw) as avg_solar,
                AVG(grid_power_kw) as avg_grid,
                SUM(charge_kw) / 60.0 as total_charge,
                SUM(discharge_kw) / 60.0 as total_discharge,
                SUM(load_kw) / 60.0 as total_load,
                SUM(solar_kw) / 60.0 as total_solar,
                SUM(CASE WHEN grid_power_kw < 0 THEN ABS(grid_power_kw) ELSE 0 END) / 60.0 as reverse
            FROM realtime
            WHERE DATE(timestamp) = DATE('now')
        ''')
        row = cursor.fetchone()
        
        if not row or not row[0]:
            print("\n[!] 오늘 데이터가 없습니다.")
            conn.close()
            return
        
        total = row[0]
        avg_soc = float(row[1] or 0)
        avg_bess = float(row[2] or 0)
        avg_tariff = float(row[3] or 0)
        avg_load = float(row[4] or 0)
        avg_solar = float(row[5] or 0)
        avg_grid = float(row[6] or 0)
        total_charge = float(row[7] or 0)
        total_discharge = float(row[8] or 0)
        total_load_kwh = float(row[9] or 0)
        total_solar_kwh = float(row[10] or 0)
        reverse_kwh = float(row[11] or 0)
        
        direct_solar = min(total_solar_kwh, total_load_kwh)
        self_supply = direct_solar + total_discharge
        self_sufficiency = (self_supply / total_load_kwh * 100) if total_load_kwh > 0 else 0
        cycle = (total_charge + total_discharge) / (2 * BESS_CAPACITY)
        saving = total_discharge * 150
        
        print(f"\n[오늘 수집 데이터] {total}개")
        print(f"\n{'-' * 62}")
        print(f"   페이지 1 (실시간 상태)")
        print(f"{'-' * 62}")
        
        boxes_p1 = [
            ('1', '배터리 상태',      f'{avg_soc:.1f}',          '%',    total > 0),
            ('2', 'BESS 출력',       f'{avg_bess:.1f}',          'kW',   total > 0),
            ('3', '현재 요금',        f'{avg_tariff:.0f}',        '원/kWh', total > 0),
            ('4', '시스템 부하',      f'{avg_load:.1f}',          'kW',   total > 0),
            ('5', '자립률',          f'{self_sufficiency:.1f}',   '%',    True),
            ('6', '일일 절감액',      f'{int(saving):,}',         '원',   True),
        ]
        
        for num, name, value, unit, ok in boxes_p1:
            status = "[OK]" if ok else "[ERR]"
            name_padded = name + ' ' * (15 - sum(2 if ord(c) > 127 else 1 for c in name))
            print(f"   {status} 박스 {num:>2}. {name_padded} : {value} {unit}")
        
        print(f"\n{'-' * 62}")
        print(f"   페이지 2 (누적 통계)")
        print(f"{'-' * 62}")
        
        boxes_p2 = [
            ('7',  '배터리 사이클',    f'{cycle:.2f}',            '회',  True),
            ('8',  '금일 충전량',      f'{total_charge:.2f}',      'kWh', total_charge > 0),
            ('9',  '금일 방전량',      f'{total_discharge:.2f}',   'kWh', total_discharge > 0),
            ('10', '역송전량',        f'{reverse_kwh:.2f}',        'kWh', True),
            ('11', '계통 전력',       f'{avg_grid:.1f}',           'kW',  True),
            ('12', '금일 소비',       f'{total_load_kwh:.2f}',     'kWh', total_load_kwh > 0),
        ]
        
        for num, name, value, unit, ok in boxes_p2:
            status = "[OK]" if ok else "[ERR]"
            name_padded = name + ' ' * (15 - sum(2 if ord(c) > 127 else 1 for c in name))
            print(f"   {status} 박스 {num:>2}. {name_padded} : {value} {unit}")
        
        print(f"\n{'-' * 62}")
        print(f"   주간 데이터 (모달용)")
        print(f"{'-' * 62}")
        
        cursor.execute('''
            SELECT 
                DATE(timestamp) as day,
                COUNT(*) as cnt
            FROM realtime
            WHERE timestamp >= DATE('now', '-7 days')
            GROUP BY DATE(timestamp)
            ORDER BY day DESC
        ''')
        weekly = cursor.fetchall()
        
        if weekly:
            print(f"\n   최근 7일간 일별 데이터 수:")
            for day, cnt in weekly:
                bar = "█" * min(cnt // 50, 20)
                status = "[OK]" if cnt > 0 else "[ERR]"
                print(f"   {status} {day}: {cnt:5d}개 {bar}")
        else:
            print("   [!] 주간 데이터 없음")
        
        conn.close()
        
    except Exception as e:
        print(f"[ERR] 에러: {e}")
        import traceback
        traceback.print_exc()


def check_comparison_db():
    """비교 DB 확인 - 옵션 3 (차이 강조 방식)"""
    print("\n" + "=" * 62)
    print("   Rule-Based vs LSTM 비교 데이터")
    print("=" * 62)
    
    if not COMPARISON_DB.exists():
        print("[ERR] comparison.db 파일이 없습니다!")
        print(f"      경로: {COMPARISON_DB}")
        print("      -> web_app_realtime.py를 처음 실행하면 자동 생성됩니다")
        return
    
    try:
        conn = sqlite3.connect(str(COMPARISON_DB))
        cursor = conn.cursor()
        
        # UNIQUE 제약 확인
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='hourly_comparison'")
        schema = cursor.fetchone()
        
        if schema and 'UNIQUE' in (schema[0] or ''):
            print("\n[!] 주의: 이전 버전 DB입니다 (UNIQUE 제약 있음)")
            print("    같은 시간 데이터가 덮어쓰기될 수 있습니다.")
            print("    -> web_app_realtime.py 재시작 시 자동 마이그레이션됩니다.")
        else:
            print("\n[OK] DB 구조: 이전 데이터 유지 가능 (UNIQUE 제약 없음)")
        
        cursor.execute('SELECT COUNT(*) FROM hourly_comparison')
        total = cursor.fetchone()[0]
        print(f"\n[OK] 총 비교 데이터: {total}개")
        
        if total == 0:
            print("[!]  아직 비교 데이터가 없습니다. 1시간마다 자동 수집됩니다.")
            conn.close()
            return
        
        # 시간대별 데이터 수
        cursor.execute('''
            SELECT 
                strftime('%Y-%m-%d %H:00', timestamp) as hour_ts,
                COUNT(*) as cnt
            FROM hourly_comparison
            GROUP BY hour_ts
            ORDER BY hour_ts DESC
            LIMIT 24
        ''')
        rows = cursor.fetchall()
        
        if rows:
            print(f"\n[시간대별 비교 데이터] (최근 24시간)")
            for hr, cnt in rows:
                status = "[OK]" if cnt > 0 else "[ERR]"
                print(f"   {status} {hr}: {cnt}개")
        
        # 최신 비교 - 옵션 3: 차이 강조 방식
        cursor.execute('''
            SELECT timestamp, hour, lstm_soc, lstm_cycle, lstm_self_sufficiency, lstm_cost_saving,
                   rb_soc, rb_cycle, rb_self_sufficiency, rb_cost_saving
            FROM hourly_comparison 
            ORDER BY id DESC LIMIT 1
        ''')
        row = cursor.fetchone()
        if row:
            print(f"\n[최신 비교 데이터]")
            print(f"   시간: {row[0]}")
            print()
            
            # 헤더 (한글 너비 보정)
            print(f"   {pad_kr('지표', 8)}  {pad_kr('LSTM', 14, 'right')}  {pad_kr('Rule-Based', 14, 'right')}    {pad_kr('차이', 12)}")
            print(f"   {'-' * 65}")
            
            # 메트릭 정의: (라벨, LSTM값, RB값, 단위, 높을수록좋음)
            metrics = [
                ('SOC',     row[2], row[6], '%',  True),
                ('사이클', row[3], row[7], '회', None),
                ('자립률', row[4], row[8], '%',  True),
                ('절감액', row[5], row[9], '원', True),
            ]
            
            lstm_wins = 0
            rb_wins = 0
            equals = 0
            
            for label, lstm_val, rb_val, unit, higher_is_better in metrics:
                diff = lstm_val - rb_val
                
                # 우위 판정
                if higher_is_better is None:
                    winner = ""
                elif abs(diff) < 0.01:
                    winner = "동일"
                    equals += 1
                elif (higher_is_better and diff > 0) or (not higher_is_better and diff < 0):
                    winner = "LSTM 우위"
                    lstm_wins += 1
                else:
                    winner = "Rule-Based 우위"
                    rb_wins += 1
                
                # 값 포맷
                if unit == '원':
                    lstm_str = f"{lstm_val:,.0f} 원"
                    rb_str = f"{rb_val:,.0f} 원"
                    diff_str = f"{diff:+,.0f} 원"
                else:
                    lstm_str = f"{lstm_val:.2f} {unit}"
                    rb_str = f"{rb_val:.2f} {unit}"
                    diff_str = f"{diff:+.2f} {unit}"
                
                # 한글 너비 보정해서 정렬
                label_padded = pad_kr(label, 8)
                lstm_padded = pad_kr(lstm_str, 14, 'right')
                rb_padded = pad_kr(rb_str, 14, 'right')
                
                if winner:
                    diff_with_winner = f"{pad_kr(diff_str, 12)} ({winner})"
                else:
                    diff_with_winner = diff_str
                
                print(f"   {label_padded}  {lstm_padded}  {rb_padded}    {diff_with_winner}")
            
            print(f"   {'-' * 65}")
            print(f"\n   [종합 우위]")
            print(f"   LSTM 우위       : {lstm_wins}개")
            print(f"   Rule-Based 우위 : {rb_wins}개")
            print(f"   동일            : {equals}개")
        
        # 수집 기간
        cursor.execute('SELECT MIN(timestamp), MAX(timestamp) FROM hourly_comparison')
        row = cursor.fetchone()
        if row and row[0]:
            print(f"\n[수집 기간] {row[0]} ~ {row[1]}")
        
        conn.close()
        
    except Exception as e:
        print(f"[ERR] 에러: {e}")
        import traceback
        traceback.print_exc()


def check_disk_usage():
    """디스크 사용량 확인"""
    print("\n" + "=" * 62)
    print("   디스크 사용량")
    print("=" * 62)
    
    try:
        if DB_PATH.exists():
            size_mb = DB_PATH.stat().st_size / (1024 * 1024)
            print(f"\n[OK] realtime.db: {size_mb:.2f} MB")
        else:
            print(f"\n[ERR] realtime.db: 없음")
        
        if COMPARISON_DB.exists():
            size_mb = COMPARISON_DB.stat().st_size / (1024 * 1024)
            print(f"[OK] comparison.db: {size_mb:.2f} MB")
        else:
            print(f"[ERR] comparison.db: 없음")
    
    except Exception as e:
        print(f"[ERR] 에러: {e}")


def main():
    print("\n")
    print("╔" + "═" * 60 + "╗")
    print("║" + " " * 60 + "║")
    print("║" + "  BESS 데이터 수집 상태 확인".center(51) + "║")
    print("║" + " " * 60 + "║")
    print("╚" + "═" * 60 + "╝")
    
    has_data = check_realtime_db()
    
    if has_data:
        check_12_boxes()
    
    check_comparison_db()
    check_disk_usage()
    
    print("\n" + "=" * 62)
    print("   다음 단계")
    print("=" * 62)
    print("1. realtime_engine.py 실행 (터미널 1)")
    print("2. web_app_realtime.py 실행 (터미널 2)")
    print("3. 브라우저 접속: http://localhost:5000")
    print("4. 박스 클릭 -> 한 주간 데이터 모달")
    print("\n")


if __name__ == '__main__':
    main()