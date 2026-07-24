import numpy as np
import config


class GRUBESSController:
    """수정된 GRU 예측 기반 BESS 제어기 (제어 로직 결함 및 Data Leakage 수정 완료)

    ※ 사용자 제공 grucontroller.py 를 채택하고 두 가지만 보강:
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
                 peak_percentile: float = 85.0,
                 flags: dict = None):
        self.capacity = capacity_kwh
        self.max_power = max_power_kw
        self.efficiency = efficiency
        self.soc_min = soc_min
        self.soc_max = soc_max
        self.peak_percentile = peak_percentile

        self.soc = soc_initial
        self.energy = capacity_kwh * soc_initial
        self.peak_threshold = None
        self.demand_target = None   # [§4] 요금적용전력 상한 목표

        # [정정 단계 플래그] 제어기 관여 정정(가·나·다·라·바)을 on/off. 기본 전부 True
        #   = 현재 최종 제어기와 완전히 동일. (마: 임계값 누수는 외부 threshold 산출에서 처리)
        _default = {'ga': True, 'na': True, 'da': True, 'ra': True, 'ba': True}
        self.flags = _default if flags is None else {**_default, **flags}
        _off = 0.80 if self.flags['na'] else 0.90   # (나) 경부하 SOC 목표
        self.SOC_TARGETS = {'off_peak': _off, 'mid_peak': 0.60, 'on_peak': 0.20}

    def set_demand_cap(self, p_cap: float):
        """[§4] 요금적용전력 상한 목표 설정.
        target = P_CAP(학습구간 무제어 요금적용전력) × DEMAND_SHAVE."""
        shave = getattr(config, 'DEMAND_SHAVE', 0.90)
        if p_cap is None or p_cap == float('inf'):
            self.demand_target = None
        else:
            self.demand_target = p_cap * shave
            print(f"[GRU 제어기] 수요전력 상한: P_CAP {p_cap:.2f} × {shave} = {self.demand_target:.2f} kW")
        return self.demand_target

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
        print(f"[GRU 제어기] (Train Set n={arr.size:,}) 피크 임계값: {self.peak_threshold:.2f} kW "
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

        # [§4/(바)] 요금적용전력 상한 관리 — ba 플래그로 on/off
        peak_hr = tariff in ('mid_peak', 'on_peak')
        cap = self.demand_target if (self.flags['ba'] and peak_hr and self.demand_target is not None) else None
        headroom = max(0.0, cap - actual_net_load) if cap is not None else float('inf')
        # [(가)] 방전 상한 기준: ga=True → 순부하(역송 방지), False → 부하
        dref = actual_net_load if self.flags['ga'] else actual_load_kw

        bess_pwr = 0.0
        action = 'idle'
        reason = 'none'

        # -------------------------------------------------------------
        # [§4] 수요전력 초과 방지 방전 (최우선) — 요금적용전력 상한 관리
        # -------------------------------------------------------------
        if cap is not None and actual_net_load > cap and self.soc > self.soc_min:
            max_d = self._max_discharge(time_step)
            dis = min(actual_net_load - cap, self.max_power, max_d, actual_net_load)
            if dis > 0:
                bess_pwr = dis
                action = 'discharge'
                reason = 'demand_charge_cut'

        # -------------------------------------------------------------
        # P0: 비상 보호
        # -------------------------------------------------------------
        elif self.soc < self.soc_min + self.EMERGENCY_LOW:
            max_c = self._max_charge(time_step)
            # [§4] 비상충전도 요금적용전력을 밀어올리지 않도록 headroom 제약
            charge_pw = min(self.max_power * 0.5, max_c, headroom)
            if charge_pw > 0:
                bess_pwr = -charge_pw
                action = 'charge'
                reason = 'emergency_low'

        elif self.soc > self.soc_max - self.EMERGENCY_HIGH:
            # [(가)] ga=True 면 순부하, False 면 부하 기준
            if dref > 0:
                max_d = self._max_discharge(time_step)
                discharge_pw = min(self.max_power * 0.5, max_d, dref)
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
            # [(가)] ga=True 면 역송 방지 순부하 상한 추가
            _caps = [excess, self.max_power, max_d]
            if self.flags['ga']:
                _caps.append(actual_net_load)
            discharge_pw = min(_caps)
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
                # [(다)] da=True 면 계통 충전을 '경부하'에서만 허용, False 면 전 시간대
                is_grid_charge_allowed = (tariff == 'off_peak') if self.flags['da'] else True

                if self.soc < self.soc_max and is_grid_charge_allowed:
                    max_c = self._max_charge(time_step)
                    _ccaps = [self.max_power * power_ratio, max_c]
                    # [(라)] ra=True 면 충전 오버슈트 클램프(진동 억제)
                    if self.flags['ra']:
                        _ccaps.append(soc_diff * self.capacity / (self.efficiency * time_step))
                    charge_pw = min(_ccaps)
                    if charge_pw > 0:
                        bess_pwr = -charge_pw
                        action = 'charge'
                        reason = f'soc_target_{tariff}'

            elif soc_diff < -self.SOC_TOLERANCE:
                # [(가)] ga 기준 방전 상한(dref)
                if self.soc > self.soc_min and dref > 0:
                    max_d = self._max_discharge(time_step)
                    discharge_pw = min(self.max_power * power_ratio, max_d, dref)
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
