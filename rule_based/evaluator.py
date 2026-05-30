import numpy as np
import pandas as pd
import config


# [1] 경제적 효율
def calc_economic(result_df: pd.DataFrame,
                  baseline_df: pd.DataFrame) -> dict:
    """전기요금 기반 경제적 효율 지표"""
    ts = config.TIME_STEP_HOURS

    r_buy = result_df['grid_power_kw'].clip(lower=0)
    b_buy = baseline_df['grid_power_kw'].clip(lower=0)

    bess_cost     = (r_buy * result_df['tariff_rate']   * ts).sum()
    baseline_cost = (b_buy * baseline_df['tariff_rate'] * ts).sum()

    saving      = baseline_cost - bess_cost
    saving_rate = saving / baseline_cost * 100 if baseline_cost > 0 else 0.0

    tou_breakdown = {}
    for period in ['off_peak', 'mid_peak', 'on_peak']:
        r_mask = result_df['tariff_period'] == period
        b_mask = baseline_df['tariff_period'] == period

        b_cost = (baseline_df.loc[b_mask, 'grid_power_kw'].clip(0)
                  * baseline_df.loc[b_mask, 'tariff_rate'] * ts).sum()
        r_cost = (result_df.loc[r_mask, 'grid_power_kw'].clip(0)
                  * result_df.loc[r_mask, 'tariff_rate'] * ts).sum()
        period_saving = b_cost - r_cost
        period_rate   = period_saving / b_cost * 100 if b_cost > 0 else 0.0

        tou_breakdown[period] = {
            'baseline_won' : round(b_cost,        0),
            'bess_won'     : round(r_cost,        0),
            'saving_won'   : round(period_saving, 0),
            'saving_pct'   : round(period_rate,   2),
        }

    peak_saving_rate = tou_breakdown['on_peak']['saving_pct']

    return {
        'baseline_cost_won'    : round(baseline_cost, 0),
        'bess_cost_won'        : round(bess_cost,     0),
        'cost_saving_won'      : round(saving,        0),
        'cost_saving_rate_pct' : round(saving_rate,   2),
        'peak_saving_rate_pct' : round(peak_saving_rate, 2),
        'tou_breakdown'        : tou_breakdown,
    }


# [2] 에너지 효율
def calc_energy(result_df: pd.DataFrame) -> dict:
    """에너지 흐름 기반 효율 지표"""
    ts = config.TIME_STEP_HOURS

    total_load    = (result_df['load_kw']  * ts).sum()
    total_solar   = (result_df['solar_kw'] * ts).sum()
    direct_solar  = (np.minimum(result_df['solar_kw'],
                                result_df['load_kw']) * ts).sum()
    discharge_kwh = (result_df['discharge_kw'] * ts).sum()
    charge_kwh    = (result_df['charge_kw']    * ts).sum()

    self_suff = (direct_solar + discharge_kwh) / total_load * 100 \
                if total_load > 0 else 0.0

    sim_hours    = len(result_df) * ts
    max_thruput  = config.BESS_MAX_POWER_KW * sim_hours
    utilization  = (charge_kwh + discharge_kwh) / max_thruput * 100 \
                   if max_thruput > 0 else 0.0

    roundtrip = discharge_kwh / charge_kwh * 100 if charge_kwh > 0 else 0.0

    surplus       = (result_df['solar_kw'] - result_df['load_kw']).clip(lower=0)
    solar_to_bess = (np.minimum(surplus, result_df['charge_kw']) * ts).sum()
    solar_used    = direct_solar + solar_to_bess
    solar_util    = solar_used / total_solar * 100 if total_solar > 0 else 0.0

    curtailment = ((surplus - result_df['charge_kw']).clip(lower=0) * ts).sum()
    energy_loss = charge_kwh - discharge_kwh

    return {
        'total_load_kwh'          : round(total_load,    2),
        'total_solar_kwh'         : round(total_solar,   2),
        'direct_solar_kwh'        : round(direct_solar,  2),
        'bess_discharge_kwh'      : round(discharge_kwh, 2),
        'self_sufficiency_pct'    : round(self_suff,     2),
        'bess_utilization_pct'    : round(utilization,   2),
        'roundtrip_efficiency_pct': round(roundtrip,     2),
        'solar_utilization_pct'   : round(solar_util,    2),
        'energy_loss_kwh'         : round(energy_loss,   2),
        'curtailment_kwh'         : round(curtailment,   2),
    }


# [3] 운영 안정성 및 효율성
def calc_stability(result_df: pd.DataFrame) -> dict:
    """운영 안정성 및 효율성 지표"""
    n   = len(result_df)
    ts  = config.TIME_STEP_HOURS
    soc = result_df['soc'].values

    soc_violation = int(((soc < config.SOC_MIN) | (soc > config.SOC_MAX)).sum())

    self_supply  = result_df['solar_kw'] + result_df['discharge_kw']
    supply_short = int((result_df['load_kw'] > self_supply).sum())

    thruput = (result_df['charge_kw'] + result_df['discharge_kw']).sum() * ts
    cycles  = thruput / (2 * config.BESS_CAPACITY_KWH)

    actions     = result_df['action'].values
    transitions = sum(1 for i in range(1, n)
                      if {actions[i-1], actions[i]} == {'charge', 'discharge'})
    sim_days      = n / 24
    trans_per_day = transitions / sim_days if sim_days > 0 else 0.0

    blocked_cnt = int(result_df['blocked'].notna().sum())
    risky = (
        ((result_df['soc'] >= config.SOC_MAX - 0.05) & (result_df['action'] == 'charge')).sum()
        + ((result_df['soc'] <= config.SOC_MIN + 0.05) & (result_df['action'] == 'discharge')).sum()
    )
    total_risk      = blocked_cnt + int(risky)
    prevention_rate = blocked_cnt / total_risk * 100 if total_risk > 0 else 100.0

    in_range     = int(((soc >= config.TARGET_SOC_MIN) & (soc <= config.TARGET_SOC_MAX)).sum())
    success_rate = in_range / n * 100 if n > 0 else 0.0

    soc_max = float(soc.max())
    soc_min = float(soc.min())
    soc_avg = float(soc.mean())

    charge_cnt    = int((result_df['action'] == 'charge').sum())
    discharge_cnt = int((result_df['action'] == 'discharge').sum())
    idle_cnt      = int((result_df['action'] == 'idle').sum())

    return {
        'soc_out_of_range_count'  : soc_violation,
        'supply_shortage_count'   : supply_short,
        'cycle_count'             : round(cycles,         2),
        'transitions_per_day'     : round(trans_per_day,  2),
        'prevention_rate_pct'     : round(prevention_rate, 2),
        'control_success_rate_pct': round(success_rate,   2),
        'soc_max_pct'             : round(soc_max * 100,  2),
        'soc_min_pct'             : round(soc_min * 100,  2),
        'soc_avg_pct'             : round(soc_avg * 100,  2),
        'charge_count'            : charge_cnt,
        'discharge_count'         : discharge_cnt,
        'idle_count'              : idle_cnt,
    }


# 통합 평가
def evaluate_all(result_df: pd.DataFrame,
                 baseline_df: pd.DataFrame) -> dict:
    """세 카테고리 평가 지표를 한 번에 계산"""
    return {
        'economic' : calc_economic(result_df, baseline_df),
        'energy'   : calc_energy(result_df),
        'stability': calc_stability(result_df),
    }


# 출력 헬퍼 함수: 라벨과 숫자/단위 정렬 맞춤
def _row(label: str, value, unit: str = '', value_width: int = 14,
        decimals: int = 0, use_comma: bool = True):
    """
    한 줄 출력 (라벨 정렬 + 숫자 오른쪽 정렬 + 단위 일직선)
    
    Parameters
    ----------
    label       : 라벨 (한글 포함 가능)
    value       : 숫자
    unit        : 단위 (원, %, kWh, 회 등)
    value_width : 숫자 출력 너비 (기본 14)
    decimals    : 소수점 자릿수 (0 = 정수, 2 = 소수점 2자리)
    use_comma   : 천 단위 콤마 사용 여부
    """
    # 한글 라벨 정렬 (라벨 영역 18칸 차지)
    label_visual_width = sum(2 if ord(c) > 127 else 1 for c in label)
    label_padding = 18 - label_visual_width
    label_str = label + ' ' * label_padding

    # 숫자 포맷
    if isinstance(value, str):
        value_str = value.rjust(value_width)
    elif use_comma and decimals == 0:
        value_str = f"{value:>{value_width},.0f}"
    elif use_comma:
        value_str = f"{value:>{value_width},.{decimals}f}"
    else:
        value_str = f"{value:>{value_width}.{decimals}f}"

    # 출력: 라벨 : 숫자 단위
    print(f"  {label_str}: {value_str} {unit}")

# 평가 결과 출력 (정렬 맞춤판)
def print_report(metrics: dict, title: str = "BESS Rule-Based 제어 평가 리포트"):
    """평가 결과 콘솔 출력"""
    sep = "=" * 62
    print(f"\n{sep}")
    print(f"  {title}")
    print(sep)

    # ── [1] 경제적 효율 
    print("\n[1] 경제적 효율")
    print("-" * 62)
    e = metrics['economic']
    _row("기준 전기요금",     e['baseline_cost_won'],     "원", decimals=0)
    _row("BESS 운영 시 요금", e['bess_cost_won'],         "원", decimals=0)
    _row("요금 절감액",       e['cost_saving_won'],       "원", decimals=0)
    _row("요금 절감률",       e['cost_saving_rate_pct'],  "%", decimals=2)
    _row("피크요금 절감률",   e['peak_saving_rate_pct'],  "%", decimals=2)

    print("\n  [시간대별 요금 breakdown]")
    print("  " + "-" * 66)
    # 표 헤더 (각 열 14칸씩)
    print(f"  {'시간대':<8}{'기준요금':>7}{'BESS요금':>13}"
          f"{'절감액':>12}{'절감률':>9}")
    print("  " + "-" * 66)
    labels = {'off_peak': '경부하', 'mid_peak': '중간부하', 'on_peak': '최대부하'}
    for period, label in labels.items():
        b = e['tou_breakdown'][period]
        # 한글 라벨 정렬 (8칸)
        label_pad = 8 - sum(2 if ord(c) > 127 else 1 for c in label)
        label_str = label + ' ' * label_pad
        # 숫자 12칸 + 단위 2칸 = 14칸으로 맞춤
        print(f"  {label_str}"
              f"{b['baseline_won']:>12,.0f}원 "
              f"{b['bess_won']:>12,.0f}원 "
              f"{b['saving_won']:>12,.0f}원 "
              f"{b['saving_pct']:>10.2f}%")

    # ── [2] 에너지 효율 
    print("\n[2] 에너지 효율")
    print("-" * 62)
    en = metrics['energy']
    _row("총 부하 에너지",   en['total_load_kwh'],           "kWh", decimals=2)
    _row("총 태양광 발전량", en['total_solar_kwh'],          "kWh", decimals=2)
    _row("태양광 직접사용",  en['direct_solar_kwh'],         "kWh", decimals=2)
    _row("BESS 방전 공급",   en['bess_discharge_kwh'],       "kWh", decimals=2)
    _row("자립률",           en['self_sufficiency_pct'],     "%",   decimals=2)
    _row("BESS 활용률",      en['bess_utilization_pct'],     "%",   decimals=2)
    _row("라운드트립 효율",  en['roundtrip_efficiency_pct'], "%",   decimals=2)
    _row("태양광 이용률",    en['solar_utilization_pct'],    "%",   decimals=2)
    _row("에너지 손실량",    en['energy_loss_kwh'],          "kWh", decimals=2)
    _row("커튼일먼트",       en['curtailment_kwh'],          "kWh", decimals=2)

    # ── [3] 운영 안정성 
    print("\n[3] 운영 안정성 및 효율성")
    print("-" * 62)
    s = metrics['stability']
    _row("SOC 범위 초과",    s['soc_out_of_range_count'],   "회",   decimals=0)
    _row("공급 부족 발생",   s['supply_shortage_count'],    "회",   decimals=0)
    _row("배터리 사이클 수", s['cycle_count'],              "회",   decimals=2)
    _row("일일 전환 빈도",   s['transitions_per_day'],      "회/일", decimals=2)
    _row("과충·방전 방지율", s['prevention_rate_pct'],      "%",    decimals=2)
    _row("제어 성공률",      s['control_success_rate_pct'], "%",    decimals=2)

    print("\n  [SOC 통계]")
    print("  " + "-" * 60)
    _row("최대 SOC",         s['soc_max_pct'], "%", decimals=2)
    _row("최소 SOC",         s['soc_min_pct'], "%", decimals=2)
    _row("평균 SOC",         s['soc_avg_pct'], "%", decimals=2)

    print("\n  [동작 횟수]")
    print("  " + "-" * 60)
    _row("충전 횟수",        s['charge_count'],    "회", decimals=0)
    _row("방전 횟수",        s['discharge_count'], "회", decimals=0)
    _row("대기(idle) 횟수",  s['idle_count'],      "회", decimals=0)

    print(f"\n{sep}\n")
