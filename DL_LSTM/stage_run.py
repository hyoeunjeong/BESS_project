"""[논문표] 표 2.11 — 정정 단계별 운영 성능 변화 (A~F)

여섯 정정을 플래그(ga,na,da,ra,ba + thr)로 on/off 하는 단일 StagedController 로
A~F 7단계를 순차 시뮬레이션한다. 제어기를 일곱 벌 복사하지 않는다.

  A  단계 = 정정 전(git 12613c1 앵커) + 전구간 임계값
  F  단계 = 모든 정정 on + 학습구간 임계값 (= 논문 최종 제어기)

전용 모듈이므로 라이브 DL_LSTM/bess_controller.py 는 건드리지 않는다.
입력: results/base_data.csv, results/pred_lstm.npy
출력: results/table_2_11_stages.csv

실행: cd DL_LSTM && python stage_run.py
"""
import os
import numpy as np
import pandas as pd

import config
from evaluator import evaluate_all

REPO    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(REPO, 'results')

TOL = 0.05          # SOC_TOLERANCE
EMG = 0.05          # EMERGENCY_LOW/HIGH
POWER_RATIOS = {'off_peak': 0.80, 'mid_peak': 0.50, 'on_peak': 1.00}


class StagedController:
    """플래그로 정정 (가~바)를 on/off 하는 제어기.
    all-off + 전구간 thr = 정정 전(12613c1) 재현, all-on + 학습 thr = 최종(F)."""

    def __init__(self, flags, thr, demand_target,
                 cap=config.BESS_CAPACITY_KWH, pmax=config.BESS_MAX_POWER_KW,
                 eff=config.BESS_EFFICIENCY, soc_min=config.SOC_MIN,
                 soc_max=config.SOC_MAX, soc0=config.SOC_INITIAL):
        self.f = flags
        self.thr = thr
        self.demand_target = demand_target
        self.cap, self.pmax, self.eff = cap, pmax, eff
        self.soc_min, self.soc_max = soc_min, soc_max
        self.soc = soc0
        self.energy = cap * soc0
        off = 0.80 if flags['na'] else 0.90          # (나)
        self.SOC_TARGETS = {'off_peak': off, 'mid_peak': 0.60, 'on_peak': 0.20}

    def _max_c(self, dt):
        return (self.soc_max - self.soc) * self.cap / (self.eff * dt) if dt > 0 else 0.0

    def _max_d(self, dt):
        return (self.soc - self.soc_min) * self.cap * self.eff / dt if dt > 0 else 0.0

    def _upd(self, p, dt):
        if p < 0:
            self.energy += -p * dt * self.eff
        elif p > 0:
            self.energy -= p * dt / self.eff
        self.soc = float(np.clip(self.energy / self.cap, 0.0, 1.0))
        self.energy = self.soc * self.cap

    def control(self, pred_nl, load, solar, tariff, dt=config.TIME_STEP_HOURS):
        f = self.f
        soc_target = self.SOC_TARGETS[tariff]
        pr = POWER_RATIOS[tariff]
        net = max(0.0, load - solar)
        # (가) 방전 상한 기준: on→순부하, off→부하
        dref = net if f['ga'] else load
        # (바) 수요전력 상한 + headroom
        peak_hr = tariff in ('mid_peak', 'on_peak')
        cap = self.demand_target if (f['ba'] and peak_hr) else None
        headroom = max(0.0, cap - net) if cap is not None else float('inf')

        p, action, reason = 0.0, 'idle', 'none'

        # P0' (바) 수요전력 초과 방지 방전 (최우선)
        if cap is not None and net > cap and self.soc > self.soc_min:
            dis = min(net - cap, self.pmax, self._max_d(dt), net)
            if dis > 0:
                p, action, reason = dis, 'discharge', 'demand_charge_cut'

        # P0 비상 충전
        elif self.soc < self.soc_min + EMG:
            c = min(self.pmax * 0.5, self._max_c(dt), headroom)   # (바)면 headroom 제약
            if c > 0:
                p, action, reason = -c, 'charge', 'emergency_low'

        # P0 비상 방전
        elif self.soc > self.soc_max - EMG:
            if dref > 0:
                d = min(self.pmax * 0.5, self._max_d(dt), dref)
                if d > 0:
                    p, action, reason = d, 'discharge', 'emergency_high'

        # P1 태양광 잉여 → 충전 (12613c1 방식: 예측 순부하<0)
        elif pred_nl < 0 and self.soc < self.soc_max:
            c = min(abs(pred_nl), self.pmax, self._max_c(dt), headroom)
            if c > 0:
                p, action, reason = -c, 'charge', 'solar_surplus'

        # P2 피크 컷 → 방전
        elif pred_nl > self.thr and self.soc > self.soc_min:
            caps = [pred_nl - self.thr, self.pmax, self._max_d(dt)]
            if f['ga']:
                caps.append(net)               # (가) 역송 방지 순부하 상한
            d = min(caps)
            if d > 0:
                p, action, reason = d, 'discharge', 'peak_cut'

        # P3 SOC 목표 추종
        else:
            diff = soc_target - self.soc
            if diff > TOL:                       # 충전
                grid_ok = (tariff == 'off_peak') if f['da'] else True   # (다)
                if self.soc < self.soc_max and grid_ok:
                    caps = [self.pmax * pr, self._max_c(dt)]
                    if f['ra']:
                        caps.append(diff * self.cap / (self.eff * dt))  # (라) 클램프
                    if f['ba']:
                        caps.append(headroom)
                    c = min(caps)
                    if c > 0:
                        p, action, reason = -c, 'charge', f'soc_target_{tariff}'
            elif diff < -TOL:                    # 방전
                if self.soc > self.soc_min and dref > 0:
                    d = min(self.pmax * pr, self._max_d(dt), dref)
                    if d > 0:
                        p, action, reason = d, 'discharge', f'soc_target_{tariff}'

        self._upd(p, dt)
        grid = load - solar - p
        return p, grid, self.soc, action, reason


def _simulate(sim, pred, flags, thr, demand_target):
    ctrl = StagedController(flags, thr, demand_target)
    recs = []
    for i, row in enumerate(sim.itertuples(index=False)):
        p, grid, soc, action, reason = ctrl.control(
            float(pred[i]), float(row.load_kw), float(row.solar_kw), row.tp)
        recs.append((row.timestamp, row.load_kw, row.solar_kw, row.smp,
                     row.tp, row.rate, p, grid, soc, action, reason))
    df = pd.DataFrame(recs, columns=['timestamp', 'load_kw', 'solar_kw', 'smp',
                                     'tariff_period', 'tariff_rate', 'bess_power_kw',
                                     'grid_power_kw', 'soc', 'action', 'reason'])
    df['charge_kw']    = df['bess_power_kw'].apply(lambda x: -x if x < 0 else 0.0)
    df['discharge_kw'] = df['bess_power_kw'].apply(lambda x:  x if x > 0 else 0.0)
    return df


def _baseline(sim):
    df = sim.rename(columns={'tp': 'tariff_period', 'rate': 'tariff_rate'}).copy()
    df['bess_power_kw'] = 0.0
    df['charge_kw'] = 0.0
    df['discharge_kw'] = 0.0
    df['soc'] = 0.0
    df['action'] = 'none'
    df['grid_power_kw'] = df['load_kw'] - df['solar_kw']
    return df


def _metrics9(m):
    e, en, st = m['economic'], m['energy'], m['stability']
    return {
        '요금절감률(%)'    : e['cost_saving_rate_pct'],
        '최대부하절감률(%)': e['peak_saving_rate_pct'],
        '요금적용전력(kW)' : e['peak_demand_kw'],
        '기본요금증감(원)' : e['base_charge_delta_won'],
        '순절감액(원)'     : e['net_saving_won'],
        '역송(kWh)'        : en.get('bess_export_kwh', 0.0),
        'SOC체류(%)'       : st['soc_in_band_rate_pct'],
        '전환(회/일)'      : st['transitions_per_day'],
        '사이클'           : st['cycle_count'],
    }


def main():
    base = pd.read_csv(os.path.join(RESULTS, 'base_data.csv'), parse_dates=['timestamp'])
    pred = np.load(os.path.join(RESULTS, 'pred_lstm.npy')).astype(float)

    SEQ = config.SEQ_LEN
    sim = base.iloc[SEQ:].reset_index(drop=True)          # 8,736행 (pred 정렬)
    assert len(sim) == len(pred), f"{len(sim)} != {len(pred)}"

    load = sim['load_kw'].values
    n_tr = int(len(sim) * config.TRAIN_RATIO)
    thr_full  = float(np.percentile(load, 85))            # 58.91
    thr_train = float(np.percentile(load[:n_tr], 85))     # 60.45

    net = np.clip(sim['load_kw'].values - sim['solar_kw'].values, 0, None)
    peak_mask = np.isin(sim['tp'].values[:n_tr], ['mid_peak', 'on_peak'])
    P_CAP = float(net[:n_tr][peak_mask].max())
    demand_target = P_CAP * getattr(config, 'DEMAND_SHAVE', 0.90)
    print(f"[임계값] 전구간 {thr_full:.2f} / 학습 {thr_train:.2f} kW | "
          f"P_CAP {P_CAP:.2f} × {getattr(config,'DEMAND_SHAVE',0.90)} = {demand_target:.2f} kW")

    STAGES = [
        ('A',  dict(ga=0, na=0, da=0, ra=0, ba=0), thr_full),
        ('A′', dict(ga=0, na=0, da=0, ra=0, ba=0), thr_train),
        ('B',  dict(ga=1, na=0, da=0, ra=0, ba=0), thr_train),
        ('C',  dict(ga=1, na=1, da=0, ra=0, ba=0), thr_train),
        ('D',  dict(ga=1, na=1, da=1, ra=0, ba=0), thr_train),
        ('E',  dict(ga=1, na=1, da=1, ra=1, ba=0), thr_train),
        ('F',  dict(ga=1, na=1, da=1, ra=1, ba=1), thr_train),
    ]

    base_sim = _baseline(sim)
    rows = []
    for label, flags, thr in STAGES:
        rdf = _simulate(sim, pred, flags, thr, demand_target)
        m = evaluate_all(rdf, base_sim)
        rows.append({'단계': label, **_metrics9(m)})

    # Rule-Based 기준 행 (동일 8,736 구간, 단순 3규칙)
    rows.append({'단계': 'Rule-Based', **_metrics9(evaluate_all(_rule_based(sim), base_sim))})

    out = pd.DataFrame(rows)
    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, 'table_2_11_stages.csv')
    out.to_csv(path, index=False, encoding='utf-8-sig')
    pd.set_option('display.width', 200, 'display.max_columns', 20)
    print(out.to_string(index=False))
    print(f"\n[표 2.11] 저장: {path}")


def _rule_based(sim):
    """단순 TOU 3규칙 (rule_based/bess_controller.py 와 동일 로직)."""
    cap, pmax, eff = config.BESS_CAPACITY_KWH, config.BESS_MAX_POWER_KW, config.BESS_EFFICIENCY
    lo, hi = config.SOC_MIN, config.SOC_MAX
    soc = config.SOC_INITIAL
    energy = cap * soc
    dt = config.TIME_STEP_HOURS
    recs = []
    for row in sim.itertuples(index=False):
        net = row.load_kw - row.solar_kw
        p, action = 0.0, 'idle'
        if net < 0:
            c = min(-net, pmax, (hi - soc) * cap / (eff * dt))
            if c > 0:
                p, action = -c, 'charge'
        elif net > 0 and row.tp in ('on_peak', 'mid_peak'):
            d = min(net, pmax, (soc - lo) * cap * eff / dt)
            if d > 0:
                p, action = d, 'discharge'
        if action == 'idle' and row.tp == 'off_peak' and soc < hi:
            c = min(pmax, (hi - soc) * cap / (eff * dt))
            if c > 0:
                p, action = -c, 'charge'
        if p < 0:
            energy += -p * dt * eff
        elif p > 0:
            energy -= p * dt / eff
        soc = float(np.clip(energy / cap, 0.0, 1.0))
        energy = soc * cap
        recs.append((row.timestamp, row.load_kw, row.solar_kw, row.smp, row.tp, row.rate,
                     p, row.load_kw - row.solar_kw - p, soc, action))
    df = pd.DataFrame(recs, columns=['timestamp', 'load_kw', 'solar_kw', 'smp',
                                     'tariff_period', 'tariff_rate', 'bess_power_kw',
                                     'grid_power_kw', 'soc', 'action'])
    df['charge_kw']    = df['bess_power_kw'].apply(lambda x: -x if x < 0 else 0.0)
    df['discharge_kw'] = df['bess_power_kw'].apply(lambda x:  x if x > 0 else 0.0)
    return df


if __name__ == '__main__':
    main()
