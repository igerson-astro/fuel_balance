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

TRANSFER_RATE = .1 #kg/s


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
        self.sample_spacing_s = 1.0
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

            raw_time_s = []
            raw_turbogen_fuel_flow_kgs = []
            raw_propulsor_fuel_flow_kgs = []

            for row in reader:
                raw_time_s.append(hours_to_seconds(float(row["Time [hr]"])))
                raw_turbogen_fuel_flow_kgs.append(lbhr_to_kgs(float(row["Turbogen Fuel Flow"])))
                raw_propulsor_fuel_flow_kgs.append(lbhr_to_kgs(float(row["Propulsor Fuel Flow"])))

        self.time_s, self.turbogen_fuel_flow_kgs, self.propulsor_fuel_flow_kgs = (
            self._prepare_data(
                raw_time_s,
                raw_turbogen_fuel_flow_kgs,
                raw_propulsor_fuel_flow_kgs,
                self.sample_spacing_s,
            )
        )

    def _prepare_data(
        self,
        time_s: list[float],
        turbogen_fuel_flow_kgs: list[float],
        propulsor_fuel_flow_kgs: list[float],
        sample_spacing_s: float,
    ) -> tuple[list[float], list[float], list[float]]:
        if len(time_s) != len(turbogen_fuel_flow_kgs) or len(time_s) != len(propulsor_fuel_flow_kgs):
            raise ValueError("Mission data columns must have the same length.")
        if len(time_s) == 0:
            return [], [], []
        if sample_spacing_s <= 0.0:
            raise ValueError("sample_spacing_s must be positive.")

        unique_time_s = [time_s[0]]
        unique_turbogen_fuel_flow_kgs = [turbogen_fuel_flow_kgs[0]]
        unique_propulsor_fuel_flow_kgs = [propulsor_fuel_flow_kgs[0]]

        for i in range(1, len(time_s)):
            current_time_s = time_s[i]
            previous_time_s = unique_time_s[-1]

            if current_time_s < previous_time_s:
                raise ValueError("Time [hr] must be monotonically increasing.")

            if current_time_s == previous_time_s:
                unique_turbogen_fuel_flow_kgs[-1] = turbogen_fuel_flow_kgs[i]
                unique_propulsor_fuel_flow_kgs[-1] = propulsor_fuel_flow_kgs[i]
                continue

            unique_time_s.append(current_time_s)
            unique_turbogen_fuel_flow_kgs.append(turbogen_fuel_flow_kgs[i])
            unique_propulsor_fuel_flow_kgs.append(propulsor_fuel_flow_kgs[i])

        if len(unique_time_s) == 1:
            return unique_time_s, unique_turbogen_fuel_flow_kgs, unique_propulsor_fuel_flow_kgs

        resampled_time_s = []
        current_time_s = unique_time_s[0]
        final_time_s = unique_time_s[-1]

        while current_time_s < final_time_s:
            resampled_time_s.append(current_time_s)
            current_time_s += sample_spacing_s
        if not resampled_time_s or resampled_time_s[-1] != final_time_s:
            resampled_time_s.append(final_time_s)

        resampled_turbogen_fuel_flow_kgs = self._interpolate_series(
            unique_time_s,
            unique_turbogen_fuel_flow_kgs,
            resampled_time_s,
        )
        resampled_propulsor_fuel_flow_kgs = self._interpolate_series(
            unique_time_s,
            unique_propulsor_fuel_flow_kgs,
            resampled_time_s,
        )

        return (
            resampled_time_s,
            resampled_turbogen_fuel_flow_kgs,
            resampled_propulsor_fuel_flow_kgs,
        )

    def _interpolate_series(
        self,
        source_time_s: list[float],
        source_values: list[float],
        target_time_s: list[float],
    ) -> list[float]:
        interpolated_values = []
        source_index = 0

        for target_time in target_time_s:
            while source_index < len(source_time_s) - 2 and source_time_s[source_index + 1] < target_time:
                source_index += 1

            left_time = source_time_s[source_index]
            right_time = source_time_s[source_index + 1]
            left_value = source_values[source_index]
            right_value = source_values[source_index + 1]

            if target_time == left_time:
                interpolated_values.append(left_value)
                continue
            if target_time == right_time:
                interpolated_values.append(right_value)
                continue

            fraction = (target_time - left_time) / (right_time - left_time)
            interpolated_values.append(left_value + fraction * (right_value - left_value))

        return interpolated_values


    def __len__(self) -> int:
        return len(self.time_s)


class tank:
    """Tank with fuel mass in kg and percent-full tracking."""

    def __init__(self, fuel_mass_kg: float, cg: float):
        if fuel_mass_kg < 0.0:
            raise ValueError("Initial fuel mass cannot be negative.")
        if fuel_mass_kg == 0.0:
            raise ValueError("Initial fuel mass must be positive.")

        self.capacity_kg = fuel_mass_kg
        self.fuel_mass_kg = fuel_mass_kg
        self.cg = cg

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

class vehicle:
    def __init__(self, fwd_tank: tank, aft_tank: tank, empty_cg: float, empty_mass: float):
        self.fwd_tank = fwd_tank
        self.aft_tank = aft_tank
        self.empty_cg = empty_cg
        self.empty_mass = empty_mass
        self.cg_init = (fwd_tank.fuel_mass_kg*fwd_tank.cg + aft_tank.fuel_mass_kg*aft_tank.cg + self.empty_cg*self.empty_mass)/(fwd_tank.fuel_mass_kg + aft_tank.fuel_mass_kg + self.empty_mass)
        self.CG = self.cg_init
        self.CG_history = []
        self.CG_history.append(self.cg_init)

    def update_history(self):
        self.CG = ((self.fwd_tank.fuel_mass_kg*self.fwd_tank.cg + self.aft_tank.fuel_mass_kg*self.aft_tank.cg + self.empty_cg*self.empty_mass)/
        (self.fwd_tank.fuel_mass_kg + self.aft_tank.fuel_mass_kg + self.empty_mass))
        self.CG_history.append(
            self.CG
            )


def main():
    try:
        import matplotlib.pyplot as plt
        plt.close("all")
    except ImportError:
        plt = None

    # mission = mission_data(
    #     r"C:\Users\IlanGerson\Documents\Scripting\fuel_balance\flops_mission_with_component_breakdown_maxendurance.csv"
    # )
    mission = mission_data(
        r"C:\Users\IlanGerson\Documents\Scripting\fuel_balance\flops_mission_with_component_breakdown_mach1.7mission.csv"
    )
    # mission = mission_data(
    #     r"C:\Users\IlanGerson\Documents\Scripting\fuel_balance\flops_mission_with_component_breakdown_reference.csv"
    # )
    
    fwd_mass_init = 1603.89

    aft_mass_init = 1053.39 

    vehicle_empty_cg = 11.069 #m
    vehicle_empty_mass = 4897 #kg
    fwd_tank_cg = 8.14 #m
    aft_tank_cg = 14.27 #m

    cg_far_limit = 11.08 #m
    cg_near_limit = 10.75 #m

    transfer_upper_bound = .9 # before fwd tank drops below this, do nothing
    transfer_lower_bound = .2 # if either tank drops below this, do nothing
    ctl_range = .4 # like a thermostat, operate transfer pump until fwd tank mass = 1.05 aft tank mass. Start operating when fwd tank mass = .95 aft tank mass

    if fwd_tank_cg is None or aft_tank_cg is None:
        raise ValueError(
            "CG plotting requires actual tank CG station inputs. "
            "Set fwd_tank_cg and aft_tank_cg before running this script."
        )


    #FWD tank definition
    fwd_tank = tank(
        fuel_mass_kg = fwd_mass_init,
        cg=fwd_tank_cg,
    )

    #AFT tank definition
    aft_tank = tank(
        fuel_mass_kg=aft_mass_init,
        cg=aft_tank_cg,
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

    plane_1 = vehicle(
        fwd_tank=fwd_tank,
        aft_tank=aft_tank,
        empty_cg= vehicle_empty_cg,
        empty_mass = vehicle_empty_mass
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

        

        fwd_RTP_pump.rate_kgs = aft_RTP_pump.rate_kgs = prop_fuel_rate
        turbogen.rate_kgs = turbogen_fuel_rate
        
        current_transfer_rate = transfer_to_fwd.rate_kgs if transfer_to_fwd.is_active else 0.0

        fwd_RTP_pump.step(dt)
        aft_RTP_pump.step(dt)
        turbogen.step(dt)
        transfer_frm_aft.step(dt)
        transfer_to_fwd.step(dt)

        fwd_tank.update_history()
        aft_tank.update_history()
        plane_1.update_history() 

        # control logic
        if (fwd_tank.fuel_mass_kg > transfer_upper_bound * fwd_tank.capacity_kg) or (aft_tank.fuel_mass_kg < transfer_lower_bound * aft_tank.capacity_kg):
            transfer_frm_aft.deactivate()
            transfer_to_fwd.deactivate()
        elif fwd_tank.fuel_mass_kg < aft_tank.fuel_mass_kg * (1-ctl_range/2):
            transfer_frm_aft.activate()
            transfer_to_fwd.activate()
        elif fwd_tank.fuel_mass_kg > aft_tank.fuel_mass_kg * (1+ctl_range/2):
            transfer_frm_aft.deactivate()
            transfer_to_fwd.deactivate()
        elif plane_1.CG >= cg_far_limit:
            transfer_frm_aft.activate()
            transfer_to_fwd.activate()
        
            

        


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

    time_s = mission.time_s

    ############################## PLOTS ####################################################
    fig, axes = plt.subplots(4, 1, sharex=True, num="Tank Fuel Balance", figsize=(11, 14))

    axes[0].plot(time_s, mission.turbogen_fuel_flow_kgs, linewidth=1.6, label="Turbogen demand")
    axes[0].plot(
        time_s,
        mission.propulsor_fuel_flow_kgs,
        linewidth=1.6,
        label="Propulsor demand",
    )
    axes[0].plot(
        time_s,
        [
            mission.turbogen_fuel_flow_kgs[i] + mission.propulsor_fuel_flow_kgs[i]
            for i in range(len(mission))
        ],
        "--",
        linewidth=1.6,
        label="Total demand",
    )
    axes[0].grid(True)
    axes[0].set_ylabel("Fuel flow [kg/s]")
    axes[0].set_title("Fuel Flow Demands vs Time")
    axes[0].legend(loc="best")

    axes[1].plot(time_s, plane_1.CG_history, linewidth=1.6, label="Vehicle CG")
    axes[1].axhline(vehicle_empty_cg, color="red", linestyle=":", linewidth=1.6, label="Empty CG")
    axes[1].axhline(cg_far_limit, color="blue", linestyle=":", linewidth=1.6, label="Aft CG Limit")
    axes[1].axhline(cg_near_limit, color="blue", linestyle=":", linewidth=1.6, label="FWD CG Limit")
    axes[1].grid(True)
    axes[1].set_ylabel("CG [m]")
    axes[1].set_title("Vehicle CG vs Time")
    axes[1].legend(loc="best")

    axes[2].plot(time_s, fwd_tank.mass_history, linewidth=1.6, label="FWD tank")
    axes[2].plot(time_s, aft_tank.mass_history, linewidth=1.6, label="AFT tank")
    axes[2].plot(time_s, total_fuel_kg, "--", linewidth=1.6, label="Total fuel")
    axes[2].axhline(0.0, linestyle=":", label="Zero fuel")
    axes[2].grid(True)
    axes[2].set_ylabel("Fuel [kg]")
    axes[2].set_title("Tank Fuel Levels vs Time")
    axes[2].legend(loc="best")

    axes[3].step(time_s, transfer_rate_kgs, where="post", linewidth=1.6)
    axes[3].grid(True)
    axes[3].set_xlabel("Time [s]")
    axes[3].set_ylabel("AFT-to-FWD transfer [kg/s]")
    axes[3].set_title("Transfer Command vs Time")

    fig.tight_layout()

    total_vehicle_mass_kg = [fuel_mass + vehicle_empty_mass for fuel_mass in total_fuel_kg]
    mass_cg_fig, mass_cg_ax = plt.subplots(1, 1, num="Vehicle Mass vs CG", figsize=(11, 6))
    mass_cg_ax.plot(plane_1.CG_history, total_vehicle_mass_kg, linewidth=1.6, label="Vehicle Mass")
    mass_cg_ax.axvline(vehicle_empty_cg, color="red", linestyle=":", linewidth=1.6, label="Empty CG")
    mass_cg_ax.axvline(cg_far_limit, color="blue", linestyle=":", linewidth=1.6, label="Aft CG Limit")
    mass_cg_ax.axvline(cg_near_limit, color="blue", linestyle=":", linewidth=1.6, label="FWD CG Limit")
    mass_cg_ax.grid(True)
    mass_cg_ax.set_xlabel("CG [m]")
    mass_cg_ax.set_ylabel("Total vehicle mass [kg]")
    mass_cg_ax.set_title("Total Vehicle Mass vs Vehicle CG")
    mass_cg_ax.legend(loc="best")
    mass_cg_fig.tight_layout()

    plt.show()

main()
