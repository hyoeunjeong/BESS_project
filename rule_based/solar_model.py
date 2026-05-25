"""
한국 월별 일조량 패턴을 반영한 시간별 PV 발전량을 계산합니다.
실제 일사량 데이터가 없을 때 사용하는 물리 기반 근사 모델입니다.
"""

import numpy as np
import pandas as pd
import config


# 월별 일조 강도 계수 (한국 평균 일조량 기준)
# 봄·가을 > 여름(장마) > 겨울
_MONTHLY_FACTOR = {
    1: 0.55, 2: 0.65, 3: 0.80, 4: 0.90,
    5: 0.95, 6: 0.85, 7: 0.70, 8: 0.75,
    9: 0.85, 10: 0.80, 11: 0.65, 12: 0.55,
}


def simulate_solar_generation(days: int = 30,
                               capacity_kw: float = config.PV_CAPACITY_KW,
                               start_date: str = '2024-01-01',
                               seed: int = 42) -> pd.DataFrame:
    """
    한국 일사량 패턴 기반 시간별 PV 발전량 시뮬레이션

    모델 구조
    ---------
    발전량 = capacity × 시간계수 × 월별계수 × 날씨계수

    시간계수 : 일출(06시)~일몰(18시) 가우시안 (정오 최대)
    월별계수 : _MONTHLY_FACTOR
    날씨계수 : N(0.85, 0.15) 클리핑 (0.3 ~ 1.0)

    Parameters
    ----------
    days       : 생성 일수
    capacity_kw: PV 설비 용량 (kW)
    start_date : 시작 날짜 문자열 ('YYYY-MM-DD')
    seed       : 난수 시드

    Returns
    -------
    DataFrame : columns = ['timestamp', 'solar_kw']
    """
    np.random.seed(seed)
    timestamps = pd.date_range(start=start_date, periods=days * 24, freq='h')

    outputs = []
    for ts in timestamps:
        h     = ts.hour
        month = ts.month

        # 시간대 계수: 06~18시 사이 가우시안
        if 6 <= h <= 18:
            time_factor = np.exp(-((h - 12) ** 2) / 8.0)
        else:
            time_factor = 0.0

        # 날씨 변동성 (구름 등)
        weather = float(np.clip(np.random.normal(0.85, 0.15), 0.3, 1.0))

        power = capacity_kw * time_factor * _MONTHLY_FACTOR[month] * weather
        outputs.append(max(0.0, power))

    return pd.DataFrame({'timestamp': timestamps, 'solar_kw': outputs})


# ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    df = simulate_solar_generation(days=3)
    print("=== 태양광 발전량 샘플 (첫 24h) ===")
    print(df.head(24).to_string(index=False))
    print(f"\n일평균 발전량 : {df['solar_kw'].sum() / 3:.2f} kWh")
    print(f"최대 발전량   : {df['solar_kw'].max():.2f} kW")
