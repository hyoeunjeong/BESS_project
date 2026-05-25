"""
bess_controller.py - LSTM 예측 기반 BESS 충방전 제어기
========================================================
국내 BESS 산업 표준 운영 방식 (피크 컷 + 재생에너지 연계 하이브리드)

운영 전략
----------
[경부하 시간대] (23~08시, 60원/kWh)
  - SOC 목표: 90% (최대 충전)
  - 적극 충전 (BESS 출력의 80% 사용)
  - 다음날 피크 시간대 대비

[중부하 시간대] (9, 12, 17~22시, 110원/kWh)
  - SOC 목표: 60% (중간 유지)
  - 태양광 잉여 발생 시 충전 (P1)
  - 피크 임계값 초과 시 방전 (P2)
  - SOC가 목표보다 낮으면 보조 충전
  - SOC가 목표보다 높으면 보조 방전

[최대부하 시간대] (10~11, 13~16시, 180원/kWh)
  - SOC 목표: 20% (최대 방전)
  - 적극 방전 (BESS 출력의 100% 사용)
  - 부하 피크 컷 + 요금 절감

[비상 보호]
  - SOC < SOC_MIN + 5% 시 시간대 무관 비상 충전
  - SOC > SOC_MAX - 5% 시 시간대 무관 비상 방전 또는 정지

제어 우선순위
--------------
P0. 비상 보호 (SOC 임계 시)
P1. 태양광 잉여 -> 충전 (자가 발전 최대 활용)
P2. 부하 피크 컷 -> 방전 (피크 발생 시 즉시)
P3. SOC 목표 추종 -> 시간대별 SOC 조정
"""

import numpy as np
import config


class LSTMBESSController:
    """LSTM 예측 기반 BESS 제어기 (국내 산업 표준)"""

    # 시간대별 SOC 목표 (산업 표준)
    SOC_TARGETS = {
        'off_peak': 0.90,
        'mid_peak': 0.60,
        'on_peak':  0.20,
    }
    
    # 시간대별 출력 비율 (BESS 최대 출력 대비)
    POWER_RATIOS = {
        'off_peak': 0.80,
        'mid_peak': 0.50,
        'on_peak':  1.00,
    }
    
    SOC_TOLERANCE = 0.05
    EMERGENCY_LOW = 0.05
    EMERGENCY_HIGH = 0.05

    def __init__(self,
                 capacity_kwh: float = config.BESS_CAPACITY_KWH,
                 max_power_kw: float = config.BESS_MAX_POWER_KW,
                 efficiency: float = config.BESS_EFFICIENCY,
                 soc_min: float = config.SOC_MIN,
                 soc_max: float = config.SOC_MAX,
                 soc_initial: float = config.SOC_INITIAL,
                 peak_percentile: float = 85.0):
        self.capacity = capacity_kwh
        self.max_power = max_power_kw
        self.efficiency = efficiency
        self.soc_min = soc_min
        self.soc_max = soc_max
        self.peak_percentile = peak_percentile

        self.soc = soc_initial
        self.energy = capacity_kwh * soc_initial
        self.peak_threshold = None

    def set_peak_threshold(self, load_kw_array: np.ndarray):
        """전체 부하 데이터를 바탕으로 피크 임계값 설정"""
        self.peak_threshold = float(
            np.percentile(load_kw_array, self.peak_percentile)
        )
        print(f"[LSTM 제어기] 피크 임계값: {self.peak_threshold:.2f} kW "
              f"(상위 {100 - self.peak_percentile:.0f}%)")

    def control(self,
                predicted_net_load: float,
                actual_load_kw: float,
                actual_solar_kw: float,
                hour: int,
                time_step: float = config.TIME_STEP_HOURS) -> dict:
        """1 타임스텝 제어 결정 + 상태 업데이트"""
        if self.peak_threshold is None:
            raise RuntimeError("set_peak_threshold()를 먼저 호출하세요.")

        tariff = config.get_tariff_period(hour)
        pred_nl = predicted_net_load
        soc_target = self.SOC_TARGETS[tariff]
        power_ratio = self.POWER_RATIOS[tariff]
        
        bess_pwr = 0.0
        action = 'idle'
        reason = 'none'

        # P0: 비상 보호 (최우선)
        if self.soc < self.soc_min + self.EMERGENCY_LOW:
            max_c = self._max_charge(time_step)
            charge_pw = min(self.max_power * 0.5, max_c)
            if charge_pw > 0:
                bess_pwr = -charge_pw
                action = 'charge'
                reason = 'emergency_low'
        
        elif self.soc > self.soc_max - self.EMERGENCY_HIGH:
            if actual_load_kw > 0:
                max_d = self._max_discharge(time_step)
                discharge_pw = min(self.max_power * 0.5, max_d, actual_load_kw)
                if discharge_pw > 0:
                    bess_pwr = discharge_pw
                    action = 'discharge'
                    reason = 'emergency_high'
        
        # P1: 태양광 잉여 -> 충전
        elif pred_nl < 0 and self.soc < self.soc_max:
            surplus = abs(pred_nl)
            max_c = self._max_charge(time_step)
            charge_pw = min(surplus, self.max_power, max_c)
            if charge_pw > 0:
                bess_pwr = -charge_pw
                action = 'charge'
                reason = 'solar_surplus'

        # P2: 부하 피크 컷 -> 방전
        elif pred_nl > self.peak_threshold and self.soc > self.soc_min:
            excess = pred_nl - self.peak_threshold
            max_d = self._max_discharge(time_step)
            discharge_pw = min(excess, self.max_power, max_d)
            if discharge_pw > 0:
                bess_pwr = discharge_pw
                action = 'discharge'
                reason = 'peak_cut'

        # P3: SOC 목표 추종
        else:
            soc_diff = soc_target - self.soc
            
            if soc_diff > self.SOC_TOLERANCE:
                # SOC가 목표보다 낮음 -> 충전
                if self.soc < self.soc_max:
                    max_c = self._max_charge(time_step)
                    charge_pw = min(self.max_power * power_ratio, max_c)
                    if charge_pw > 0:
                        bess_pwr = -charge_pw
                        action = 'charge'
                        reason = f'soc_target_{tariff}'
            
            elif soc_diff < -self.SOC_TOLERANCE:
                # SOC가 목표보다 높음 -> 방전
                if self.soc > self.soc_min and actual_load_kw > 0:
                    max_d = self._max_discharge(time_step)
                    discharge_pw = min(self.max_power * power_ratio, max_d, actual_load_kw)
                    if discharge_pw > 0:
                        bess_pwr = discharge_pw
                        action = 'discharge'
                        reason = f'soc_target_{tariff}'

        # SOC 업데이트
        self._update_soc(bess_pwr, time_step)

        # 계통 전력 계산
        grid_power = actual_load_kw - actual_solar_kw - bess_pwr

        return {
            'bess_power_kw': bess_pwr,
            'grid_power_kw': grid_power,
            'soc': self.soc,
            'action': action,
            'tariff_period': tariff,
            'reason': reason,
            'soc_target': soc_target,
        }

    def reset(self, soc_initial: float = config.SOC_INITIAL):
        self.soc = soc_initial
        self.energy = self.capacity * soc_initial

    def _max_charge(self, dt: float) -> float:
        avail = (self.soc_max - self.soc) * self.capacity
        return avail / (self.efficiency * dt) if dt > 0 else 0.0

    def _max_discharge(self, dt: float) -> float:
        avail = (self.soc - self.soc_min) * self.capacity
        return (avail * self.efficiency) / dt if dt > 0 else 0.0

    def _update_soc(self, bess_pwr: float, dt: float):
        if bess_pwr < 0:
            self.energy += -bess_pwr * dt * self.efficiency
        elif bess_pwr > 0:
            self.energy -= bess_pwr * dt / self.efficiency
        self.soc = float(np.clip(self.energy / self.capacity, 0.0, 1.0))
        self.energy = self.soc * self.capacity