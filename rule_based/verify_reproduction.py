"""
논문 재현 검증 스크립트 (Acceptance Test)
=====================================================================
수정 작업 후 이 스크립트를 실행하여, 규칙 기반 운영이 논문에 실린
21개 지표를 그대로 재현하는지 확인한다.

  · config.py 의 요금 설정이 논문 조건인지
  · evaluator.py 의 지표 계산이 올바른지
  · 평가 구간(8,371 / 8,760) 정의가 맞는지
를 한 번에 검증한다.

실행
    python verify_reproduction.py
    python verify_reproduction.py --data-dir path/to/data

종료 코드
    0 = 전부 일치, 1 = 불일치 발생
=====================================================================
"""

import os
import sys
import glob
import argparse
import numpy as np
import pandas as pd

import config
import evaluator


# ── 논문에 실린 기댓값 (규칙 기반) ───────────────────────────────
EXPECTED_8371 = {
    'cost_saving_rate_pct'     : 6.92,
    'cost_saving_won_manwon'   : 244.8,
    'peak_saving_rate_pct'     : 26.27,
    'mid_peak_rate_pct'        : 8.49,
    'off_peak_rate_pct'        : -20.41,
    'peak_demand_reduction_pct': -18.85,
    'peak_demand_kw'           : 84.66,
    'baseline_peak_demand_kw'  : 71.24,
    'cycle_count'              : 297.86,
    'transitions_per_day'      : 0.30,
    'soc_in_band_rate_pct'     : 21.72,
    'soc_above_band_pct'       : 31.49,
    'soc_below_band_pct'       : 46.79,
    'off_peak_charge_kwh'      : 30636,
    'mid_peak_charge_kwh'      : 268,
    'on_peak_charge_kwh'       : 401,
    'off_peak_discharge_kwh'   : 0,
    'mid_peak_discharge_kwh'   : 11459,
    'on_peak_discharge_kwh'    : 16809,
    'bess_export_kwh'          : 0,
}
EXPECTED_8760 = {
    'cost_saving_rate_pct'     : 6.73,
    'cycle_count'              : 298.67,
    'peak_demand_reduction_pct': -18.85,
}

# 지표별 허용 오차
TOL = {
    'cost_saving_won_manwon': 0.15,
    'off_peak_charge_kwh': 1.0, 'mid_peak_charge_kwh': 1.0, 'on_peak_charge_kwh': 1.0,
    'off_peak_discharge_kwh': 1.0, 'mid_peak_discharge_kwh': 1.0,
    'on_peak_discharge_kwh': 1.0, 'bess_export_kwh': 0.1,
}
DEFAULT_TOL = 0.011


# ── 데이터 로드 ──────────────────────────────────────────────────
def find_file(data_dir, patterns):
    roots = [data_dir, '.', 'data', 'data/cache', 'data/api_cache',
             'rule_based/data/api_cache', '..']
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for pat in patterns:
            hits = sorted(glob.glob(os.path.join(root, pat)))
            if hits:
                return hits[0]
    return None


def load_inputs(data_dir):
    kma = find_file(data_dir, ['kma_st*_*.csv', 'kma_*.csv'])
    load = find_file(data_dir, ['load_2025*.csv', 'load_*.csv'])
    if not kma or not load:
        raise SystemExit(
            "입력 파일을 찾지 못했습니다.\n"
            "  필요: kma_st108_20250101_20251231.csv, load_20250101_20251231.csv\n"
            "  --data-dir 로 경로를 지정하세요.")
    print(f"  일사량 : {kma}")
    print(f"  부하   : {load}")

    k = pd.read_csv(kma)
    k.columns = [c.strip('\ufeff') for c in k.columns]
    k['timestamp'] = pd.to_datetime(k['timestamp'])

    l = pd.read_csv(load)
    l.columns = [c.strip('\ufeff') for c in l.columns]
    l['timestamp'] = pd.to_datetime(l['timestamp'])
    if 'load_kw' not in l.columns:
        l['load_kw'] = l['load_mw'] * (config.TARGET_AVG_LOAD_KW / l['load_mw'].mean())

    cap = config.PV_CAPACITY_KW
    solar = (k['icsr'].fillna(0) * cap * 0.2778).clip(0, cap)

    df = l[['timestamp', 'load_kw']].merge(
        pd.DataFrame({'timestamp': k['timestamp'], 'solar_kw': solar}),
        on='timestamp')
    df['hour'] = df['timestamp'].dt.hour
    mo = df['timestamp'].dt.month
    df['tariff_period'] = [config.get_tariff_period(int(h), int(m))
                           for h, m in zip(df['hour'], mo)]
    df['tariff_rate'] = [config.get_tariff_rate(int(h), int(m))
                         for h, m in zip(df['hour'], mo)]
    return df.sort_values('timestamp').reset_index(drop=True)


# ── 규칙 기반 참조 구현 (논문 식 25) ─────────────────────────────
class ReferenceRuleBased:
    """논문 2.6.1 의 규칙 ①②③ 을 그대로 구현한 참조 제어기"""

    def __init__(self):
        self.cap  = config.BESS_CAPACITY_KWH
        self.pmax = config.BESS_MAX_POWER_KW
        self.eta  = config.BESS_EFFICIENCY
        self.lo   = config.SOC_MIN
        self.hi   = config.SOC_MAX
        self.soc  = config.SOC_INITIAL
        self.e    = self.cap * self.soc

    def _max_charge(self, dt):
        return (self.hi - self.soc) * self.cap / (self.eta * dt)

    def _max_discharge(self, dt):
        return (self.soc - self.lo) * self.cap * self.eta / dt

    def step(self, load, solar, period, dt=1.0):
        net = load - solar
        p, action = 0.0, 'idle'

        if net < 0:                                        # ① 잉여 충전
            c = min(-net, self.pmax, self._max_charge(dt))
            if c > 0:
                p, action = -c, 'charge'
        elif net > 0 and period in ('on_peak', 'mid_peak'):  # ② 고요금 방전
            d = min(net, self.pmax, self._max_discharge(dt))
            if d > 0:
                p, action = d, 'discharge'

        if action == 'idle' and period == 'off_peak' and self.soc < self.hi:  # ③
            c = min(self.pmax, self._max_charge(dt))
            if c > 0:
                p, action = -c, 'charge'

        if p < 0:
            self.e += -p * dt * self.eta
        elif p > 0:
            self.e -= p * dt / self.eta
        self.soc = float(np.clip(self.e / self.cap, 0.0, 1.0))
        self.e = self.soc * self.cap
        return p, self.soc, action


def simulate(df):
    ctrl = ReferenceRuleBased()
    rec = [ctrl.step(r.load_kw, r.solar_kw, r.tariff_period)
           for r in df.itertuples()]
    o = df.copy()
    o['bess_power_kw'] = [x[0] for x in rec]
    o['soc']           = [x[1] for x in rec]
    o['action']        = [x[2] for x in rec]
    o['charge_kw']     = o['bess_power_kw'].clip(upper=0).abs()
    o['discharge_kw']  = o['bess_power_kw'].clip(lower=0)
    o['grid_power_kw'] = o['load_kw'] - o['solar_kw'] - o['bess_power_kw']
    return o


def make_baseline(df):
    b = df.copy()
    for c in ('bess_power_kw', 'charge_kw', 'discharge_kw', 'soc'):
        b[c] = 0.0
    b['action'] = 'none'
    b['grid_power_kw'] = b['load_kw'] - b['solar_kw']
    return b


# ── 지표 평탄화 ──────────────────────────────────────────────────
def flatten(m):
    e, en, st = m['economic'], m['energy'], m['stability']
    bp = en['energy_by_period']
    return {
        'cost_saving_rate_pct'     : e['cost_saving_rate_pct'],
        'cost_saving_won_manwon'   : e['cost_saving_won'] / 10000,
        'peak_saving_rate_pct'     : e['peak_saving_rate_pct'],
        'mid_peak_rate_pct'        : e['breakdown']['mid_peak']['rate_pct'],
        'off_peak_rate_pct'        : e['breakdown']['off_peak']['rate_pct'],
        'peak_demand_reduction_pct': e['peak_demand_reduction_pct'],
        'peak_demand_kw'           : e['peak_demand_kw'],
        'baseline_peak_demand_kw'  : e['baseline_peak_demand_kw'],
        'cycle_count'              : st['cycle_count'],
        'transitions_per_day'      : st['transitions_per_day'],
        'soc_in_band_rate_pct'     : st['soc_in_band_rate_pct'],
        'soc_above_band_pct'       : st['soc_above_band_pct'],
        'soc_below_band_pct'       : st['soc_below_band_pct'],
        'off_peak_charge_kwh'      : bp['off_peak']['charge_kwh'],
        'mid_peak_charge_kwh'      : bp['mid_peak']['charge_kwh'],
        'on_peak_charge_kwh'       : bp['on_peak']['charge_kwh'],
        'off_peak_discharge_kwh'   : bp['off_peak']['discharge_kwh'],
        'mid_peak_discharge_kwh'   : bp['mid_peak']['discharge_kwh'],
        'on_peak_discharge_kwh'    : bp['on_peak']['discharge_kwh'],
        'bess_export_kwh'          : en['bess_export_kwh'],
    }


def compare(actual, expected, title):
    print(f"\n{'='*70}\n  {title}\n{'='*70}")
    print(f"  {'지표':<28}{'논문':>12}{'재현':>12}{'판정':>8}")
    print("  " + "-" * 62)
    fails = []
    for key, want in expected.items():
        got = actual.get(key)
        if got is None:
            print(f"  {key:<28}{want:>12}{'없음':>12}{'FAIL':>8}")
            fails.append((key, want, None)); continue
        tol = TOL.get(key, DEFAULT_TOL)
        ok = abs(got - want) <= tol
        print(f"  {key:<28}{want:>12.2f}{got:>12.2f}{'OK' if ok else 'FAIL':>8}")
        if not ok:
            fails.append((key, want, got))
    print("  " + "-" * 62)
    print(f"  {len(expected)-len(fails)}/{len(expected)} 일치")
    return fails


# ── 사전 점검 ────────────────────────────────────────────────────
def check_config():
    print("[사전 점검] config.py")
    problems = []

    mode = getattr(config, 'TARIFF_MODE', None)
    print(f"  TARIFF_MODE = {mode}")
    if mode != 'paper':
        problems.append("TARIFF_MODE 가 'paper' 가 아닙니다. 논문 재현 불가.")

    on  = {h for h in range(24) if config.get_tariff_period(h, 6) == 'on_peak'}
    mid = {h for h in range(24) if config.get_tariff_period(h, 6) == 'mid_peak'}
    print(f"  최대부하 시간 = {sorted(on)}")
    print(f"  중간부하 시간 = {sorted(mid)}")
    if on != {10, 11, 13, 14, 15, 16}:
        problems.append(f"최대부하 시간대 불일치. 기대 [10,11,13,14,15,16], 실제 {sorted(on)}")
    if mid != {9, 12, 17, 18, 19, 20, 21, 22}:
        problems.append(f"중간부하 시간대 불일치. 실제 {sorted(mid)}")

    rates = {config.get_tariff_period(h, 6): config.get_tariff_rate(h, 6)
             for h in range(24)}
    print(f"  단가 = 경 {rates['off_peak']} / 중 {rates['mid_peak']} / 최 {rates['on_peak']} 원/kWh")
    if (rates['off_peak'], rates['mid_peak'], rates['on_peak']) != (60.0, 110.0, 180.0):
        problems.append("단가가 60/110/180 이 아닙니다.")

    for name, want in (('BESS_CAPACITY_KWH', 100.0), ('BESS_MAX_POWER_KW', 25.0),
                       ('BESS_EFFICIENCY', 0.95), ('SOC_MIN', 0.10),
                       ('SOC_MAX', 0.90), ('SOC_INITIAL', 0.50),
                       ('PV_CAPACITY_KW', 50.0), ('TARGET_AVG_LOAD_KW', 50.0)):
        got = getattr(config, name, None)
        if got != want:
            problems.append(f"{name} = {got} (기대 {want})")

    if problems:
        print("\n  [실패] 설정 불일치:")
        for p in problems:
            print(f"    · {p}")
    else:
        print("  [정상] 논문 조건과 일치")
    return problems


def check_evaluator():
    print("\n[사전 점검] evaluator.py 필수 지표")
    required = {
        'calc_economic'  : ['peak_demand_reduction_pct', 'peak_demand_kw',
                            'base_charge_delta_won', 'net_saving_won'],
        'calc_energy'    : ['bess_export_kwh', 're_self_sufficiency_pct',
                            'energy_by_period', 'curtailment_kwh'],
        'calc_stability' : ['soc_in_band_rate_pct', 'soc_above_band_pct',
                            'overcharge_count'],
        'calc_prediction_metrics': ['nmae_pct'],
    }
    n = 24
    dummy = pd.DataFrame({
        'timestamp': pd.date_range('2025-01-01', periods=n, freq='h'),
        'load_kw': 50.0, 'solar_kw': 0.0, 'charge_kw': 0.0, 'discharge_kw': 0.0,
        'grid_power_kw': 50.0, 'soc': 0.5, 'action': 'idle',
        'tariff_period': 'off_peak', 'tariff_rate': 60.0,
    })
    got = {}
    got['calc_economic']  = evaluator.calc_economic(dummy, dummy)
    got['calc_energy']    = evaluator.calc_energy(dummy)
    got['calc_stability'] = evaluator.calc_stability(dummy)
    got['calc_prediction_metrics'] = evaluator.calc_prediction_metrics(
        np.ones(10) * 40, np.ones(10) * 41)

    problems = []
    for fn, keys in required.items():
        missing = [k for k in keys if k not in got[fn]]
        mark = 'OK' if not missing else 'FAIL'
        print(f"  {mark:>4}  {fn:<26} {'' if not missing else '누락: ' + str(missing)}")
        if missing:
            problems.append(f"{fn} 에 {missing} 없음")
    return problems


# ── 메인 ─────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', default='data')
    args = ap.parse_args()

    print("=" * 70)
    print("  논문 재현 검증 (Acceptance Test)")
    print("=" * 70)

    problems = check_config()
    problems += check_evaluator()
    if problems:
        print("\n[중단] 사전 점검 실패. 위 항목을 먼저 수정하세요.")
        return 1

    print("\n[데이터 로드]")
    df = load_inputs(args.data_dir)
    print(f"  총 {len(df):,}시점 "
          f"({df['timestamp'].min().date()} ~ {df['timestamp'].max().date()})")

    fails = []

    # (1) 논문 구간 8,371
    sub = df[df['hour'] != 0].iloc[24:].reset_index(drop=True)
    if len(sub) != 8371:
        print(f"\n  [경고] 논문 구간이 {len(sub):,}시점입니다 (기대 8,371).")
    r = simulate(sub)
    m = evaluator.evaluate_all(r, make_baseline(sub))
    fails += compare(flatten(m), EXPECTED_8371,
                     f"규칙 기반 — 논문 평가 구간 ({len(sub):,}시점)")

    # (2) 1년 전체 8,760
    r2 = simulate(df)
    m2 = evaluator.evaluate_all(r2, make_baseline(df))
    fails += compare(flatten(m2), EXPECTED_8760,
                     f"규칙 기반 — 1년 전체 ({len(df):,}시점)")

    # (3) 참고 출력
    e = m['economic']; en = m['energy']
    print(f"\n{'='*70}\n  참고: 새로 계산된 지표 (논문 미보고)\n{'='*70}")
    print(f"  기본요금 증감        : {e['base_charge_delta_won']:>+12,.0f} 원"
          f"  ({e['months_evaluated']}개월 × {e['base_charge_rate_won_per_kw']:,}원/kW)")
    print(f"  순 절감액            : {e['net_saving_won']:>+12,.0f} 원")
    print(f"  기본요금 잠식률      : "
          f"{e['base_charge_delta_won']/e['cost_saving_won']*100:>12.1f} %")
    print(f"  태양광 잉여          : {en['solar_surplus_kwh']:>12,.1f} kWh")
    print(f"   └ BESS 흡수         : {en['solar_to_bess_kwh']:>12,.1f} kWh")
    print(f"   └ 커튼일먼트        : {en['curtailment_kwh']:>12,.1f} kWh")

    print(f"\n{'='*70}")
    if fails:
        print(f"  검증 실패 — 불일치 {len(fails)}건")
        for k, w, g in fails:
            print(f"    · {k}: 기대 {w}, 실제 {g}")
        print("=" * 70)
        return 1
    print("  검증 통과 — 논문 수치를 그대로 재현합니다")
    print("=" * 70)
    return 0


if __name__ == '__main__':
    sys.exit(main())
