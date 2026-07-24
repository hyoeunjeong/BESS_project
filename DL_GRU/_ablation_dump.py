"""[§6-2] 예측 기여도 ablation 보조 스크립트 (GRU 예측 덤프)

GRU 모델의 1년치 순부하 예측을 계산해 repo 루트 results/_pred_gru.npy 로 저장한다.
DL_GRU 디렉토리 안에서 실행해야 한다(로컬 config/data_loader/models 임포트).
    cd DL_GRU && python _ablation_dump.py
통합 러너(DL_LSTM/_ablation_run.py)가 이 파일을 읽어 GRU 행을 채운다.
"""
import os
import numpy as np

import config
from data_loader import load_data, make_sequences, inverse_target, load_scaler
from models.gru_model import predict, load_model

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT  = os.path.join(REPO, 'results', '_pred_gru.npy')


def main():
    df = load_data(config.LOAD_DATA_PATH, config.SMP_DATA_PATH)
    scaler = load_scaler()                       # GRU train-only 스케일러
    X, y, scaler = make_sequences(df, scaler=scaler, fit_scaler=False)
    model = load_model(X.shape[2])
    y_pred_kw = inverse_target(predict(model, X), scaler)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    np.save(OUT, np.asarray(y_pred_kw, dtype=float))
    print(f"[ablation] GRU 예측 저장: {OUT}  shape={y_pred_kw.shape}")


if __name__ == '__main__':
    main()
