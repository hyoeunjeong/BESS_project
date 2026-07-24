"""[논문표] 표 2.11 정정 단계별 운영 성능 변화 + 표 2.15 κ 민감도

여섯 정정을 플래그(ga,na,da,ra,ba + thr)로 on/off 하며 A~F 단계를 순차
시뮬레이션한다.
[통일] 라이브 LSTMBESSController(제어) 와 evaluator.evaluate_all(지표) 을
그대로 공유하므로, F 단계(모든 플래그 ON)는 compare.py 의 LSTM 값과 일치한다.

입력: results/base_data.csv, results/pred_lstm.npy
출력: results/table_2_11_stages.csv, results/table_2_15_kappa.csv
실행: cd DL_LSTM && python stage_run.py
"""
import os
import numpy as np
import pandas as pd

from bess_controller import LSTMBESSController
from evaluator import evaluate_all

REPO    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(REPO, 'results')

CAP, PMAX, EFF, SMIN, SMAX = 100., 25., 0.95, 0.10, 0.90
SEQ = 24


def simulate(df, pred, flags, thr, tgt_cap):
    """[통일] 라이브 LSTMBESSController 를 flags 로 호출 → 전체 결과 DataFrame."""
    ctrl = LSTMBESSController(flags=flags)
    ctrl.peak_threshold = thr
    ctrl.demand_target  = tgt_cap
    recs = []
    for i, r in enumerate(df.itertuples(index=False)):
        ts = pd.Timestamp(r.timestamp)
        res = ctrl.control(predicted_net_load=float(pred[i]),
                           actual_load_kw=float(r.load_kw),
                           actual_solar_kw=float(r.solar_kw),
                           hour=int(ts.hour), month=int(ts.month),
                           weekday=int(ts.weekday()), date=ts.date())
        recs.append((r.timestamp, r.load_kw, r.solar_kw, r.smp,
                     res['tariff_period'], r.rate, res['bess_power_kw'],
                     res['grid_power_kw'], res['soc'], res['action'], res['reason']))
    out = pd.DataFrame(recs, columns=['timestamp', 'load_kw', 'solar_kw', 'smp',
        'tariff_period', 'tariff_rate', 'bess_power_kw', 'grid_power_kw',
        'soc', 'action', 'reason'])
    out['charge_kw']    = out['bess_power_kw'].apply(lambda x: -x if x < 0 else 0.0)
    out['discharge_kw'] = out['bess_power_kw'].apply(lambda x:  x if x > 0 else 0.0)
    return out


def _baseline(sim):
    """무제어 기준 — evaluator 공유용 전체 DataFrame."""
    b = sim.rename(columns={'tp': 'tariff_period', 'rate': 'tariff_rate'}).copy()
    for c in ('bess_power_kw', 'charge_kw', 'discharge_kw', 'soc'):
        b[c] = 0.0
    b['action'] = 'none'
    b['grid_power_kw'] = b['load_kw'] - b['solar_kw']
    return b


def metrics9(m):
    """evaluate_all 결과 → 표 2.11 의 9개 지표."""
    e, en, st = m['economic'], m['energy'], m['stability']
    return {
        '요금절감률(%)'    : e['cost_saving_rate_pct'],
        '최대부하절감률(%)': e['peak_saving_rate_pct'],
        '요금적용전력(kW)' : e['peak_demand_kw'],
        '기본요금증감(원)' : e['base_charge_delta_won'],
        '순절감액(원)'     : e['net_saving_won'],
        '역송(kWh)'        : round(en.get('bess_export_kwh', 0.0), 0),
        'SOC체류(%)'       : st['soc_in_band_rate_pct'],
        '전환(회/일)'      : st['transitions_per_day'],
        '사이클'           : st['cycle_count'],
    }


def _rule_based(sim):
    """단순 TOU 3규칙 — 전체 결과 DataFrame(evaluator 공유)."""
    E = CAP * 0.5
    recs = []
    for r in sim.itertuples(index=False):
        soc = E / CAP; nl = r.load_kw - r.solar_kw; p = 0.; a = 'idle'
        sec = r.tp.split('_')[0]
        if nl < 0:
            mc = (SMAX - soc) * CAP / EFF
            if mc > 0: p = -min(-nl, PMAX, mc); a = 'charge'
        elif nl > 0 and sec in ('on', 'mid'):
            md = (soc - SMIN) * CAP * EFF
            if md > 0: p = min(nl, PMAX, md); a = 'discharge'
        if a == 'idle' and sec == 'off' and soc < SMAX:
            mc = (SMAX - soc) * CAP / EFF
            if mc > 0: p = -min(PMAX, mc); a = 'charge'
        E = min(max(E + ((-p * EFF) if p < 0 else (-p / EFF)), 0), CAP)
        recs.append((r.timestamp, r.load_kw, r.solar_kw, r.smp, r.tp, r.rate,
                     p, r.load_kw - r.solar_kw - p, E / CAP, a))
    out = pd.DataFrame(recs, columns=['timestamp', 'load_kw', 'solar_kw', 'smp',
        'tariff_period', 'tariff_rate', 'bess_power_kw', 'grid_power_kw', 'soc', 'action'])
    out['charge_kw']    = out['bess_power_kw'].apply(lambda x: -x if x < 0 else 0.0)
    out['discharge_kw'] = out['bess_power_kw'].apply(lambda x:  x if x > 0 else 0.0)
    return out


OFF = dict(ga=0, na=0, da=0, ra=0, ba=0)
STAGES = [
    ('A',  {**OFF},                                'full'),
    ('A′', {**OFF},                                'train'),
    ('B',  {**OFF, 'ga': 1},                       'train'),
    ('C',  {**OFF, 'ga': 1, 'na': 1},              'train'),
    ('D',  {**OFF, 'ga': 1, 'na': 1, 'da': 1},     'train'),
    ('E',  {**OFF, 'ga': 1, 'na': 1, 'da': 1, 'ra': 1}, 'train'),
    ('F',  dict(ga=1, na=1, da=1, ra=1, ba=1),     'train'),
]


def main():
    base = pd.read_csv(os.path.join(RESULTS, 'base_data.csv'), parse_dates=['timestamp'])
    pred = np.load(os.path.join(RESULTS, 'pred_lstm.npy')).astype(float)
    sim = base.iloc[SEQ:].reset_index(drop=True)
    assert len(sim) == len(pred), f"{len(sim)} != {len(pred)}"

    ntr = int(len(sim) * 0.70)
    load = sim['load_kw'].values
    thr_full  = float(np.percentile(load, 85))
    thr_train = float(np.percentile(load[:ntr], 85))
    nl = (sim.load_kw - sim.solar_kw).clip(lower=0)
    mo = sim.tp.isin(['mid_peak', 'on_peak'])
    P_CAP = float(nl[:ntr][mo[:ntr]].max())
    TGT = P_CAP * 0.90
    baseline = _baseline(sim)
    print(f"피크 임계값  전구간 {thr_full:.2f} kW / 학습구간 {thr_train:.2f} kW")
    print(f"목표 수요전력 P_tgt = {P_CAP:.2f} × 0.90 = {TGT:.2f} kW\n")

    rows = []
    for label, fl, th in STAGES:
        res = simulate(sim, pred, fl, thr_full if th == 'full' else thr_train, TGT)
        rows.append({'단계': label, **metrics9(evaluate_all(res, baseline))})
    rows.append({'단계': 'Rule-Based', **metrics9(evaluate_all(_rule_based(sim), baseline))})
    out = pd.DataFrame(rows)
    os.makedirs(RESULTS, exist_ok=True)
    p11 = os.path.join(RESULTS, 'table_2_11_stages.csv')
    out.to_csv(p11, index=False, encoding='utf-8-sig')
    pd.set_option('display.width', 220, 'display.max_columns', 20)
    print(out.to_string(index=False))
    print(f"\n[표 2.11] 저장: {p11}")

    # ── 표 2.15: κ 민감도 (F 단계) ──
    F = dict(ga=1, na=1, da=1, ra=1, ba=1)
    krows = []
    for kappa in (0.85, 0.90, 0.95):
        e = evaluate_all(simulate(sim, pred, F, thr_train, P_CAP * kappa), baseline)['economic']
        krows.append({'κ': kappa, 'P_tgt(kW)': round(P_CAP * kappa, 2),
                      '요금절감률(%)': e['cost_saving_rate_pct'], '요금적용전력(kW)': e['peak_demand_kw'],
                      '기본요금증감(원)': e['base_charge_delta_won'], '순절감액(원)': e['net_saving_won'],
                      '채택': '기준' if kappa == 0.90 else ''})
    kdf = pd.DataFrame(krows)
    p15 = os.path.join(RESULTS, 'table_2_15_kappa.csv')
    kdf.to_csv(p15, index=False, encoding='utf-8-sig')
    print("\n" + kdf.to_string(index=False))
    print(f"[표 2.15] 저장: {p15}  (κ=0.90 사전 설계값 고정)")


if __name__ == '__main__':
    main()
