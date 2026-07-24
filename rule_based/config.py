import os
from pathlib import Path

# .env 파일 로드 (python-dotenv 사용)
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


# BESS 사양  (LSTM 프로젝트와 동일 조건 유지)
BESS_CAPACITY_KWH = 100.0
BESS_MAX_POWER_KW = 25.0
BESS_EFFICIENCY   = 0.95
SOC_MIN           = 0.10
SOC_MAX           = 0.90
SOC_INITIAL       = 0.50
TARGET_SOC_MIN    = 0.20
TARGET_SOC_MAX    = 0.80

# 태양광 사양
PV_CAPACITY_KW = 50.0
PV_EFFICIENCY  = 0.18

# =====================================================================
#  전력요금 체계 (TOU)  ─  산업용(갑)Ⅱ 고압A 선택Ⅱ (계약전력 4~300kW)
#  TARIFF_MODE: 'seasonal'(2025 시행, 기본) / '2026'(개편, 민감도) / 'paper'(가상)
#  요일·공휴일: 일요일·공휴일 전 시간 경부하, 토요일 최대부하→중간부하로 계량.
# =====================================================================
TARIFF_MODE = 'seasonal'

# 계약전력(kW) — 기본요금 요금적용전력의 30% 하한 산정용. 무제어 피크 ~71kW → 100kW 가정.
CONTRACT_POWER_KW = 100.0

# 2025년 시행: 여름/봄가을 최대 11·13~17, 중간 8~10·12·18~21 / 겨울 최대 9~11·16~18, 중간 8·12~15·19~21
_BANDS_2025 = {
    'summer_spring': {'on': {11, 13, 14, 15, 16, 17},
                      'mid': {8, 9, 10, 12, 18, 19, 20, 21}},
    'winter':        {'on': {9, 10, 11, 16, 17, 18},
                      'mid': {8, 12, 13, 14, 15, 19, 20, 21}},
}
# 2026년 개편: 여름·봄가을 최대 15~20(저녁), 중간 8~14·21 / 겨울은 2025와 동일
_BANDS_2026 = {
    'summer_spring': {'on': {15, 16, 17, 18, 19, 20},
                      'mid': {8, 9, 10, 11, 12, 13, 14, 21}},
    'winter':        {'on': {9, 10, 11, 16, 17, 18},
                      'mid': {8, 12, 13, 14, 15, 19, 20, 21}},
}
# 가상 요금(paper) — 실존하지 않는 시나리오
PAPER_ON_PEAK_HOURS  = {10, 11, 13, 14, 15, 16}
PAPER_MID_PEAK_HOURS = {9, 12, 17, 18, 19, 20, 21, 22}
PAPER_TARIFF = {'off_peak': 60.0, 'mid_peak': 110.0, 'on_peak': 180.0}

# 계절별 단가 (원/kWh) — seasonal·2026 공용
SEASONAL_TARIFF = {
    'summer': {'off_peak': 90.8, 'mid_peak': 116.6, 'on_peak': 150.1},
    'spring': {'off_peak': 90.8, 'mid_peak':  95.6, 'on_peak': 114.8},
    'winter': {'off_peak': 98.2, 'mid_peak': 115.1, 'on_peak': 144.5},
}
BASE_CHARGE_WON_PER_KW = 7470       # 기본요금 (원/kW·월)

# 2025년 관공서 공휴일 (임시공휴일 제외). ※ 사용자 확정 목록. 2025-06-03 대선일 제외.
HOLIDAYS_2025 = {
    '2025-01-01',
    '2025-01-28', '2025-01-29', '2025-01-30',
    '2025-03-03',
    '2025-05-05', '2025-05-06',
    '2025-06-06',
    '2025-08-15',
    '2025-10-03',
    '2025-10-05', '2025-10-06', '2025-10-07', '2025-10-08',
    '2025-10-09',
    '2025-12-25',
}

def get_season(month: int) -> str:
    if month in (6, 7, 8):       return 'summer'
    if month in (11, 12, 1, 2):  return 'winter'
    return 'spring'

def _raw_period(hour: int, season: str) -> str:
    if TARIFF_MODE == 'paper':
        if hour in PAPER_ON_PEAK_HOURS:  return 'on_peak'
        if hour in PAPER_MID_PEAK_HOURS: return 'mid_peak'
        return 'off_peak'
    bands = _BANDS_2026 if TARIFF_MODE == '2026' else _BANDS_2025
    key = 'winter' if season == 'winter' else 'summer_spring'
    if hour in bands[key]['on']:  return 'on_peak'
    if hour in bands[key]['mid']: return 'mid_peak'
    return 'off_peak'

def get_tariff_period(hour: int, month: int = 6,
                      weekday: int = None, date=None) -> str:
    """한전 규정: 일요일·공휴일 전 시간 경부하, 토요일 최대부하→중간부하."""
    season = get_season(month)
    period = _raw_period(hour, season)
    if TARIFF_MODE == 'paper':
        return period
    d = str(date)[:10] if date is not None else None
    if (d is not None and d in HOLIDAYS_2025) or weekday == 6:
        return 'off_peak'
    if weekday == 5 and period == 'on_peak':
        return 'mid_peak'
    return period

def get_tariff_rate(hour: int, month: int = 6,
                    weekday: int = None, date=None) -> float:
    period = get_tariff_period(hour, month, weekday, date)
    if TARIFF_MODE == 'paper':
        return PAPER_TARIFF[period]
    return SEASONAL_TARIFF[get_season(month)][period]

# 하위 호환: 기존 코드가 config.TOU_TARIFF 를 참조하는 경우 대비
TOU_TARIFF = SEASONAL_TARIFF

# 시뮬레이션 설정
TIME_STEP_HOURS  = 1.0
SIMULATION_DAYS  = 30

# 경로
LOAD_DATA_PATH = 'data/load_data.csv'
SMP_DATA_PATH  = 'data/smp_data.csv'
RESULT_DIR     = 'results'

TARGET_AVG_LOAD_KW = 50.0

# 공공 API 설정
# 데이터 소스 우선순위: 'api' (API 우선) | 'csv' (CSV 우선) | 'auto' (API → CSV → 가상)
DATA_SOURCE = 'auto'

# 실측 데이터 강제(재현성). True 면 태양광 시뮬레이션 등 합성 폴백을 예외로 차단한다.
STRICT_DATA = True

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