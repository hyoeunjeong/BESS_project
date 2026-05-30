import numpy as np
import pandas as pd
import config

# LSTM 예측 성능 (추가 지표)
def calc_prediction_metrics(y_true: np.ndarray,
                             y_pred: np.ndarray) -> dict:
    """
    순부하 예측 정확도 지표

    Parameters
    ----------
    y_true : 실제 순부하 (kW, 역정규화)
    y_pred : LSTM 예측 순부하 (kW, 역정규화)
    """
    mae  = float(np.mean(np.abs(y_true - y_pred)))
    rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
    # MAPE (0 제거)
    mask  = y_true != 0
    mape  = float(np.mean(np.abs((y_true[mask] - y_pred[mask])
                                  / y_true[mask])) * 100) if mask.sum() > 0 else 0.0
    return {
        'mae_kw' : round(mae,  3),
        'rmse_kw': round(rmse, 3),
        'mape_pct': round(mape, 2),
    }

# 경제적 효율
def calc_economic(result_df  : pd.DataFrame,
                  baseline_df: pd.DataFrame) -> dict:
    ts = config.TIME_STEP_HOURS

    r_buy  = result_df['grid_power_kw'].clip(lower=0)
    b_buy  = baseline_df['grid_power_kw'].clip(lower=0)

    bess_cost     = (r_buy * result_df['tariff_rate']   * ts).sum()
    baseline_cost = (b_buy * baseline_df['tariff_rate'] * ts).sum()

    saving      = baseline_cost - bess_cost
    saving_rate = saving / baseline_cost * 100 if baseline_cost > 0 else 0.0

    rp = result_df[result_df['tariff_period'] == 'on_peak']
    bp = baseline_df[baseline_df['tariff_period'] == 'on_peak']
    pc_r = (rp['grid_power_kw'].clip(0) * rp['tariff_rate'] * ts).sum()
    pc_b = (bp['grid_power_kw'].clip(0) * bp['tariff_rate'] * ts).sum()
    peak_saving_rate = (pc_b - pc_r) / pc_b * 100 if pc_b > 0 else 0.0

    # 시간대별 요금 breakdown 
    breakdown = {}
    for period in ('off_peak', 'mid_peak', 'on_peak'):
        rp_ = result_df[result_df['tariff_period']   == period]
        bp_ = baseline_df[baseline_df['tariff_period'] == period]
        b_cost = (bp_['grid_power_kw'].clip(0) * bp_['tariff_rate'] * ts).sum()
        r_cost = (rp_['grid_power_kw'].clip(0) * rp_['tariff_rate'] * ts).sum()
        saved  = b_cost - r_cost
        rate   = (saved / b_cost * 100) if b_cost > 0 else 0.0
        breakdown[period] = {
            'baseline' : round(b_cost, 0),
            'bess'     : round(r_cost, 0),
            'saving'   : round(saved,  0),
            'rate_pct' : round(rate,   2),
        }

    return {
        'baseline_cost_won'    : round(baseline_cost,    0),
        'bess_cost_won'        : round(bess_cost,        0),
        'cost_saving_won'      : round(saving,           0),
        'cost_saving_rate_pct' : round(saving_rate,      2),
        'peak_saving_rate_pct' : round(peak_saving_rate, 2),
        'breakdown'            : breakdown,
    }


# 에너지 효율
def calc_energy(result_df: pd.DataFrame) -> dict:
    ts = config.TIME_STEP_HOURS

    total_load    = (result_df['load_kw']     * ts).sum()
    total_solar   = (result_df['solar_kw']    * ts).sum()
    direct_solar  = (np.minimum(result_df['solar_kw'],
                                result_df['load_kw'])  * ts).sum()
    discharge_kwh = (result_df['discharge_kw'] * ts).sum()
    charge_kwh    = (result_df['charge_kw']    * ts).sum()

    self_suff   = (direct_solar + discharge_kwh) / total_load * 100 \
                  if total_load > 0 else 0.0
    sim_hours   = len(result_df) * ts
    utilization = (charge_kwh + discharge_kwh) / (config.BESS_MAX_POWER_KW * sim_hours) * 100 \
                  if sim_hours > 0 else 0.0
    roundtrip   = discharge_kwh / charge_kwh * 100 if charge_kwh > 0 else 0.0

    # 태양광 이용률: (직접사용 + BESS충전에 사용된 태양광) / 총 발전량
    # 충전 전력의 출처는 정확히 알 수 없지만, 태양광 잉여(solar > load)가 있을 때 충전이 일어났다면 태양광 충전으로 간주
    solar_surplus = (np.maximum(result_df['solar_kw'] - result_df['load_kw'], 0) * ts).sum()
    solar_to_bess = min(solar_surplus, charge_kwh)
    solar_used    = direct_solar + solar_to_bess
    solar_util    = (solar_used / total_solar * 100) if total_solar > 0 else 0.0

    # 커튼일먼트(curtailment): 태양광 잉여 중 BESS도 못 받아 버려진 양
    curtailment = max(0.0, solar_surplus - solar_to_bess)

    # 에너지 손실량: 라운드트립 손실 (충전된 양 - 방전된 양)
    energy_loss = max(0.0, charge_kwh - discharge_kwh)

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


# 운영 안정성 및 효율성
def calc_stability(result_df: pd.DataFrame) -> dict:
    n   = len(result_df)
    ts  = config.TIME_STEP_HOURS
    soc = result_df['soc'].values

    soc_violation = int(((soc < config.SOC_MIN) | (soc > config.SOC_MAX)).sum())

    self_supply  = result_df['solar_kw'] + result_df['discharge_kw']
    supply_short = int((result_df['load_kw'] > self_supply).sum())

    thruput  = (result_df['charge_kw'] + result_df['discharge_kw']).sum() * ts
    cycles   = thruput / (2 * config.BESS_CAPACITY_KWH)

    actions     = result_df['action'].values
    transitions = sum(1 for i in range(1, n)
                      if {actions[i-1], actions[i]} == {'charge', 'discharge'})
    sim_days     = n / 24
    tpd          = transitions / sim_days if sim_days > 0 else 0.0

    # 과충·방전 방지율 (LSTM 제어기는 blocked 없음 → SOC 근접 상황으로 계산)
    risky = (
        ((result_df['soc'] >= config.SOC_MAX - 0.05) & (result_df['action'] == 'charge')).sum()
        + ((result_df['soc'] <= config.SOC_MIN + 0.05) & (result_df['action'] == 'discharge')).sum()
    )
    prevention_rate = 100.0 - (int(risky) / n * 100) if n > 0 else 100.0

    in_range     = int(((soc >= config.TARGET_SOC_MIN) & (soc <= config.TARGET_SOC_MAX)).sum())
    success_rate = in_range / n * 100 if n > 0 else 0.0

    # SOC 통계 (Rule-Based와 동일 형식)
    soc_max = float(np.max(soc)) * 100   # %
    soc_min = float(np.min(soc)) * 100
    soc_avg = float(np.mean(soc)) * 100

    # 동작 횟수 (Rule-Based와 동일 형식)
    charge_count    = int((result_df['action'] == 'charge').sum())
    discharge_count = int((result_df['action'] == 'discharge').sum())
    idle_count      = int((result_df['action'] == 'idle').sum())

    return {
        'soc_out_of_range_count'  : soc_violation,
        'supply_shortage_count'   : supply_short,
        'cycle_count'             : round(cycles,  2),
        'transitions_per_day'     : round(tpd,     2),
        'prevention_rate_pct'     : round(prevention_rate, 2),
        'control_success_rate_pct': round(success_rate,    2),
        # SOC 통계
        'soc_max_pct'             : round(soc_max, 2),
        'soc_min_pct'             : round(soc_min, 2),
        'soc_avg_pct'             : round(soc_avg, 2),
        # 동작 횟수
        'charge_count'            : charge_count,
        'discharge_count'         : discharge_count,
        'idle_count'              : idle_count,
    }

# 통합 평가
def evaluate_all(result_df  : pd.DataFrame,
                 baseline_df: pd.DataFrame,
                 y_true     : np.ndarray = None,
                 y_pred     : np.ndarray = None) -> dict:
    metrics = {
        'economic' : calc_economic(result_df, baseline_df),
        'energy'   : calc_energy(result_df),
        'stability': calc_stability(result_df),
    }
    if y_true is not None and y_pred is not None:
        metrics['prediction'] = calc_prediction_metrics(y_true, y_pred)
    return metrics


def _display_width(text: str) -> int:
    """
    한글(전각) 2칸, ASCII 1칸으로 표시 너비 계산.
    가운뎃점(·) 등 일부 punctuation은 콘솔에서 1칸 표시되므로 예외 처리.
    """
    # 1칸으로 표시되는 비ASCII 문자들 (한글 콘솔 기준)
    NARROW_NON_ASCII = {
        '·',   # 가운뎃점 (U+00B7) — 콘솔에서 1칸 표시
        '×',   # 곱하기
        '÷',   # 나누기
        '±',   # 플러스마이너스
        '°',   # 도
        '′', '″',   # 분/초 기호
        '∼',
    }
    w = 0
    for c in str(text):
        if c in NARROW_NON_ASCII:
            w += 1
        elif ord(c) > 127:
            w += 2
        else:
            w += 1
    return w


def _pad(text: str, width: int, align: str = 'left') -> str:
    """
    한글 표시 너비를 고려한 정렬 패딩

    Parameters
    ----------
    width : 목표 표시 너비 (한글 2칸 기준)
    align : 'left' | 'right'
    """
    pad = max(0, width - _display_width(text))
    return (' ' * pad + str(text)) if align == 'right' else (str(text) + ' ' * pad)


def print_report(metrics: dict, title: str = "LSTM BESS 평가 리포트"):
    sep = "=" * 62
    print(f"\n{sep}")
    print(f"  {title}")
    print(sep)

    if 'prediction' in metrics:
        print("\n[0] LSTM 예측 성능")
        print("-" * 60)
        p = metrics['prediction']
        print(f"  MAE               : {p['mae_kw']:>15.3f} kW")
        print(f"  RMSE              : {p['rmse_kw']:>15.3f} kW")
        print(f"  MAPE              : {p['mape_pct']:>15.2f} %")

    # ── [1] 경제적 효율
    print("\n[1] 경제적 효율")
    print("-" * 60)
    e = metrics['economic']
    print(f"  기준 전기요금     : {e['baseline_cost_won']:>15,.0f} 원")
    print(f"  BESS 운영 시 요금 : {e['bess_cost_won']:>15,.0f} 원")
    print(f"  요금 절감액       : {e['cost_saving_won']:>15,.0f} 원")
    print(f"  요금 절감률       : {e['cost_saving_rate_pct']:>15.2f} %")
    print(f"  피크요금 절감률   : {e['peak_saving_rate_pct']:>15.2f} %")

    # 시간대별 요금 breakdown
    if 'breakdown' in e:
        print("\n  [시간대별 요금 breakdown]")
        print("  " + "-" * 58)
        # 한글은 2칸, ASCII는 1칸으로 계산하여 정렬
        print(f"  {_pad('시간대', 10)}"
              f"{_pad('기준요금', 14, align='right')}"
              f"{_pad('BESS요금', 14, align='right')}"
              f"{_pad('절감액', 14, align='right')}"
              f"{_pad('절감률', 9, align='right')}")
        label_map = {'off_peak': '경부하', 'mid_peak': '중간부하', 'on_peak': '최대부하'}
        for period in ('off_peak', 'mid_peak', 'on_peak'):
            b = e['breakdown'][period]
            print(f"  {_pad(label_map[period], 10)}"
                  f"{b['baseline']:>12,.0f}원"
                  f"{b['bess']:>12,.0f}원"
                  f"{b['saving']:>12,.0f}원"
                  f"{b['rate_pct']:>7.2f}%")

    # ── [2] 에너지 효율
    print("\n[2] 에너지 효율")
    print("-" * 60)
    en = metrics['energy']
    print(f"  총 부하 에너지    : {en['total_load_kwh']:>15,.2f} kWh")
    if 'total_solar_kwh' in en:
        print(f"  총 태양광 발전량  : {en['total_solar_kwh']:>15,.2f} kWh")
    print(f"  태양광 직접 사용  : {en['direct_solar_kwh']:>15,.2f} kWh")
    print(f"  BESS 방전 공급    : {en['bess_discharge_kwh']:>15,.2f} kWh")
    print(f"  자립률            : {en['self_sufficiency_pct']:>15.2f} %")
    print(f"  BESS 활용률       : {en['bess_utilization_pct']:>15.2f} %")
    print(f"  라운드트립 효율   : {en['roundtrip_efficiency_pct']:>15.2f} %")
    if 'solar_utilization_pct' in en:
        print(f"  태양광 이용률     : {en['solar_utilization_pct']:>15.2f} %")
    if 'energy_loss_kwh' in en:
        print(f"  에너지 손실량     : {en['energy_loss_kwh']:>15,.2f} kWh")
    if 'curtailment_kwh' in en:
        print(f"  커튼일먼트        : {en['curtailment_kwh']:>15,.2f} kWh")

    # ── [3] 운영 안정성 및 효율성
    print("\n[3] 운영 안정성 및 효율성")
    print("-" * 60)
    s = metrics['stability']
    print(f"  SOC 범위 초과     : {s['soc_out_of_range_count']:>15} 회")
    print(f"  공급 부족 발생    : {s['supply_shortage_count']:>15} 회")
    print(f"  배터리 사이클 수  : {s['cycle_count']:>15.2f} 회")
    print(f"  일일 전환 빈도    : {s['transitions_per_day']:>15.2f} 회/일")
    print(f"  과충·방전 방지율  : {s['prevention_rate_pct']:>15.2f} %")
    print(f"  제어 성공률       : {s['control_success_rate_pct']:>15.2f} %")

    # SOC 통계
    if 'soc_max_pct' in s:
        print("\n  [SOC 통계]")
        print("  " + "-" * 58)
        print(f"  최대 SOC          : {s['soc_max_pct']:>15.2f} %")
        print(f"  최소 SOC          : {s['soc_min_pct']:>15.2f} %")
        print(f"  평균 SOC          : {s['soc_avg_pct']:>15.2f} %")

    # 동작 횟수
    if 'charge_count' in s:
        print("\n  [동작 횟수]")
        print("  " + "-" * 58)
        print(f"  충전 횟수         : {s['charge_count']:>15,} 회")
        print(f"  방전 횟수         : {s['discharge_count']:>15,} 회")
        print(f"  대기(idle) 횟수   : {s['idle_count']:>15,} 회")

    print(f"\n{sep}\n")


# 비교 출력 (Rule-Based vs LSTM)
def print_comparison(rb_metrics: dict, lstm_metrics: dict):
    """두 방식의 지표를 나란히 출력합니다 (논문 표 형식)."""
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  {'지표':<30} {'Rule-Based':>16} {'LSTM':>16}")
    print(sep)

    def _row(label, rb_val, lstm_val, fmt='{:.2f}'):
        rb_s   = fmt.format(rb_val)   if isinstance(rb_val,   float) else str(rb_val)
        lstm_s = fmt.format(lstm_val) if isinstance(lstm_val, float) else str(lstm_val)
        print(f"  {label:<30} {rb_s:>16} {lstm_s:>16}")

    e_rb   = rb_metrics['economic'];   e_dl   = lstm_metrics['economic']
    en_rb  = rb_metrics['energy'];     en_dl  = lstm_metrics['energy']
    s_rb   = rb_metrics['stability'];  s_dl   = lstm_metrics['stability']

    print("  [경제적 효율]")
    _row('요금 절감률 (%)',       e_rb['cost_saving_rate_pct'],  e_dl['cost_saving_rate_pct'])
    _row('피크 요금 절감률 (%)',  e_rb['peak_saving_rate_pct'],  e_dl['peak_saving_rate_pct'])
    print("  [에너지 효율]")
    _row('자립률 (%)',            en_rb['self_sufficiency_pct'], en_dl['self_sufficiency_pct'])
    _row('BESS 활용률 (%)',       en_rb['bess_utilization_pct'], en_dl['bess_utilization_pct'])
    _row('라운드트립 효율 (%)',   en_rb['roundtrip_efficiency_pct'], en_dl['roundtrip_efficiency_pct'])
    print("  [운영 안정성 및 효율성]")
    _row('SOC 범위 초과 (회)',    float(s_rb['soc_out_of_range_count']),
                                  float(s_dl['soc_out_of_range_count']), fmt='{:.0f}')
    _row('일일 전환 빈도 (회/일)',s_rb['transitions_per_day'],   s_dl['transitions_per_day'])
    _row('과충·방전 방지율 (%)',  s_rb['prevention_rate_pct'],   s_dl['prevention_rate_pct'])
    _row('제어 성공률 (%)',       s_rb['control_success_rate_pct'], s_dl['control_success_rate_pct'])

    if 'prediction' in lstm_metrics:
        p = lstm_metrics['prediction']
        print("  [LSTM 예측 성능]")
        _row('MAE (kW)',  p['mae_kw'],   0.0, fmt='{:.3f}')
        _row('RMSE (kW)', p['rmse_kw'], 0.0, fmt='{:.3f}')
        _row('MAPE (%)',  p['mape_pct'], 0.0, fmt='{:.2f}')
    print(sep + "\n")
