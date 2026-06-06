"""
models/lstm_model.py  ─  LSTM 순부하 예측 모델
===============================================
Stacked LSTM + Dropout 구조.
DNN·RNN·Transformer 등 다른 아키텍처는 사용하지 않습니다.

입력  : (batch, seq_len, n_features)
출력  : (batch,)  ← 다음 1시간 순부하 예측값 (정규화 스케일)
"""

import os
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config


# =====================================================================
# LSTM 모델 클래스
# =====================================================================
class BESSLSTMModel(nn.Module):
    """
    BESS 충방전 제어를 위한 순부하(net load) 예측 LSTM 모델

    구조
    ----
    Stacked LSTM (n_layers)
      → Dropout
      → Fully Connected (hidden → 1)
    """

    def __init__(self,
                 n_features : int,
                 hidden_size: int  = config.LSTM_HIDDEN,
                 n_layers   : int  = config.LSTM_LAYERS,
                 dropout    : float= config.DROPOUT):
        super().__init__()
        self.n_layers    = n_layers
        self.hidden_size = hidden_size

        self.lstm = nn.LSTM(
            input_size  = n_features,
            hidden_size = hidden_size,
            num_layers  = n_layers,
            batch_first = True,
            dropout     = dropout if n_layers > 1 else 0.0,
        )
        self.dropout = nn.Dropout(dropout)
        self.fc      = nn.Linear(hidden_size, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Parameters
        ----------
        x : (batch, seq_len, n_features)

        Returns
        -------
        Tensor : (batch,)
        """
        out, _ = self.lstm(x)            # (batch, seq_len, hidden)
        out    = self.dropout(out[:, -1, :])   # 마지막 타임스텝
        return self.fc(out).squeeze(-1)  # (batch,)


# =====================================================================
# 학습
# =====================================================================
def train(X_train: np.ndarray, y_train: np.ndarray,
          X_val  : np.ndarray, y_val  : np.ndarray,
          n_features: int) -> tuple:
    """
    LSTM 모델 학습 (Early Stopping 포함)

    Parameters
    ----------
    X_train, y_train : 훈련 데이터 (numpy float32)
    X_val,   y_val   : 검증 데이터
    n_features       : 입력 피처 수

    Returns
    -------
    model   : 최적 가중치가 로드된 BESSLSTMModel
    history : {'train_loss': [...], 'val_loss': [...]}
    """
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"[LSTM 학습] 디바이스: {device}  |  "
          f"파라미터 - hidden:{config.LSTM_HIDDEN}  "
          f"layers:{config.LSTM_LAYERS}  "
          f"dropout:{config.DROPOUT}  "
          f"lr:{config.LEARNING_RATE}")

    # 텐서 변환
    Xtr = torch.from_numpy(X_train).to(device)
    ytr = torch.from_numpy(y_train).to(device)
    Xvl = torch.from_numpy(X_val).to(device)
    yvl = torch.from_numpy(y_val).to(device)

    loader = DataLoader(TensorDataset(Xtr, ytr),
                        batch_size=config.BATCH_SIZE,
                        shuffle=True)

    model     = BESSLSTMModel(n_features).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=config.LEARNING_RATE)
    criterion = nn.MSELoss()
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
    optimizer, mode='min', patience=5,
    factor=0.5)

    os.makedirs(os.path.dirname(config.MODEL_SAVE_PATH), exist_ok=True)
    best_val  = float('inf')
    patience  = config.PATIENCE
    wait      = 0
    history   = {'train_loss': [], 'val_loss': []}

    for epoch in range(1, config.EPOCHS + 1):
        # ── Train ──
        model.train()
        batch_losses = []
        for xb, yb in loader:
            optimizer.zero_grad()
            loss = criterion(model(xb), yb)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            batch_losses.append(loss.item())

        # ── Validation ──
        model.eval()
        with torch.no_grad():
            val_loss = criterion(model(Xvl), yvl).item()

        tr_loss = float(np.mean(batch_losses))
        history['train_loss'].append(tr_loss)
        history['val_loss'].append(val_loss)
        scheduler.step(val_loss)

        if epoch % 10 == 0 or epoch == 1:
            print(f"  Epoch {epoch:3d}/{config.EPOCHS}  "
                  f"Train Loss: {tr_loss:.5f}  "
                  f"Val Loss: {val_loss:.5f}")

        # ── Early Stopping ──
        if val_loss < best_val:
            best_val = val_loss
            torch.save(model.state_dict(), config.MODEL_SAVE_PATH)
            wait = 0
        else:
            wait += 1
            if wait >= patience:
                print(f"  [Early Stopping] Epoch {epoch}에서 학습 조기 종료")
                break

    print(f"\n[학습 완료] Best Val Loss: {best_val:.5f} → {config.MODEL_SAVE_PATH}")
    model.load_state_dict(torch.load(config.MODEL_SAVE_PATH,
                                     map_location=device))
    model.eval()
    return model, history


# =====================================================================
# 추론
# =====================================================================
def predict(model: BESSLSTMModel,
            X    : np.ndarray) -> np.ndarray:
    """
    정규화된 입력 배열 → 정규화된 예측값 반환

    Parameters
    ----------
    model : 학습된 BESSLSTMModel
    X     : (N, seq_len, n_features)  float32

    Returns
    -------
    numpy ndarray : (N,)  정규화 스케일의 예측값
    """
    device = next(model.parameters()).device
    model.eval()
    with torch.no_grad():
        out = model(torch.from_numpy(X).to(device))
    return out.cpu().numpy()


# =====================================================================
# 저장된 모델 불러오기
# =====================================================================
def load_model(n_features: int,
               path: str = config.MODEL_SAVE_PATH) -> BESSLSTMModel:
    """저장된 .pt 파일에서 모델 복원"""
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model  = BESSLSTMModel(n_features).to(device)
    model.load_state_dict(torch.load(path, map_location=device))
    model.eval()
    print(f"[모델 로드] {path}")
    return model
