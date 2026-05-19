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

    @property
    def fill_fraction(self) -> float:
        return self.fuel_mass_kg / self.capacity_kg

    @property
    def percent_full(self) -> float:
        return 100.0 * self.fill_fraction

    def add_fuel(self, mass_add_kg: float) -> None:
        self.fuel_mass_kg += mass_add_kg

    def mass_subtract(self, mass_remove_kg: float) -> None:
        self.fuel_mass_kg -= mass_remove_kg


def main():
    mission = mission_data(
        r"C:\Users\IlanGerson\Documents\Scripting\fuel_balance\flops_mission_with_component_breakdown_maxendurance.csv"
    )
    fwd_mass_init = LBHR_TO_KGS*1603.89

    aft_mass_init = LBHR_TO_KGS*1053.39

    aft_tank = tank(
        fuel_mass_kg=aft_mass_init
    )
    fwd_tank = tank(
        fuel_mass_kg = fwd_mass_init
    )

    print(" ")

main()
