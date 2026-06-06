import os
import platform
import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt

_FONT_MAP = {'Windows': 'Malgun Gothic', 'Darwin': 'AppleGothic'}
matplotlib.rc('font', family=_FONT_MAP.get(platform.system(), 'DejaVu Sans'))
matplotlib.rcParams['axes.unicode_minus'] = False

# 1. LSTM 학습 곡선
def plot_training_curve(history: dict, save_path: str = None):
    """Train / Val Loss 곡선"""
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(history['train_loss'], label='Train Loss', lw=1.5)
    ax.plot(history['val_loss'],   label='Val Loss',   lw=1.5)
    ax.set_xlabel('Epoch')
    ax.set_ylabel('MSE Loss')
    ax.set_title('[LSTM] 학습 곡선 (Train / Val Loss)', fontweight='bold')
    ax.legend()
    ax.grid(alpha=0.3)
    plt.tight_layout()
    _save_or_show(save_path)

# 2. 순부하 예측 vs 실제 비교
def plot_prediction(y_true: np.ndarray, y_pred: np.ndarray,
                    hours: int = 168, save_path: str = None):
    """실제 vs 예측 순부하 (기본 7일=168h)"""
    n  = min(hours, len(y_true))
    t  = np.arange(n)
    fig, axes = plt.subplots(2, 1, figsize=(13, 7))

    # 시계열 비교
    axes[0].plot(t, y_true[:n], label='실제 순부하',   lw=1.5, alpha=0.85)
    axes[0].plot(t, y_pred[:n], label='LSTM 예측',     lw=1.5, alpha=0.85, ls='--')
    axes[0].set_ylabel('순부하 (kW)')
    axes[0].set_title('[LSTM] 순부하 예측 vs 실제 (7일)', fontweight='bold')
    axes[0].legend()
    axes[0].grid(alpha=0.3)

    # 잔차
    resid = y_pred[:n] - y_true[:n]
    axes[1].bar(t, resid, color=['#e74c3c' if r > 0 else '#2ecc71' for r in resid],
                alpha=0.7, width=1)
    axes[1].axhline(0, color='black', lw=0.8)
    axes[1].set_xlabel('시간 (h)')
    axes[1].set_ylabel('잔차 (kW)')
    axes[1].set_title('예측 잔차 (예측 - 실제)')
    axes[1].grid(alpha=0.3)

    plt.tight_layout()
    _save_or_show(save_path)

# 3. 하루치 운영 그래프
def plot_daily_operation(result_df: pd.DataFrame,
                          day_idx: int = 0,
                          save_path: str = None):
    """LSTM 기반 BESS 하루 운영 시각화"""
    s = day_idx * 24
    d = result_df.iloc[s: s + 24].copy()
    h = list(range(24))

    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)

    ax = axes[0]
    ax.plot(h, d['load_kw'],  'r-',     lw=2, label='부하 (Load)')
    ax.plot(h, d['solar_kw'], color='orange', lw=2, label='태양광 (Solar)')
    ax.plot(h, d['predicted_net_load_kw'], 'b--', lw=1.5,
            label='LSTM 예측 순부하', alpha=0.8)
    ax.fill_between(h, 0, d['solar_kw'], color='orange', alpha=0.2)
    ax.set_ylabel('전력 (kW)')
    ax.set_title(f'[LSTM] BESS 운영 – Day {day_idx + 1}', fontweight='bold')
    ax.legend(loc='upper right')
    ax.grid(alpha=0.3)

    ax = axes[1]
    colors = ['green' if x < 0 else 'royalblue' if x > 0 else 'gray'
              for x in d['bess_power_kw']]
    ax.bar(h, d['bess_power_kw'], color=colors, alpha=0.75)
    ax.axhline(0, color='black', lw=0.5)
    ax.set_ylabel('BESS 전력 (kW)')
    ax.set_title('BESS 충·방전  (음수=충전 / 양수=방전)')
    ax.grid(alpha=0.3)

    ax = axes[2]
    ax.plot(h, d['soc'] * 100, 'purple', lw=2, marker='o', ms=4)
    ax.axhline(10, color='red', ls='--', alpha=0.5, label='SOC 하한 (10%)')
    ax.axhline(90, color='red', ls='--', alpha=0.5, label='SOC 상한 (90%)')
    ax.fill_between(h, 20, 80, color='green', alpha=0.08, label='목표 범위')
    ax.set_ylabel('SOC (%)')
    ax.set_xlabel('시간 (h)')
    ax.set_title('배터리 SOC 변화')
    ax.set_ylim(0, 100)
    ax.set_xticks(range(0, 24, 2))
    ax.legend(loc='lower right')
    ax.grid(alpha=0.3)

    plt.tight_layout()
    _save_or_show(save_path)

# 4. SOC 추세
def plot_soc_trend(result_df: pd.DataFrame, save_path: str = None):
    fig, ax = plt.subplots(figsize=(14, 4))
    ax.plot(result_df['timestamp'], result_df['soc'] * 100,
            'purple', lw=1, alpha=0.8)
    ax.axhline(10, color='red', ls='--', alpha=0.5, label='SOC 하한 (10%)')
    ax.axhline(90, color='red', ls='--', alpha=0.5, label='SOC 상한 (90%)')
    ax.fill_between(result_df['timestamp'], 20, 80,
                    color='green', alpha=0.08, label='목표 범위')
    ax.set_xlabel('시간')
    ax.set_ylabel('SOC (%)')
    ax.set_title('[LSTM] 전체 기간 SOC 추세', fontweight='bold')
    ax.set_ylim(0, 100)
    ax.legend()
    ax.grid(alpha=0.3)
    plt.xticks(rotation=45)
    plt.tight_layout()
    _save_or_show(save_path)


# 5. Rule-Based vs LSTM 종합 비교 차트
def plot_comparison(rb_metrics: dict, lstm_metrics: dict,
                    save_path: str = None):
    """
    Rule-Based vs LSTM 핵심 지표 막대 비교 차트
    논문 결과 그림으로 직접 사용 가능합니다.
    """
    labels = [
        '요금 절감률\n(%)',
        '피크 요금\n절감률 (%)',
        '자립률\n(%)',
        'BESS 활용률\n(%)',
        '제어 성공률\n(%)',
        '과충·방전\n방지율 (%)',
    ]

    e_rb = rb_metrics['economic'];   e_dl = lstm_metrics['economic']
    n_rb = rb_metrics['energy'];     n_dl = lstm_metrics['energy']
    s_rb = rb_metrics['stability'];  s_dl = lstm_metrics['stability']

    rb_vals = [
        e_rb['cost_saving_rate_pct'],
        e_rb['peak_saving_rate_pct'],
        n_rb['self_sufficiency_pct'],
        n_rb['bess_utilization_pct'],
        s_rb['control_success_rate_pct'],
        s_rb['prevention_rate_pct'],
    ]
    dl_vals = [
        e_dl['cost_saving_rate_pct'],
        e_dl['peak_saving_rate_pct'],
        n_dl['self_sufficiency_pct'],
        n_dl['bess_utilization_pct'],
        s_dl['control_success_rate_pct'],
        s_dl['prevention_rate_pct'],
    ]

    x   = np.arange(len(labels))
    w   = 0.35
    fig, ax = plt.subplots(figsize=(13, 6))
    b1 = ax.bar(x - w/2, rb_vals, w, label='Rule-Based', color='#ff6b6b', alpha=0.85)
    b2 = ax.bar(x + w/2, dl_vals, w, label='LSTM',       color='#339af0', alpha=0.85)

    for b in list(b1) + list(b2):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.3,
                f"{b.get_height():.1f}", ha='center', va='bottom', fontsize=9)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel('값 (%)')
    ax.set_title('Rule-Based vs LSTM  –  BESS 제어 성능 비교',
                 fontsize=13, fontweight='bold')
    ax.legend(fontsize=11)
    ax.grid(axis='y', alpha=0.3)
    ax.set_ylim(0, max(max(rb_vals), max(dl_vals)) * 1.15)
    plt.tight_layout()
    _save_or_show(save_path)


# 6. 레이더 차트 (선택 사용)
def plot_radar(rb_metrics: dict, lstm_metrics: dict,
               save_path: str = None):
    """레이더 차트 (5개 축) – 논문 부록용"""
    cats = ['요금 절감률', '피크절감률', '자립률', '제어 성공률', '방지율']

    e_rb = rb_metrics['economic'];   e_dl = lstm_metrics['economic']
    n_rb = rb_metrics['energy'];     n_dl = lstm_metrics['energy']
    s_rb = rb_metrics['stability'];  s_dl = lstm_metrics['stability']

    rb_v = [e_rb['cost_saving_rate_pct'], e_rb['peak_saving_rate_pct'],
            n_rb['self_sufficiency_pct'],
            s_rb['control_success_rate_pct'], s_rb['prevention_rate_pct']]
    dl_v = [e_dl['cost_saving_rate_pct'], e_dl['peak_saving_rate_pct'],
            n_dl['self_sufficiency_pct'],
            s_dl['control_success_rate_pct'], s_dl['prevention_rate_pct']]

    angles = np.linspace(0, 2 * np.pi, len(cats), endpoint=False).tolist()
    angles += angles[:1]
    rb_v  += rb_v[:1]
    dl_v  += dl_v[:1]

    fig, ax = plt.subplots(figsize=(6, 6), subplot_kw={'polar': True})
    ax.plot(angles, rb_v, 'o--', color='#ff6b6b', lw=2, label='Rule-Based')
    ax.plot(angles, dl_v, 'o-',  color='#339af0', lw=2, label='LSTM')
    ax.fill(angles, dl_v, color='#339af0', alpha=0.12)
    ax.set_thetagrids(np.degrees(angles[:-1]), cats)
    ax.set_title('종합 성능 비교 (레이더 차트)', fontweight='bold', pad=15)
    ax.legend(loc='upper right', bbox_to_anchor=(1.25, 1.1))
    plt.tight_layout()
    _save_or_show(save_path)

def _save_or_show(save_path):
    if save_path:
        os.makedirs(os.path.dirname(save_path) or '.', exist_ok=True)
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"   저장됨: {save_path}")
    else:
        plt.show()
    plt.close()
