"""
restart_engine.py - realtime_engine 재시작 및 데이터베이스 초기화
"""

import sqlite3
import os
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent / 'realtime_data' / 'realtime.db'
COMPARISON_DB = Path(__file__).parent / 'realtime_data' / 'comparison.db'

def reset_databases():
    """데이터베이스 초기화"""
    print("\n" + "=" * 62)
    print("  🔄 데이터베이스 초기화")
    print("=" * 62)
    
    # realtime.db 백업
    if DB_PATH.exists():
        backup_path = DB_PATH.parent / f"realtime_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        import shutil
        shutil.copy(str(DB_PATH), str(backup_path))
        print(f"\n✅ realtime.db 백업: {backup_path}")
        
        # 테이블만 유지, 과거 데이터 제거
        try:
            conn = sqlite3.connect(str(DB_PATH))
            cursor = conn.cursor()
            
            # 과거 데이터 삭제 (2025년 10월 이전)
            cursor.execute('''
                DELETE FROM realtime 
                WHERE timestamp < '2026-05-23'
            ''')
            conn.commit()
            deleted = cursor.rowcount
            print(f"✅ 과거 데이터 삭제: {deleted}개")
            
            # 남은 데이터 확인
            cursor.execute('SELECT COUNT(*) FROM realtime')
            remaining = cursor.fetchone()[0]
            print(f"✅ 남은 데이터: {remaining}개")
            
            conn.close()
        except Exception as e:
            print(f"❌ 에러: {e}")
    
    # comparison.db 초기화
    if COMPARISON_DB.exists():
        os.remove(COMPARISON_DB)
        print(f"✅ comparison.db 삭제 (재시작시 자동 생성)")

def main():
    print("\n")
    print("╔" + "═" * 60 + "╗")
    print("║" + " " * 60 + "║")
    print("║" + "  BESS 실시간 엔진 재시작 도구".center(60) + "║")
    print("║" + " " * 60 + "║")
    print("╚" + "═" * 60 + "╝")
    
    print("\n⚠️  주의: 이 작업은 과거 데이터를 삭제합니다!")
    response = input("\n계속 진행하시겠습니까? (yes/no): ").lower().strip()
    
    if response != 'yes':
        print("\n❌ 작업 취소됨")
        return
    
    reset_databases()
    
    print("\n" + "=" * 62)
    print("  📋 다음 단계 (새 터미널에서 실행)")
    print("=" * 62)
    print("\n1️⃣  realtime_engine.py 재시작")
    print("    → python realtime_engine.py")
    print("\n2️⃣  30초 후, 웹 앱 시작 (다른 터미널)")
    print("    → python web_app_realtime.py")
    print("\n3️⃣  브라우저에서 확인")
    print("    → http://localhost:5000")
    print("\n4️⃣  몇 분 후 데이터 확인")
    print("    → python check_data_windows.py")
    print("\n" + "=" * 62)
    print("✅ 준비 완료! realtime_engine.py를 시작하세요.")
    print("=" * 62 + "\n")

if __name__ == '__main__':
    main()
