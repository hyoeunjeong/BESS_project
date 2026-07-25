
"""
Rule-Based vs LSTM vs GRU  ─  BESS 운영 성능 비교 (정정본)

원본 대비 변경 사항
─────────────────────────────────────────────────────────────────────
[정정 1] 동일 구간 보장
  기존: timestamp 의 min/max '범위'로만 잘라냈다.
        → 범위 안에 뚫린 시점(예: SMP 결측으로 인한 매일 00시)이
          방식마다 다르면 서로 다른 시점 집합을 비교하게 된다.
        실제로 기존 결과에서 total_load_kwh 가
          Rule-Based 436,839 kWh vs LSTM/GRU 419,743 kWh 로 달랐고,
          off_peak 기준요금만 1,595,000원 차이가 났다(= 00시 행 유무).
  정정: 세 결과의 timestamp '교집합'으로 inner join 한다.

[정정 2] 기준 시나리오 공유
  기존: 방식별로 각자 baseline 을 만들어 baseline_cost 가 서로 달랐다.
        → 절감률의 분모가 달라 비교가 성립하지 않는다.
  정정: 정렬된 공통 프레임에서 baseline 을 한 번만 만들어 공유한다.

[정정 3] 예측 오차의 y_true 정의 통일
  기존: y_true = load_kw - solar_kw   (클리핑 없음)
        학습 타깃은 max(load - solar, 0) 이므로 표 2.6 과 다른 값이 나온다.
  정정: 학습 타깃과 동일하게 0 하한 클리핑을 적용한다.

[추가] 정합성 검증
  · 세 방식의 load_kw / solar_kw 계열이 동일한지 확인
  · 시점 수, 결측 시각대를 콘솔에 명시적으로 출력
─────────────────────────────────────────────────────────────────────
"""

import os
import sys
import numpy as np
import pandas as pd

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_LSTM_DIR = os.path.join(_THIS_DIR, 'DL_LSTM')
_GRU_DIR  = os.path.join(_THIS_DIR, 'DL_GRU')
_RB_DIR   = os.path.join(_THIS_DIR, 'rule_based')

if _LSTM_DIR not in sys.path:
    sys.path.insert(0, _LSTM_DIR)

import config
from evaluator import evaluate_all, calc_prediction_metrics, _pad

SOURCES = {
    'Rule-Based': os.path.join(_RB_DIR,   'results', 'rb_simulation_result.csv'),
    'LSTM'      : os.path.join(_LSTM_DIR, 'results', 'lstm_simulation_result.csv'),
    'GRU'       : os.path.join(_GRU_DIR,  'results', 'gru_simulation_result.csv'),
}
OUT_DIR = os.path.join(_THIS_DIR, 'comparison_results')


# =====================================================================
# 로드 및 정렬
# =====================================================================
def load_results() -> dict:
    frames = {}
    for name, path in SOURCES.items():
        if not os.path.exists(path):
            print(f"  [건너뜀] {name}: 파일 없음 ({path})")
            continue
        df = pd.read_csv(path, parse_dates=['timestamp'])
        df = df.drop_duplicates('timestamp').sort_values('timestamp').reset_index(drop=True)
        # [감사] 세대 오독 방지 — 2026-07 감사에서 8,372행 구세대 파일 6개가 교차
        #        폴더에 동일 파일명으로 존재했다. 정본은 RB 8,760 / LSTM·GRU 8,736.
        _expect = {'Rule-Based': 8760, 'LSTM': 8736, 'GRU': 8736}.get(name)
        if _expect is not None:
            assert len(df) == _expect, \
                f'{name}: 행수 {len(df)} (정본 {_expect}). 구세대(8,372) 파일 의심 — 중단.'
        frames[name] = df
        print(f"  [로드] {name:12s} {len(df):>6,}행  "
              f"{df['timestamp'].min().date()} ~ {df['timestamp'].max().date()}")
    if not frames:
        raise SystemExit("비교할 결과 파일이 없습니다. 각 폴더에서 main.py 를 먼저 실행하세요.")
    return frames


def align_frames(frames: dict) -> dict:
    """
    [정정 1] timestamp 교집합으로 정렬한다.
    범위(min~max)가 아니라 실제 시점 집합의 교집합을 쓴다.
    """
    common = None
    for df in frames.values():
        s = set(df['timestamp'])
        common = s if common is None else (common & s)
    common = pd.DatetimeIndex(sorted(common))

    print(f"\n[동일 구간 정렬]")
    sliced = []
    for name, df in frames.items():
        dropped = len(df) - len(common)
        print(f"  {name:12s} {len(df):>6,}행 → {len(common):>6,}행  (탈락 {dropped:,})")
        if dropped > 0:
            sliced.append((name, dropped))

    if sliced:
        print("\n  [경고] 아래 방식은 시뮬레이션 결과를 사후에 잘라낸 상태입니다.")
        for name, d in sliced:
            print(f"         · {name}: {d:,}행 탈락")
        print("         SOC 궤적과 에너지 수지는 연속 시뮬레이션의 산물이므로,")
        print("         결과를 사후에 슬라이싱하면 충·방전 수지가 맞지 않습니다.")
        print("         (라운드트립 효율이 100%를 넘으면 이 문제입니다)")
        print("         → 공통 구간에서 처음부터 재시뮬레이션할 것.")

    out = {}
    for name, df in frames.items():
        a = (df.set_index('timestamp')
               .loc[common]
               .rename_axis('timestamp')
               .reset_index())
        out[name] = a

    # 결측 시각대 진단
    hours = pd.Series(common.hour).value_counts()
    missing = sorted(set(range(24)) - set(hours.index))
    if missing:
        print(f"  [주의] 공통 구간에서 완전히 누락된 시각대: {missing}")
        print(f"         → SMP 수집 파서(api_client) 점검 필요")
    n_days = len(set(common.date))
    print(f"  최종 비교 구간: {len(common):,}시점 / {n_days}일 "
          f"(완전한 1년이면 8,760)")
    return out


def check_consistency(frames: dict):
    """세 방식이 정말 동일한 부하·태양광을 썼는지 확인"""
    print("\n[입력 정합성 검증]")
    ref_name, ref = next(iter(frames.items()))
    ok = True
    for name, df in frames.items():
        if name == ref_name:
            continue
        for col in ('load_kw', 'solar_kw'):
            if col not in df.columns or col not in ref.columns:
                continue
            d = np.abs(df[col].values - ref[col].values).max()
            mark = 'O' if d < 1e-6 else 'X'
            if d >= 1e-6:
                ok = False
            print(f"  {mark}  {name:12s} vs {ref_name:12s}  {col:9s} 최대차 {d:.3e}")
    if ok:
        print("  → 세 방식이 동일한 부하·태양광 계열을 사용함")
    else:
        print("  → [경고] 입력이 다릅니다. 성능 차이를 제어 로직 탓으로 볼 수 없습니다.")


def build_shared_baseline(df: pd.DataFrame) -> pd.DataFrame:
    """
    [정정 2] 무제어 기준 시나리오를 한 번만 만들어 모든 방식이 공유한다.
    """
    b = df.copy()
    b['bess_power_kw'] = 0.0
    b['charge_kw']     = 0.0
    b['discharge_kw']  = 0.0
    b['soc']           = 0.0
    b['action']        = 'none'
    b['grid_power_kw'] = b['load_kw'] - b['solar_kw']
    ts = pd.to_datetime(b['timestamp'])
    if 'tariff_period' not in b.columns:
        b['tariff_period'] = [config.get_tariff_period(int(h), int(m), int(wd), d)
                              for h, m, wd, d in zip(ts.dt.hour, ts.dt.month, ts.dt.weekday, ts.dt.date)]
    b['tariff_rate'] = [config.get_tariff_rate(int(h), int(m), int(wd), d)
                        for h, m, wd, d in zip(ts.dt.hour, ts.dt.month, ts.dt.weekday, ts.dt.date)]
    return b


def prediction_metrics(df: pd.DataFrame):
    """[정정 3] 학습 타깃과 동일하게 0 하한 클리핑을 적용
    [§3-3] 논문 표 예측 지표는 test 구간(뒤 15%)을 헤드라인으로 쓴다.
           full 구간 값은 mae_full_kw 등으로 참고 보존."""
    if 'predicted_net_load_kw' not in df.columns:
        return None
    y_true = np.clip(df['load_kw'].values - df['solar_kw'].values, 0, None)
    y_pred = df['predicted_net_load_kw'].values
    n  = len(y_true)
    t2 = int(n * (config.TRAIN_RATIO + config.VAL_RATIO))
    test = calc_prediction_metrics(y_true[t2:], y_pred[t2:])   # §3-3 논문 표
    full = calc_prediction_metrics(y_true, y_pred)             # 참고
    test['mae_full_kw']  = full['mae_kw']
    test['rmse_full_kw'] = full['rmse_kw']
    test['nmae_full_pct'] = full['nmae_pct']
    return test


# =====================================================================
# 출력
# =====================================================================
ROWS = [
    ('[Ⅰ. 경제적 효율]',        None, None, None),
    ('  전력요금 절감률',        'economic', 'cost_saving_rate_pct',   '%'),
    ('  전력요금 절감액',        'economic', 'cost_saving_won',        '원'),
    ('  최대부하 절감률',        'economic', 'peak_saving_rate_pct',   '%'),
    ('  중간부하 절감률',        'bd',       'mid_peak',               '%'),
    ('  경부하 절감률',          'bd',       'off_peak',               '%'),
    ('  계통 측 최대전력',       'economic', 'peak_demand_kw',         'kW'),
    ('  최대수요전력 저감률',    'economic', 'peak_demand_reduction_pct', '%'),
    ('  기본요금 증감',          'economic', 'base_charge_delta_won',  '원'),
    ('  순 절감액',              'economic', 'net_saving_won',         '원'),

    ('[Ⅱ. 에너지 효율]',        None, None, None),
    ('  총 처리 에너지',         'energy', 'total_throughput_kwh',     'kWh'),
    ('  배터리 사이클 수',       'stability', 'cycle_count',           '회'),
    ('  일일 전환 빈도',         'stability', 'transitions_per_day',   '회/일'),
    ('  라운드트립 효율',        'energy', 'roundtrip_efficiency_pct', '%'),
    ('  BESS 활용률',            'energy', 'bess_utilization_pct',     '%'),
    ('  에너지 자립률',          'energy', 'self_sufficiency_pct',     '%'),
    ('  재생E 기원 자립률',      'energy', 're_self_sufficiency_pct',  '%'),
    ('  BESS 기인 역송',         'energy', 'bess_export_kwh',          'kWh'),
    ('  커튼일먼트',             'energy', 'curtailment_kwh',          'kWh'),

    ('[Ⅲ. 운영 안정성]',        None, None, None),
    ('  SOC 권장구간 체류율',    'stability', 'soc_in_band_rate_pct',  '%'),
    ('  SOC > 80% 비율',         'stability', 'soc_above_band_pct',    '%'),
    ('  SOC < 20% 비율',         'stability', 'soc_below_band_pct',    '%'),
    ('  과충전 횟수',            'stability', 'overcharge_count',      '회'),
    ('  과방전 횟수',            'stability', 'overdischarge_count',   '회'),
    ('  충전 횟수',              'stability', 'charge_count',          '회'),
    ('  방전 횟수',              'stability', 'discharge_count',       '회'),
    ('  대기(idle) 횟수',        'stability', 'idle_count',            '회'),
    ('  예측값 참조 발동 비율',  'stability', 'prediction_driven_pct', '%'),
]


def _get(m, cat, key):
    if cat == 'bd':
        return m.get('economic', {}).get('breakdown', {}).get(key, {}).get('rate_pct')
    return m.get(cat, {}).get(key)


def print_table(metrics: dict):
    names = list(metrics.keys())
    LW, CW = 30, 16
    sep = "=" * (LW + CW * len(names) + 2)
    print(f"\n{sep}")
    print("  " + _pad('지표', LW) + "".join(_pad(n, CW, 'right') for n in names))
    print(sep)

    for label, cat, key, unit in ROWS:
        if cat is None:
            print(f"\n  {label}")
            continue
        vals = [_get(metrics[n], cat, key) for n in names]
        if all(v is None for v in vals):
            continue
        cells = []
        for v in vals:
            if v is None:
                cells.append('-')
            elif isinstance(v, str):
                cells.append(v)
            elif unit == '원':
                cells.append(f"{v:+,.0f}" if 'delta' in str(key) else f"{v:,.0f}")
            elif unit == '회' and float(v).is_integer():
                cells.append(f"{v:,.0f}")
            else:
                cells.append(f"{v:,.2f}")
        print("  " + _pad(label, LW) + "".join(_pad(c, CW, 'right') for c in cells))

    # 예측 성능
    if any('prediction' in metrics[n] for n in names):
        print(f"\n  [예측 성능 — test 구간(뒤 15%), 학습 타깃(0 하한 클리핑) 기준]")
        for k, lab, fmt in (('mae_kw', 'MAE (kW)', '{:.3f}'),
                            ('rmse_kw', 'RMSE (kW)', '{:.3f}'),
                            ('nmae_pct', '상대 오차 (%)', '{:.2f}')):
            cells = [fmt.format(metrics[n]['prediction'][k])
                     if 'prediction' in metrics[n] else '-' for n in names]
            print("  " + _pad('  ' + lab, LW) + "".join(_pad(c, CW, 'right') for c in cells))
    print(f"\n{sep}\n")


def save_csv(metrics: dict, out_path: str):
    names = list(metrics.keys())
    rows = []
    for cat in ('economic', 'energy', 'stability', 'prediction'):
        for name in names:
            for k, v in metrics[name].get(cat, {}).items():
                if isinstance(v, dict):
                    continue
                rows.append({'카테고리': cat, '지표': k, '방식': name, '값': v})
    wide = (pd.DataFrame(rows)
              .pivot_table(index=['카테고리', '지표'], columns='방식',
                           values='값', aggfunc='first')
              .reset_index())
    wide.to_csv(out_path, index=False, encoding='utf-8-sig')
    return wide


# =====================================================================
def main():
    print("=" * 78)
    print("  Rule-Based vs LSTM vs GRU  ─  BESS 성능 비교 (동일 구간 보장)")
    print("=" * 78)
    print(f"  TARIFF_MODE = {getattr(config, 'TARIFF_MODE', 'n/a')}\n")

    frames = load_results()
    frames = align_frames(frames)
    check_consistency(frames)

    baseline = build_shared_baseline(next(iter(frames.values())))

    metrics = {}
    for name, df in frames.items():
        pm = prediction_metrics(df)
        m = evaluate_all(df, baseline)
        if pm:
            m['prediction'] = pm
        metrics[name] = m

    print_table(metrics)

    os.makedirs(OUT_DIR, exist_ok=True)
    out_csv = os.path.join(OUT_DIR, 'comparison_metrics.csv')
    save_csv(metrics, out_csv)
    print(f"비교 결과 저장: {out_csv}")
    print(f"기준(무제어) 전력량요금: "
          f"{metrics[list(metrics)[0]]['economic']['baseline_cost_won']:,.0f} 원 "
          f"— 모든 방식이 동일한 분모를 사용함\n")


if __name__ == '__main__':
    main()
