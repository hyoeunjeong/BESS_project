"""[논문표] 표 2.11 정정 단계별 운영 성능 변화 + 표 2.15 κ 민감도

여섯 정정을 플래그(ga,na,da,ra,ba + thr)로 on/off 하며 A~F 단계를 순차
시뮬레이션한다. 예측값은 모든 단계에서 동일하므로 단계 간 차이는 오직 제어
로직에서만 발생한다. 지시서 참조 구현(stage_run.py)의 알고리즘·자체 평가식을
그대로 따르되, 저장소 base_data 의 시간대 라벨('off_peak'…)에 맞춰 매핑했다.

전용 모듈이므로 라이브 DL_LSTM/bess_controller.py 는 건드리지 않는다.
입력: results/base_data.csv, results/pred_lstm.npy
출력: results/table_2_11_stages.csv, results/table_2_15_kappa.csv

실행: cd DL_LSTM && python stage_run.py
"""
import os
import numpy as np
import pandas as pd

REPO    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(REPO, 'results')

CAP, PMAX, EFF, SMIN, SMAX = 100., 25., 0.95, 0.10, 0.90
TG_NEW = {'off': 0.80, 'mid': 0.60, 'on': 0.20}   # 정정 (나) 이후
TG_OLD = {'off': 0.90, 'mid': 0.60, 'on': 0.20}   # 초기 설계
PR     = {'off': 0.80, 'mid': 0.50, 'on': 1.00}
EPS = DLT = 0.05
SEQ = 24
BASE_CHARGE = 7470          # 원/kW·월


def _sec(tp):
    """base_data 시간대 라벨('off_peak'/'mid_peak'/'on_peak') → 'off'/'mid'/'on'."""
    return tp.split('_')[0]


def simulate(df, pred, flags, thr, tgt_cap):
    """flags: dict(ga,na,da,ra,ba)  ※ ma(임계값 누수)는 thr 인자로 반영."""
    TG = TG_NEW if flags['na'] else TG_OLD
    E = CAP * 0.5
    rows = []
    for i, r in enumerate(df.itertuples()):
        soc = E / CAP
        sec = _sec(r.tp)
        load, solar = r.load_kw, r.solar_kw
        nl_act = max(0., load - solar); sur = max(0., solar - load)
        dcap = nl_act if flags['ga'] else load                 # (가)
        peak_hr = sec in ('mid', 'on')
        head = max(0., tgt_cap - nl_act) if (flags['ba'] and peak_hr) else 1e9  # (바)
        mc = (SMAX - soc) * CAP / EFF; md = (soc - SMIN) * CAP * EFF
        p = 0.; act = 'idle'
        if flags['ba'] and peak_hr and nl_act > tgt_cap and soc > SMIN:   # P0' (바)
            d = min(nl_act - tgt_cap, PMAX, md, dcap)
            if d > 0: p, act = d, 'discharge'
        elif soc < SMIN + EPS:                                  # P0 비상 충전
            c = min(PMAX * 0.5, mc, head)
            if c > 0: p, act = -c, 'charge'
        elif soc > SMAX - EPS:                                  # P0 비상 방전
            d = min(PMAX * 0.5, md, dcap)
            if d > 0: p, act = d, 'discharge'
        elif sur > 0 and soc < SMAX:                            # P1 잉여 충전(실측)
            c = min(sur, PMAX, mc)
            if c > 0: p, act = -c, 'charge'
        elif pred[i] > thr and soc > SMIN:                      # P2 피크 컷
            cap2 = dcap if flags['ga'] else PMAX                # (가) 정정 전엔 부하 상한 없음
            d = min(pred[i] - thr, PMAX, md, cap2)
            if d > 0: p, act = d, 'discharge'
        else:                                                  # P3 SOC 목표 추종
            sd = TG[sec] - soc
            if sd > DLT and soc < SMAX:
                allow = (sec == 'off') if flags['da'] else True    # (다)
                if allow:
                    clamp = sd * CAP / EFF if flags['ra'] else 1e9  # (라)
                    c = min(PMAX * PR[sec], mc, clamp, head)
                    if c > 0: p, act = -c, 'charge'
            elif sd < -DLT and soc > SMIN:
                d = min(PMAX * PR[sec], md, dcap)
                if d > 0: p, act = d, 'discharge'
        E = min(max(E + ((-p * EFF) if p < 0 else (-p / EFF)), 0), CAP)
        rows.append((p, E / CAP, act))
    return pd.DataFrame(rows, columns=['bess', 'soc', 'action'])


def evaluate(df, res):
    grid = df.load_kw - df.solar_kw - res.bess
    base = (df.load_kw - df.solar_kw).clip(lower=0)
    mo   = df.tp.isin(['mid_peak', 'on_peak'])
    on   = df.tp == 'on_peak'
    cb = (base * df.rate).sum(); cr = (grid.clip(lower=0) * df.rate).sum()
    cb_on = (base[on] * df.rate[on]).sum(); cr_on = (grid.clip(lower=0)[on] * df.rate[on]).sum()
    ch = (-res.bess).clip(lower=0); di = res.bess.clip(lower=0)
    pk_b = base[mo].max(); pk_r = grid.clip(lower=0)[mo].max()
    dchg = (pk_r - pk_b) * BASE_CHARGE * 12
    exp = (-grid).clip(lower=0)
    act = res.action.tolist()
    return {
        '요금절감률(%)'    : round((cb - cr) / cb * 100, 2),
        '최대부하절감률(%)': round((cb_on - cr_on) / cb_on * 100, 2),
        '요금적용전력(kW)' : round(pk_r, 2),
        '기본요금증감(원)' : round(dchg, 0),
        '순절감액(원)'     : round((cb - cr) - dchg, 0),
        '역송(kWh)'        : round(float(np.minimum(di, exp).sum()), 0),
        'SOC체류(%)'       : round(float(((res.soc >= .2) & (res.soc <= .8)).mean() * 100), 2),
        '전환(회/일)'      : round(sum(1 for k in range(1, len(res))
                                     if {act[k-1], act[k]} == {'charge', 'discharge'}) / (len(res) / 24), 2),
        '사이클'           : round(float((ch.sum() + di.sum()) / 200), 2),
    }


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


def _rule_based(sim):
    E = CAP * 0.5; rows = []
    for r in sim.itertuples():
        nl = r.load_kw - r.solar_kw; p = 0.; a = 'idle'; soc = E / CAP
        if nl < 0:
            mc = (SMAX - soc) * CAP / EFF
            if mc > 0: p = -min(-nl, PMAX, mc); a = 'charge'
        elif nl > 0 and _sec(r.tp) in ('on', 'mid'):
            md = (soc - SMIN) * CAP * EFF
            if md > 0: p = min(nl, PMAX, md); a = 'discharge'
        if a == 'idle' and _sec(r.tp) == 'off' and soc < SMAX:
            mc = (SMAX - soc) * CAP / EFF
            if mc > 0: p = -min(PMAX, mc); a = 'charge'
        E = min(max(E + ((-p * EFF) if p < 0 else (-p / EFF)), 0), CAP)
        rows.append((p, E / CAP, a))
    return pd.DataFrame(rows, columns=['bess', 'soc', 'action'])


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
    print(f"피크 임계값  전구간 {thr_full:.2f} kW / 학습구간 {thr_train:.2f} kW")
    print(f"목표 수요전력 P_tgt = {P_CAP:.2f} × 0.90 = {TGT:.2f} kW\n")

    rows = []
    for label, fl, th in STAGES:
        res = simulate(sim, pred, fl, thr_full if th == 'full' else thr_train, TGT)
        rows.append({'단계': label, **evaluate(sim, res)})
    rows.append({'단계': 'Rule-Based', **evaluate(sim, _rule_based(sim))})
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
        m = evaluate(sim, simulate(sim, pred, F, thr_train, P_CAP * kappa))
        krows.append({'κ': kappa, 'P_tgt(kW)': round(P_CAP * kappa, 2),
                      '요금절감률(%)': m['요금절감률(%)'], '요금적용전력(kW)': m['요금적용전력(kW)'],
                      '기본요금증감(원)': m['기본요금증감(원)'], '순절감액(원)': m['순절감액(원)'],
                      '채택': '기준' if kappa == 0.90 else ''})
    kdf = pd.DataFrame(krows)
    p15 = os.path.join(RESULTS, 'table_2_15_kappa.csv')
    kdf.to_csv(p15, index=False, encoding='utf-8-sig')
    print("\n" + kdf.to_string(index=False))
    print(f"[표 2.15] 저장: {p15}  (κ=0.90 사전 설계값 고정)")


if __name__ == '__main__':
    main()
