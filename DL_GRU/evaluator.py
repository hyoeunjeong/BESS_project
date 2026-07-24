"""
BESS 운영 성능 평가 모듈 (정정본)

원본 대비 변경 사항
─────────────────────────────────────────────────────────────────────
[추가] 논문 표에 실렸으나 코드에 없던 지표
  · 최대수요전력 저감률 / 계통 측 최대전력      (논문 식 35, 표 2.7)
  · BESS 기인 계통 역송                        (표 2.7, 표 2.10)
  · 재생에너지 기원 자립률                      (논문 식 37, 표 2.7)
  · 시간대별 충·방전량                          (표 2.9)
  · 제어 계층별 발동 횟수                       (표 2.11)
  · 정규화 MAE (상대 오차)                      (논문 식 24, 표 2.6)
  · 기본요금 영향 정량화                        (2.8.2.4 논의의 정량화)
  · SOC 분포 (80% 초과 / 20% 미만 비율)         (2.6.1, 2.8.4)

[정정] 계산 오류
  · 태양광 이용률·커튼일먼트가 항상 100% / 0 으로 고정되던 문제
      solar_to_bess = min(총잉여, 총충전량)  ← 계통 충전분까지 포함됨
    → 시점별 min(잉여_t, 충전_t) 누적으로 수정
  · SOC 운용범위 이탈 판정에 부동소수점 허용오차(1e-6) 도입
    → 0.9000000001 같은 수치 잔차가 위반으로 계수되던 문제

[명칭] 논문 용어와 일치시킴 (기존 키는 하위 호환으로 병기)
  · control_success_rate_pct → soc_in_band_rate_pct (SOC 권장구간 체류율)
─────────────────────────────────────────────────────────────────────
"""

import numpy as np
import pandas as pd
import config

# SOC 판정 허용오차 (부동소수점 잔차 흡수)
_SOC_TOL = 1e-6


# =====================================================================
# [0] 예측 성능
# =====================================================================
def calc_prediction_metrics(y_true: np.ndarray,
                            y_pred: np.ndarray) -> dict:
    """
    순부하 예측 정확도

    [주의] y_true 는 학습 타깃과 동일한 정의를 써야 한다.
           본 연구의 타깃은 0 을 하한으로 클리핑된 순부하이므로
               y_true = max(load - solar, 0)
           클리핑하지 않은 값을 넘기면 표 2.6 과 다른 수치가 나온다.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)

    mae  = float(np.mean(np.abs(y_true - y_pred)))
    rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))

    # 논문 식 (24): 구간 평균 순부하로 정규화한 MAE (= 상대 오차)
    y_bar = float(np.mean(y_true))
    nmae  = mae / y_bar * 100 if y_bar > 0 else 0.0

    # MAPE — 순부하가 0 에 근접하는 시점에서 발산하므로 참고용
    mask = np.abs(y_true) > 1e-6
    mape = float(np.mean(np.abs((y_true[mask] - y_pred[mask])
                                / y_true[mask])) * 100) if mask.sum() else 0.0

    return {
        'mae_kw'      : round(mae,  3),
        'rmse_kw'     : round(rmse, 3),
        'nmae_pct'    : round(nmae, 2),   # 논문 표 2.6 '상대 오차'
        'mean_true_kw': round(y_bar, 2),
        'mape_pct'    : round(mape, 2),   # 참고용
        'n_samples'   : int(len(y_true)),
    }


# =====================================================================
# [1] 경제적 효율
# =====================================================================
def calc_economic(result_df: pd.DataFrame,
                  baseline_df: pd.DataFrame) -> dict:
    ts = config.TIME_STEP_HOURS

    r_buy = result_df['grid_power_kw'].clip(lower=0)
    b_buy = baseline_df['grid_power_kw'].clip(lower=0)

    bess_cost     = float((r_buy * result_df['tariff_rate']   * ts).sum())
    baseline_cost = float((b_buy * baseline_df['tariff_rate'] * ts).sum())

    saving      = baseline_cost - bess_cost
    saving_rate = saving / baseline_cost * 100 if baseline_cost > 0 else 0.0

    # ── 시간대별 요금 breakdown ──────────────────────────────────
    breakdown = {}
    for period in ('off_peak', 'mid_peak', 'on_peak'):
        rp = result_df[result_df['tariff_period'] == period]
        bp = baseline_df[baseline_df['tariff_period'] == period]
        b_cost = float((bp['grid_power_kw'].clip(0) * bp['tariff_rate'] * ts).sum())
        r_cost = float((rp['grid_power_kw'].clip(0) * rp['tariff_rate'] * ts).sum())
        saved  = b_cost - r_cost
        breakdown[period] = {
            'baseline': round(b_cost, 0),
            'bess'    : round(r_cost, 0),
            'saving'  : round(saved,  0),
            'rate_pct': round(saved / b_cost * 100 if b_cost > 0 else 0.0, 2),
        }

    peak_saving_rate = breakdown['on_peak']['rate_pct']

    # ── [추가] 최대수요전력 (논문 식 35) ─────────────────────────
    #   기본요금은 요금 시간대와 무관하게 순시 최대전력으로 산정되므로
    #   전력량요금 절감률과 별도로 평가해야 한다.
    peak_r = float(result_df['grid_power_kw'].max())
    peak_b = float(baseline_df['grid_power_kw'].max())
    peak_demand_reduction = (peak_b - peak_r) / peak_b * 100 if peak_b > 0 else 0.0

    # ── [추가] 기본요금 영향 정량화 (2.8.2.4) ────────────────────
    #   한국전력 고압 요금의 '요금적용전력'은 최근 12개월 최대수요전력을
    #   기준으로 하므로, 연간 기본요금 ≈ 연최대수요전력 × 단가 × 12개월.
    #   월별 최대치를 각각 적용하는 대안도 함께 산출한다.
    rate_kw = getattr(config, 'BASE_CHARGE_WON_PER_KW', 0.0)
    n_months = _month_count(result_df)

    base_annual_r = peak_r * rate_kw * n_months
    base_annual_b = peak_b * rate_kw * n_months
    base_delta    = base_annual_r - base_annual_b       # 양수 = 기본요금 증가

    m_peak_r = _monthly_peak_sum(result_df)
    m_peak_b = _monthly_peak_sum(baseline_df)
    base_monthly_delta = (m_peak_r - m_peak_b) * rate_kw

    # 전력량요금 절감액에서 기본요금 증가분을 차감한 순 절감액
    net_saving = saving - base_delta

    return {
        'baseline_cost_won'   : round(baseline_cost, 0),
        'bess_cost_won'       : round(bess_cost,     0),
        'cost_saving_won'     : round(saving,        0),
        'cost_saving_rate_pct': round(saving_rate,   2),
        'peak_saving_rate_pct': peak_saving_rate,
        'breakdown'           : breakdown,
        # 최대수요전력
        'baseline_peak_demand_kw'  : round(peak_b, 2),
        'peak_demand_kw'           : round(peak_r, 2),
        'peak_demand_reduction_pct': round(peak_demand_reduction, 2),
        # 기본요금
        'base_charge_rate_won_per_kw': rate_kw,
        'months_evaluated'           : n_months,
        'base_charge_baseline_won'   : round(base_annual_b, 0),
        'base_charge_bess_won'       : round(base_annual_r, 0),
        'base_charge_delta_won'      : round(base_delta, 0),
        'base_charge_delta_monthly_won': round(base_monthly_delta, 0),
        'net_saving_won'             : round(net_saving, 0),
        'net_saving_rate_pct'        : round(
            net_saving / (baseline_cost + base_annual_b) * 100
            if (baseline_cost + base_annual_b) > 0 else 0.0, 2),
    }


def _month_count(df: pd.DataFrame) -> int:
    if 'timestamp' not in df.columns:
        return 12
    t = pd.to_datetime(df['timestamp'])
    return int(t.dt.to_period('M').nunique())


def _monthly_peak_sum(df: pd.DataFrame) -> float:
    """월별 최대수요전력의 합 (월별 기본요금 산정 방식)"""
    if 'timestamp' not in df.columns:
        return float(df['grid_power_kw'].max()) * 12
    t = pd.to_datetime(df['timestamp'])
    return float(df.assign(_m=t.dt.to_period('M'))
                   .groupby('_m')['grid_power_kw'].max().sum())


# =====================================================================
# [2] 에너지 효율
# =====================================================================
def calc_energy(result_df: pd.DataFrame) -> dict:
    ts = config.TIME_STEP_HOURS

    load   = result_df['load_kw'].values
    solar  = result_df['solar_kw'].values
    chg    = result_df['charge_kw'].values
    dis    = result_df['discharge_kw'].values
    grid   = result_df['grid_power_kw'].values

    total_load   = float(load.sum()  * ts)
    total_solar  = float(solar.sum() * ts)
    direct_solar = float(np.minimum(solar, load).sum() * ts)
    charge_kwh    = float(chg.sum() * ts)
    discharge_kwh = float(dis.sum() * ts)

    # 자립률 (논문 식 36) — 계통 구매 후 재방전분을 포함하므로
    #                        엄밀한 재생에너지 자립도가 아님
    self_suff = (direct_solar + discharge_kwh) / total_load * 100 \
                if total_load > 0 else 0.0

    # [추가] 재생에너지 기원 자립률 (논문 식 37)
    #        태양광과 부하만의 함수 → 제어 방식과 무관하게 동일한 값
    re_self_suff = direct_solar / total_load * 100 if total_load > 0 else 0.0

    sim_hours   = len(result_df) * ts
    utilization = (charge_kwh + discharge_kwh) \
                  / (config.BESS_MAX_POWER_KW * sim_hours) * 100 \
                  if sim_hours > 0 else 0.0
    roundtrip = discharge_kwh / charge_kwh * 100 if charge_kwh > 0 else 0.0

    # ── [정정] 태양광 이용률 / 커튼일먼트 ────────────────────────
    #   기존:  solar_to_bess = min(총잉여, 총충전량)
    #          → 총충전량에 계통 구매 충전이 포함되어 항상 총잉여보다 커지므로
    #            이용률 100%, 커튼일먼트 0 으로 고정되었다.
    #   정정:  시점별 min(잉여_t, 충전_t) 를 누적한다.
    surplus_t     = np.maximum(solar - load, 0.0)
    solar_to_bess = float(np.minimum(surplus_t, chg).sum() * ts)
    surplus_total = float(surplus_t.sum() * ts)
    curtailment   = max(0.0, surplus_total - solar_to_bess)
    solar_util    = (direct_solar + solar_to_bess) / total_solar * 100 \
                    if total_solar > 0 else 0.0

    # ── [추가] BESS 기인 계통 역송 (표 2.7) ──────────────────────
    #   grid < 0 인 시점의 역송량 중 BESS 방전에 기인한 몫.
    #   태양광 자체 잉여로 인한 역송(= 무제어에서도 발생)과 구분한다.
    export_t   = np.maximum(-grid, 0.0)
    bess_export = float(np.minimum(dis, export_t).sum() * ts)

    energy_loss = max(0.0, charge_kwh - discharge_kwh)

    # ── [추가] 시간대별 충·방전량 (표 2.9) ───────────────────────
    by_period = {}
    for p in ('off_peak', 'mid_peak', 'on_peak'):
        m = result_df['tariff_period'] == p
        by_period[p] = {
            'charge_kwh'   : round(float(result_df.loc[m, 'charge_kw'].sum()    * ts), 1),
            'discharge_kwh': round(float(result_df.loc[m, 'discharge_kw'].sum() * ts), 1),
        }

    return {
        'total_load_kwh'          : round(total_load,    2),
        'total_solar_kwh'         : round(total_solar,   2),
        'direct_solar_kwh'        : round(direct_solar,  2),
        'bess_charge_kwh'         : round(charge_kwh,    2),
        'bess_discharge_kwh'      : round(discharge_kwh, 2),
        'total_throughput_kwh'    : round(charge_kwh + discharge_kwh, 2),
        'self_sufficiency_pct'    : round(self_suff,     2),
        're_self_sufficiency_pct' : round(re_self_suff,  2),
        'bess_utilization_pct'    : round(utilization,   2),
        'roundtrip_efficiency_pct': round(roundtrip,     2),
        'solar_surplus_kwh'       : round(surplus_total, 2),
        'solar_to_bess_kwh'       : round(solar_to_bess, 2),
        'solar_utilization_pct'   : round(solar_util,    2),
        'curtailment_kwh'         : round(curtailment,   2),
        'bess_export_kwh'         : round(bess_export,   2),
        'energy_loss_kwh'         : round(energy_loss,   2),
        'energy_by_period'        : by_period,
    }


# =====================================================================
# [3] 운영 안정성
# =====================================================================
def calc_stability(result_df: pd.DataFrame) -> dict:
    n   = len(result_df)
    ts  = config.TIME_STEP_HOURS
    soc = result_df['soc'].values

    # ── [정정] 허용오차 도입 ─────────────────────────────────────
    overcharge    = int((soc > config.SOC_MAX + _SOC_TOL).sum())
    overdischarge = int((soc < config.SOC_MIN - _SOC_TOL).sum())
    soc_violation = overcharge + overdischarge

    thruput = float((result_df['charge_kw'] + result_df['discharge_kw']).sum() * ts)
    cycles  = thruput / (2 * config.BESS_CAPACITY_KWH)

    actions     = result_df['action'].values
    transitions = sum(1 for i in range(1, n)
                      if {actions[i-1], actions[i]} == {'charge', 'discharge'})
    sim_days = n / 24
    tpd      = transitions / sim_days if sim_days > 0 else 0.0

    # SOC 권장구간 체류율 (논문 식 39)
    in_band = int(((soc >= config.TARGET_SOC_MIN - _SOC_TOL) &
                   (soc <= config.TARGET_SOC_MAX + _SOC_TOL)).sum())
    band_rate = in_band / n * 100 if n > 0 else 0.0

    # [추가] SOC 분포 — 논문 2.6.1 / 2.8.4 의 양극단 운용 근거
    above = int((soc > config.TARGET_SOC_MAX + _SOC_TOL).sum()) / n * 100 if n else 0.0
    below = int((soc < config.TARGET_SOC_MIN - _SOC_TOL).sum()) / n * 100 if n else 0.0

    out = {
        'overcharge_count'        : overcharge,
        'overdischarge_count'     : overdischarge,
        'soc_out_of_range_count'  : soc_violation,
        'cycle_count'             : round(cycles, 2),
        'transitions_per_day'     : round(tpd,    2),
        'soc_in_band_rate_pct'    : round(band_rate, 2),
        'control_success_rate_pct': round(band_rate, 2),   # 하위 호환 별칭
        'soc_above_band_pct'      : round(above, 2),
        'soc_below_band_pct'      : round(below, 2),
        'soc_max_pct'             : round(float(soc.max()) * 100, 2),
        'soc_min_pct'             : round(float(soc.min()) * 100, 2),
        'soc_avg_pct'             : round(float(soc.mean()) * 100, 2),
        'charge_count'            : int((result_df['action'] == 'charge').sum()),
        'discharge_count'         : int((result_df['action'] == 'discharge').sum()),
        'idle_count'              : int((result_df['action'] == 'idle').sum()),
    }

    # ── [추가] 제어 계층별 발동 횟수 (표 2.11) ───────────────────
    if 'reason' in result_df.columns:
        rc = result_df['reason'].value_counts().to_dict()
        out['reason_counts'] = {str(k): int(v) for k, v in rc.items()}
        pred_driven = int(rc.get('solar_surplus', 0) + rc.get('peak_cut', 0))
        out['prediction_driven_count']   = pred_driven
        out['prediction_driven_pct']     = round(pred_driven / n * 100, 2) if n else 0.0

    return out


# =====================================================================
# 통합 평가
# =====================================================================
def evaluate_all(result_df: pd.DataFrame,
                 baseline_df: pd.DataFrame,
                 y_true: np.ndarray = None,
                 y_pred: np.ndarray = None) -> dict:
    _assert_aligned(result_df, baseline_df)
    metrics = {
        'economic' : calc_economic(result_df, baseline_df),
        'energy'   : calc_energy(result_df),
        'stability': calc_stability(result_df),
    }
    if y_true is not None and y_pred is not None:
        metrics['prediction'] = calc_prediction_metrics(y_true, y_pred)
    return metrics


def _assert_aligned(a: pd.DataFrame, b: pd.DataFrame):
    """결과와 기준 시나리오가 동일 시점 집합인지 확인"""
    if len(a) != len(b):
        raise ValueError(
            f"결과({len(a)}행)와 기준({len(b)}행)의 시점 수가 다릅니다. "
            "동일 구간 비교가 성립하지 않습니다.")
    if 'timestamp' in a.columns and 'timestamp' in b.columns:
        if not pd.to_datetime(a['timestamp']).equals(pd.to_datetime(b['timestamp'])):
            raise ValueError("결과와 기준의 timestamp 계열이 일치하지 않습니다.")


# =====================================================================
# 출력 유틸 (기존 함수 유지)
# =====================================================================
def _display_width(text: str) -> int:
    NARROW_NON_ASCII = {'·', '×', '÷', '±', '°', '′', '″', '∼'}
    w = 0
    for c in str(text):
        if c in NARROW_NON_ASCII: w += 1
        elif ord(c) > 127:        w += 2
        else:                     w += 1
    return w


def _pad(text: str, width: int, align: str = 'left') -> str:
    pad = max(0, width - _display_width(text))
    return (' ' * pad + str(text)) if align == 'right' else (str(text) + ' ' * pad)


def print_report(metrics: dict, title: str = "BESS 평가 리포트"):
    sep = "=" * 66
    print(f"\n{sep}\n  {title}\n{sep}")

    if 'prediction' in metrics:
        p = metrics['prediction']
        print("\n[0] 순부하 예측 성능")
        print("-" * 64)
        print(f"  평가 시점 수      : {p['n_samples']:>15,} 개")
        print(f"  MAE               : {p['mae_kw']:>15.3f} kW")
        print(f"  RMSE              : {p['rmse_kw']:>15.3f} kW")
        print(f"  상대 오차 (정규화 MAE) : {p['nmae_pct']:>10.2f} %"
              f"   (평균 순부하 {p['mean_true_kw']:.2f} kW)")
        print(f"  MAPE (참고)       : {p['mape_pct']:>15.2f} %")

    e = metrics['economic']
    print("\n[1] 경제적 효율")
    print("-" * 64)
    print(f"  기준 전력량요금   : {e['baseline_cost_won']:>15,.0f} 원")
    print(f"  BESS 운영 시 요금 : {e['bess_cost_won']:>15,.0f} 원")
    print(f"  요금 절감액       : {e['cost_saving_won']:>15,.0f} 원")
    print(f"  요금 절감률       : {e['cost_saving_rate_pct']:>15.2f} %")
    print(f"  최대부하 절감률   : {e['peak_saving_rate_pct']:>15.2f} %")

    print("\n  [시간대별 요금 절감률]")
    lm = {'off_peak': '경부하', 'mid_peak': '중간부하', 'on_peak': '최대부하'}
    for p_ in ('off_peak', 'mid_peak', 'on_peak'):
        b = e['breakdown'][p_]
        print(f"  {_pad(lm[p_], 12)}{b['rate_pct']:>10.2f} %"
              f"   (절감 {b['saving']:>12,.0f} 원)")

    print("\n  [최대수요전력 · 기본요금]")
    print(f"  무제어 최대전력   : {e['baseline_peak_demand_kw']:>15.2f} kW")
    print(f"  BESS 최대전력     : {e['peak_demand_kw']:>15.2f} kW")
    print(f"  최대수요전력 저감률: {e['peak_demand_reduction_pct']:>14.2f} %"
          f"   {'(악화)' if e['peak_demand_reduction_pct'] < 0 else ''}")
    if e['base_charge_rate_won_per_kw']:
        print(f"  기본요금 증감     : {e['base_charge_delta_won']:>+15,.0f} 원"
              f"   ({e['months_evaluated']}개월, {e['base_charge_rate_won_per_kw']:,}원/kW)")
        print(f"  순 절감액         : {e['net_saving_won']:>+15,.0f} 원"
              f"   (전력량요금 절감 − 기본요금 증가)")

    en = metrics['energy']
    print("\n[2] 에너지 효율")
    print("-" * 64)
    print(f"  총 충전 / 방전    : {en['bess_charge_kwh']:>10,.0f} / "
          f"{en['bess_discharge_kwh']:,.0f} kWh")
    print(f"  총 처리 에너지    : {en['total_throughput_kwh']:>15,.0f} kWh")
    print(f"  라운드트립 효율   : {en['roundtrip_efficiency_pct']:>15.2f} %")
    print(f"  BESS 활용률       : {en['bess_utilization_pct']:>15.2f} %")
    print(f"  자립률            : {en['self_sufficiency_pct']:>15.2f} %")
    print(f"  재생E 기원 자립률 : {en['re_self_sufficiency_pct']:>15.2f} %")
    print(f"  태양광 잉여       : {en['solar_surplus_kwh']:>15,.1f} kWh")
    print(f"   └ BESS 흡수      : {en['solar_to_bess_kwh']:>15,.1f} kWh")
    print(f"   └ 커튼일먼트     : {en['curtailment_kwh']:>15,.1f} kWh")
    print(f"  BESS 기인 역송    : {en['bess_export_kwh']:>15,.1f} kWh")

    print("\n  [시간대별 충·방전량 (kWh)]")
    print(f"  {_pad('시간대', 12)}{_pad('충전', 12, 'right')}{_pad('방전', 12, 'right')}")
    for p_ in ('off_peak', 'mid_peak', 'on_peak'):
        d = en['energy_by_period'][p_]
        print(f"  {_pad(lm[p_], 12)}{d['charge_kwh']:>12,.0f}{d['discharge_kwh']:>12,.0f}")

    s = metrics['stability']
    print("\n[3] 운영 안정성")
    print("-" * 64)
    print(f"  배터리 사이클 수  : {s['cycle_count']:>15.2f} 회")
    print(f"  일일 전환 빈도    : {s['transitions_per_day']:>15.2f} 회/일")
    print(f"  SOC 권장구간 체류 : {s['soc_in_band_rate_pct']:>15.2f} %")
    print(f"   └ 80% 초과       : {s['soc_above_band_pct']:>15.2f} %")
    print(f"   └ 20% 미만       : {s['soc_below_band_pct']:>15.2f} %")
    print(f"  과충전 / 과방전   : {s['overcharge_count']:>10,} / "
          f"{s['overdischarge_count']:,} 회")
    print(f"  충전/방전/대기    : {s['charge_count']:>10,} / "
          f"{s['discharge_count']:,} / {s['idle_count']:,} 회")

    if 'reason_counts' in s:
        print("\n  [제어 계층별 발동 횟수]")
        for k, v in sorted(s['reason_counts'].items(), key=lambda x: -x[1]):
            if k == 'none':
                continue
            print(f"  {_pad(k, 24)}{v:>12,} 회")
        print(f"  {_pad('예측값 참조 발동 비율', 24)}"
              f"{s['prediction_driven_pct']:>11.2f} %")

    print(f"\n{sep}\n")
