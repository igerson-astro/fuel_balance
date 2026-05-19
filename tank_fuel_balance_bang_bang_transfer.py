#!/usr/bin/env python3
"""Tank fuel balance from a FLOPS-style mission CSV."""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path


KG_TO_LB = 2.20462262185
DEFAULT_MISSION_FILE = "flops_mission_with_component_breakdown_reference.csv"


def normalize_name(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def find_column(fieldnames: list[str], aliases: list[str]) -> str:
    normalized_names = {name: normalize_name(name) for name in fieldnames}
    normalized_aliases = [normalize_name(alias) for alias in aliases]

    for alias in normalized_aliases:
        for name, normalized_name in normalized_names.items():
            if normalized_name == alias:
                return name

    for alias in normalized_aliases:
        for name, normalized_name in normalized_names.items():
            if alias in normalized_name:
                return name

    alias_text = ", ".join(aliases)
    names_text = ", ".join(fieldnames)
    raise ValueError(
        f"Could not find required column. Tried aliases: {alias_text}. "
        f"Available columns: {names_text}"
    )


def parse_required_float(row: dict[str, str], column_name: str, row_number: int) -> float:
    raw_value = row.get(column_name, "")
    if raw_value is None or raw_value.strip() == "":
        raise ValueError(f"Row {row_number}: column '{column_name}' is empty.")

    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"Row {row_number}: column '{column_name}' must be numeric, got {raw_value!r}."
        ) from exc

    if not math.isfinite(value):
        raise ValueError(
            f"Row {row_number}: column '{column_name}' must be finite, got {raw_value!r}."
        )

    return value


def load_mission_rows(mission_path: Path) -> tuple[list[dict[str, str]], str, str, str]:
    with mission_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"Mission file has no header row: {mission_path}")

        rows = list(reader)
        if not rows:
            raise ValueError(f"Mission file contains no data rows: {mission_path}")

    time_column = find_column(reader.fieldnames, ["Time [hr]", "Time_hr", "Time hr", "Time"])
    turbogen_column = find_column(
        reader.fieldnames,
        ["Turbogen Fuel Flow", "TurbogenFuelFlow", "TG Fuel Flow"],
    )
    propulsor_column = find_column(
        reader.fieldnames,
        ["Propulsor Fuel Flow", "PropulsorFuelFlow", "Prop Fuel Flow"],
    )
    return rows, time_column, turbogen_column, propulsor_column


def compute_results(
    rows: list[dict[str, str]],
    time_column: str,
    turbogen_column: str,
    propulsor_column: str,
    *,
    fwd_initial_kg: float,
    aft_initial_kg: float,
    fwd_capacity_lb: float | None,
    aft_capacity_lb: float | None,
    transfer_min_lbhr: float,
    transfer_max_lbhr: float,
    clamp_transfer_to_available_aft_fuel: bool,
    integration_method: str,
) -> dict[str, list[float]]:
    n = len(rows)

    time_hr = [0.0] * n
    tg_fuel_flow_lbhr = [0.0] * n
    prop_fuel_flow_lbhr = [0.0] * n

    for index, row in enumerate(rows, start=2):
        list_index = index - 2
        time_hr[list_index] = parse_required_float(row, time_column, index)
        tg_fuel_flow_lbhr[list_index] = parse_required_float(row, turbogen_column, index)
        prop_fuel_flow_lbhr[list_index] = parse_required_float(row, propulsor_column, index)

    for index in range(1, n):
        if time_hr[index] < time_hr[index - 1]:
            raise ValueError("Time column must be monotonically increasing.")

    integration_method = integration_method.lower()
    if integration_method not in {"trapezoid", "previous"}:
        raise ValueError(f"Unknown integration_method: {integration_method}")

    fwd_initial_lb = fwd_initial_kg * KG_TO_LB
    aft_initial_lb = aft_initial_kg * KG_TO_LB
    fwd_capacity_lb = fwd_initial_lb if fwd_capacity_lb is None else fwd_capacity_lb
    aft_capacity_lb = aft_initial_lb if aft_capacity_lb is None else aft_capacity_lb

    if fwd_capacity_lb <= 0 or aft_capacity_lb <= 0:
        raise ValueError("Tank capacities must be positive.")

    fwd_fuel_lb = [math.nan] * n
    aft_fuel_lb = [math.nan] * n
    total_fuel_lb = [math.nan] * n
    fwd_fill_frac = [math.nan] * n
    aft_fill_frac = [math.nan] * n

    transfer_lbhr = [0.0] * n
    transfer_fuel_lb = [0.0] * n
    fwd_direct_demand_lbhr = [0.0] * n
    aft_direct_demand_lbhr = [0.0] * n
    fwd_net_burn_lbhr = [0.0] * n
    aft_net_burn_lbhr = [0.0] * n

    fwd_fuel_lb[0] = fwd_initial_lb
    aft_fuel_lb[0] = aft_initial_lb
    total_fuel_lb[0] = fwd_fuel_lb[0] + aft_fuel_lb[0]
    fwd_fill_frac[0] = fwd_fuel_lb[0] / fwd_capacity_lb
    aft_fill_frac[0] = aft_fuel_lb[0] / aft_capacity_lb

    for k in range(1, n):
        dt_hr = time_hr[k] - time_hr[k - 1]

        if dt_hr == 0:
            fwd_fuel_lb[k] = fwd_fuel_lb[k - 1]
            aft_fuel_lb[k] = aft_fuel_lb[k - 1]
            total_fuel_lb[k] = total_fuel_lb[k - 1]
            fwd_fill_frac[k] = fwd_fuel_lb[k] / fwd_capacity_lb
            aft_fill_frac[k] = aft_fuel_lb[k] / aft_capacity_lb
            continue

        if integration_method == "trapezoid":
            tg_rate = 0.5 * (tg_fuel_flow_lbhr[k - 1] + tg_fuel_flow_lbhr[k])
            prop_rate = 0.5 * (prop_fuel_flow_lbhr[k - 1] + prop_fuel_flow_lbhr[k])
        else:
            tg_rate = tg_fuel_flow_lbhr[k - 1]
            prop_rate = prop_fuel_flow_lbhr[k - 1]

        fwd_direct_rate = tg_rate + 0.5 * prop_rate
        aft_direct_rate = 0.5 * prop_rate

        fwd_direct_demand_lbhr[k] = fwd_direct_rate
        aft_direct_demand_lbhr[k] = aft_direct_rate

        fwd_pre_transfer = fwd_fuel_lb[k - 1] - fwd_direct_rate * dt_hr
        aft_pre_transfer = aft_fuel_lb[k - 1] - aft_direct_rate * dt_hr

        transfer_needed_lb = (
            fwd_capacity_lb * aft_pre_transfer - aft_capacity_lb * fwd_pre_transfer
        ) / (fwd_capacity_lb + aft_capacity_lb)
        transfer_needed_lbhr = transfer_needed_lb / dt_hr

        if transfer_needed_lbhr > 0:
            transfer_command_lbhr = min(
                max(transfer_needed_lbhr, transfer_min_lbhr),
                transfer_max_lbhr,
            )
        else:
            transfer_command_lbhr = 0.0

        if clamp_transfer_to_available_aft_fuel:
            transfer_command_lbhr = min(transfer_command_lbhr, aft_pre_transfer / dt_hr)

        transfer_lbhr[k] = transfer_command_lbhr
        transfer_fuel_lb[k] = transfer_command_lbhr * dt_hr

        fwd_fuel_lb[k] = fwd_pre_transfer + transfer_fuel_lb[k]
        aft_fuel_lb[k] = aft_pre_transfer - transfer_fuel_lb[k]

        fwd_net_burn_lbhr[k] = fwd_direct_rate - transfer_command_lbhr
        aft_net_burn_lbhr[k] = aft_direct_rate + transfer_command_lbhr

        total_fuel_lb[k] = fwd_fuel_lb[k] + aft_fuel_lb[k]
        fwd_fill_frac[k] = fwd_fuel_lb[k] / fwd_capacity_lb
        aft_fill_frac[k] = aft_fuel_lb[k] / aft_capacity_lb

    return {
        "time_hr": time_hr,
        "tg_fuel_flow_lbhr": tg_fuel_flow_lbhr,
        "prop_fuel_flow_lbhr": prop_fuel_flow_lbhr,
        "fwd_direct_demand_lbhr": fwd_direct_demand_lbhr,
        "aft_direct_demand_lbhr": aft_direct_demand_lbhr,
        "transfer_lbhr": transfer_lbhr,
        "transfer_fuel_lb": transfer_fuel_lb,
        "fwd_net_burn_lbhr": fwd_net_burn_lbhr,
        "aft_net_burn_lbhr": aft_net_burn_lbhr,
        "fwd_fuel_lb": fwd_fuel_lb,
        "aft_fuel_lb": aft_fuel_lb,
        "total_fuel_lb": total_fuel_lb,
        "fwd_fill_frac": fwd_fill_frac,
        "aft_fill_frac": aft_fill_frac,
        "fill_percent_difference": [
            100.0 * (fwd_fill_frac[i] - aft_fill_frac[i]) for i in range(n)
        ],
        "fwd_initial_lb": [fwd_initial_lb],
        "aft_initial_lb": [aft_initial_lb],
    }


def write_results_csv(
    mission_path: Path,
    source_rows: list[dict[str, str]],
    time_column: str,
    results: dict[str, list[float]],
) -> Path:
    output_path = mission_path.with_name(f"{mission_path.stem}_tank_balance_results.csv")

    fieldnames = list(source_rows[0].keys()) + [
        "TurbogenFuelFlow_lbhr",
        "PropulsorFuelFlow_lbhr",
        "FwdDirectDemand_lbhr",
        "AftDirectDemand_lbhr",
        "AftToFwdTransfer_lbhr",
        "AftToFwdTransferFuel_lb",
        "FwdNetBurn_lbhr",
        "AftNetBurn_lbhr",
        "FwdFuel_lb",
        "AftFuel_lb",
        "TotalFuel_lb",
        "FwdFillPercent",
        "AftFillPercent",
        "FillPercentDifference",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()

        for i, row in enumerate(source_rows):
            out_row = dict(row)
            out_row[time_column] = results["time_hr"][i]
            out_row["TurbogenFuelFlow_lbhr"] = results["tg_fuel_flow_lbhr"][i]
            out_row["PropulsorFuelFlow_lbhr"] = results["prop_fuel_flow_lbhr"][i]
            out_row["FwdDirectDemand_lbhr"] = results["fwd_direct_demand_lbhr"][i]
            out_row["AftDirectDemand_lbhr"] = results["aft_direct_demand_lbhr"][i]
            out_row["AftToFwdTransfer_lbhr"] = results["transfer_lbhr"][i]
            out_row["AftToFwdTransferFuel_lb"] = results["transfer_fuel_lb"][i]
            out_row["FwdNetBurn_lbhr"] = results["fwd_net_burn_lbhr"][i]
            out_row["AftNetBurn_lbhr"] = results["aft_net_burn_lbhr"][i]
            out_row["FwdFuel_lb"] = results["fwd_fuel_lb"][i]
            out_row["AftFuel_lb"] = results["aft_fuel_lb"][i]
            out_row["TotalFuel_lb"] = results["total_fuel_lb"][i]
            out_row["FwdFillPercent"] = 100.0 * results["fwd_fill_frac"][i]
            out_row["AftFillPercent"] = 100.0 * results["aft_fill_frac"][i]
            out_row["FillPercentDifference"] = results["fill_percent_difference"][i]
            writer.writerow(out_row)

    return output_path


def maybe_plot(results: dict[str, list[float]]) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("Plotting skipped: matplotlib is not installed.", file=sys.stderr)
        return

    time_hr = results["time_hr"]
    fwd_fuel_lb = results["fwd_fuel_lb"]
    aft_fuel_lb = results["aft_fuel_lb"]
    total_fuel_lb = results["total_fuel_lb"]
    fwd_fill_frac = results["fwd_fill_frac"]
    aft_fill_frac = results["aft_fill_frac"]
    tg_fuel_flow_lbhr = results["tg_fuel_flow_lbhr"]
    prop_fuel_flow_lbhr = results["prop_fuel_flow_lbhr"]
    transfer_lbhr = results["transfer_lbhr"]
    total_demand_lbhr = [
        tg_fuel_flow_lbhr[i] + prop_fuel_flow_lbhr[i] for i in range(len(time_hr))
    ]
    fwd_fill_percent = [100.0 * value for value in fwd_fill_frac]
    aft_fill_percent = [100.0 * value for value in aft_fill_frac]

    fig, axes = plt.subplots(4, 1, sharex=True, num="Tank Fuel Balance", figsize=(11, 14))

    axes[0].plot(time_hr, tg_fuel_flow_lbhr, linewidth=1.6, label="Turbogen demand")
    axes[0].plot(time_hr, prop_fuel_flow_lbhr, linewidth=1.6, label="Propulsor demand")
    axes[0].plot(time_hr, total_demand_lbhr, "--", linewidth=1.6, label="Total demand")
    axes[0].grid(True)
    axes[0].set_ylabel("Fuel flow [lb/hr]")
    axes[0].set_title("Fuel Flow Demands vs Time")
    axes[0].legend(loc="best")

    axes[1].plot(time_hr, fwd_fuel_lb, linewidth=1.6, label="FWD tank")
    axes[1].plot(time_hr, aft_fuel_lb, linewidth=1.6, label="AFT tank")
    axes[1].plot(time_hr, total_fuel_lb, "--", linewidth=1.6, label="Total fuel")
    axes[1].axhline(0.0, linestyle=":", label="Zero fuel")
    axes[1].grid(True)
    axes[1].set_ylabel("Fuel [lb]")
    axes[1].set_title("Tank Fuel Levels vs Time")
    axes[1].legend(loc="best")

    axes[2].plot(time_hr, fwd_fill_percent, linewidth=1.6, label="FWD fill %")
    axes[2].plot(time_hr, aft_fill_percent, linewidth=1.6, label="AFT fill %")
    axes[2].axhline(0.0, linestyle=":", label="Empty")
    axes[2].grid(True)
    axes[2].set_ylabel("Fill [% of specified tank capacity]")
    axes[2].set_title("Tank Fill Percentages vs Time")
    axes[2].legend(loc="best")

    axes[3].plot(time_hr, transfer_lbhr, linewidth=1.6)
    axes[3].grid(True)
    axes[3].set_xlabel("Time [hr]")
    axes[3].set_ylabel("AFT-to-FWD transfer [lb/hr]")
    axes[3].set_title("Transfer Command vs Time")

    fig.tight_layout()

    plt.show()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute tank fuel balance from a FLOPS-style mission CSV."
    )
    parser.add_argument(
        "mission_file",
        nargs="?",
        default=DEFAULT_MISSION_FILE,
        help=f"Mission CSV path. Default: {DEFAULT_MISSION_FILE}",
    )
    parser.add_argument("--fwd-initial-kg", type=float, default=1604.0)
    parser.add_argument("--aft-initial-kg", type=float, default=1053.0)
    parser.add_argument("--fwd-capacity-lb", type=float, default=None)
    parser.add_argument("--aft-capacity-lb", type=float, default=None)
    parser.add_argument("--transfer-min-lbhr", type=float, default=100.0)
    parser.add_argument("--transfer-max-lbhr", type=float, default=101.0)
    parser.add_argument(
        "--clamp-transfer-to-available-aft-fuel",
        action="store_true",
        help="Limit transfer during each timestep by current AFT fuel.",
    )
    parser.add_argument(
        "--integration-method",
        choices=["trapezoid", "previous"],
        default="trapezoid",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="Skip interactive plots.",
    )
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
    mission_path = Path(args.mission_file).expanduser().resolve()

    if not mission_path.is_file():
        print(f"Mission file not found: {mission_path}", file=sys.stderr)
        return 1

    rows, time_column, turbogen_column, propulsor_column = load_mission_rows(mission_path)
    results = compute_results(
        rows,
        time_column,
        turbogen_column,
        propulsor_column,
        fwd_initial_kg=args.fwd_initial_kg,
        aft_initial_kg=args.aft_initial_kg,
        fwd_capacity_lb=args.fwd_capacity_lb,
        aft_capacity_lb=args.aft_capacity_lb,
        transfer_min_lbhr=args.transfer_min_lbhr,
        transfer_max_lbhr=args.transfer_max_lbhr,
        clamp_transfer_to_available_aft_fuel=args.clamp_transfer_to_available_aft_fuel,
        integration_method=args.integration_method,
    )
    results_path = write_results_csv(mission_path, rows, time_column, results)

    print("\nFuel balance complete.")
    print(f"Input file:   {mission_path}")
    print(f"Results file: {results_path}")
    print(
        f"Initial FWD:  {results['fwd_initial_lb'][0]:.1f} lb, "
        f"Initial AFT: {results['aft_initial_lb'][0]:.1f} lb"
    )
    print(
        f"Final FWD:    {results['fwd_fuel_lb'][-1]:.1f} lb, "
        f"Final AFT:   {results['aft_fuel_lb'][-1]:.1f} lb"
    )
    print(
        f"Final FWD %:  {100.0 * results['fwd_fill_frac'][-1]:.2f} %, "
        f"Final AFT %: {100.0 * results['aft_fill_frac'][-1]:.2f} %"
    )
    print(
        f"Minimum FWD:  {min(results['fwd_fuel_lb']):.1f} lb, "
        f"Minimum AFT: {min(results['aft_fuel_lb']):.1f} lb"
    )
    print(f"Max transfer: {max(results['transfer_lbhr']):.1f} lb/hr")
    print(
        f"Max Fill % Diff:  "
        f"{max(abs(value) for value in results['fill_percent_difference']):.2f} %\n"
    )

    if not args.no_plots:
        maybe_plot(results)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
