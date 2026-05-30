import sqlite3
import os
from pathlib import Path
from datetime import datetime
import sys

DB_PATH = Path(__file__).parent / 'realtime_data' / 'realtime.db'

# config.py에서 설정값 import (DL_LSTM 폴더 우선)
sys.path.insert(0, str(Path(__file__).parent / 'DL_LSTM'))
try:
    import config
    SOC_MIN = config.SOC_MIN
    SOC_MAX = config.SOC_MAX
    SOC_INITIAL = config.SOC_INITIAL
except ImportError:
    SOC_MIN = 0.10
    SOC_MAX = 0.90
    SOC_INITIAL = 0.50


def pad_kr(text, width, align='left'):
    text_w = sum(2 if ord(c) > 127 else 1 for c in str(text))
    pad = width - text_w
    if pad <= 0:
        return str(text)
    if align == 'right':
        return ' ' * pad + str(text)
    return str(text) + ' ' * pad


def get_tariff_period(hour):
    if hour in [10, 11, 13, 14, 15, 16]:
        return 'on_peak'
    elif hour in [9, 12, 17, 18, 19, 20, 21, 22]:
        return 'mid_peak'
    else:
        return 'off_peak'


def print_header(title):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def diagnose_current_state():
    """1. 현재 BESS 상태"""
    print_header("1. 현재 BESS 상태")
    
    if not DB_PATH.exists():
        print("[ERR] realtime.db 없음")
        return None
    
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # 최신 데이터
    cursor.execute('''
        SELECT timestamp, hour, load_kw, solar_kw, soc, bess_power_kw,
               grid_power_kw, tariff_period, action
        FROM realtime ORDER BY id DESC LIMIT 1
    ''')
    row = cursor.fetchone()
    
    if not row:
        print("[ERR] 데이터 없음")
        conn.close()
        return None
    
    ts, hour, load, solar, soc, bess_pwr, grid, tariff, action = row
    soc_pct = float(soc) * 100
    net_load = float(load) - float(solar)
    now_hour = datetime.now().hour
    
    print(f"\n   {pad_kr('마지막 데이터', 16)}: {ts}")
    print(f"   {pad_kr('현재 시간', 16)}: {datetime.now().strftime('%H:%M:%S')} ({now_hour}시)")
    print(f"   {pad_kr('데이터 시간대', 16)}: {tariff} ({hour}시)")
    print(f"   {pad_kr('현재 시간대', 16)}: {get_tariff_period(now_hour)} ({now_hour}시)")
    
    print(f"\n   {pad_kr('부하', 16)}: {load:.2f} kW")
    print(f"   {pad_kr('태양광', 16)}: {solar:.2f} kW")
    print(f"   {pad_kr('순부하', 16)}: {net_load:.2f} kW (부하 - 태양광)")
    print(f"   {pad_kr('계통', 16)}: {grid:.2f} kW")
    
    print(f"\n   {pad_kr('SOC', 16)}: {soc_pct:.1f} %  (최저 {SOC_MIN*100:.0f}% / 최대 {SOC_MAX*100:.0f}%)")
    print(f"   {pad_kr('BESS 출력', 16)}: {bess_pwr:.2f} kW")
    print(f"   {pad_kr('현재 동작', 16)}: {action}")
    
    # SOC 상태 평가
    print(f"\n   [SOC 상태 평가]")
    if soc_pct <= SOC_MIN * 100 + 1:
        print(f"   [!] SOC가 최저치({SOC_MIN*100:.0f}%)에 도달 - 더 이상 방전 불가")
    elif soc_pct >= SOC_MAX * 100 - 1:
        print(f"   [!] SOC가 최대치({SOC_MAX*100:.0f}%)에 도달 - 더 이상 충전 불가")
    elif soc_pct < 25:
        print(f"   [!] SOC가 낮음 (25% 미만) - 충전이 필요한 상태")
    elif soc_pct > 75:
        print(f"   [OK] SOC가 충분히 높음")
    else:
        print(f"   [OK] SOC가 정상 범위")
    
    conn.close()
    
    return {
        'soc': soc_pct,
        'load': load,
        'solar': solar,
        'net_load': net_load,
        'bess_power': bess_pwr,
        'action': action,
        'tariff': tariff,
        'hour': hour,
    }


def diagnose_control_logic(state):
    """2. 제어 로직 흐름 분석"""
    print_header("2. 제어 로직 흐름 분석 (왜 그렇게 동작하는지)")
    
    if state is None:
        return
    
    soc = state['soc'] / 100
    net_load = state['net_load']
    tariff = state['tariff']
    
    # 피크 임계값을 알 수 없으므로 추정 (현재 부하의 1.2배라고 가정)
    estimated_threshold = state['load'] * 1.2
    
    print(f"\n   현재 상황:")
    print(f"   - 순부하        : {net_load:.2f} kW")
    print(f"   - SOC           : {soc*100:.1f} %  (범위: {SOC_MIN*100:.0f}~{SOC_MAX*100:.0f}%)")
    print(f"   - 시간대        : {tariff}")
    print(f"   - 추정 피크 임계값: ~{estimated_threshold:.2f} kW (현재 부하 x 1.2 가정)")
    
    print(f"\n   각 제어 조건 평가:")
    print(f"   {'-' * 66}")
    
    # P1: 태양광 잉여 -> 충전
    p1_cond1 = net_load < 0
    p1_cond2 = soc < SOC_MAX
    p1_active = p1_cond1 and p1_cond2
    print(f"   P1 (태양광 잉여 충전):")
    print(f"      [{'O' if p1_cond1 else 'X'}] 순부하 < 0       (실제 {net_load:.2f} < 0)")
    print(f"      [{'O' if p1_cond2 else 'X'}] SOC < {SOC_MAX*100:.0f}%        (실제 {soc*100:.1f} < {SOC_MAX*100:.0f})")
    print(f"      => {'발동' if p1_active else '발동 안 함'}")
    
    # P2: 피크 부하 -> 방전
    p2_cond1 = net_load > estimated_threshold
    p2_cond2 = soc > SOC_MIN
    p2_active = (not p1_active) and p2_cond1 and p2_cond2
    print(f"\n   P2 (피크 부하 방전):")
    print(f"      [{'O' if p2_cond1 else 'X'}] 순부하 > 임계값  (실제 {net_load:.2f} > {estimated_threshold:.2f})")
    print(f"      [{'O' if p2_cond2 else 'X'}] SOC > {SOC_MIN*100:.0f}%        (실제 {soc*100:.1f} > {SOC_MIN*100:.0f})")
    print(f"      => {'발동' if p2_active else '발동 안 함'}")
    
    # P3: 경부하 시간대 -> 충전
    p3_cond1 = tariff == 'off_peak'
    p3_cond2 = soc < SOC_MAX
    p3_active = (not p1_active) and (not p2_active) and p3_cond1 and p3_cond2
    print(f"\n   P3 (경부하 시간대 충전):")
    print(f"      [{'O' if p3_cond1 else 'X'}] 경부하 시간      (실제 {tariff})")
    print(f"      [{'O' if p3_cond2 else 'X'}] SOC < {SOC_MAX*100:.0f}%        (실제 {soc*100:.1f} < {SOC_MAX*100:.0f})")
    print(f"      => {'발동' if p3_active else '발동 안 함'}")
    
    # P4: 최대부하 시간대 -> 방전
    p4_cond1 = tariff == 'on_peak'
    p4_cond2 = soc > SOC_MIN
    p4_active = (not p1_active) and (not p2_active) and (not p3_active) and p4_cond1 and p4_cond2
    print(f"\n   P4 (최대부하 시간대 방전):")
    print(f"      [{'O' if p4_cond1 else 'X'}] 최대부하 시간    (실제 {tariff})")
    print(f"      [{'O' if p4_cond2 else 'X'}] SOC > {SOC_MIN*100:.0f}%        (실제 {soc*100:.1f} > {SOC_MIN*100:.0f})")
    print(f"      => {'발동' if p4_active else '발동 안 함'}")
    
    print(f"\n   {'-' * 66}")
    
    # 최종 진단
    print(f"\n   [최종 진단]")
    if p1_active:
        print(f"   - P1 발동 예상 -> 충전 동작")
    elif p2_active:
        print(f"   - P2 발동 예상 -> 방전 동작")
    elif p3_active:
        print(f"   - P3 발동 예상 -> 충전 동작")
    elif p4_active:
        print(f"   - P4 발동 예상 -> 방전 동작")
    else:
        print(f"   - 모든 조건 미충족 -> idle (대기)")
        print(f"\n   [원인 분석]")
        
        if soc <= SOC_MIN + 0.001:
            print(f"   *** SOC가 최저치에 도달해서 방전 불가 ***")
            if tariff != 'off_peak':
                print(f"   *** 현재 {tariff} 시간대라 P3 충전도 안 됨 ***")
                print(f"   *** off_peak (23~8시) 까지 기다려야 충전 시작됨 ***")
        elif soc >= SOC_MAX - 0.001:
            print(f"   *** SOC가 최대치에 도달해서 충전 불가 ***")
        elif tariff == 'mid_peak':
            print(f"   *** mid_peak는 P3, P4 어느 쪽도 발동 안 함 ***")
            print(f"   *** 그래서 P1 (태양광 잉여)나 P2 (피크) 조건이 안 맞으면 idle ***")


def diagnose_24h_pattern():
    """3. 최근 24시간 동작 패턴"""
    print_header("3. 최근 24시간 동작 분포")
    
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT action, COUNT(*) as cnt
        FROM realtime
        WHERE timestamp >= datetime('now', 'localtime', '-24 hours')
        GROUP BY action
        ORDER BY cnt DESC
    ''')
    rows = cursor.fetchall()
    
    total = sum(row[1] for row in rows)
    
    if total == 0:
        print("\n   [!] 최근 24시간 데이터 없음")
        conn.close()
        return
    
    print(f"\n   {pad_kr('동작', 12)} {pad_kr('횟수', 8, 'right')}  {pad_kr('비율', 8, 'right')}  분포")
    print(f"   {'-' * 66}")
    
    for action, cnt in rows:
        pct = cnt / total * 100
        bar = '#' * min(int(pct / 2), 30)
        print(f"   {pad_kr(action or 'unknown', 12)} {pad_kr(f'{cnt:,}', 8, 'right')}  {pad_kr(f'{pct:.1f}%', 8, 'right')}  {bar}")
    
    print(f"   {'-' * 66}")
    print(f"   {pad_kr('합계', 12)} {pad_kr(f'{total:,}', 8, 'right')}")
    
    # 시간대별 동작 분포
    print(f"\n   [시간대별 동작 빈도]")
    cursor.execute('''
        SELECT tariff_period, action, COUNT(*) as cnt
        FROM realtime
        WHERE timestamp >= datetime('now', 'localtime', '-24 hours')
        GROUP BY tariff_period, action
        ORDER BY tariff_period, cnt DESC
    ''')
    rows = cursor.fetchall()
    
    print(f"\n   {pad_kr('시간대', 12)} {pad_kr('동작', 12)} {pad_kr('횟수', 8, 'right')}")
    print(f"   {'-' * 40}")
    for tariff, action, cnt in rows:
        print(f"   {pad_kr(tariff, 12)} {pad_kr(action or 'unknown', 12)} {pad_kr(f'{cnt:,}', 8, 'right')}")
    
    conn.close()


def diagnose_soc_history():
    """4. SOC 변화 추이"""
    print_header("4. SOC 변화 추이 (최근 24시간)")
    
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # 1시간 단위 SOC 변화
    cursor.execute('''
        SELECT 
            strftime('%H:00', timestamp) as hour_label,
            MIN(soc) * 100 as min_soc,
            AVG(soc) * 100 as avg_soc,
            MAX(soc) * 100 as max_soc,
            tariff_period
        FROM realtime
        WHERE timestamp >= datetime('now', 'localtime', '-24 hours')
        GROUP BY strftime('%H', timestamp), tariff_period
        ORDER BY timestamp
    ''')
    rows = cursor.fetchall()
    
    if not rows:
        print("\n   [!] 데이터 없음")
        conn.close()
        return
    
    print(f"\n   {pad_kr('시간', 8)} {pad_kr('시간대', 10)} {pad_kr('최소', 8, 'right')} {pad_kr('평균', 8, 'right')} {pad_kr('최대', 8, 'right')}  분포 (0~100%)")
    print(f"   {'-' * 66}")
    
    for hour, min_soc, avg_soc, max_soc, tariff in rows:
        # 평균 SOC 위치 표시
        bar_len = 30
        pos = int(avg_soc / 100 * bar_len)
        bar = ' ' * pos + '|' + ' ' * (bar_len - pos - 1)
        bar = '[' + bar + ']'
        
        tariff_str = tariff or '?'
        print(f"   {pad_kr(hour, 8)} {pad_kr(tariff_str, 10)} {pad_kr(f'{min_soc:.1f}', 8, 'right')} {pad_kr(f'{avg_soc:.1f}', 8, 'right')} {pad_kr(f'{max_soc:.1f}', 8, 'right')}  {bar}")
    
    # SOC 변화량 분석
    print(f"\n   [SOC 변화량 분석]")
    cursor.execute('''
        SELECT 
            MIN(soc) * 100 as overall_min,
            AVG(soc) * 100 as overall_avg,
            MAX(soc) * 100 as overall_max
        FROM realtime
        WHERE timestamp >= datetime('now', 'localtime', '-24 hours')
    ''')
    overall = cursor.fetchone()
    
    if overall:
        print(f"   - 최저 SOC: {overall[0]:.1f} %")
        print(f"   - 평균 SOC: {overall[1]:.1f} %")
        print(f"   - 최대 SOC: {overall[2]:.1f} %")
        soc_range = overall[2] - overall[0]
        if soc_range < 5:
            print(f"   [!] SOC 변화 범위가 너무 작음 ({soc_range:.1f}%) - BESS 활용 부족")
        elif soc_range > 50:
            print(f"   [OK] SOC가 활발히 변동 중 ({soc_range:.1f}%)")
        else:
            print(f"   [OK] SOC 변화 범위: {soc_range:.1f}%")
    
    conn.close()


def diagnose_recent_actions():
    """5. 최근 10개 동작 상세"""
    print_header("5. 최근 10개 동작 상세 (시간 역순)")
    
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT timestamp, hour, load_kw, solar_kw, soc, bess_power_kw,
               tariff_period, action
        FROM realtime ORDER BY id DESC LIMIT 10
    ''')
    rows = cursor.fetchall()
    
    if not rows:
        print("\n   [!] 데이터 없음")
        conn.close()
        return
    
    print(f"\n   {pad_kr('시간', 20)} {pad_kr('부하', 8, 'right')} {pad_kr('태양', 8, 'right')} {pad_kr('SOC', 7, 'right')} {pad_kr('BESS', 8, 'right')} {pad_kr('시간대', 10)} {pad_kr('동작', 10)}")
    print(f"   {'-' * 90}")
    
    for row in rows:
        ts, hour, load, solar, soc, bess, tariff, action = row
        ts_short = ts.split('.')[0] if '.' in ts else ts
        print(f"   {pad_kr(ts_short, 20)} {pad_kr(f'{load:.1f}', 8, 'right')} {pad_kr(f'{solar:.1f}', 8, 'right')} {pad_kr(f'{soc*100:.1f}', 7, 'right')} {pad_kr(f'{bess:.1f}', 8, 'right')} {pad_kr(tariff or '?', 10)} {pad_kr(action or '?', 10)}")
    
    conn.close()


def recommendation(state):
    """6. 권장 조치"""
    print_header("6. 권장 조치")
    
    if state is None:
        print("\n   데이터 부족으로 분석 불가")
        return
    
    soc = state['soc']
    tariff = state['tariff']
    action = state['action']
    now_hour = datetime.now().hour
    current_tariff = get_tariff_period(now_hour)
    
    print()
    
    if soc <= 11 and action == 'idle':
        print("   [진단] SOC가 최저치에 갇혀 있고 idle 상태")
        print()
        print("   [해결책]")
        
        if current_tariff == 'off_peak':
            print("   * 현재 off_peak 시간대인데 충전 안 함")
            print("     -> bess_controller.py의 P3 로직 점검 필요")
            print("     -> realtime_engine.py가 controller를 제대로 호출하는지 확인")
        else:
            print(f"   * 현재 {current_tariff} 시간대라 P3 충전 안 됨")
            print(f"   * 다음 off_peak 시간대(23~8시)까지 기다리거나")
            print(f"   * 비상 충전(P0) 로직 추가 권장:")
            print()
            print("     bess_controller.py에 다음 추가:")
            print("     ```")
            print("     # P0: SOC가 20% 미만이면 시간대 무관 비상 충전")
            print("     if self.soc < self.soc_min + 0.10:")
            print("         max_c = self._max_charge(time_step)")
            print("         charge_pw = min(self.max_power * 0.5, max_c)")
            print("         if charge_pw > 0:")
            print("             bess_pwr = -charge_pw")
            print("             action = 'charge'")
            print("     ```")
    elif soc >= 89 and action == 'idle':
        print("   [진단] SOC가 최대치에 갇혀 있고 idle 상태")
        print("   * on_peak 시간대까지 기다리면 P4 방전 시작")
    elif action == 'idle':
        print("   [진단] 정상 범위의 SOC에서 idle 상태")
        print(f"   * 현재 {tariff} 시간대 + 순부하 보통")
        print("   * 모든 제어 조건 불충족으로 정상 idle")
    else:
        print(f"   [진단] BESS 정상 동작 중 ({action})")


def main():
    print("\n")
    print("=" * 70)
    print("                  BESS 동작 상태 진단 도구")
    print("=" * 70)
    
    state = diagnose_current_state()
    diagnose_control_logic(state)
    diagnose_24h_pattern()
    diagnose_soc_history()
    diagnose_recent_actions()
    recommendation(state)
    
    print("\n")


if __name__ == '__main__':
    main()
