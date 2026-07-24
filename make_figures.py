"""논문 그림 일괄 생성기 (figures/*.png, 300dpi)
=====================================================================
공통 입력(results/base_data.csv, pred_*.npy)과 시뮬레이션 결과 CSV,
표 산출물(table_2_11_stages.csv, prediction_contribution.csv)을 읽어
그림 2.3~2.12 를 생성한다. 2.1(시스템 구성)·2.2(게이트 구조)는 개념도이므로
기존 도식을 유지한다.

선행: make_tables.py, DL_LSTM/stage_run.py, 각 main.py, _build_base_data.py,
      _ablation_dump.py, _ablation_run.py 를 먼저 실행해 산출물을 만들어 둘 것.
실행: python make_figures.py
=====================================================================
"""
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams['font.family'] = 'Malgun Gothic'
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['figure.dpi'] = 300
plt.rcParams['savefig.dpi'] = 300

REPO    = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(REPO, 'results')
FIGS    = os.path.join(REPO, 'figures')
os.makedirs(FIGS, exist_ok=True)

COLORS = {'Rule-Based': '#8d99ae', 'LSTM': '#2a6f97', 'GRU': '#e07a5f'}
SEQ = 24

RESULT_CSV = {
    'Rule-Based': os.path.join(REPO, 'rule_based', 'results', 'rb_simulation_result.csv'),
    'LSTM'      : os.path.join(REPO, 'DL_LSTM',    'results', 'lstm_simulation_result.csv'),
    'GRU'       : os.path.join(REPO, 'DL_GRU',     'results', 'gru_simulation_result.csv'),
}


def _save(fig, name):
    path = os.path.join(FIGS, name)
    fig.tight_layout()
    fig.savefig(path, bbox_inches='tight')
    plt.close(fig)
    print(f"  저장: {path}")


def load_all():
    base = pd.read_csv(os.path.join(RESULTS, 'base_data.csv'), parse_dates=['timestamp'])
    res = {n: pd.read_csv(p, parse_dates=['timestamp']) for n, p in RESULT_CSV.items()
           if os.path.exists(p)}
    return base, res


# ── 2.3 LSTM 예측 결과 및 잔차 (7일) ──────────────────────────────
def fig_2_3(base):
    pred = np.load(os.path.join(RESULTS, 'pred_lstm.npy'))
    sim = base.iloc[SEQ:].reset_index(drop=True)
    true = sim['net_load_kw'].values
    ts = sim['timestamp']
    # [B-1] 테스트 구간(뒤 15%) 시작에서 7일
    d0 = int(len(sim) * 0.85)
    sl = slice(d0, d0 + 24 * 7)
    day0 = pd.Timestamp(ts.iloc[d0]).date()
    day1 = pd.Timestamp(ts.iloc[d0 + 24 * 7 - 1]).date()
    resid = pred[sl] - true[sl]
    within5 = float((np.abs(resid) <= 5).mean() * 100)
    fig, ax = plt.subplots(2, 1, figsize=(11, 6), sharex=True,
                           gridspec_kw={'height_ratios': [3, 1]})
    ax[0].plot(ts[sl], true[sl], color='#333', lw=1.6, label='실측 순부하')
    ax[0].plot(ts[sl], pred[sl], color=COLORS['LSTM'], lw=1.4, ls='--', label='LSTM 예측')
    ax[0].set_ylabel('순부하 (kW)'); ax[0].legend(loc='upper right'); ax[0].grid(alpha=.3)
    ax[0].set_title(f'그림 2.3  LSTM 순부하 예측 결과 및 잔차 (테스트 구간 {day0} ~ {day1})')
    ax[1].bar(ts[sl], resid, width=0.03, color='#c1121f', alpha=.7)
    ax[1].axhline(0, color='#333', lw=.8)
    ax[1].axhline(5, color='#457b9d', lw=.8, ls=':'); ax[1].axhline(-5, color='#457b9d', lw=.8, ls=':')
    ax[1].set_ylabel('잔차 (kW)'); ax[1].grid(alpha=.3)
    _save(fig, 'fig_2_03.png')
    print(f"[fig 2.3] 구간 {day0}~{day1}, 잔차 ±5kW 이내 비율 {within5:.1f}%")


# ── 2.4 세 방식 성능 비교 (막대) ─────────────────────────────────
def fig_2_4():
    m = pd.read_csv(os.path.join(REPO, 'comparison_results', 'comparison_metrics.csv'))
    m.columns = [c.strip('﻿') for c in m.columns]

    def val(cat, key, name):
        r = m[(m['카테고리'] == cat) & (m['지표'] == key)]
        return float(r[name].iloc[0]) if len(r) else np.nan

    specs = [
        ('요금 절감률 (%)',      'economic', 'cost_saving_rate_pct'),
        ('최대부하 절감률 (%)',  'economic', 'peak_saving_rate_pct'),
        ('SOC 체류율 (%)',       'stability', 'soc_in_band_rate_pct'),
        ('배터리 사이클',        'stability', 'cycle_count'),
        ('순 절감액 (원)',       'economic', 'net_saving_won'),
    ]
    names = ['Rule-Based', 'LSTM', 'GRU']
    fig, axes = plt.subplots(1, 5, figsize=(15, 3.6))
    for ax, (title, cat, key) in zip(axes, specs):
        vals = [val(cat, key, n) for n in names]
        ax.bar(names, vals, color=[COLORS[n] for n in names], alpha=.9)
        ax.set_title(title, fontsize=10); ax.grid(axis='y', alpha=.3)
        ax.tick_params(axis='x', rotation=20)
        for i, v in enumerate(vals):
            ax.text(i, v, f'{v:,.0f}' if abs(v) > 100 else f'{v:.2f}',
                    ha='center', va='bottom', fontsize=8)
    fig.suptitle('그림 2.4  세 방식 성능 비교', y=1.03)
    _save(fig, 'fig_2_04.png')


# ── 2.5 정정 단계별 순절감액 + 요금적용전력 (★ 이중 y축) ──────────
def fig_2_5():
    t = pd.read_csv(os.path.join(RESULTS, 'table_2_11_stages.csv'))
    t = t[t['단계'] != 'Rule-Based']
    labels = t['단계'].tolist()
    net = t['순절감액(원)'].values
    peak = t['요금적용전력(kW)'].values
    colors = ['#adb5bd'] * len(labels)
    colors[-1] = '#e07a5f'                       # F 단계만 강조
    fig, ax1 = plt.subplots(figsize=(10, 5.2))
    ax1.axhline(0, color='#c1121f', lw=1.2, ls='-', zorder=1)   # 0선 명시
    ax1.bar(labels, net, color=colors, zorder=2)
    ax1.set_ylabel('순 절감액 (원)'); ax1.grid(axis='y', alpha=.3)
    for i, v in enumerate(net):
        ax1.text(i, v, f'{v:,.0f}', ha='center',
                 va='bottom' if v >= 0 else 'top', fontsize=8)
    ax2 = ax1.twinx()
    ax2.plot(labels, peak, color='#1d3557', marker='o', lw=1.8, label='요금적용전력')
    ax2.set_ylabel('요금적용전력 (kW)')
    ax1.set_title('그림 2.5  정정 단계별 순 절감액과 요금적용전력 (A→F)')
    ax2.legend(loc='upper left')
    _save(fig, 'fig_2_05.png')


# ── 2.6 계절별 절감률 비교 ───────────────────────────────────────
def _season(m):
    if m in (6, 7, 8):   return '여름'
    if m in (11, 12, 1, 2): return '겨울'
    return '봄·가을'


def fig_2_6(res):
    seasons = ['여름', '봄·가을', '겨울']
    names = ['Rule-Based', 'LSTM', 'GRU']
    data = {n: [] for n in names}
    for n in names:
        df = res[n].copy()
        df['season'] = df['timestamp'].dt.month.map(_season)
        df['baseline'] = (df['load_kw'] - df['solar_kw']).clip(lower=0)
        for s in seasons:
            d = df[df['season'] == s]
            cb = (d['baseline'] * d['tariff_rate']).sum()
            cr = (d['grid_power_kw'].clip(lower=0) * d['tariff_rate']).sum()
            data[n].append((cb - cr) / cb * 100 if cb else 0.0)
    x = np.arange(len(seasons)); w = 0.25
    fig, ax = plt.subplots(figsize=(9, 4.8))
    for i, n in enumerate(names):
        ax.bar(x + (i - 1) * w, data[n], w, color=COLORS[n], label=n)
    ax.set_xticks(x); ax.set_xticklabels(seasons)
    ax.set_ylabel('요금 절감률 (%)'); ax.grid(axis='y', alpha=.3); ax.legend()
    ax.set_title('그림 2.6  계절별 요금 절감률 비교')
    _save(fig, 'fig_2_06.png')


# ── 2.7 예측 기여도 분해 ─────────────────────────────────────────
def fig_2_7():
    p = pd.read_csv(os.path.join(RESULTS, 'prediction_contribution.csv'))
    p.columns = [c.strip('﻿') for c in p.columns]
    order = ['no_pred', 'persistence', 'LSTM', 'GRU', 'perfect']
    label = {'no_pred': '무예측', 'persistence': 'Persistence',
             'LSTM': 'LSTM', 'GRU': 'GRU', 'perfect': '완전예측'}
    p = p.set_index('prediction_source').reindex(order).dropna(how='all')
    fig, ax = plt.subplots(figsize=(9, 4.8))
    xs = [label[i] for i in p.index]
    ax.bar(xs, p['net_saving_won'], color=['#adb5bd', '#8ecae6',
           COLORS['LSTM'], COLORS['GRU'], '#588157'][:len(p)])
    ax.set_ylabel('순 절감액 (원)'); ax.grid(axis='y', alpha=.3)
    for i, v in enumerate(p['net_saving_won']):
        ax.text(i, v, f'{v:,.0f}', ha='center', va='bottom', fontsize=8)
    ax.set_title('그림 2.7  예측 소스별 순 절감액 (예측 기여도 분해)')
    _save(fig, 'fig_2_07.png')


# ── 2.8 / 2.9 일별 동작 (같은 날짜) ─────────────────────────────
def _peak_day(base):
    b = base.copy()
    b['date'] = b['timestamp'].dt.date
    daily = b.groupby('date')['load_kw'].mean()
    return daily.idxmax()


def _fig_daily(df, day, title, fname):
    d = df[df['timestamp'].dt.date == day].reset_index(drop=True)
    h = d['timestamp'].dt.hour
    fig, ax1 = plt.subplots(figsize=(10, 5))
    ax1.plot(h, d['load_kw'], color='#333', lw=1.6, label='부하')
    ax1.plot(h, d['solar_kw'], color='#f4a261', lw=1.4, label='태양광')
    ax1.bar(h, d['discharge_kw'], color='#2a9d8f', alpha=.6, label='방전')
    ax1.bar(h, -d['charge_kw'], color='#e76f51', alpha=.6, label='충전')
    ax1.set_xlabel('시각 (h)'); ax1.set_ylabel('전력 (kW)')
    ax1.grid(alpha=.3); ax1.legend(loc='upper left', fontsize=8)
    ax2 = ax1.twinx()
    ax2.plot(h, d['soc'] * 100, color='#6a4c93', lw=1.6, ls='--', label='SOC')
    ax2.set_ylabel('SOC (%)'); ax2.set_ylim(0, 100)
    ax2.legend(loc='upper right', fontsize=8)
    ax1.set_title(f'{title}  ({day})')
    _save(fig, fname)


def fig_2_8_2_9(res, day):
    _fig_daily(res['Rule-Based'], day, '그림 2.8  규칙 기반 일별 동작', 'fig_2_08.png')
    _fig_daily(res['LSTM'], day, '그림 2.9  예측 기반(LSTM) 일별 동작', 'fig_2_09.png')


# ── 2.10 SOC 추세 비교 (7일, 3궤적) ─────────────────────────────
def fig_2_10(res, day):
    # [B-4] 2.8·2.9 와 같은 주간(피크일부터 7일)
    d0 = pd.Timestamp(day); d1 = d0 + pd.Timedelta(days=7)
    fig, ax = plt.subplots(figsize=(11, 4.8))
    for n in ['Rule-Based', 'LSTM', 'GRU']:
        d = res[n]
        w = d[(d['timestamp'] >= d0) & (d['timestamp'] < d1)]
        ax.plot(w['timestamp'], w['soc'] * 100, color=COLORS[n], lw=1.5, label=n)
    ax.axhspan(20, 80, color='#2a9d8f', alpha=.10, label='권장 20~80%')
    ax.axhline(10, color='#c1121f', lw=.6, ls=':'); ax.axhline(90, color='#c1121f', lw=.6, ls=':')
    ax.set_ylabel('SOC (%)'); ax.set_ylim(0, 100); ax.grid(alpha=.3); ax.legend(loc='upper right')
    ax.set_title(f'그림 2.10  SOC 추세 비교 ({day} 부터 7일)')
    _save(fig, 'fig_2_10.png')


# ── 2.11 일일 충·방전 전환 빈도 ──────────────────────────────────
def fig_2_11(res):
    names = ['Rule-Based', 'LSTM', 'GRU']
    freqs = []
    for n in names:
        a = res[n]['action'].tolist()
        tr = sum(1 for k in range(1, len(a)) if {a[k-1], a[k]} == {'charge', 'discharge'})
        freqs.append(tr / (len(a) / 24))
    fig, ax = plt.subplots(figsize=(7, 4.6))
    ax.bar(names, freqs, color=[COLORS[n] for n in names], alpha=.9)
    for i, v in enumerate(freqs):
        ax.text(i, v, f'{v:.2f}', ha='center', va='bottom', fontsize=9)
    ax.set_ylabel('전환 빈도 (회/일)'); ax.grid(axis='y', alpha=.3)
    ax.set_title('그림 2.11  일일 충·방전 전환 빈도')
    _save(fig, 'fig_2_11.png')


# ── 2.12 세 평가축 레이더 ────────────────────────────────────────
def fig_2_12():
    m = pd.read_csv(os.path.join(REPO, 'comparison_results', 'comparison_metrics.csv'))
    m.columns = [c.strip('﻿') for c in m.columns]

    def val(cat, key, name):
        r = m[(m['카테고리'] == cat) & (m['지표'] == key)]
        return float(r[name].iloc[0]) if len(r) else np.nan

    names = ['Rule-Based', 'LSTM', 'GRU']
    # 축: 경제(순절감), 에너지(커튼일먼트 낮을수록↑), 안정성(SOC체류)
    axes_def = [
        ('경제성',   lambda n: val('economic', 'net_saving_won', n)),
        ('에너지효율', lambda n: -val('energy', 'curtailment_kwh', n)),
        ('운영안정성', lambda n: val('stability', 'soc_in_band_rate_pct', n)),
    ]
    raw = {lab: [f(n) for n in names] for lab, f in axes_def}
    # 0~1 정규화
    norm = {}
    for lab, vals in raw.items():
        lo, hi = min(vals), max(vals)
        norm[lab] = [(v - lo) / (hi - lo) if hi > lo else 0.5 for v in vals]
    labels = list(raw.keys())
    ang = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
    ang += ang[:1]
    fig, ax = plt.subplots(figsize=(6, 6), subplot_kw=dict(polar=True))
    for i, n in enumerate(names):
        vals = [norm[lab][i] for lab in labels] + [norm[labels[0]][i]]
        ax.plot(ang, vals, color=COLORS[n], lw=2, label=n)
        ax.fill(ang, vals, color=COLORS[n], alpha=.12)
    ax.set_xticks(ang[:-1]); ax.set_xticklabels(labels)
    ax.set_yticklabels([]); ax.legend(loc='upper right', bbox_to_anchor=(1.25, 1.1))
    ax.set_title('그림 2.12  세 평가축 종합 비교 (정규화)')
    _save(fig, 'fig_2_12.png')


def main():
    base, res = load_all()
    print("[그림 생성]")
    fig_2_3(base)
    fig_2_4()
    fig_2_5()
    fig_2_6(res)
    fig_2_7()
    day = _peak_day(base)
    fig_2_8_2_9(res, day)
    fig_2_10(res, day)
    fig_2_11(res)
    fig_2_12()
    print("완료 — figures/ (2.1 시스템구성·2.2 게이트구조는 개념도로 별도 유지)")


if __name__ == '__main__':
    main()
