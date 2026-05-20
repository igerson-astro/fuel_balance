#!/usr/bin/env python3
"""Minimal tank model.

This file contains only:
- `lbhr_to_kgs(...)` to convert `lb/hr` to `kg/s`
- `hours_to_seconds(...)` to convert `hr` to `s`
- `mission_data`, which loads only time and fuel-flow columns in SI units
- `tank`, which stores fuel mass and percent full
"""

from __future__ import annotations

import csv
from pathlib import Path


LB_TO_KG = 0.45359237
SECONDS_PER_HOUR = 3600.0
LBHR_TO_KGS = LB_TO_KG / SECONDS_PER_HOUR

TRANSFER_RATE = .2 #kg/s


def lbhr_to_kgs(mass_flow_lbhr: float) -> float:
    return mass_flow_lbhr * LBHR_TO_KGS


def hours_to_seconds(time_hr: float) -> float:
    return time_hr * SECONDS_PER_HOUR


def integrate_kg(time_s: list[float], mass_flow_kgs: list[float]) -> float:
    if len(time_s) != len(mass_flow_kgs):
        raise ValueError("time_s and mass_flow_kgs must have the same length.")
    if len(time_s) == 0:
        return 0.0

    total_mass_kg = 0.0
    for i in range(1, len(time_s)):
        dt_s = time_s[i] - time_s[i - 1]
        if dt_s < 0.0:
            raise ValueError("time_s must be monotonically increasing.")
        avg_mass_flow_kgs = 0.5 * (mass_flow_kgs[i - 1] + mass_flow_kgs[i])
        total_mass_kg += avg_mass_flow_kgs * dt_s
    return total_mass_kg


class mission_data:
    """Container for time and fuel-flow data from a CSV in SI units."""

    def __init__(self, csv_file: str | Path):
        self.csv_file = Path(csv_file)
        if not self.csv_file.is_file():
            raise FileNotFoundError(f"CSV file not found: {self.csv_file}")

        with self.csv_file.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError(f"CSV file has no header row: {self.csv_file}")
            if "Time [hr]" not in reader.fieldnames:
                raise ValueError("CSV file is missing 'Time [hr]' column.")
            if "Turbogen Fuel Flow" not in reader.fieldnames:
                raise ValueError("CSV file is missing 'Turbogen Fuel Flow' column.")
            if "Propulsor Fuel Flow" not in reader.fieldnames:
                raise ValueError("CSV file is missing 'Propulsor Fuel Flow' column.")

            self.time_s = []
            self.turbogen_fuel_flow_kgs = []
            self.propulsor_fuel_flow_kgs = []

            for row in reader:
                self.time_s.append(hours_to_seconds(float(row["Time [hr]"])))
                self.turbogen_fuel_flow_kgs.append(lbhr_to_kgs(float(row["Turbogen Fuel Flow"])))
                self.propulsor_fuel_flow_kgs.append(lbhr_to_kgs(float(row["Propulsor Fuel Flow"])))


    def __len__(self) -> int:
        return len(self.time_s)


class tank:
    """Tank with fuel mass in kg and percent-full tracking."""

    def __init__(self, fuel_mass_kg: float):
        if fuel_mass_kg < 0.0:
            raise ValueError("Initial fuel mass cannot be negative.")
        if fuel_mass_kg == 0.0:
            raise ValueError("Initial fuel mass must be positive.")

        self.capacity_kg = fuel_mass_kg
        self.fuel_mass_kg = fuel_mass_kg

        self.mass_history = []
        self.update_history()

    @property
    def fill_fraction(self) -> float:
        return self.fuel_mass_kg / self.capacity_kg

    @property
    def percent_full(self) -> float:
        return 100.0 * self.fill_fraction

    def mass_add(self, mass_add_kg: float) -> None:
        self.fuel_mass_kg += mass_add_kg

    def mass_subtract(self, mass_remove_kg: float) -> None:
        self.fuel_mass_kg -= mass_remove_kg
    
    def update_history(self):
        self.mass_history.append(self.fuel_mass_kg)


class fuel_vector:
    def __init__(self, target_tank: tank, rate_kgs: float = 0.0, direction: int = 1):
        self.tank = target_tank
        self.rate_kgs = rate_kgs
        self.is_active = False
        self.direction = direction

    def activate(self) -> None:
        self.is_active = True

    def deactivate(self) -> None:
        self.is_active = False

    def step(self, dt_s: float) -> None:
        if not self.is_active:
            return
        delta_mass_kg = self.rate_kgs * dt_s

        # positive direction indicates mass is being added to tank,
        # negative direction implies mass leaves
        if self.direction > 0:
            self.tank.mass_add(delta_mass_kg)
        else:
            self.tank.mass_subtract(delta_mass_kg)


def main():
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        plt = None

    mission = mission_data(
        r"C:\Users\IlanGerson\Documents\Scripting\fuel_balance\flops_mission_with_component_breakdown_maxendurance.csv"
    )
    # mission = mission_data(
    #     r"C:\Users\IlanGerson\Documents\Scripting\fuel_balance\flops_mission_with_component_breakdown_mach1.7mission.csv"
    # )
    # mission = mission_data(
    #     r"C:\Users\IlanGerson\Documents\Scripting\fuel_balance\flops_mission_with_component_breakdown_reference.csv"
    # )
    
    fwd_mass_init = 1603.89 #assume these are in kg?

    aft_mass_init = 1053.39 #assume these are in kg?

    #FWD tank definition
    fwd_tank = tank(
        fuel_mass_kg = fwd_mass_init
    )

    #AFT tank definition
    aft_tank = tank(
        fuel_mass_kg=aft_mass_init
    )

    fwd_RTP_pump = fuel_vector(
        target_tank=fwd_tank,
        rate_kgs=0.0,
        direction = -1
        )
    aft_RTP_pump = fuel_vector(
        target_tank=aft_tank,
        rate_kgs=0.0,
        direction=-1,
    )
    turbogen = fuel_vector(
        target_tank = fwd_tank,
        rate_kgs=0.0, 
        direction=-1
        )
    transfer_to_fwd = fuel_vector(
        target_tank = fwd_tank,
        rate_kgs = TRANSFER_RATE,
        direction = 1
    )
    transfer_frm_aft = fuel_vector(
        target_tank=aft_tank,
        rate_kgs=TRANSFER_RATE,
        direction=-1,
    )

    fwd_direct_demand_kgs = [0.0]
    aft_direct_demand_kgs = [0.0]
    transfer_rate_kgs = [0.0]
    total_fuel_kg = [fwd_tank.fuel_mass_kg + aft_tank.fuel_mass_kg]
    fill_percent_difference = [fwd_tank.percent_full - aft_tank.percent_full]

    fwd_RTP_pump.activate()
    aft_RTP_pump.activate()
    turbogen.activate()

    for i in range(1, len(mission.time_s)):
        t = mission.time_s[i]
        t_prev = mission.time_s[i - 1]
        dt = t-t_prev

        prop_fuel_rate = mission.propulsor_fuel_flow_kgs[i] / 2.0
        turbogen_fuel_rate = mission.turbogen_fuel_flow_kgs[i]

        transfer_upper_bound = .8 # before fwd tank drops below this, do nothing
        transfer_lower_bound = .2 # if either tank drops below this, do nothing
        ctl_range = .1 # like a thermostat, operate transfer pump until fwd tank mass = 1.05 aft tank mass. Start operating when fwd tank mass = .95 aft tank mass

        fwd_RTP_pump.rate_kgs = aft_RTP_pump.rate_kgs = prop_fuel_rate
        turbogen.rate_kgs = turbogen_fuel_rate
        
          

        # control logic
        if (fwd_tank.fuel_mass_kg > transfer_upper_bound * fwd_tank.capacity_kg) or (aft_tank.fuel_mass_kg < transfer_lower_bound * aft_tank.capacity_kg):
            transfer_frm_aft.deactivate()
            transfer_to_fwd.deactivate()
        elif fwd_tank.fuel_mass_kg < (1-ctl_range/2) * aft_tank.fuel_mass_kg:
            transfer_frm_aft.activate()
            transfer_to_fwd.activate()
        else:
            transfer_frm_aft.deactivate()
            transfer_to_fwd.deactivate()

        current_transfer_rate = transfer_to_fwd.rate_kgs if transfer_to_fwd.is_active else 0.0

        fwd_RTP_pump.step(dt)
        aft_RTP_pump.step(dt)
        turbogen.step(dt)
        transfer_frm_aft.step(dt)
        transfer_to_fwd.step(dt)

        fwd_tank.update_history()
        aft_tank.update_history()

        ################################################################
        fwd_direct_demand_kgs.append(prop_fuel_rate + turbogen_fuel_rate)
        aft_direct_demand_kgs.append(prop_fuel_rate)
        transfer_rate_kgs.append(current_transfer_rate)
        total_fuel_kg.append(fwd_tank.fuel_mass_kg + aft_tank.fuel_mass_kg)
        fill_percent_difference.append(fwd_tank.percent_full - aft_tank.percent_full)
        #################################################################

    ################################## PRINTOUTS #######################################    
    print("\nFuel balance complete.")
    print(f"Input file:   {mission.csv_file}")
    print(f"Initial FWD:  {fwd_mass_init:.1f} kg, Initial AFT: {aft_mass_init:.1f} kg")
    print(
        f"Final FWD:    {fwd_tank.fuel_mass_kg:.1f} kg, "
        f"Final AFT:   {aft_tank.fuel_mass_kg:.1f} kg"
    )
    print(
        f"Final FWD %:  {fwd_tank.percent_full:.2f} %, "
        f"Final AFT %: {aft_tank.percent_full:.2f} %"
    )
    print(
        f"Minimum FWD:  {min(fwd_tank.mass_history):.1f} kg, "
        f"Minimum AFT: {min(aft_tank.mass_history):.1f} kg"
    )
    print(f"Max transfer: {max(transfer_rate_kgs):.3f} kg/s")
    print(f"Max Fill % Diff:  {max(abs(value) for value in fill_percent_difference):.2f} %\n")

    if plt is None:
        print("Plotting skipped: matplotlib is not installed.")
        return

    time_hr = [value / SECONDS_PER_HOUR for value in mission.time_s]

    ############################## PLOTS ####################################################
    fig, axes = plt.subplots(3, 1, sharex=True, num="Tank Fuel Balance", figsize=(11, 11))

    axes[0].plot(time_hr, mission.turbogen_fuel_flow_kgs, linewidth=1.6, label="Turbogen demand")
    axes[0].plot(
        time_hr,
        mission.propulsor_fuel_flow_kgs,
        linewidth=1.6,
        label="Propulsor demand",
    )
    axes[0].plot(
        time_hr,
        [mission.turbogen_fuel_flow_kgs[i] + mission.propulsor_fuel_flow_kgs[i] for i in range(len(mission))],
        "--",
        linewidth=1.6,
        label="Total demand",
    )
    axes[0].grid(True)
    axes[0].set_ylabel("Fuel flow [kg/s]")
    axes[0].set_title("Fuel Flow Demands vs Time")
    axes[0].legend(loc="best")

    axes[1].plot(time_hr, fwd_tank.mass_history, linewidth=1.6, label="FWD tank")
    axes[1].plot(time_hr, aft_tank.mass_history, linewidth=1.6, label="AFT tank")
    axes[1].plot(time_hr, total_fuel_kg, "--", linewidth=1.6, label="Total fuel")
    axes[1].axhline(0.0, linestyle=":", label="Zero fuel")
    axes[1].grid(True)
    axes[1].set_ylabel("Fuel [kg]")
    axes[1].set_title("Tank Fuel Levels vs Time")
    axes[1].legend(loc="best")

    axes[2].plot(time_hr, transfer_rate_kgs, linewidth=1.6)
    axes[2].grid(True)
    axes[2].set_xlabel("Time [hr]")
    axes[2].set_ylabel("AFT-to-FWD transfer [kg/s]")
    axes[2].set_title("Transfer Command vs Time")

    fig.tight_layout()
    plt.show()

main()
