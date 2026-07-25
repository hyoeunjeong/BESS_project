# -*- coding: utf-8 -*-
"""표 2.13 — 계절별 전력요금 절감률(%) 산출 스크립트.

기존 함수만 재사용한다(신규 산식 구현 없음):
  - compare.align_frames          : timestamp 교집합 정렬(8,736)
  - compare.build_shared_baseline : 공유 무제어 기준(grid_base = load - solar, cost = clip(0) × tariff_rate)
  - make_figures._season          : 계절 구분(여름 {6,7,8} / 겨울 {11,12,1,2} / 봄·가을 나머지)

출력: results/table_2_13_seasonal.csv
      계절 × {baseline_cost, <model>_bess_cost, <model>_saving, <model>_rate}  (미반올림 원가 포함)

매 실행 시 검산 2건을 출력하고, 불일치하면 assert 로 멈춘다:
  (A) 계절 합산 절감률 == 전체(8,736) 직접 절감률  (표2.9 전체와 대조)
  (B) 계절별 시점 수 합 == 8,736 (누락·중복 없음)
"""
import os
import pandas as pd
import compare
from make_figures import _season

# [감사] 절대경로로 고정한다. 2026-07 감사에서 8,372행 구세대 파일 6개가
# 동일 파일명으로 교차 폴더(DL_GRU/…/lstm_…, DL_LSTM/…/gru_…)에 존재했으므로,
# 상대경로·cwd 의존은 세대 오독 위험이 있다. 명시적 폴더+파일명으로만 주소 지정한다.
_HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = {
    'Rule-Based': os.path.join(_HERE, 'rule_based', 'results', 'rb_simulation_result.csv'),
    'LSTM':       os.path.join(_HERE, 'DL_LSTM',    'results', 'lstm_simulation_result.csv'),
    'GRU':        os.path.join(_HERE, 'DL_GRU',     'results', 'gru_simulation_result.csv'),
}
# 정본 행수(정렬 전). 구세대(8,372)를 읽으면 즉시 멈춘다.
EXPECTED_ROWS = {'Rule-Based': 8760, 'LSTM': 8736, 'GRU': 8736}

# 표2.9 전체 절감률(감사 확정) — 검산 (A) 대조용
TABLE_2_9_ALL = {'Rule-Based': 0.747, 'LSTM': 0.866, 'GRU': 0.872}


def main():
    frames = {k: pd.read_csv(v, parse_dates=['timestamp']) for k, v in MODELS.items()}
    for k, df in frames.items():                                    # [감사] 세대 오독 방지
        assert len(df) == EXPECTED_ROWS[k], \
            f'{k}: 행수 {len(df)} (정본 {EXPECTED_ROWS[k]}). 구세대(8,372) 파일 의심 — 중단.'
    aligned = compare.align_frames(frames)                          # 8,736 교집합
    for k, df in aligned.items():                                   # [감사] 정렬 후 공통창 확인
        assert len(df) == 8736, f'{k}: 정렬 후 {len(df)}행 (기대 8,736) — 중단.'

    # 공유 무제어 기준(모든 방식 동일). tariff_rate 도 여기서 확정한다.
    base = compare.build_shared_baseline(aligned['Rule-Based'])
    rate = base['tariff_rate']
    base_cost = base['grid_power_kw'].clip(lower=0) * rate           # 무제어 요금(시점별)
    ctrl_cost = {name: aligned[name]['grid_power_kw'].clip(lower=0) * rate for name in MODELS}
    season = base['timestamp'].dt.month.map(_season)

    SEASONS = ['여름', '봄·가을', '겨울']
    rows = []
    # 계절 합산용 누적치(미반올림)
    agg = {name: {'base': 0.0, 'ctrl': 0.0} for name in MODELS}
    count_sum = 0

    for s in SEASONS:
        m = (season == s)
        count_sum += int(m.sum())
        bc = base_cost[m].sum()                                     # 계절 baseline_cost(공유)
        rec = {'계절': s, 'n시점': int(m.sum()), 'baseline_cost': bc}
        for name in MODELS:
            cc = ctrl_cost[name][m].sum()
            saving = bc - cc
            rec[f'{name}_bess_cost'] = cc
            rec[f'{name}_saving']    = saving
            rec[f'{name}_rate']      = (saving / bc * 100) if bc > 0 else float('nan')
            agg[name]['base'] += bc
            agg[name]['ctrl'] += cc
        rows.append(rec)

    out = pd.DataFrame(rows)
    out.to_csv('results/table_2_13_seasonal.csv', index=False, encoding='utf-8-sig')

    # ── 표 (반올림 3자리) ─────────────────────────────
    print('\n[표 2.13] 계절별 전력요금 절감률 (%)')
    disp = out[['계절'] + [f'{n}_rate' for n in MODELS]].copy()
    disp.columns = ['계절'] + list(MODELS)
    for n in MODELS:
        disp[n] = disp[n].round(3)
    print(disp.to_string(index=False))

    # ── [1a-1] 미반올림 원가 ─────────────────────────
    print('\n[1a-1] 계절별 baseline_cost / bess_cost / saving (미반올림, 원)')
    for _, r in out.iterrows():
        print(f"  {r['계절']:<5} base={r['baseline_cost']:,.4f}")
        for n in MODELS:
            print(f"        {n:<11} bess_cost={r[f'{n}_bess_cost']:,.4f}  "
                  f"saving={r[f'{n}_saving']:,.4f}  rate={r[f'{n}_rate']:.6f}%")

    # ── [1a-2] 봄·가을 예측기반/규칙 배수 (미반올림) ──
    sg = out[out['계절'] == '봄·가을'].iloc[0]
    r_rb, r_lstm, r_gru = sg['Rule-Based_rate'], sg['LSTM_rate'], sg['GRU_rate']
    print('\n[1a-2] 봄·가을 배수 (미반올림 rate 기준)')
    print(f"  규칙={r_rb:.6f}%  LSTM={r_lstm:.6f}%  GRU={r_gru:.6f}%")
    print(f"  LSTM/규칙 = {r_lstm / r_rb:.2f}배   GRU/규칙 = {r_gru / r_rb:.2f}배")
    print(f"  → 본문 '7.9배'(원본 0.563/0.071=7.93) 재현값 확정: {r_lstm / r_rb:.2f}배")

    # ── [1b] 검산 ────────────────────────────────────
    print('\n[1b] 검산')
    # (A) 계절 합산 절감률 == 전체 직접 절감률, 그리고 표2.9 대조
    print('  (A) 계절합산 vs 전체직접 vs 표2.9')
    for n in MODELS:
        overall_from_seasons = (agg[n]['base'] - agg[n]['ctrl']) / agg[n]['base'] * 100
        overall_direct = (base_cost.sum() - ctrl_cost[n].sum()) / base_cost.sum() * 100
        ref = TABLE_2_9_ALL[n]
        print(f"      {n:<11} 계절합산={overall_from_seasons:.6f}%  직접={overall_direct:.6f}%  표2.9={ref}")
        assert abs(overall_from_seasons - overall_direct) < 1e-6, f'{n}: 계절합산≠직접'
        assert abs(overall_from_seasons - ref) < 5e-4, f'{n}: 표2.9 전체와 불일치'
    # (B) 시점 수 합
    print(f"  (B) 계절별 시점 수 합 = {count_sum} (여름+봄가을+겨울)")
    assert count_sum == len(base) == 8736, f'시점 수 불일치: {count_sum}'
    print('  ✓ 검산 (A)(B) 통과')
    return out


if __name__ == '__main__':
    main()
