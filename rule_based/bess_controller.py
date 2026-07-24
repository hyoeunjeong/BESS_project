import config


class RuleBasedBESSController:

    def __init__(self,
                 capacity_kwh : float = config.BESS_CAPACITY_KWH,
                 max_power_kw : float = config.BESS_MAX_POWER_KW,
                 efficiency   : float = config.BESS_EFFICIENCY,
                 soc_min      : float = config.SOC_MIN,
                 soc_max      : float = config.SOC_MAX,
                 soc_initial  : float = config.SOC_INITIAL):

        self.capacity   = capacity_kwh
        self.max_power  = max_power_kw
        self.efficiency = efficiency
        self.soc_min    = soc_min
        self.soc_max    = soc_max

        self.soc    = soc_initial
        self.energy = capacity_kwh * soc_initial   # 현재 저장 에너지 (kWh)

    # 공개 메서드
    def control(self,
                load_kw  : float,
                solar_kw : float,
                hour     : int,
                time_step: float = config.TIME_STEP_HOURS,
                month    : int = 6,
                weekday  : int = None,
                date=None) -> dict:

        net_load      = load_kw - solar_kw
        tariff_period = config.get_tariff_period(hour, month, weekday, date)

        bess_power = 0.0
        action     = 'idle'
        blocked    = None

        # ── Rule 1: 태양광 잉여 → 충전 
        if net_load < 0:
            surplus        = -net_load
            avail_cap      = (self.soc_max - self.soc) * self.capacity
            max_chargeable = avail_cap / (self.efficiency * time_step)

            if max_chargeable > 0:
                charge_pw  = min(surplus, self.max_power, max_chargeable)
                bess_power = -charge_pw
                action     = 'charge'
            else:
                blocked = 'overcharge'

        # ── Rule 2: 순부하 발생 → 시간대에 따라 방전 
        elif net_load > 0:
            if tariff_period in ('on_peak', 'mid_peak'):
                discharge_pw = min(net_load, self.max_power)
            else:
                # 경부하 시간대: 계통이 더 저렴 → 방전 자제
                discharge_pw = 0.0

            avail_energy    = (self.soc - self.soc_min) * self.capacity
            max_dischargeable = (avail_energy * self.efficiency) / time_step

            if max_dischargeable > 0 and discharge_pw > 0:
                bess_power = min(discharge_pw, max_dischargeable)
                action     = 'discharge'
            elif discharge_pw > 0:
                blocked = 'overdischarge'

        # ── Rule 3: 경부하 시간대 + idle → 계통에서 충전 
        if action == 'idle' and tariff_period == 'off_peak' \
                and self.soc < self.soc_max:
            avail_cap      = (self.soc_max - self.soc) * self.capacity
            max_chargeable = avail_cap / (self.efficiency * time_step)
            charge_pw      = min(self.max_power, max_chargeable)
            if charge_pw > 0:
                bess_power = -charge_pw
                action     = 'charge'

        # ── SOC 업데이트 
        self._update_soc(bess_power, time_step)

        # ── 계통 전력 계산 
        # 계통 = 부하 - 태양광 - BESS 방전(+방전/-충전)
        grid_power = load_kw - solar_kw - bess_power

        return {
            'bess_power_kw' : bess_power,
            'grid_power_kw' : grid_power,
            'soc'           : self.soc,
            'action'        : action,
            'blocked'       : blocked,
            'tariff_period' : tariff_period,
        }

    def reset(self, soc_initial: float = config.SOC_INITIAL):
        """제어기 상태 초기화"""
        self.soc    = soc_initial
        self.energy = self.capacity * soc_initial

    # 내부 메서드
    def _update_soc(self, bess_power: float, time_step: float):
        """충·방전에 따른 SOC 갱신 (효율 손실 반영)"""
        if bess_power < 0:   # 충전
            energy_in    = -bess_power * time_step * self.efficiency
            self.energy += energy_in
        elif bess_power > 0:  # 방전
            energy_out   = bess_power * time_step / self.efficiency
            self.energy -= energy_out

        # SOC 클리핑 (수치 오차 방지)
        self.soc    = float(max(0.0, min(1.0, self.energy / self.capacity)))
        self.energy = self.soc * self.capacity


if __name__ == '__main__':
    ctrl = RuleBasedBESSController()
    print("=== Rule-Based 제어기 단독 테스트 ===\n")
    cases = [
        (30.0, 40.0, 13, "태양광 잉여 (낮)"),
        (50.0,  0.0, 14, "최대부하 시간 (오후 2시)"),
        (45.0,  0.0, 19, "중간부하 (저녁 7시)"),
        (15.0,  0.0,  3, "경부하 충전 (새벽 3시)"),
    ]
    for load, solar, hour, desc in cases:
        r = ctrl.control(load, solar, hour)
        print(f"[{desc}]")
        print(f"  부하={load} kW  태양광={solar} kW  → "
              f"BESS={r['bess_power_kw']:+.1f} kW ({r['action']})  "
              f"계통={r['grid_power_kw']:+.1f} kW  SOC={r['soc']*100:.1f}%\n")
