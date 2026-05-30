"""
API 키는 .env 파일에서 로드한다.
"""

import os
from pathlib import Path

# .env 파일 로드 (python-dotenv 사용)
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / '.env'
    if not env_path.exists():
        env_path = Path(__file__).parent.parent / '.env'
    load_dotenv(dotenv_path=env_path)
except ImportError:
    print("[경고] python-dotenv가 설치되지 않았습니다.")
    print("       pip install python-dotenv 로 설치하세요.")


# BESS 사양  (Rule-Based 와 동일 조건 유지)
BESS_CAPACITY_KWH = 100.0
BESS_MAX_POWER_KW = 25.0
BESS_EFFICIENCY   = 0.95
SOC_MIN           = 0.10
SOC_MAX           = 0.90
SOC_INITIAL       = 0.50
TARGET_SOC_MIN    = 0.20
TARGET_SOC_MAX    = 0.80

# 태양광 설비 사양 (서울 강동구 도시기반시설본부 실제 사례 + 한화큐셀 Q.PEAK DUO 기준)
PV_CAPACITY_KW    = 50.0     # PCS 용량 (강동구 도시기반시설본부와 동일)
PV_EFFICIENCY     = 0.21     # 모듈 효율 (한국 표준 단결정 PERC)

# 설치 조건 (한국 표준)
PV_AZIMUTH        = 180.0    # 방위각: 정남향
PV_TILT           = 30.0     # 경사각 (서울 위도 37.5° 최적값)

# 패널 물성치 (KS C 8526 표준)
PV_TEMP_COEFF     = -0.0040  # 온도계수 -0.40%/°C (25°C 이탈 시)
PV_NOCT           = 45.0     # 공칭 작동 셀온도 (°C)

# 시스템 효율
PV_PR             = 0.78     # Performance Ratio (한국 평균)
PV_INVERTER_EFF   = 0.96     # 인버터 효율 (KS 인증 기준)

# 설치 위치 (서울 강동구 도시기반시설본부)
SITE_LATITUDE     = 37.5301
SITE_LONGITUDE    = 127.1238
SITE_NAME         = '서울 강동구 도시기반시설본부 (모델 시뮬레이션)'

# 시간대별 전기요금  (한국전력 산업용 / 고압A)
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

# 시뮬레이션 설정
TIME_STEP_HOURS  = 1.0
SIMULATION_DAYS  = 30

# LSTM 하이퍼파라미터
SEQ_LEN         = 24
PRED_HORIZON    = 1

LSTM_HIDDEN     = 128
LSTM_LAYERS     = 2
DROPOUT         = 0.2
LEARNING_RATE   = 1e-3
BATCH_SIZE      = 32
EPOCHS          = 100
PATIENCE        = 10

TRAIN_RATIO = 0.70
VAL_RATIO   = 0.15

# 경로
LOAD_DATA_PATH   = 'data/load_data.csv'
SMP_DATA_PATH    = 'data/smp_data.csv'
MODEL_SAVE_DIR   = 'models/saved'
MODEL_SAVE_PATH  = 'models/saved/lstm_best.pt'
SCALER_SAVE_PATH = 'models/saved/scaler.pkl'
RESULT_DIR       = 'results'

TARGET_AVG_LOAD_KW = 50.0

# 공공 API 설정
DATA_SOURCE = 'auto'

COMMON_API_KEY = os.getenv('COMMON_API_KEY', '')

# 기상청 단기예보 API 키 (.env에 별도 키 있으면 사용, 없으면 공통키 재사용)
KMA_FORECAST_API_KEY = os.getenv('KMA_FORECAST_API_KEY', '') or COMMON_API_KEY

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

API_LOAD_URL     = 'https://api.odcloud.kr/api/15065266/v1/uddi:6ade08d2-0014-4d22-b10c-c811e3273c70'
API_SMP_URL      = 'https://apis.data.go.kr/B552115/SmpWithForecastDemand/getSmpWithForecastDemand'
API_WEATHER_URL  = 'http://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList'
API_FORECAST_URL = 'http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst'

API_START_DATE = '2025-01-01'
API_END_DATE   = '2025-12-31'

SMP_DAILY_QUOTA = 10000
API_CACHE_DIR = 'data/cache'

USE_WEATHER_FEATURES = False
WEATHER_STATION_ID   = 108

# 기상청 단기예보 격자좌표 (5km × 5km)
# 서울 강동구 도시기반시설본부 = (62, 126)
FORECAST_NX = 62
FORECAST_NY = 126

# Flask 보안 키 (.env에서 로드)
FLASK_SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'bess-monitoring-default-secret-change-me')
