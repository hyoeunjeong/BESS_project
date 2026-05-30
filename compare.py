import os
import sys
import numpy as np
import pandas as pd

# 경로 설정 — DL_LSTM 폴더의 모듈(config, evaluator)을 import 가능하게 함
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_LSTM_DIR = os.path.join(_THIS_DIR, 'DL_LSTM')
_RB_DIR   = os.path.join(_THIS_DIR, 'rule_based')

# DL_LSTM 폴더를 sys.path 최우선으로 추가
if _LSTM_DIR not in sys.path:
    sys.path.insert(0, _LSTM_DIR)

import config
from evaluator import evaluate_all, _display_width, _pad


# ── 경로 설정 
RB_CSV   = os.path.join(_RB_DIR,   'results', 'rb_simulation_result.csv')
LSTM_CSV = os.path.join(_LSTM_DIR, 'results', 'lstm_simulation_result.csv')
OUT_DIR  = os.path.join(_THIS_DIR, 'comparison_results')


# 파일 검증
def _check_files() -> bool:
    ok = True
    print(f"\n[경로 확인]")
    print(f"  Rule-Based CSV: {RB_CSV}")
    print(f"  LSTM CSV      : {LSTM_CSV}")
    for label, p in (('Rule-Based', RB_CSV), ('LSTM', LSTM_CSV)):
        if not os.path.exists(p):
            print(f"  [오류] {label} 파일 없음: {p}")
            ok = False
    return ok


# Baseline 구성 (BESS 없는 시나리오)
def build_baseline(df: pd.DataFrame) -> pd.DataFrame:
    """
    BESS 없는 기준 시나리오 (계통 = 부하 - 태양광)
    evaluator.calc_economic 과 정합되도록 모든 컬럼을 0으로 채움
    """
    b = df.copy()
    b['bess_power_kw'] = 0.0
    b['charge_kw']     = 0.0
    b['discharge_kw']  = 0.0
    b['soc']           = 0.0
    b['action']        = 'none'
    b['grid_power_kw'] = b['load_kw'] - b['solar_kw']
    # tariff_rate 가 없으면 config 에서 매핑
    if 'tariff_rate' not in b.columns and 'tariff_period' in b.columns:
        b['tariff_rate'] = b['tariff_period'].map(config.TOU_TARIFF)
    return b


# 비교 표 출력
def print_comparison_table(rb_m: dict, dl_m: dict):
    """Rule-Based 와 LSTM 의 모든 지표를 나란히 출력"""
    sep = "=" * 78
    label_width = 36   # 한글 표시 너비 기준
    col_width   = 18   # 우측 값 폭

    print(f"\n{sep}")
    print(f"  {_pad('지표', label_width)}"
          f"{_pad('Rule-Based', col_width, align='right')}"
          f"{_pad('LSTM', col_width, align='right')}"
          f"{_pad('차이', col_width, align='right')}")
    print(sep)

    # 비교 행 정의: (라벨, rb_key, dl_key, 카테고리, 단위, 소수자리)
    # 카테고리: 'eco', 'energy', 'stab', 'pred'
    rows = [
        ('[경제적 효율]',              None, None, None, None, None),
        ('  기준 전기요금',            'eco', 'baseline_cost_won',     '원',  0),
        ('  BESS 운영 요금',           'eco', 'bess_cost_won',         '원',  0),
        ('  요금 절감액',              'eco', 'cost_saving_won',       '원',  0),
        ('  요금 절감률',              'eco', 'cost_saving_rate_pct',  '%',   2),
        ('  피크 요금 절감률',         'eco', 'peak_saving_rate_pct',  '%',   2),

        ('[시간대별 요금 절감률]',     None, None, None, None, None),
        ('  경부하',                   'eco_bd', 'off_peak',           '%',   2),
        ('  중간부하',                 'eco_bd', 'mid_peak',           '%',   2),
        ('  최대부하',                 'eco_bd', 'on_peak',            '%',   2),

        ('[에너지 효율]',              None, None, None, None, None),
        ('  총 부하 에너지',           'energy', 'total_load_kwh',         'kWh', 2),
        ('  총 태양광 발전량',         'energy', 'total_solar_kwh',        'kWh', 2),
        ('  태양광 직접 사용',         'energy', 'direct_solar_kwh',       'kWh', 2),
        ('  BESS 방전 공급',           'energy', 'bess_discharge_kwh',     'kWh', 2),
        ('  자립률',                   'energy', 'self_sufficiency_pct',   '%',   2),
        ('  BESS 활용률',              'energy', 'bess_utilization_pct',   '%',   2),
        ('  라운드트립 효율',          'energy', 'roundtrip_efficiency_pct', '%', 2),
        ('  태양광 이용률',            'energy', 'solar_utilization_pct',  '%',   2),
        ('  에너지 손실량',            'energy', 'energy_loss_kwh',        'kWh', 2),
        ('  커튼일먼트',               'energy', 'curtailment_kwh',        'kWh', 2),

        ('[운영 안정성 및 효율성]',    None, None, None, None, None),
        ('  SOC 범위 초과',            'stab', 'soc_out_of_range_count', '회',  0),
        ('  공급 부족 발생',           'stab', 'supply_shortage_count',  '회',  0),
        ('  배터리 사이클 수',         'stab', 'cycle_count',            '회',  2),
        ('  일일 전환 빈도',           'stab', 'transitions_per_day',    '회/일', 2),
        ('  과충·방전 방지율',         'stab', 'prevention_rate_pct',    '%',   2),
        ('  제어 성공률',              'stab', 'control_success_rate_pct', '%', 2),

        ('[SOC 통계]',                 None, None, None, None, None),
        ('  최대 SOC',                 'stab', 'soc_max_pct',            '%',   2),
        ('  최소 SOC',                 'stab', 'soc_min_pct',            '%',   2),
        ('  평균 SOC',                 'stab', 'soc_avg_pct',            '%',   2),

        ('[동작 횟수]',                None, None, None, None, None),
        ('  충전 횟수',                'stab', 'charge_count',           '회',  0),
        ('  방전 횟수',                'stab', 'discharge_count',        '회',  0),
        ('  대기(idle) 횟수',          'stab', 'idle_count',             '회',  0),
    ]

    for row in rows:
        label = row[0]
        # 섹션 헤더
        if row[1] is None:
            print(f"\n  {label}")
            continue

        category, key, unit, decimals = row[1], row[2], row[3], row[4]

        # 값 추출
        rb_val = _get_value(rb_m, category, key)
        dl_val = _get_value(dl_m, category, key)

        # 둘 다 없으면 스킵
        if rb_val is None and dl_val is None:
            continue

        # 차이 계산 (LSTM - Rule-Based)
        if rb_val is not None and dl_val is not None:
            diff = dl_val - rb_val
        else:
            diff = None

        # 포맷팅
        rb_s   = _fmt(rb_val,  decimals, unit)
        dl_s   = _fmt(dl_val,  decimals, unit)
        diff_s = _fmt_diff(diff, decimals, unit)

        print(f"  {_pad(label, label_width)}"
              f"{_pad(rb_s,   col_width, align='right')}"
              f"{_pad(dl_s,   col_width, align='right')}"
              f"{_pad(diff_s, col_width, align='right')}")

    # LSTM 예측 성능 (LSTM 전용)
    if 'prediction' in dl_m:
        print(f"\n  [LSTM 예측 성능 (참고)]")
        p = dl_m['prediction']
        mae_s  = f"{p['mae_kw']:.3f} kW"
        rmse_s = f"{p['rmse_kw']:.3f} kW"
        mape_s = f"{p['mape_pct']:.2f} %"
        print(f"  {_pad('  MAE',  label_width)}"
              f"{_pad('-', col_width, align='right')}"
              f"{_pad(mae_s, col_width, align='right')}")
        print(f"  {_pad('  RMSE', label_width)}"
              f"{_pad('-', col_width, align='right')}"
              f"{_pad(rmse_s, col_width, align='right')}")
        print(f"  {_pad('  MAPE', label_width)}"
              f"{_pad('-', col_width, align='right')}"
              f"{_pad(mape_s, col_width, align='right')}")

    print(f"\n{sep}\n")


def _get_value(metrics: dict, category: str, key: str):
    """metrics 딕셔너리에서 카테고리/키로 값 추출"""
    if category == 'eco':
        return metrics.get('economic', {}).get(key)
    elif category == 'eco_bd':
        # 시간대별 breakdown 의 rate_pct
        bd = metrics.get('economic', {}).get('breakdown', {})
        return bd.get(key, {}).get('rate_pct')
    elif category == 'energy':
        return metrics.get('energy', {}).get(key)
    elif category == 'stab':
        return metrics.get('stability', {}).get(key)
    return None


def _fmt(val, decimals: int, unit: str) -> str:
    """값 포맷팅"""
    if val is None:
        return '-'
    try:
        if decimals == 0:
            return f"{val:,.0f} {unit}"
        return f"{val:,.{decimals}f} {unit}"
    except (TypeError, ValueError):
        return str(val)


def _fmt_diff(diff, decimals: int, unit: str) -> str:
    """차이 포맷팅 (부호 포함)"""
    if diff is None:
        return '-'
    try:
        sign = '+' if diff >= 0 else ''
        if decimals == 0:
            return f"{sign}{diff:,.0f} {unit}"
        return f"{sign}{diff:,.{decimals}f} {unit}"
    except (TypeError, ValueError):
        return str(diff)


# 종합 우수성 분석
# 지표 메타데이터: (카테고리, 키, 표시명, 방향, 가중치)
# 방향: 'higher' = 높을수록 좋음, 'lower' = 낮을수록 좋음
# 가중치: 핵심 지표 가중 평균 계산용 (0이면 카테고리별 승률에만 포함)
_METRIC_META = [
    # 경제적 효율
    ('eco', 'cost_saving_rate_pct',      '요금 절감률',          'higher', 3.0),
    ('eco', 'peak_saving_rate_pct',      '피크 요금 절감률',     'higher', 2.5),
    ('eco', 'cost_saving_won',           '요금 절감액',          'higher', 0.0),
    # 시간대별 (참고용, 가중치 0)
    ('eco_bd', 'off_peak',               '경부하 절감률',        'higher', 0.0),
    ('eco_bd', 'mid_peak',               '중간부하 절감률',      'higher', 0.0),
    ('eco_bd', 'on_peak',                '최대부하 절감률',      'higher', 0.0),

    # 에너지 효율
    ('energy', 'self_sufficiency_pct',    '자립률',              'higher', 2.0),
    ('energy', 'bess_utilization_pct',    'BESS 활용률',         'higher', 1.5),
    ('energy', 'roundtrip_efficiency_pct','라운드트립 효율',     'higher', 1.0),
    ('energy', 'solar_utilization_pct',   '태양광 이용률',       'higher', 1.0),
    ('energy', 'energy_loss_kwh',         '에너지 손실량',       'lower',  0.0),
    ('energy', 'curtailment_kwh',         '커튼일먼트',          'lower',  0.0),

    # 운영 안정성
    ('stab', 'control_success_rate_pct',  '제어 성공률',         'higher', 2.0),
    ('stab', 'prevention_rate_pct',       '과충·방전 방지율',    'higher', 1.5),
    ('stab', 'soc_out_of_range_count',    'SOC 범위 초과',       'lower',  0.0),
    ('stab', 'supply_shortage_count',     '공급 부족 발생',      'lower',  1.0),
    ('stab', 'cycle_count',               '배터리 사이클 수',    'lower',  0.0),
    ('stab', 'transitions_per_day',       '일일 전환 빈도',      'lower',  0.0),
]

# 카테고리 라벨
_CATEGORY_LABELS = {
    'eco'    : '경제적 효율',
    'eco_bd' : '시간대별 요금',
    'energy' : '에너지 효율',
    'stab'   : '운영 안정성',
}

# 승패 판정 임계값 (상대 차이 %)
_WIN_THRESHOLD = 1.0


def _calc_improvement(rb_val, dl_val, direction: str) -> float | None:
    """
    상대적 개선률 (%) 계산

    direction='higher' : (dl - rb) / |rb| * 100  (높을수록 좋음)
    direction='lower'  : (rb - dl) / |rb| * 100  (낮을수록 좋음)
    """
    if rb_val is None or dl_val is None:
        return None
    try:
        rb_v = float(rb_val)
        dl_v = float(dl_val)
        if abs(rb_v) < 1e-9:
            # 기준값이 0에 가까우면 절대 차이만 반환
            return (dl_v - rb_v) if direction == 'higher' else (rb_v - dl_v)
        if direction == 'higher':
            return (dl_v - rb_v) / abs(rb_v) * 100
        else:
            return (rb_v - dl_v) / abs(rb_v) * 100
    except (TypeError, ValueError):
        return None


def _judge(improvement: float | None) -> str:
    """개선률을 보고 승/무/패 판정"""
    if improvement is None:
        return 'na'
    if improvement > _WIN_THRESHOLD:
        return 'lstm'   # LSTM 우수
    elif improvement < -_WIN_THRESHOLD:
        return 'rb'     # Rule-Based 우수
    else:
        return 'tie'    # 유사


def analyze_superiority(rb_m: dict, dl_m: dict) -> dict:
    """
    각 지표에 대해 LSTM vs Rule-Based 우수성 분석

    Returns
    -------
    dict with keys: 'per_metric', 'per_category', 'overall', 'core_score'
    """
    per_metric = []

    for category, key, label, direction, weight in _METRIC_META:
        rb_val = _get_value(rb_m, category, key)
        dl_val = _get_value(dl_m, category, key)
        if rb_val is None or dl_val is None:
            continue

        improvement = _calc_improvement(rb_val, dl_val, direction)
        verdict     = _judge(improvement)

        per_metric.append({
            'category'   : category,
            'key'        : key,
            'label'      : label,
            'direction'  : direction,
            'weight'     : weight,
            'rb_val'     : rb_val,
            'dl_val'     : dl_val,
            'improvement': improvement,
            'verdict'    : verdict,
        })

    # 카테고리별 집계
    per_category = {}
    main_cats = ('eco', 'energy', 'stab')   # 종합 점수는 이 3개만 사용 (eco_bd 제외)
    for cat in main_cats:
        items = [m for m in per_metric if m['category'] == cat]
        if not items:
            continue
        lstm_wins = sum(1 for m in items if m['verdict'] == 'lstm')
        rb_wins   = sum(1 for m in items if m['verdict'] == 'rb')
        ties      = sum(1 for m in items if m['verdict'] == 'tie')
        total     = lstm_wins + rb_wins + ties
        win_rate  = (lstm_wins / (lstm_wins + rb_wins) * 100) \
                    if (lstm_wins + rb_wins) > 0 else 50.0

        if lstm_wins > rb_wins:
            judgment = 'LSTM'
        elif rb_wins > lstm_wins:
            judgment = 'RB'
        else:
            judgment = '유사'

        per_category[cat] = {
            'lstm_wins': lstm_wins, 'rb_wins': rb_wins, 'ties': ties,
            'total': total, 'win_rate': win_rate, 'judgment': judgment,
        }

    # 종합 점수
    total_lstm = sum(c['lstm_wins'] for c in per_category.values())
    total_rb   = sum(c['rb_wins']   for c in per_category.values())
    total_tie  = sum(c['ties']      for c in per_category.values())
    total_all  = total_lstm + total_rb + total_tie
    overall_rate = (total_lstm / (total_lstm + total_rb) * 100) \
                   if (total_lstm + total_rb) > 0 else 50.0

    if total_lstm > total_rb:
        overall_judgment = 'LSTM'
    elif total_rb > total_lstm:
        overall_judgment = 'RB'
    else:
        overall_judgment = '유사'

    # 핵심 지표 가중 평균 개선률
    core_metrics = [m for m in per_metric if m['weight'] > 0 and m['improvement'] is not None]
    if core_metrics:
        total_weight = sum(m['weight'] for m in core_metrics)
        weighted_sum = sum(m['improvement'] * m['weight'] for m in core_metrics)
        core_score = weighted_sum / total_weight
    else:
        core_score = 0.0

    return {
        'per_metric'  : per_metric,
        'per_category': per_category,
        'overall'     : {
            'lstm_wins': total_lstm, 'rb_wins': total_rb, 'ties': total_tie,
            'total': total_all, 'win_rate': overall_rate, 'judgment': overall_judgment,
        },
        'core_score'  : core_score,
        'core_metrics': core_metrics,
    }


def print_superiority(analysis: dict):
    sep = "=" * 78

    print(f"\n{sep}")
    print("  종합 우수성 분석")
    print(sep)

    # ── 카테고리별 비교 결과 
    print("\n  [카테고리별 비교 결과]")
    print("  " + "─" * 74)
    print(f"  {_pad('카테고리', 24)}"
          f"{_pad('LSTM 우수', 11, align='right')}"
          f"{_pad('유사', 8, align='right')}"
          f"{_pad('RB 우수', 11, align='right')}"
          f"{_pad('개선률', 10, align='right')}"
          f"{_pad('결과', 10, align='right')}")
    print("  " + "─" * 74)

    for cat in ('eco', 'energy', 'stab'):
        if cat not in analysis['per_category']:
            continue
        c = analysis['per_category'][cat]
        label = f"{_CATEGORY_LABELS[cat]} ({c['total']}개 지표)"
        rate  = f"{c['win_rate']:.1f}%"
        result = c['judgment']
        print(f"  {_pad(label, 24)}"
              f"{_pad(str(c['lstm_wins']), 11, align='right')}"
              f"{_pad(str(c['ties']),      8, align='right')}"
              f"{_pad(str(c['rb_wins']),  11, align='right')}"
              f"{_pad(rate,  10, align='right')}"
              f"{_pad(result,10, align='right')}")

    print("  " + "─" * 74)
    o = analysis['overall']
    overall_rate_s = f"{o['win_rate']:.1f}%"
    print(f"  {_pad('종합', 24)}"
          f"{_pad(str(o['lstm_wins']), 11, align='right')}"
          f"{_pad(str(o['ties']),      8, align='right')}"
          f"{_pad(str(o['rb_wins']),  11, align='right')}"
          f"{_pad(overall_rate_s,    10, align='right')}"
          f"{_pad(o['judgment'],     10, align='right')}")

    # ── 핵심 지표 개선률 
    print("\n  [핵심 지표 개선률 (LSTM vs Rule-Based)]")
    print("  " + "─" * 74)
    for m in analysis['core_metrics']:
        imp   = m['improvement']
        verdict_str = '개선' if imp > _WIN_THRESHOLD else ('악화' if imp < -_WIN_THRESHOLD else '유사')

        # 원본 값 표시 (양수/음수 모두 동일 너비가 되도록 통일)
        rb_v = m['rb_val']
        dl_v = m['dl_val']
        unit = '%' if m['key'].endswith('_pct') else ''
        if unit == '%':
            detail = f"(Rule-Based {rb_v:>7.2f}% → LSTM {dl_v:>7.2f}%)"
        elif 'won' in m['key']:
            detail = f"(Rule-Based {rb_v:>11,.0f}원 → LSTM {dl_v:>11,.0f}원)"
        else:
            detail = f"(Rule-Based {rb_v:>11,.2f}  → LSTM {dl_v:>11,.2f} )"

        # 라벨 패딩 + 개선률 부호 일관 표시 ({imp:+7.1f} → +/- 부호가 7자 안에 포함됨)
        label_padded = _pad(m['label'], 18)
        print(f"  {label_padded} : {imp:+7.1f}% {verdict_str}  {detail}")

    print("  " + "─" * 74)
    cs = analysis['core_score']
    cs_verdict = '개선' if cs > _WIN_THRESHOLD else ('악화' if cs < -_WIN_THRESHOLD else '유사')
    print(f"  {_pad('핵심 지표 가중 평균', 18)} : {cs:+7.1f}% {cs_verdict}")

    # ── 최종 평가 
    print("\n  [최종 평가]")
    print("  " + "─" * 74)
    judgment = o['judgment']
    if judgment == 'LSTM':
        print(f"   LSTM 방식이 종합적으로 약 {abs(cs):.1f}% 더 우수합니다.")
    elif judgment == 'RB':
        print(f"   Rule-Based 방식이 종합적으로 약 {abs(cs):.1f}% 더 우수합니다.")
    else:
        print(f"   두 방식이 종합적으로 유사한 성능을 보입니다.")

    # 강점/약점 추출 (상위 3개씩)
    sorted_metrics = sorted(
        [m for m in analysis['core_metrics'] if abs(m['improvement']) > _WIN_THRESHOLD],
        key=lambda x: x['improvement'], reverse=True
    )
    strengths = [m for m in sorted_metrics if m['improvement'] > 0][:3]
    weaknesses = [m for m in sorted_metrics if m['improvement'] < 0][-3:]
    weaknesses.reverse()   # 가장 큰 악화부터

    if strengths:
        print(f"\n  강점:")
        for m in strengths:
            print(f"    · {_pad(m['label'], 18)} {m['improvement']:+6.1f}% 개선")

    if weaknesses:
        print(f"\n  약점:")
        for m in weaknesses:
            print(f"    · {_pad(m['label'], 18)} {m['improvement']:+6.1f}% (감소)")

    print(f"\n{sep}\n")


# CSV 저장
def save_comparison_csv(rb_m: dict, dl_m: dict, out_path: str):
    """비교 결과를 CSV로 저장 (long format)"""
    rows = []

    # 경제적 효율
    for k, v in rb_m['economic'].items():
        if k == 'breakdown':
            for period, bd in v.items():
                for sub_k, sub_v in bd.items():
                    rows.append({
                        '카테고리': '경제적 효율 (breakdown)',
                        '지표'    : f'{period}_{sub_k}',
                        'Rule-Based': sub_v,
                        'LSTM'    : dl_m['economic']['breakdown'].get(period, {}).get(sub_k, None),
                    })
        else:
            rows.append({
                '카테고리': '경제적 효율',
                '지표'    : k,
                'Rule-Based': v,
                'LSTM'    : dl_m['economic'].get(k),
            })

    # 에너지 효율
    for k, v in rb_m['energy'].items():
        rows.append({
            '카테고리': '에너지 효율',
            '지표'    : k,
            'Rule-Based': v,
            'LSTM'    : dl_m['energy'].get(k),
        })

    # 운영 안정성
    for k, v in rb_m['stability'].items():
        rows.append({
            '카테고리': '운영 안정성',
            '지표'    : k,
            'Rule-Based': v,
            'LSTM'    : dl_m['stability'].get(k),
        })

    # LSTM 예측 (참고)
    if 'prediction' in dl_m:
        for k, v in dl_m['prediction'].items():
            rows.append({
                '카테고리': 'LSTM 예측 성능',
                '지표'    : k,
                'Rule-Based': '-',
                'LSTM'    : v,
            })

    pd.DataFrame(rows).to_csv(out_path, index=False, encoding='utf-8-sig')

# 메인
def main():
    print("=" * 78)
    print("  Rule-Based vs LSTM  ─  BESS 성능 비교")
    print("=" * 78)

    if not _check_files():
        print("\n먼저 각 폴더에서 main.py 를 실행하세요:")
        print("  cd rule_based && python main.py")
        print("  cd DL_LSTM    && python main.py")
        sys.exit(1)

    rb_df   = pd.read_csv(RB_CSV,   parse_dates=['timestamp'])
    lstm_df = pd.read_csv(LSTM_CSV, parse_dates=['timestamp'])

    # 공통 기간으로 맞추기
    start = max(rb_df['timestamp'].min(), lstm_df['timestamp'].min())
    end   = min(rb_df['timestamp'].max(), lstm_df['timestamp'].max())
    rb_df   = rb_df[(rb_df['timestamp']   >= start) & (rb_df['timestamp']   <= end)].reset_index(drop=True)
    lstm_df = lstm_df[(lstm_df['timestamp'] >= start) & (lstm_df['timestamp'] <= end)].reset_index(drop=True)
    print(f"\n비교 기간: {start.date()} ~ {end.date()}  ({len(rb_df)//24}일, {len(rb_df):,}h)")

    # Baseline 구성 (각각의 시뮬레이션 결과로부터)
    rb_base   = build_baseline(rb_df)
    lstm_base = build_baseline(lstm_df)

    # evaluator.py 의 평가 함수 재사용 (단일 책임)
    rb_metrics = evaluate_all(rb_df, rb_base)

    # LSTM 예측 데이터가 CSV 에 있으면 함께 평가
    y_true = lstm_df['load_kw'].values - lstm_df['solar_kw'].values \
             if 'load_kw' in lstm_df.columns else None
    y_pred = lstm_df['predicted_net_load_kw'].values \
             if 'predicted_net_load_kw' in lstm_df.columns else None
    dl_metrics = evaluate_all(lstm_df, lstm_base, y_true=y_true, y_pred=y_pred)

    # 비교 표 출력
    print_comparison_table(rb_metrics, dl_metrics)

    # 종합 우수성 분석 출력
    analysis = analyze_superiority(rb_metrics, dl_metrics)
    print_superiority(analysis)

    # CSV 저장
    os.makedirs(OUT_DIR, exist_ok=True)
    out_csv = os.path.join(OUT_DIR, 'comparison_metrics.csv')
    save_comparison_csv(rb_metrics, dl_metrics, out_csv)
    print(f"비교 결과 저장: {out_csv}\n")


if __name__ == '__main__':
    main()
