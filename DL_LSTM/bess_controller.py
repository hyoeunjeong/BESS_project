import numpy as np
import config


class LSTMBESSController:
    """수정된 LSTM 예측 기반 BESS 제어기 (제어 로직 결함 및 Data Leakage 수정 완료)

    ※ 사용자 제공 lstmcotroller.py 를 채택하고 두 가지만 보강:
        · month 파라미터 추가 (계절 요금 config.get_tariff_period(hour, month) 호환)
        · [#3] 계통 충전을 '경부하'에서만 허용 (중간/최대부하 계통충전 전면 금지)
    """

    # 시간대별 SOC 목표 (임계값 간섭 방지를 위해 off_peak 목표를 0.90 -> 0.80으로 하향)
    SOC_TARGETS = {
        'off_peak': 0.80,  # 기존 0.90에서 0.80으로 하향 (P0 비상 방전 85%와의 간섭 제거)
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

    def set_peak_threshold(self, train_load_kw_array: np.ndarray):
        """
        [Data Leakage 차단]
        전체 데이터(Test Set 포함)가 아닌 반드시 오프라인 Train Set 데이터만 전달받아
        피크 임계값을 설정하도록 수정.
        """
        arr = np.asarray(train_load_kw_array, dtype=float)
        arr = arr[~np.isnan(arr)]
        self.peak_threshold = float(
            np.percentile(arr, self.peak_percentile)
        )
        print(f"[LSTM 제어기] (Train Set n={arr.size:,}) 피크 임계값: {self.peak_threshold:.2f} kW "
              f"(상위 {100 - self.peak_percentile:.0f}%)")
        return self.peak_threshold

    def control(self,
                predicted_net_load: float,
                actual_load_kw: float,
                actual_solar_kw: float,
                hour: int,
                time_step: float = config.TIME_STEP_HOURS,
                month: int = 6,
                weekday: int = None,
                date=None) -> dict:
        """1 타임스텝 제어 결정 + 상태 업데이트"""
        if self.peak_threshold is None:
            raise RuntimeError("set_peak_threshold()를 먼저 호출하세요.")

        tariff = config.get_tariff_period(hour, month, weekday, date)
        pred_nl = predicted_net_load
        soc_target = self.SOC_TARGETS[tariff]
        power_ratio = self.POWER_RATIOS[tariff]

        # [핵심 수정] 계통 역송 방지를 위한 실제 '순부하(Net Load)' 및 '태양광 잉여' 계산
        actual_net_load = max(0.0, actual_load_kw - actual_solar_kw)
        actual_surplus = max(0.0, actual_solar_kw - actual_load_kw)

        bess_pwr = 0.0
        action = 'idle'
        reason = 'none'

        # -------------------------------------------------------------
        # P0: 비상 보호 (최우선)
        # -------------------------------------------------------------
        if self.soc < self.soc_min + self.EMERGENCY_LOW:
            max_c = self._max_charge(time_step)
            charge_pw = min(self.max_power * 0.5, max_c)
            if charge_pw > 0:
                bess_pwr = -charge_pw
                action = 'charge'
                reason = 'emergency_low'

        elif self.soc > self.soc_max - self.EMERGENCY_HIGH:
            # [수정] actual_load_kw -> actual_net_load로 변경 (역송 완전 차단)
            if actual_net_load > 0:
                max_d = self._max_discharge(time_step)
                discharge_pw = min(self.max_power * 0.5, max_d, actual_net_load)
                if discharge_pw > 0:
                    bess_pwr = discharge_pw
                    action = 'discharge'
                    reason = 'emergency_high'

        # -------------------------------------------------------------
        # P1: 태양광 잉여 -> 충전
        # -------------------------------------------------------------
        # [개선] 실측 태양광 잉여를 우선 흡수해 커튼일먼트(태양광 낭비)를 최소화한다.
        #   기존은 pred_nl<0 일 때만 충전해, 예측이 순부하>0 로 빗나가면 실제 잉여를
        #   버렸다(잉여 176h 중 136h 스킵). 실측 잉여 기준으로 바꿔 계통 역송/낭비를 막는다.
        #   충전량은 actual_surplus 로 한정 → 계통 전력 구매 충전은 발생하지 않는다.
        elif actual_surplus > 0 and self.soc < self.soc_max:
            max_c = self._max_charge(time_step)
            charge_pw = min(actual_surplus, self.max_power, max_c)
            if charge_pw > 0:
                bess_pwr = -charge_pw
                action = 'charge'
                reason = 'solar_surplus'

        # -------------------------------------------------------------
        # P2: 부하 피크 컷 -> 방전
        # -------------------------------------------------------------
        elif pred_nl > self.peak_threshold and self.soc > self.soc_min:
            excess = pred_nl - self.peak_threshold
            max_d = self._max_discharge(time_step)
            # [수정] 과도한 방전으로 인한 역송을 막기 위해 actual_net_load 제약 추가
            discharge_pw = min(excess, self.max_power, max_d, actual_net_load)
            if discharge_pw > 0:
                bess_pwr = discharge_pw
                action = 'discharge'
                reason = 'peak_cut'

        # -------------------------------------------------------------
        # P3: SOC 목표 추종
        # -------------------------------------------------------------
        else:
            soc_diff = soc_target - self.soc

            if soc_diff > self.SOC_TOLERANCE:
                # SOC가 목표보다 낮음 -> 충전
                # [#3] 계통 충전은 '경부하'에서만 허용한다.
                #   중간·최대부하 시간대의 비싼 계통 전력(110/180원) 구매 충전을 전면 금지.
                #   (태양광 잉여 충전 P1 은 시간대 무관하게 위에서 이미 허용됨)
                is_grid_charge_allowed = (tariff == 'off_peak')

                if self.soc < self.soc_max and is_grid_charge_allowed:
                    max_c = self._max_charge(time_step)
                    # [정정 라] 충전 오버슈트 클램프: SOC가 목표를 넘어 상승하지
                    #           않도록 제한 → 다음 스텝의 P0 강제 방전(진동) 억제
                    clamp = soc_diff * self.capacity / (self.efficiency * time_step)
                    charge_pw = min(self.max_power * power_ratio, max_c, clamp)
                    if charge_pw > 0:
                        bess_pwr = -charge_pw
                        action = 'charge'
                        reason = f'soc_target_{tariff}'

            elif soc_diff < -self.SOC_TOLERANCE:
                # SOC가 목표보다 높음 -> 방전
                # [수정] actual_load_kw -> actual_net_load로 변경 (역송 방지)
                if self.soc > self.soc_min and actual_net_load > 0:
                    max_d = self._max_discharge(time_step)
                    discharge_pw = min(self.max_power * power_ratio, max_d, actual_net_load)
                    if discharge_pw > 0:
                        bess_pwr = discharge_pw
                        action = 'discharge'
                        reason = f'soc_target_{tariff}'

        # SOC 상태 업데이트
        self._update_soc(bess_pwr, time_step)

        # 계통 전력 계산 (음수면 계통으로 역송됨을 의미)
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
        if bess_pwr < 0:  # 충전 (-kW)
            self.energy += -bess_pwr * dt * self.efficiency
        elif bess_pwr > 0:  # 방전 (+kW)
            self.energy -= bess_pwr * dt / self.efficiency
        self.soc = float(np.clip(self.energy / self.capacity, 0.0, 1.0))
        self.energy = self.soc * self.capacity
