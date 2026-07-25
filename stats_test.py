"""[재현] 5시드 통계 — 평균±σ, 변동계수(CV), Welch t/p (README §11.1·§11.2).

results/seed_sweep_lstm.csv · seed_sweep_gru.csv 를 읽어 예측(test MAE)·운영(순절감)
지표의 평균±표준편차와 두 모델 차이의 Welch t-검정을 출력한다.

실행: python stats_test.py
"""
import os
import pandas as pd
from scipy import stats

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')
L = pd.read_csv(os.path.join(RESULTS, 'seed_sweep_lstm.csv'))
G = pd.read_csv(os.path.join(RESULTS, 'seed_sweep_gru.csv'))


def _line(tag, col, v, dec=4):
    mu, sd = v.mean(), v.std(ddof=1)
    cv = sd / mu * 100 if mu else 0.0
    return f"{tag:5s} {col:11s} {mu:,.{dec}f} ± {sd:,.{dec}f}  (CV {cv:.2f}%)"


def main():
    # 예측 정확도 (test MAE)
    print(_line('LSTM', 'mae_test', L['mae_test']))
    print(_line('GRU',  'mae_test', G['mae_test']))
    ratio = G['mae_test'].std(ddof=1) / L['mae_test'].std(ddof=1)
    print(f"sigma ratio (GRU/LSTM) = {ratio:.2f}")
    a, b = L['mae_test'].values, G['mae_test'].values
    t, p = stats.ttest_ind(a, b, equal_var=False)
    va, vb, na, nb = a.var(ddof=1), b.var(ddof=1), len(a), len(b)
    df = (va / na + vb / nb) ** 2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    print(f"Welch t = {t:.4f}, p = {p:.4f}, df = {df:.2f}")
    # 운영 성과 (순절감)
    print(_line('LSTM', 'net_saving', L['net_saving_won'], dec=0))
    print(_line('GRU',  'net_saving', G['net_saving_won'], dec=0))


if __name__ == '__main__':
    main()
