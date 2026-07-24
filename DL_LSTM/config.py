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

# ─────────────────────────────────────────────────────────────────────
# [주의] 아래 물성치·시스템 효율 상수는 solar_estimator.py(기상청 단기예보
#        기반 물리 모델) 전용이며, 본 논문의 시뮬레이션 경로에서는
#        사용되지 않는다.
#        논문 실험의 태양광은 data_loader._load_solar_from_rb_cache() 의
#            solar_kw = clip(icsr * PV_CAPACITY_KW * 0.2778, 0, PV_CAPACITY_KW)
#        간이 선형 모델로 산출된다 (논문 식 (3)).
#        → 성능비·인버터 효율·온도 보정 미반영. 논문 2.4.2 (2) 참조.
# ─────────────────────────────────────────────────────────────────────
PV_AZIMUTH        = 180.0    # 방위각: 정남향
PV_TILT           = 30.0     # 경사각 (서울 위도 37.5° 최적값)
PV_TEMP_COEFF     = -0.0040  # 온도계수 -0.40%/°C (25°C 이탈 시)
PV_NOCT           = 45.0     # 공칭 작동 셀온도 (°C)
PV_PR             = 0.78     # Performance Ratio (한국 평균)
PV_INVERTER_EFF   = 0.96     # 인버터 효율 (KS 인증 기준)

# 설치 위치 (서울 강동구 도시기반시설본부)
SITE_LATITUDE     = 37.5301
SITE_LONGITUDE    = 127.1238
SITE_NAME         = '서울 강동구 도시기반시설본부 (모델 시뮬레이션)'


# =====================================================================
#  전력요금 체계 (TOU)  ─  산업용(갑)Ⅱ 고압A 선택Ⅱ (계약전력 4~300kW)
# ---------------------------------------------------------------------
#  TARIFF_MODE (기본값 'seasonal')
#    'seasonal' : 2025년 시행 요금표 시간대 + 계절별 단가. (기본/권장)
#    '2026'     : 2026년 개편 시간대(여름·봄가을 최대부하 저녁 이동). 민감도 분석용.
#    'paper'    : 가상 요금 시나리오(60/110/180). 실존하지 않으며 재현/비교 기준 아님.
#
#  요일·공휴일 규정(한전): 일요일·공휴일은 전 시간 경부하, 토요일 최대부하는 중간부하로 계량.
# =====================================================================
TARIFF_MODE = 'seasonal'

# 계약전력(kW) — 기본요금 요금적용전력의 30% 하한 산정용. 무제어 피크 ~71kW → 100kW 가정.
CONTRACT_POWER_KW = 100.0

# [§4] 수요전력 저감 목표 계수. target = 학습구간 무제어 요금적용전력(P_CAP) × DEMAND_SHAVE.
#   튜닝 파라미터('10% 저감' 사전 설계). 0.85/0.90/0.95 민감도 분석 대상.
DEMAND_SHAVE = 0.90

# ── 시간대 구분표 (hour → on/mid, 그 외 off) ─────────────────────
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
BASE_CHARGE_WON_PER_KW = 7470   # 기본요금 (원/kW·월)

# 2025년 관공서 공휴일 (임시공휴일 제외 — 요금 규정상 임시공휴일은 평일 취급)
#   ※ 사용자 확정 목록. 2025-06-03 대선일은 임시공휴일이라 제외.
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
    """요일·공휴일 보정 전 기본 시간대(모드별 시간대표)."""
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
    """(시간, 월, 요일, 날짜) → 요금 시간대 구분.

    weekday: 0=월 … 5=토, 6=일.  date: 'YYYY-MM-DD' 또는 datetime.date.
    한전 규정: 일요일·공휴일 전 시간 경부하, 토요일 최대부하→중간부하.
    """
    season = get_season(month)
    period = _raw_period(hour, season)
    if TARIFF_MODE == 'paper':
        return period   # 가상 시나리오 — 요일/공휴일 규칙 미적용

    d = str(date)[:10] if date is not None else None
    if (d is not None and d in HOLIDAYS_2025) or weekday == 6:
        return 'off_peak'          # 공휴일·일요일 → 전 시간 경부하
    if weekday == 5 and period == 'on_peak':
        return 'mid_peak'          # 토요일 최대부하 → 중간부하
    return period


def get_tariff_rate(hour: int, month: int = 6,
                    weekday: int = None, date=None) -> float:
    """(시간, 월, 요일, 날짜) → 전력량요금 단가 (원/kWh)"""
    period = get_tariff_period(hour, month, weekday, date)
    if TARIFF_MODE == 'paper':
        return PAPER_TARIFF[period]
    return SEASONAL_TARIFF[get_season(month)][period]


# 하위 호환: 기존 코드가 config.TOU_TARIFF 를 참조하는 경우 대비
TOU_TARIFF = SEASONAL_TARIFF


# 시뮬레이션 설정
TIME_STEP_HOURS  = 1.0
SIMULATION_DAYS  = 30

# LSTM / GRU 하이퍼파라미터
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

# 재현성 (논문 표 2.3): random / numpy / torch 공통 시드
RANDOM_SEED = 42

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

# 실측 데이터 강제(재현성). True 면 가상 데이터·합성 태양광 폴백을 예외로 차단하고,
# 태양광은 반드시 기상청 실측 캐시(_load_solar_from_rb_cache)로만 로드한다.
STRICT_DATA = True

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


# ── 자기 점검 (python config.py 로 실행) ──────────────────────────
if __name__ == '__main__':
    print(f"TARIFF_MODE = {TARIFF_MODE}\n")
    label = {'off_peak': '경부하', 'mid_peak': '중간부하', 'on_peak': '최대부하'}
    for mo, season in ((6, '여름'), (4, '봄가을'), (12, '겨울')):
        buckets = {'off_peak': [], 'mid_peak': [], 'on_peak': []}
        for h in range(24):
            buckets[get_tariff_period(h, mo)].append(h)
        print(f"[{season} (m={mo})]")
        for p in ('off_peak', 'mid_peak', 'on_peak'):
            hrs = buckets[p]
            print(f"  {label[p]:5s} {get_tariff_rate(hrs[0], mo):6.1f}원/kWh  "
                  f"{len(hrs):2d}h  {hrs}")
        print()
        if TARIFF_MODE == 'paper':
            print("  (연중 단일 구조이므로 계절 무관 — 이하 생략)")
            break
