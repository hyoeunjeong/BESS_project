"""논문 표 일괄 생성기
=====================================================================
저장소의 시뮬레이션 결과 CSV(각 방식 main.py 산출물)와 공통 입력
(results/base_data.csv, pred_*.npy)을 읽어 논문 표를 results/ 에 생성한다.

선행:
    cd rule_based && python main.py      # rb_simulation_result.csv (+reason)
    cd DL_LSTM   && python main.py       # lstm_simulation_result.csv (+reason)
    cd DL_GRU    && python main.py       # gru_simulation_result.csv (+reason)
    cd DL_LSTM   && python _build_base_data.py   # base_data.csv, pred_lstm.npy
    cd DL_GRU    && python _ablation_dump.py      # pred_gru.npy

실행:
    python make_tables.py
=====================================================================
"""
import os
import sys
import subprocess
import pandas as pd

REPO    = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(REPO, 'results')

RESULT_CSV = {
    'Rule-Based': os.path.join(REPO, 'rule_based', 'results', 'rb_simulation_result.csv'),
    'LSTM'      : os.path.join(REPO, 'DL_LSTM',    'results', 'lstm_simulation_result.csv'),
    'GRU'       : os.path.join(REPO, 'DL_GRU',     'results', 'gru_simulation_result.csv'),
}


def _load_results():
    out = {}
    for name, path in RESULT_CSV.items():
        if not os.path.exists(path):
            raise SystemExit(f"[중단] 결과 CSV 없음: {path}\n  먼저 각 방식 main.py 를 실행하세요.")
        out[name] = pd.read_csv(path, parse_dates=['timestamp'])
    return out


# =====================================================================
# 표 2.12 — 제어 계층별 발동 횟수
# =====================================================================
# DL 제어기 7종 reason + none, rule_based 는 자체 reason(+blocked)
REASON_ORDER = [
    'demand_charge_cut',                       # P0'
    'emergency_low', 'emergency_high',         # P0
    'solar_surplus',                           # P1
    'peak_cut',                                # P2
    'soc_target_off_peak', 'soc_target_mid_peak', 'soc_target_on_peak',  # P3
    'none',
    # rule_based 고유
    'peak_discharge', 'offpeak_grid_charge',
]


def build_table_2_12(results: dict) -> pd.DataFrame:
    rows = []

    def col_for(df):
        n = len(df)
        act = df['action'].value_counts().to_dict()
        rea = df['reason'].value_counts().to_dict() if 'reason' in df.columns else {}
        d = {
            'charge_count'   : int(act.get('charge', 0)),
            'discharge_count': int(act.get('discharge', 0)),
            'idle_count'     : int(act.get('idle', 0)) + int(act.get('none', 0)),
        }
        for r in REASON_ORDER:
            d[f'reason_{r}'] = int(rea.get(r, 0))
        # 예측값 참조 발동 비율 = peak_cut / 전체시점 × 100
        d['prediction_reference_pct'] = round(int(rea.get('peak_cut', 0)) / n * 100, 2) if n else 0.0
        # rule_based blocked (있으면)
        if 'blocked' in df.columns:
            blk = df['blocked'].fillna('none').value_counts().to_dict()
            d['blocked_overcharge']    = int(blk.get('overcharge', 0))
            d['blocked_overdischarge'] = int(blk.get('overdischarge', 0))
        return d

    cols = {name: col_for(df) for name, df in results.items()}
    # 모든 지표 키 합집합(순서 유지)
    keys = []
    for name in results:
        for k in cols[name]:
            if k not in keys:
                keys.append(k)
    for k in keys:
        rows.append({'metric': k, **{name: cols[name].get(k, 0) for name in results}})

    out = pd.DataFrame(rows)
    path = os.path.join(RESULTS, 'table_2_12_reasons.csv')
    out.to_csv(path, index=False, encoding='utf-8-sig')
    print(f"[표 2.12] 저장: {path}")
    print(out.to_string(index=False))
    return out


def _run_stage_tables():
    """표 2.11/2.15 는 DL_LSTM/stage_run.py 가 생성한다(config 의존 → 서브프로세스)."""
    script = os.path.join(REPO, 'DL_LSTM', 'stage_run.py')
    print("[표 2.11/2.15] stage_run.py 실행 중…")
    r = subprocess.run([sys.executable, script], cwd=os.path.join(REPO, 'DL_LSTM'),
                       capture_output=True, text=True, encoding='utf-8')
    if r.returncode != 0:
        print("  [경고] stage_run.py 실패:\n" + (r.stderr or '')[-800:])
    else:
        print("  표 2.11/2.15 생성 완료")


def main():
    os.makedirs(RESULTS, exist_ok=True)
    _run_stage_tables()                 # 표 2.11, 2.15
    results = _load_results()
    build_table_2_12(results)           # 표 2.12
    print("\n[make_tables] 완료 — results/table_2_11_stages.csv, "
          "table_2_12_reasons.csv, table_2_15_kappa.csv")


if __name__ == '__main__':
    main()
