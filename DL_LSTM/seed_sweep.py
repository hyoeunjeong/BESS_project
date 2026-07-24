"""
5-시드 반복 학습 (논문 §3.2 후속과제: 반복 학습 검증)
=====================================================================
시드 42/1/7/13/2024 로 재학습하여 예측 성능의 평균±표준편차를 산출한다.
표 2.6 을 'MAE X ± σ' 형태로 방어하기 위한 강건성 실험.

  실행:  python seed_sweep.py lstm     (DL_LSTM 에서)
         python seed_sweep.py gru      (DL_GRU 에서)

[캐논 모델 보호] 스윕이 .pt 를 덮어쓰므로, 시작 시 백업하고 종료 시 복원한다.
                 → main.py(시드 42)로 만든 시뮬레이션 CSV와의 정합성 유지.
"""
import os
import sys
import shutil
import numpy as np

import config
import evaluator
from data_loader import load_data, make_sequences, split_sequences, inverse_target

KIND = (sys.argv[1] if len(sys.argv) > 1 else 'lstm').lower()
if KIND == 'gru':
    from models.gru_model import train, predict
    SAVE_PATH = config.MODEL_SAVE_PATH.replace('lstm', 'gru')
else:
    from models.lstm_model import train, predict
    SAVE_PATH = config.MODEL_SAVE_PATH

SEEDS = [42, 1, 7, 13, 2024]

# ── 캐논 모델 보호 ────────────────────────────────────────────────
bak = SAVE_PATH + '.canonical_bak'
if os.path.exists(SAVE_PATH):
    shutil.copy(SAVE_PATH, bak)

df = load_data(config.LOAD_DATA_PATH, config.SMP_DATA_PATH)

rows = []
for s in SEEDS:
    X, y, scaler = make_sequences(df, fit_scaler=True)
    (X_tr, y_tr), (X_val, y_val), _ = split_sequences(X, y)
    model, _ = train(X_tr, y_tr, X_val, y_val, X.shape[2], seed=s)
    y_pred = predict(model, X)
    m = evaluator.calc_prediction_metrics(inverse_target(y, scaler),
                                          inverse_target(y_pred, scaler))
    rows.append((s, m['mae_kw'], m['rmse_kw'], m['nmae_pct']))
    print(f"[{KIND}] seed {s:5d}: "
          f"MAE {m['mae_kw']:.3f}  RMSE {m['rmse_kw']:.3f}  nMAE {m['nmae_pct']:.2f}")

# ── 캐논 모델 복원 ────────────────────────────────────────────────
if os.path.exists(bak):
    shutil.move(bak, SAVE_PATH)
    print(f"[캐논 모델 복원] {SAVE_PATH}")

a = np.array([[r[1], r[2], r[3]] for r in rows], dtype=float)
def _sd(c):
    return a[:, c].std(ddof=1)

print(f"\n================ {KIND.upper()} 5-seed 요약 (n={len(SEEDS)}) ================")
print(f"  MAE   : {a[:,0].mean():.3f} ± {_sd(0):.3f} kW")
print(f"  RMSE  : {a[:,1].mean():.3f} ± {_sd(1):.3f} kW")
print(f"  nMAE  : {a[:,2].mean():.2f} ± {_sd(2):.2f} %")
print("=" * 56)
