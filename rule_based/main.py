import os
import pandas as pd
import config
from data_loader import load_all_data
from bess_controller import RuleBasedBESSController
from simulator import run_simulation, run_baseline_simulation
from evaluator import evaluate_all, print_report
from visualizer import plot_daily_operation, plot_metrics_summary, plot_soc_trend


def find_extreme_days(result_df: pd.DataFrame) -> dict:
    """극단 사례 4개 자동 선정"""
    df = result_df.copy()
    df['date'] = df['timestamp'].dt.date

    daily = df.groupby('date').agg({
        'load_kw': 'mean', 'smp': 'mean', 'solar_kw': 'mean',
    }).reset_index()

    first_date = df['timestamp'].min().date()
    daily['day_idx'] = daily['date'].apply(lambda d: (d - first_date).days)

    peak_load   = daily.loc[daily['load_kw'].idxmax()]
    peak_smp    = daily.loc[daily['smp'].idxmax()]
    best_solar  = daily.loc[daily['solar_kw'].idxmax()]
    worst_solar = daily.loc[daily['solar_kw'].idxmin()]

    return {
        'peak_load':   {'day_idx': int(peak_load['day_idx']),   'date': str(peak_load['date']),
                        'value': peak_load['load_kw'],   'label': '부하 최대일',   'unit': 'kW'},
        'peak_price':  {'day_idx': int(peak_smp['day_idx']),    'date': str(peak_smp['date']),
                        'value': peak_smp['smp'],        'label': 'SMP 최대일',    'unit': '원/kWh'},
        'best_solar':  {'day_idx': int(best_solar['day_idx']),  'date': str(best_solar['date']),
                        'value': best_solar['solar_kw'], 'label': '태양광 최대일', 'unit': 'kW'},
        'worst_solar': {'day_idx': int(worst_solar['day_idx']), 'date': str(worst_solar['date']),
                        'value': worst_solar['solar_kw'],'label': '태양광 최소일', 'unit': 'kW'},
    }


def main():
    print("=" * 62)
    print("BESS Rule-Based 충/방전 제어 시뮬레이션")
    print(f"  부하   : {'ODcloud API' if config.USE_LOAD_API else 'CSV'}")
    print(f"  SMP    : {'API' if config.USE_SMP_API else 'CSV'}")
    print(f"  태양광 : {'기상청 API' if config.USE_KMA_API else '시뮬레이션'}")
    print("=" * 62)

    # 1. 데이터 로드
    print("\n[1/5] 데이터 로드 중...")
    merged = load_all_data(
        use_load_api=config.USE_LOAD_API,
        use_smp_api=config.USE_SMP_API,
        use_kma_api=config.USE_KMA_API,
    )

    # 2. Rule-Based 시뮬레이션
    print("\n[2/5] Rule-Based BESS 시뮬레이션 실행 중...")
    controller = RuleBasedBESSController()
    result_df  = run_simulation(merged, controller)
    print("   - 시뮬레이션 완료")

    # 3. 기준 시나리오
    print("\n[3/5] 기준 시나리오 (BESS 없음) 시뮬레이션 중...")
    baseline_df = run_baseline_simulation(merged)
    print("   - 기준 시나리오 완료")

    # 4. 평가지표
    print("\n[4/5] 평가지표 계산 중...")
    metrics = evaluate_all(result_df, baseline_df)
    print_report(metrics)

    # 5. 결과 저장 및 시각화
    print("[5/5] 결과 저장 및 시각화 중...")
    output_dir = config.RESULT_DIR
    os.makedirs(output_dir, exist_ok=True)

    result_df.to_csv(f'{output_dir}/simulation_result.csv',
                     index=False, encoding='utf-8-sig')
    print(f"   - 결과 CSV: {output_dir}/simulation_result.csv")

    # 극단 사례
    print("\n   극단 사례 자동 선정 중...")
    extreme_days = find_extreme_days(result_df)
    print("\n   선정된 극단 사례")
    print("   " + "-" * 58)
    for key, info in extreme_days.items():
        label = info['label']
        padding = 14 - sum(2 if ord(c) > 127 else 1 for c in label)
        display_label = label + " " * padding
        print(f"   - {display_label} : {info['date']} "
              f"(평균 {info['value']:>8.2f} {info['unit']})")
    print("   " + "-" * 58)

    # 그래프
    print("\n   그래프 생성 중...")
    for key, info in extreme_days.items():
        suffix = {'peak_load': '1_peak_load', 'peak_price': '2_peak_price',
                  'best_solar': '3_best_solar', 'worst_solar': '4_worst_solar'}[key]
        plot_daily_operation(result_df, day_idx=info['day_idx'],
                              save_path=f"{output_dir}/extreme_{suffix}_{info['date']}.png")

    plot_soc_trend(result_df, save_path=f'{output_dir}/soc_trend.png')
    plot_metrics_summary(metrics, save_path=f'{output_dir}/metrics_summary.png')

    print("\n시뮬레이션 완료")
    print(f"결과 폴더: ./{output_dir}/")


if __name__ == '__main__':
    main()
