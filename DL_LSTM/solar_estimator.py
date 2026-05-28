"""
solar_estimator.py  ─  단기예보 → 태양광 발전량 추정 모듈
============================================================
기상청 단기예보 8개 변수를 받아 시간별 태양광 발전량(kW)을 추정합니다.

변환 흐름:
    [1] 청천일사량 (태양 위치 기반 이론 최대값)
    [2] 구름 보정 (SKY)
    [3] 강수 보정 (PTY, PCP)
    [4] 적설 보정 (SNO) — 패널 위 눈 덮임
    [5] 일사량 → DC 발전량
    [6] 온도 보정 (TMP, WSD, NOCT)
    [7] 시스템 효율 (PR, 인버터)
    → 최종 AC 출력 (kW)

참고 표준:
    - KS C 8526 (태양광 모듈 시험 표준)
    - 한국에너지공단 신재생에너지 백서
    - PVGIS (유럽 표준 청천일사량 모델)
"""

import numpy as np
import pandas as pd
from datetime import datetime

import config


# =====================================================================
# 보정 계수 (한국 환경 기준)
# =====================================================================

# SKY (하늘상태) → 일사량 투과율
# 한국기상학회 연구 기반 (구름 광학 두께 평균값)
SKY_TRANSMITTANCE = {
    1: 1.00,   # 맑음
    3: 0.65,   # 구름많음
    4: 0.35,   # 흐림
}

# PTY (강수형태) → 추가 감쇠율
PTY_REDUCTION = {
    0: 1.00,   # 없음
    1: 0.30,   # 비
    2: 0.20,   # 비/눈
    3: 0.25,   # 눈
    4: 0.40,   # 소나기
    5: 0.35,   # 빗방울
    6: 0.30,   # 빗방울눈날림
    7: 0.30,   # 눈날림
}

# 태양 상수 (대기권 외 일사량)
SOLAR_CONSTANT = 1361.0  # W/m²


# =====================================================================
# [1] 청천일사량 계산 (Clear-Sky Irradiance)
# =====================================================================

def solar_position(timestamp: pd.Timestamp,
                   latitude: float,
                   longitude: float) -> tuple:
    """
    태양 위치 계산 (간이 NOAA 알고리즘)

    Returns
    -------
    (elevation_deg, azimuth_deg) : 태양 고도각, 방위각 (도)
    """
    # 1년 중 일수 (day of year)
    doy = timestamp.dayofyear

    # 적위 (Declination, δ) — Cooper 공식
    decl = 23.45 * np.sin(np.deg2rad(360.0 * (284 + doy) / 365.0))
    decl_rad = np.deg2rad(decl)

    # 균시차 (Equation of Time) — 분 단위
    B = np.deg2rad(360.0 * (doy - 81) / 365.0)
    eot = 9.87 * np.sin(2 * B) - 7.53 * np.cos(B) - 1.5 * np.sin(B)

    # 표준 자오선 (한국 = 135°E, KST = UTC+9)
    standard_meridian = 135.0
    lon_correction = 4.0 * (longitude - standard_meridian)  # 분

    # 진태양시 (Solar Time)
    local_time = timestamp.hour + timestamp.minute / 60.0
    solar_time = local_time + (eot + lon_correction) / 60.0

    # 시각 (Hour Angle, ω)
    hour_angle = 15.0 * (solar_time - 12.0)
    hour_angle_rad = np.deg2rad(hour_angle)

    # 위도
    lat_rad = np.deg2rad(latitude)

    # 태양 고도각 (Elevation, α)
    sin_elev = (np.sin(lat_rad) * np.sin(decl_rad) +
                np.cos(lat_rad) * np.cos(decl_rad) * np.cos(hour_angle_rad))
    elevation = np.rad2deg(np.arcsin(np.clip(sin_elev, -1.0, 1.0)))

    # 방위각 (Azimuth) — 남쪽 0°, 서쪽 +90°
    cos_az = ((np.sin(decl_rad) - np.sin(np.deg2rad(elevation)) * np.sin(lat_rad)) /
              (np.cos(np.deg2rad(elevation)) * np.cos(lat_rad) + 1e-9))
    azimuth = np.rad2deg(np.arccos(np.clip(cos_az, -1.0, 1.0)))
    if hour_angle > 0:
        azimuth = 360.0 - azimuth

    return elevation, azimuth


def clear_sky_irradiance(timestamp: pd.Timestamp,
                         latitude: float,
                         longitude: float) -> float:
    """
    수평면 청천일사량 (Global Horizontal Irradiance, GHI) 추정

    간이 ASHRAE 모델 사용:
        GHI = I0 × sin(α) × τ_atm
    """
    elevation, _ = solar_position(timestamp, latitude, longitude)

    if elevation <= 0:
        return 0.0  # 해 아래 (밤)

    elev_rad = np.deg2rad(elevation)

    # 대기 투과율 (Air Mass 기반 간이 모델)
    air_mass = 1.0 / (np.sin(elev_rad) + 0.50572 * (elevation + 6.07995) ** -1.6364)
    atm_transmittance = 0.7 ** (air_mass ** 0.678)  # 청정 대기 기준

    ghi = SOLAR_CONSTANT * np.sin(elev_rad) * atm_transmittance
    return max(0.0, ghi)


def tilted_irradiance(ghi: float,
                      timestamp: pd.Timestamp,
                      latitude: float,
                      longitude: float,
                      tilt_deg: float,
                      azimuth_deg: float) -> float:
    """
    경사면 일사량 (POA Irradiance, Plane of Array)

    경사 패널이 받는 일사량 = GHI × 경사 변환 계수
    간이 isotropic sky model 사용
    """
    if ghi <= 0:
        return 0.0

    elevation, sun_az = solar_position(timestamp, latitude, longitude)
    if elevation <= 0:
        return 0.0

    elev_rad = np.deg2rad(elevation)
    tilt_rad = np.deg2rad(tilt_deg)
    sun_az_rad = np.deg2rad(sun_az)
    panel_az_rad = np.deg2rad(azimuth_deg)

    # 입사각 (Angle of Incidence)
    cos_aoi = (np.sin(elev_rad) * np.cos(tilt_rad) +
               np.cos(elev_rad) * np.sin(tilt_rad) * np.cos(sun_az_rad - panel_az_rad))
    cos_aoi = max(0.0, cos_aoi)

    # 직달 성분 변환
    direct_ratio = cos_aoi / max(np.sin(elev_rad), 0.01)

    # 산란 + 반사 성분 (isotropic)
    diffuse_factor = (1 + np.cos(tilt_rad)) / 2
    reflected_factor = 0.2 * (1 - np.cos(tilt_rad)) / 2  # 알베도 0.2

    # 단순화: 직달 70% / 산란 30% 가정
    poa = ghi * (0.7 * direct_ratio + 0.3 * diffuse_factor + reflected_factor)
    return max(0.0, poa)


# =====================================================================
# [2~4] 기상 보정 (SKY, PTY, SNO)
# =====================================================================

def cloud_correction(irradiance: float, sky: float) -> float:
    """SKY 코드 → 일사량 투과율 적용"""
    sky_code = int(sky) if not pd.isna(sky) else 1
    factor = SKY_TRANSMITTANCE.get(sky_code, 0.65)
    return irradiance * factor


def precipitation_correction(irradiance: float,
                             pty: float,
                             pcp: float = 0.0) -> float:
    """PTY/PCP → 강수 시 추가 감쇠"""
    pty_code = int(pty) if not pd.isna(pty) else 0
    factor = PTY_REDUCTION.get(pty_code, 1.0)

    # 강수량 추가 보정 (5mm/h 이상이면 추가 감소)
    if not pd.isna(pcp) and pcp > 5.0:
        factor *= 0.8
    if not pd.isna(pcp) and pcp > 15.0:
        factor *= 0.7

    return irradiance * factor


def snow_correction(irradiance: float, sno: float) -> float:
    """
    SNO → 패널 위 눈 덮임 보정 (차별화 포인트)

    SNO < 1cm: 영향 미미
    1~5cm: 일부 차단
    > 5cm: 거의 완전 차단
    """
    if pd.isna(sno) or sno <= 0:
        return irradiance
    if sno < 1.0:
        return irradiance * 0.85
    if sno < 5.0:
        return irradiance * 0.10
    return irradiance * 0.0


# =====================================================================
# [5~7] 발전량 변환 (POA → DC → AC)
# =====================================================================

def cell_temperature(ambient_temp: float,
                     irradiance: float,
                     wind_speed: float = 1.0,
                     noct: float = None) -> float:
    """
    셀 온도 추정 (NOCT 모델 + 풍속 보정)

    T_cell = T_ambient + (NOCT - 20) × (G / 800) × (1 / (1 + 0.1 × WSD))
    """
    if noct is None:
        noct = config.PV_NOCT

    temp = ambient_temp if not pd.isna(ambient_temp) else 25.0
    wsd = wind_speed if not pd.isna(wind_speed) and wind_speed > 0 else 1.0

    # 기본 NOCT 모델
    delta_t = (noct - 20.0) * (irradiance / 800.0)
    # 풍속 냉각 보정
    delta_t /= (1.0 + 0.1 * wsd)

    return temp + delta_t


def estimate_pv_power(forecast_row: dict,
                      latitude: float = None,
                      longitude: float = None) -> dict:
    """
    단기예보 1시간치 → 태양광 발전량(kW) 변환

    Parameters
    ----------
    forecast_row : dict
        {'timestamp': pd.Timestamp,
         'SKY': float, 'PTY': float, 'TMP': float,
         'REH': float, 'WSD': float, 'POP': float,
         'PCP': float, 'SNO': float}

    Returns
    -------
    dict : 단계별 중간값 + 최종 발전량
        {'timestamp', 'ghi', 'poa', 'irr_after_sky', 'irr_after_pty',
         'irr_after_sno', 'cell_temp', 'dc_power', 'ac_power'}
    """
    if latitude is None:
        latitude = config.SITE_LATITUDE
    if longitude is None:
        longitude = config.SITE_LONGITUDE

    ts = forecast_row['timestamp']

    # [1] 청천일사량 (수평면 → 경사면)
    ghi = clear_sky_irradiance(ts, latitude, longitude)
    poa = tilted_irradiance(ghi, ts, latitude, longitude,
                            config.PV_TILT, config.PV_AZIMUTH)

    # [2] 구름 보정
    irr_sky = cloud_correction(poa, forecast_row.get('SKY', 1))

    # [3] 강수 보정
    irr_pty = precipitation_correction(irr_sky,
                                       forecast_row.get('PTY', 0),
                                       forecast_row.get('PCP', 0))

    # [4] 적설 보정
    irr_final = snow_correction(irr_pty, forecast_row.get('SNO', 0))

    # [5] 일사량 → DC 발전량 (W → kW)
    # DC_kW = (G / 1000 W/m²) × PV_CAPACITY × (1 + tc × ΔT)
    if irr_final <= 1.0:
        return {
            'timestamp': ts,
            'ghi': round(ghi, 1),
            'poa': round(poa, 1),
            'irr_after_sky': round(irr_sky, 1),
            'irr_after_pty': round(irr_pty, 1),
            'irr_after_sno': round(irr_final, 1),
            'cell_temp': np.nan,
            'dc_power': 0.0,
            'ac_power': 0.0,
        }

    # [6] 온도 보정
    t_cell = cell_temperature(
        forecast_row.get('TMP', 25.0),
        irr_final,
        forecast_row.get('WSD', 1.0),
    )
    temp_loss = 1.0 + config.PV_TEMP_COEFF * (t_cell - 25.0)

    dc_power = (irr_final / 1000.0) * config.PV_CAPACITY_KW * temp_loss

    # [7] 시스템 효율 (PR + 인버터)
    ac_power = dc_power * config.PV_PR * config.PV_INVERTER_EFF

    # 출력 한계 (PCS 용량 초과 방지)
    ac_power = min(ac_power, config.PV_CAPACITY_KW)
    ac_power = max(0.0, ac_power)

    return {
        'timestamp': ts,
        'ghi': round(ghi, 1),
        'poa': round(poa, 1),
        'irr_after_sky': round(irr_sky, 1),
        'irr_after_pty': round(irr_pty, 1),
        'irr_after_sno': round(irr_final, 1),
        'cell_temp': round(t_cell, 1),
        'dc_power': round(dc_power, 2),
        'ac_power': round(ac_power, 2),
    }


# =====================================================================
# 메인 함수: 단기예보 DataFrame → 태양광 DataFrame
# =====================================================================

def forecast_to_solar(forecast_df: pd.DataFrame,
                      latitude: float = None,
                      longitude: float = None,
                      detail: bool = False) -> pd.DataFrame:
    """
    단기예보 DataFrame → 태양광 발전량 DataFrame

    Parameters
    ----------
    forecast_df : pd.DataFrame
        fetch_short_forecast() 출력
        columns = [timestamp, SKY, PTY, TMP, REH, WSD, POP, PCP, SNO]
    detail : bool
        True면 중간값(GHI, POA, 셀온도 등) 모두 포함

    Returns
    -------
    pd.DataFrame :
        간단: [timestamp, solar_kw]
        상세: [timestamp, ghi, poa, ..., dc_power, ac_power]
    """
    if forecast_df is None or len(forecast_df) == 0:
        return pd.DataFrame(columns=['timestamp', 'solar_kw'])

    records = []
    for _, row in forecast_df.iterrows():
        result = estimate_pv_power(row.to_dict(), latitude, longitude)
        records.append(result)

    df = pd.DataFrame(records)

    if detail:
        return df

    return df[['timestamp', 'ac_power']].rename(columns={'ac_power': 'solar_kw'})


# =====================================================================
# 단독 테스트
# =====================================================================
if __name__ == '__main__':
    print("=" * 70)
    print("  solar_estimator.py 단독 테스트")
    print(f"  설치 위치: {config.SITE_NAME}")
    print(f"  위도/경도: {config.SITE_LATITUDE}, {config.SITE_LONGITUDE}")
    print(f"  PV 사양:   {config.PV_CAPACITY_KW}kW, 효율 {config.PV_EFFICIENCY*100:.0f}%, "
          f"경사 {config.PV_TILT}°, 방위 {config.PV_AZIMUTH}°")
    print("=" * 70)

    # 단기예보 가져오기
    from api_client import fetch_short_forecast
    forecast_df = fetch_short_forecast()

    # 태양광 추정 (상세 모드)
    solar_df = forecast_to_solar(forecast_df, detail=True)

    print()
    print(f"입력: 단기예보 {len(forecast_df)}행")
    print(f"출력: 태양광 추정 {len(solar_df)}행")
    print()

    # 첫 24시간 시간별 출력
    print("=== 첫 24시간 발전량 (낮/밤 패턴 확인) ===")
    display_cols = ['timestamp', 'ghi', 'poa', 'irr_after_sky', 'cell_temp', 'ac_power']
    print(solar_df[display_cols].head(24).to_string(index=False))

    print()
    print("=== 일별 통계 ===")
    solar_df['date'] = pd.to_datetime(solar_df['timestamp']).dt.date
    daily = solar_df.groupby('date').agg(
        피크_W제곱미터=('poa', 'max'),
        피크_kW=('ac_power', 'max'),
        일발전량_kWh=('ac_power', 'sum'),
    )
    print(daily.to_string())

    print()
    print(f"=== 전체 평균 ===")
    print(f"  낮 시간 평균 일사량 (POA): {solar_df[solar_df['poa']>0]['poa'].mean():.0f} W/m²")
    print(f"  낮 시간 평균 발전량:        {solar_df[solar_df['ac_power']>0]['ac_power'].mean():.1f} kW")
    print(f"  최대 발전량:                {solar_df['ac_power'].max():.1f} kW")
    print(f"  PV 용량 대비:               {solar_df['ac_power'].max()/config.PV_CAPACITY_KW*100:.0f}%")
