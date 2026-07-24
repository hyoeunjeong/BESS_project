import os
import numpy as np
import pandas as pd

import config
from data_loader  import (load_data, make_sequences, split_sequences,
                           inverse_target, save_scaler, load_scaler,
                           FEATURE_COLS)
from models.gru_model  import train, predict, load_model
from bess_controller    import GRUBESSController
from simulator          import run_gru_simulation, run_baseline_simulation
from evaluator          import evaluate_all, print_report
from visualizer         import (plot_training_curve, plot_prediction,
                                 plot_daily_operation, plot_soc_trend,
                                 plot_comparison, plot_radar)

# 옵션
SKIP_TRAINING = True   # True → 저장된 모델(.pt) 로드 (학습 건너뜀)
FULL_YEAR     = True   # True → 1년치 전체 시뮬레이션 (월별 비교용)
                        # False → 테스트셋(15%, 약 55일)만 시뮬레이션 (기존 동작)


# 극단 사례 자동 선정 (Rule-Based main.py 와 동일 로직)
def find_extreme_days(result_df: pd.DataFrame) -> dict:
    df       = result_df.copy()
    df['date'] = df['timestamp'].dt.date
    first    = df['timestamp'].min().date()
    daily    = df.groupby('date').agg(
        load_mean  = ('load_kw',  'mean'),
        smp_mean   = ('smp',      'mean'),
        solar_mean = ('solar_kw', 'mean'),
    ).reset_index()
    daily['day_idx'] = daily['date'].apply(lambda d: (d - first).days)

    def _pick(col, largest):
        r = daily.loc[daily[col].idxmax() if largest else daily[col].idxmin()]
        return {'day_idx': int(r['day_idx']), 'date': str(r['date']),
                'value': float(r[col])}

    return {
        'peak_load'  : {**_pick('load_mean', True),  'label': '부하 최대일',    'unit': 'kW'},
        'peak_smp'   : {**_pick('smp_mean',  True),  'label': 'SMP 최대일',     'unit': '원/kWh'},
        'best_solar' : {**_pick('solar_mean',True),  'label': '태양광 최대일',  'unit': 'kW'},
        'worst_solar': {**_pick('solar_mean',False), 'label': '태양광 최소일',  'unit': 'kW'},
    }

# 메인
def main():
    print("=" * 62)
    if FULL_YEAR:
        print("  GRU 기반 BESS 충·방전 제어 시뮬레이션 [1년치 모드]")
    else:
        print("  GRU 기반 BESS 충·방전 제어 시뮬레이션 [테스트셋 모드]")
    print("  (한국전력거래소 실제 데이터 기반)")
    print("=" * 62)
    print(f"   SKIP_TRAINING = {SKIP_TRAINING}")
    print(f"   FULL_YEAR     = {FULL_YEAR}")

    os.makedirs(config.RESULT_DIR,    exist_ok=True)
    os.makedirs(config.MODEL_SAVE_DIR, exist_ok=True)

    # ── 1. 데이터 로드 
    print("\n[1/8] 데이터 로드 및 피처 엔지니어링")
    df = load_data(config.LOAD_DATA_PATH, config.SMP_DATA_PATH)

    # ── 2. 시퀀스 생성 + 분할 
    print("\n[2/8] GRU 시퀀스 생성 및 Train/Val/Test 분할")
    
    # FULL_YEAR 모드면 기존 scaler 재사용 (학습 때와 동일한 정규화 유지)
    if FULL_YEAR and SKIP_TRAINING and os.path.exists(config.SCALER_SAVE_PATH):
        print("   기존 scaler 재사용 (FULL_YEAR + SKIP_TRAINING)")
        scaler = load_scaler()
        X, y, scaler = make_sequences(df, scaler=scaler, fit_scaler=False)
    else:
        # [§5-1] 데이터 누수 차단: 스케일러를 학습 구간(앞 70% 행)에서만 fit.
        #   기존 fit_scaler=True 는 val/test 를 포함한 전체로 fit → 미래 정보 누수.
        from sklearn.preprocessing import MinMaxScaler
        i_tr = int(len(df) * config.TRAIN_RATIO)
        scaler = MinMaxScaler().fit(df[FEATURE_COLS].values[:i_tr].astype(np.float32))
        print(f"   [§5-1] 스케일러 fit 범위: 학습 구간 앞 {i_tr:,}행 "
              f"({config.TRAIN_RATIO:.0%}) — val/test 미포함")
        X, y, scaler = make_sequences(df, scaler=scaler, fit_scaler=False)
        save_scaler(scaler)

    (X_tr, y_tr), (X_val, y_val), (X_te, y_te) = split_sequences(X, y)
    n_features = X.shape[2]
    print(f"   Train : {X_tr.shape}  Val : {X_val.shape}  Test : {X_te.shape}")
    print(f"   전체   : {X.shape}")
    print(f"   피처 수: {n_features}  →  {FEATURE_COLS}")

    # ── 3. GRU 학습 
    print("\n[3/8] GRU 모델 학습")
    if SKIP_TRAINING and os.path.exists(config.MODEL_SAVE_PATH):
        print("   저장된 모델 로드 (SKIP_TRAINING=True)")
        model   = load_model(n_features)
        history = None
    else:
        if FULL_YEAR and not SKIP_TRAINING:
            print("   [주의] FULL_YEAR=True 인데 SKIP_TRAINING=False 입니다.")
            print("   학습을 새로 진행합니다. (이미 학습된 모델이 있다면 SKIP_TRAINING=True 권장)")
        model, history = train(X_tr, y_tr, X_val, y_val, n_features)

    # ── 4. 예측 (테스트셋 또는 1년치 전체) 
    if FULL_YEAR:
        print("\n[4/8] 1년치 전체 예측")
        y_pred_scaled = predict(model, X)
        y_true_kw     = inverse_target(y,             scaler)
    else:
        print("\n[4/8] 테스트셋 예측")
        y_pred_scaled = predict(model, X_te)
        y_true_kw     = inverse_target(y_te,          scaler)

    # 역정규화
    y_pred_kw = inverse_target(y_pred_scaled, scaler)
    print(f"   예측 완료: {len(y_pred_kw):,}개 샘플")

    # [§5-2/§3-3] 예측 지표를 train/val/test/full 구간으로 분리 산출 (FULL_YEAR 전용).
    #   논문 표에는 test 를 싣고, full 은 부록/참고용으로 보존한다.
    pred_split = None
    if FULL_YEAR:
        from evaluator import calc_prediction_metrics
        _n  = len(y_true_kw)
        _t1 = int(_n * config.TRAIN_RATIO)
        _t2 = int(_n * (config.TRAIN_RATIO + config.VAL_RATIO))
        pred_split = {
            'train': calc_prediction_metrics(y_true_kw[:_t1],    y_pred_kw[:_t1]),
            'val'  : calc_prediction_metrics(y_true_kw[_t1:_t2], y_pred_kw[_t1:_t2]),
            'test' : calc_prediction_metrics(y_true_kw[_t2:],    y_pred_kw[_t2:]),
            'full' : calc_prediction_metrics(y_true_kw,          y_pred_kw),
        }
        _t = pred_split['test']
        print(f"   [예측 성능·논문표=test] MAE {_t['mae_kw']:.3f} kW  RMSE {_t['rmse_kw']:.3f} kW  "
              f"상대오차 {_t['nmae_pct']:.2f}%  (n={_t['n_samples']:,})")
        print(f"   (참고) train MAE {pred_split['train']['mae_kw']:.3f} / "
              f"val {pred_split['val']['mae_kw']:.3f} / full {pred_split['full']['mae_kw']:.3f} kW")

    # 시뮬레이션 시작 인덱스 계산
    # 시퀀스 생성 시 seq_len 만큼 앞이 잘림
    if FULL_YEAR:
        # 전체 데이터의 시작 (seq_len부터)
        test_start_idx = config.SEQ_LEN
    else:
        # 테스트 시작 행 인덱스 (Train + Val 뒤)
        n_train   = len(X_tr)
        n_val     = len(X_val)
        test_start_idx = config.SEQ_LEN + n_train + n_val

    # ── 5. GRU BESS 시뮬레이션 
    print("\n[5/8] GRU 기반 BESS 시뮬레이션")
    controller  = GRUBESSController()
    gru_result = run_gru_simulation(df, y_pred_kw, test_start_idx, controller)
    print(f"   진행상태: 완료 ({len(gru_result):,}행)")

    # ── 6. 기준 시나리오 
    print("\n[6/8] 기준 시나리오 (BESS 없음) 시뮬레이션")
    test_df  = df.iloc[test_start_idx: test_start_idx + len(y_pred_kw)].reset_index(drop=True)
    baseline = run_baseline_simulation(test_df)
    print("   진행상태: 완료")

    # ── 7. 평가 
    print("\n[7/8] 평가 지표 계산")
    metrics = evaluate_all(gru_result, baseline,
                           y_true=y_true_kw, y_pred=y_pred_kw)

    # [§3-3] 논문 표 예측 지표는 test 구간 값으로 교체, 4구간 전체는 참고용 보존
    if pred_split is not None:
        metrics['prediction'] = pred_split['test']
        metrics['prediction_splits'] = pred_split

    if FULL_YEAR:
        print_report(metrics, title="GRU BESS 1년치 평가 리포트")
    else:
        print_report(metrics)

    # ── 8. 결과 저장 + 시각화 
    print("[8/8] 결과 저장 및 시각화")
    print("-" * 60)

    csv_path = os.path.join(config.RESULT_DIR, 'gru_simulation_result.csv')
    
    # FULL_YEAR 모드면 기존 테스트셋 결과 자동 백업
    if FULL_YEAR and os.path.exists(csv_path):
        backup_path = os.path.join(config.RESULT_DIR, 'gru_simulation_result_testset.csv')
        if not os.path.exists(backup_path):
            import shutil
            shutil.copy(csv_path, backup_path)
            print(f"   기존 테스트셋 결과 백업: {backup_path}")
    
    gru_result.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"   CSV   : {csv_path}  ({len(gru_result):,}행, {len(gru_result)//24}일)")

    # 학습 곡선
    if history:
        plot_training_curve(
            history,
            save_path=os.path.join(config.RESULT_DIR, 'gru_training_curve.png'))

    # 예측 정확도
    suffix = '_yearly' if FULL_YEAR else ''
    plot_prediction(
        y_true_kw, y_pred_kw,
        save_path=os.path.join(config.RESULT_DIR, f'gru_prediction{suffix}.png'))

    print("=" * 62)
    # 극단 사례 운영 그래프
    extreme = find_extreme_days(gru_result)
    print("\n   극단 사례 선정")
    print("-" * 60)
    for info in extreme.values():
        label = info['label']
        # 한글은 2칸, 영문/숫자는 1칸으로 계산하여 전체 18칸을 맞춤
        display_width = sum(2 if ord(c) > 127 else 1 for c in label)
        padding = " " * (15 - display_width)
    
        # 라벨 뒤에 계산된 공백(padding)을 추가하고 콜론(:)을 배치
        print(f"   - {label}{padding}: {info['date']}  "
              f"(평균 {info['value']:>8.2f} {info['unit']})")
    print("-" * 60)     
    tag_map = {
        'peak_load'  : f'extreme_peak_load{suffix}',
        'peak_smp'   : f'extreme_peak_smp{suffix}',
        'best_solar' : f'extreme_best_solar{suffix}',
        'worst_solar': f'extreme_worst_solar{suffix}',
    }
    for key, tag in tag_map.items():
        info = extreme[key]
        plot_daily_operation(
            gru_result,
            day_idx   = info['day_idx'],
            save_path = os.path.join(config.RESULT_DIR,
                                     f'{tag}_{info["date"]}.png'))

    plot_soc_trend(gru_result,
                   save_path=os.path.join(config.RESULT_DIR, f'gru_soc_trend{suffix}.png'))
    print("=" * 62)
    print(f"\n시뮬레이션 완료 → 결과 폴더: ./{config.RESULT_DIR}/")
    
    if FULL_YEAR:
        print("\n[1년치 모드] 다음 단계:")
        print("   1. cd .. (프로젝트 루트로)")
        print("   2. python compare.py  (Rule-Based vs GRU 1년치 비교)")
        print("   3. comparison_results/comparison_metrics.csv 업데이트 확인")
    else:
        print("\n※ Rule-Based 결과와 비교하려면 아래 함수를 활용하세요:")
        print("   from evaluator import print_comparison")
        print("   from visualizer import plot_comparison, plot_radar")


if __name__ == '__main__':
    main()

    