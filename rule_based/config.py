"""
config.py  ─  Rule-Based BESS 시뮬레이션 설정
================================================
Deep Learning(LSTM) 프로젝트와 완전히 독립된 설정 파일입니다.
BESS/태양광 물리 파라미터는 LSTM 프로젝트와 동일하게 유지하여
공정한 비교 실험이 가능합니다.

API 키는 .env 파일에서 로드합니다.
  .env 파일이 없으면 .env.example을 복사해서 사용하세요.
"""

import os
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────
# .env 파일 로드 (python-dotenv 사용)
# ─────────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    # 프로젝트 루트의 .env 파일 로드
    env_path = Path(__file__).parent / '.env'
    if not env_path.exists():
        # rule_based 하위에서 실행 시 상위 폴더의 .env도 확인
        env_path = Path(__file__).parent.parent / '.env'
    load_dotenv(dotenv_path=env_path)
except ImportError:
    print("[경고] python-dotenv가 설치되지 않았습니다.")
    print("       pip install python-dotenv 로 설치하세요.")


# =====================================================================
# BESS 사양  (LSTM 프로젝트와 동일 조건 유지)
# =====================================================================
BESS_CAPACITY_KWH = 100.0
BESS_MAX_POWER_KW = 25.0
BESS_EFFICIENCY   = 0.95
SOC_MIN           = 0.10
SOC_MAX           = 0.90
SOC_INITIAL       = 0.50
TARGET_SOC_MIN    = 0.20
TARGET_SOC_MAX    = 0.80

# =====================================================================
# 태양광 사양
# =====================================================================
PV_CAPACITY_KW = 50.0
PV_EFFICIENCY  = 0.18

# =====================================================================
# 시간대별 전기요금  (한국전력 산업용 갑 II / 고압A)
# =====================================================================
TOU_TARIFF = {
    'off_peak' : 60.0,
    'mid_peak' : 110.0,
    'on_peak'  : 180.0,
}

def get_tariff_period(hour: int) -> str:
    if hour in [10, 11, 13, 14, 15, 16]:
        return 'on_peak'
    elif hour in [9, 12, 17, 18, 19, 20, 21, 22]:
        return 'mid_peak'
    else:
        return 'off_peak'

# =====================================================================
# 시뮬레이션 설정
# =====================================================================
TIME_STEP_HOURS  = 1.0
SIMULATION_DAYS  = 30

# =====================================================================
# 경로
# =====================================================================
LOAD_DATA_PATH = 'data/load_data.csv'
SMP_DATA_PATH  = 'data/smp_data.csv'
RESULT_DIR     = 'results'

TARGET_AVG_LOAD_KW = 50.0

# =====================================================================
# 공공 API 설정
# =====================================================================
# 데이터 소스 우선순위: 'api' (API 우선) | 'csv' (CSV 우선) | 'auto' (API → CSV → 가상)
DATA_SOURCE = 'auto'

# 공통 인증키 (공공데이터포털 + ODcloud)
# ※ .env 파일에서 로드 (보안상 코드에 직접 입력하지 마세요)
COMMON_API_KEY = os.getenv('COMMON_API_KEY', '')

# API 키가 없으면 경고 출력
if not COMMON_API_KEY:
    print("=" * 60)
    print("[경고] COMMON_API_KEY가 설정되지 않았습니다!")
    print("=" * 60)
    print("해결 방법:")
    print("1. .env.example 파일을 .env로 복사")
    print("   (PowerShell)  copy .env.example .env")
    print("2. .env 파일을 열어 COMMON_API_KEY 값 입력")
    print("   (PowerShell)  notepad .env")
    print("3. 프로그램 재시작")
    print("=" * 60)

# 각 API에서 사용할 인증키 매핑 (rule_based/api_client.py에서 사용)
API_KEYS = {
    'load': COMMON_API_KEY,
    'smp' : COMMON_API_KEY,
    'kma' : COMMON_API_KEY,
}

# API 엔드포인트 (URL은 공개 정보이므로 코드에 직접 명시)
API_LOAD_URL    = 'https://api.odcloud.kr/api/15065266/v1/uddi:6ade08d2-0014-4d22-b10c-c811e3273c70'
API_SMP_URL     = 'https://apis.data.go.kr/B552115/SmpWithForecastDemand/getSmpWithForecastDemand'
API_WEATHER_URL = 'http://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList'

# API 데이터 조회 기간
API_START_DATE = '20250101'
API_END_DATE   = '20251231'

# 기상청 관측 지점 (108=서울, 159=부산, 143=대구, 156=광주, 184=제주)
KMA_STATION_ID = 108

# API 캐싱 디렉토리
API_CACHE_DIR = 'data/api_cache'

# 데이터 소스 선택
USE_LOAD_API = True   # True: ODcloud API, False: CSV
USE_SMP_API  = True   # True: Public API, False: CSV
USE_KMA_API  = True   # True: 기상청 API, False: 시뮬레이션