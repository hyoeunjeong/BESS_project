import os
import platform
import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt

# ── 한글 폰트 설정 
_FONT_MAP = {'Windows': 'Malgun Gothic', 'Darwin': 'AppleGothic'}
matplotlib.rc('font', family=_FONT_MAP.get(platform.system(), 'DejaVu Sans'))
matplotlib.rcParams['axes.unicode_minus'] = False


# 1. 하루치 운영 그래프
def plot_daily_operation(result_df: pd.DataFrame,
                          day_idx: int = 0,
                          save_path: str = None):
    """
    하루(24시간) BESS 운영 시각화

    Parameters
    ----------
    result_df  : run_simulation() 반환 DataFrame
    day_idx    : 표시할 날짜 인덱스 (0 = 첫째 날)
    save_path  : PNG 저장 경로 (None 이면 화면 표시)
    """
    s = day_idx * 24
    d = result_df.iloc[s: s + 24].copy()
    h = list(range(24))

    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)

    # 전력 흐름
    ax = axes[0]
    ax.plot(h, d['load_kw'],  'r-',     lw=2, label='부하 (Load)')
    ax.plot(h, d['solar_kw'], color='orange', lw=2, label='태양광 (Solar)')
    ax.fill_between(h, 0, d['solar_kw'], color='orange', alpha=0.2)
    ax.set_ylabel('전력 (kW)')
    ax.set_title(f'[Rule-Based] BESS 운영 – Day {day_idx + 1}',
                 fontweight='bold')
    ax.legend(loc='upper right')
    ax.grid(alpha=0.3)

    # BESS 충·방전
    ax = axes[1]
    colors = ['green' if x < 0 else 'royalblue' if x > 0 else 'gray'
              for x in d['bess_power_kw']]
    ax.bar(h, d['bess_power_kw'], color=colors, alpha=0.75)
    ax.axhline(0, color='black', lw=0.5)
    ax.set_ylabel('BESS 전력 (kW)')
    ax.set_title('BESS 충·방전  (음수=충전 / 양수=방전)')
    ax.grid(alpha=0.3)

    # SOC
    ax = axes[2]
    ax.plot(h, d['soc'] * 100, 'purple', lw=2, marker='o', ms=4)
    ax.axhline(10, color='red', ls='--', alpha=0.5, label='SOC 하한 (10%)')
    ax.axhline(90, color='red', ls='--', alpha=0.5, label='SOC 상한 (90%)')
    ax.fill_between(h, 20, 80, color='green', alpha=0.08, label='목표 범위 (20~80%)')
    ax.set_ylabel('SOC (%)')
    ax.set_xlabel('시간 (h)')
    ax.set_title('배터리 SOC 변화')
    ax.set_ylim(0, 100)
    ax.set_xticks(range(0, 24, 2))
    ax.legend(loc='lower right')
    ax.grid(alpha=0.3)

    plt.tight_layout()
    _save_or_show(save_path)


# 2. 전체 기간 SOC 추세
def plot_soc_trend(result_df: pd.DataFrame, save_path: str = None):
    """전체 시뮬레이션 기간 SOC 변화 추세"""
    fig, ax = plt.subplots(figsize=(14, 4))
    ax.plot(result_df['timestamp'], result_df['soc'] * 100,
            'purple', lw=1, alpha=0.8)
    ax.axhline(10, color='red', ls='--', alpha=0.5, label='SOC 하한 (10%)')
    ax.axhline(90, color='red', ls='--', alpha=0.5, label='SOC 상한 (90%)')
    ax.fill_between(result_df['timestamp'], 20, 80,
                    color='green', alpha=0.08, label='목표 범위')
    ax.set_xlabel('시간')
    ax.set_ylabel('SOC (%)')
    ax.set_title('[Rule-Based] 전체 기간 SOC 추세', fontweight='bold')
    ax.set_ylim(0, 100)
    ax.legend()
    ax.grid(alpha=0.3)
    plt.xticks(rotation=45)
    plt.tight_layout()
    _save_or_show(save_path)


# 3. 평가 지표 요약 차트
def plot_metrics_summary(metrics: dict, save_path: str = None):
    """경제·에너지·안정성 지표 막대 요약"""
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    fig.suptitle('[Rule-Based] 평가 지표 요약', fontsize=14, fontweight='bold')

    # 경제적 효율
    e  = metrics['economic']
    ax = axes[0]
    bars = ax.bar(['기준\n시나리오', 'BESS\n운영'],
                  [e['baseline_cost_won'], e['bess_cost_won']],
                  color=['#ff6b6b', '#51cf66'], alpha=0.85)
    ax.set_ylabel('전기요금 (원)')
    ax.set_title(f"경제적 효율\n절감률: {e['cost_saving_rate_pct']:.1f}%")
    ax.grid(axis='y', alpha=0.3)
    for b in bars:
        ax.text(b.get_x() + b.get_width() / 2, b.get_height(),
                f"{b.get_height():,.0f}", ha='center', va='bottom', fontsize=9)

    # 에너지 효율
    en = metrics['energy']
    ax = axes[1]
    labels = ['자립률', 'BESS\n활용률', '라운드트립\n효율']
    vals   = [en['self_sufficiency_pct'],
              en['bess_utilization_pct'],
              en['roundtrip_efficiency_pct']]
    bars = ax.bar(labels, vals, color=['#4dabf7', '#9775fa', '#ffd43b'], alpha=0.85)
    ax.set_ylabel('비율 (%)')
    ax.set_title('에너지 효율')
    ax.set_ylim(0, 110)
    ax.grid(axis='y', alpha=0.3)
    for b in bars:
        ax.text(b.get_x() + b.get_width() / 2, b.get_height(),
                f"{b.get_height():.1f}%", ha='center', va='bottom', fontsize=9)

    # 운영 안정성
    s  = metrics['stability']
    ax = axes[2]
    labels = ['과충·방전\n방지율', '제어\n성공률']
    vals   = [s['prevention_rate_pct'], s['control_success_rate_pct']]
    bars = ax.bar(labels, vals, color=['#69db7c', '#74c0fc'], alpha=0.85)
    ax.set_ylabel('비율 (%)')
    ax.set_title(f"운영 안정성\n사이클: {s['cycle_count']:.1f}회")
    ax.set_ylim(0, 110)
    ax.grid(axis='y', alpha=0.3)
    for b in bars:
        ax.text(b.get_x() + b.get_width() / 2, b.get_height(),
                f"{b.get_height():.1f}%", ha='center', va='bottom', fontsize=9)

    plt.tight_layout()
    _save_or_show(save_path)


# 내부 유틸
def _save_or_show(save_path: str):
    if save_path:
        os.makedirs(os.path.dirname(save_path) or '.', exist_ok=True)
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"   저장됨: {save_path}")
    else:
        plt.show()
    plt.close()
