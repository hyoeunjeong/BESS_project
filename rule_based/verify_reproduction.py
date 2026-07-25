"""
재현 검증 스크립트 (Acceptance Test) — seasonal 요금 체계
=====================================================================
저장소 코드가 README §10 의 규칙 기반(Rule-Based) 정본 수치를 그대로
재생성하는지 자동 검증한다. 별도 참조 구현을 두지 않고, 실제 파이프라인
(data_loader → simulator → evaluator)을 그대로 실행해 대조하므로
'검증 코드와 본 코드가 따로 노는' 위험이 없다.

  · config.py 의 요금/BESS 설정이 정본 조건인지 (사전 점검)
  · 시간대(최대부하 1,476h) 산정이 맞는지
  · 규칙 기반 핵심 지표(순절감·요금적용전력·사이클·SOC 등)를 재현하는지

실행
    cd rule_based && python verify_reproduction.py

종료 코드
    0 = 전부 일치, 1 = 불일치/설정 오류
=====================================================================
"""

import sys
import numpy as np

import config
import data_loader
import simulator
import evaluator


# ── 정본 기댓값 (seasonal, 3자 공통창 8,736시점) ──────────────────
#   [정렬] 논문 표2.8 및 compare.py 는 Rule-Based/LSTM/GRU 세 결과를
#   timestamp 교집합(8,736시점)으로 정렬해 비교한다. LSTM/GRU 는 입력
#   시퀀스 24시간을 소모하므로 첫 24시각이 없고, 교집합은 RB[24:] 가 된다.
#   따라서 본 검증도 같은 8,736 창으로 맞춰 표2.8 값을 그대로 대조한다.
#   (참고: RB 단독 8,760시점 값은 순절감 281,362원 / 사이클 243.62 /
#          SOC 체류 15.74% 이며, 정렬 전 값이므로 표2.8과 다르다.)
#   숫자를 지어내지 않는다 — 코드가 재현 못 하면 여기 값을 바꾸지 말고 원인을 규명한다.
EXPECTED_RB = {
    'n_rows'                 : 8736,
    'on_peak_hours'          : 1476,     # 최대부하(주말·공휴일 반영)
    'mid_peak_hours'         : 2696,
    'off_peak_hours'         : 4564,
    'cost_saving_rate_pct'   : 0.747,
    'cost_saving_won'        : 285497,
    'net_saving_won'         : 285497,
    'peak_demand_kw'         : 71.24,    # 요금적용전력(중간·최대부하)
    'baseline_peak_demand_kw': 71.24,
    'base_charge_delta_won'  : 0,        # 규칙기반은 요금적용전력을 못 낮춤
    'cycle_count'            : 243.4,
    'soc_in_band_rate_pct'   : 15.77,
    'prevention_rate_pct'    : 100.0,    # 과충·방전 0
}

# 3자 공통창 정렬 시 소모되는 선행 시각 수(LSTM/GRU 입력 시퀀스 길이).
_SEQ_LEN = 24

# 상대 허용 오차 (기본 1.5%), 정수 카운트는 절대 오차 0
REL_TOL = 0.015
EXACT_KEYS = {'n_rows', 'on_peak_hours', 'mid_peak_hours', 'off_peak_hours'}


def check_config():
    print("[사전 점검] config.py")
    problems = []

    mode = getattr(config, 'TARIFF_MODE', None)
    print(f"  TARIFF_MODE = {mode}")
    if mode != 'seasonal':
        problems.append(f"TARIFF_MODE='{mode}' (정본 기본값 'seasonal' 아님)")

    for name, want in (('BESS_CAPACITY_KWH', 100.0), ('BESS_MAX_POWER_KW', 25.0),
                       ('SOC_MIN', 0.10), ('SOC_MAX', 0.90), ('SOC_INITIAL', 0.50),
                       ('CONTRACT_POWER_KW', 100.0),
                       ('BASE_CHARGE_WON_PER_KW', 7470)):
        got = getattr(config, name, None)
        mark = 'OK' if got == want else 'FAIL'
        print(f"  {mark:>4}  {name:24s} = {got}  (기대 {want})")
        if got != want:
            problems.append(f"{name}={got} (기대 {want})")

    if problems:
        print("\n  [실패] 설정 불일치:")
        for p in problems:
            print(f"    · {p}")
    else:
        print("  [정상] 정본 조건과 일치")
    return problems


def run_pipeline():
    merged = data_loader.load_all_data(
        use_load_api=config.USE_LOAD_API,
        use_smp_api=config.USE_SMP_API,
        use_kma_api=config.USE_KMA_API,
    )
    result   = simulator.run_simulation(merged)
    baseline = simulator.run_baseline_simulation(merged)
    # [정렬] 표2.8(compare.align_frames)과 동일한 8,736 공통창으로 맞춘다.
    #   RB 단독은 8,760시점이나 LSTM/GRU 는 seq_len=24 소모로 첫 24시각이
    #   없어 교집합이 RB[24:] 가 된다. 같은 창을 잘라 표2.8 값을 재현한다.
    #   (연속 시뮬레이션 결과를 사후에 슬라이싱한 것으로, compare.align_frames
    #    와 동일한 방식이다.)
    result   = result.iloc[_SEQ_LEN:].reset_index(drop=True)
    baseline = baseline.iloc[_SEQ_LEN:].reset_index(drop=True)
    m = evaluator.evaluate_all(result, baseline)
    e, st = m['economic'], m['stability']
    return {
        'n_rows'                 : len(result),
        'on_peak_hours'          : int((result['tariff_period'] == 'on_peak').sum()),
        'mid_peak_hours'         : int((result['tariff_period'] == 'mid_peak').sum()),
        'off_peak_hours'         : int((result['tariff_period'] == 'off_peak').sum()),
        'cost_saving_rate_pct'   : e['cost_saving_rate_pct'],
        'cost_saving_won'        : e['cost_saving_won'],
        'net_saving_won'         : e['net_saving_won'],
        'peak_demand_kw'         : e['peak_demand_kw'],
        'baseline_peak_demand_kw': e['baseline_peak_demand_kw'],
        'base_charge_delta_won'  : e['base_charge_delta_won'],
        'cycle_count'            : st['cycle_count'],
        'soc_in_band_rate_pct'   : st['soc_in_band_rate_pct'],
        'prevention_rate_pct'    : st['prevention_rate_pct'],
    }


def compare(actual, expected):
    print(f"\n{'='*66}\n  규칙 기반 정본 재현 (seasonal, 3자 공통창 8,736시점)\n{'='*66}")
    print(f"  {'지표':<26}{'정본':>14}{'재현':>14}{'판정':>7}")
    print("  " + "-" * 60)
    fails = []
    for key, want in expected.items():
        got = actual.get(key)
        if got is None:
            print(f"  {key:<26}{want:>14}{'없음':>14}{'FAIL':>7}")
            fails.append((key, want, None)); continue
        if key in EXACT_KEYS:
            ok = int(got) == int(want)
        elif want == 0:
            ok = abs(got) <= 1.0
        else:
            ok = abs(got - want) <= abs(want) * REL_TOL
        print(f"  {key:<26}{want:>14,.2f}{got:>14,.2f}{'OK' if ok else 'FAIL':>7}")
        if not ok:
            fails.append((key, want, got))
    print("  " + "-" * 60)
    print(f"  {len(expected)-len(fails)}/{len(expected)} 일치")
    return fails


def main():
    print("=" * 66)
    print("  재현 검증 (Acceptance Test) — seasonal")
    print("=" * 66)

    problems = check_config()
    if problems:
        print("\n[중단] 설정 불일치. 위 항목을 먼저 수정하세요.")
        return 1

    print("\n[파이프라인 실행]")
    actual = run_pipeline()
    fails = compare(actual, EXPECTED_RB)

    print(f"\n{'='*66}")
    if fails:
        print(f"  검증 실패 — 불일치 {len(fails)}건")
        for k, w, g in fails:
            print(f"    · {k}: 정본 {w}, 재현 {g}")
        print("  (숫자를 맞추려 코드를 바꾸지 말 것 — 원인을 규명하거나 정본값을 재검토)")
        print("=" * 66)
        return 1
    print("  검증 통과 — 저장소 코드가 정본 수치를 그대로 재현합니다")
    print("  ※ LSTM/GRU 예측·경제 지표는 `python ../compare.py` 로 대조")
    print("=" * 66)
    return 0


if __name__ == '__main__':
    sys.exit(main())
