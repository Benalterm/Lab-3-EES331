# EES 3315 Lab 3 Part A

from pathlib import Path
import pandas as pd

# Constants
hour_year = 8760
day_year = 365
month_year = 12
grid_limit_MW = 100
time_step_hours = 1
shortfall_cost_per_MWh = 2000

# Load the original CSV
project_folder = Path(__file__).resolve().parent
csv_path = project_folder / "Lab_Data.csv"
output_path = project_folder / "part_a.csv"

data = pd.read_csv(csv_path)

# Remove completely empty columns
data = data.dropna(axis=1, how="all")

if len(data) != hour_year:
    raise ValueError(f"Expected {hour_year} rows, found {len(data)}.")

# Start with the batch block off in every hour
data["batch_on"] = 0

# Find each day's cheapest consecutive 8-hour window
for (month, day), daily_rows in data.groupby(["month", "day"], sort=True):
    daily_rows = daily_rows.sort_values("hour_of_day")
    lowest_average = None
    lowest_start_hour = None

    for start_hour in range(17):
        window = daily_rows.loc[
            daily_rows["hour_of_day"].between(start_hour, start_hour + 7),
            "DA_LMP_$/MWh",
        ]

        # Only use complete windows with eight valid prices
        if len(window) != 8 or window.isna().any():
            continue

        window_average = window.mean()

        # Using < keeps the earlier window if prices tie
        if lowest_average is None or window_average < lowest_average:
            lowest_average = window_average
            lowest_start_hour = start_hour

    if lowest_start_hour is None:
        raise ValueError(
            f"{int(month)}/{int(day)}: no complete 8-hour windows"
        )

    # Mark the selected eight hours
    batch_indices = daily_rows.index[
        daily_rows["hour_of_day"].between(
            lowest_start_hour, lowest_start_hour + 7
        )
    ]
    data.loc[batch_indices, "batch_on"] = 1

# Calculate PUE separately for every hour
data["PUE"] = 0.0

for h in range(hour_year):
    temperature = data.at[h, "Temp_F"]

    pue = min(
        1.10 + 0.005 * max(temperature - 55, 0),
        1.30,
    )

    data.at[h, "PUE"] = pue

# Compute power and facility demand, including cooling
data["compute_MW"] = 80 + 20 * data["batch_on"]
data["demand_MW"] = data["compute_MW"] * data["PUE"]

# --------------------------------------------------
# Part A: grid only, no turbines or battery
# --------------------------------------------------
for h in range(hour_year):
    demand = data.at[h, "demand_MW"]
    pue = data.at[h, "PUE"]

    # Facility power that cannot be supplied by the grid
    total_shed = max(demand - grid_limit_MW, 0)

    # Shed batch first, including its cooling
    batch_demand = 20 * data.at[h, "batch_on"] * pue
    batch_shed = min(total_shed, batch_demand)
    base_shed = total_shed - batch_shed

    data.at[h, "shed_facility_MW"] = total_shed
    data.at[h, "shed_batch_MW"] = batch_shed
    data.at[h, "shed_base_MW"] = base_shed
    data.at[h, "shed_compute_MW"] = total_shed / pue
    data.at[h, "import_MW"] = min(demand, grid_limit_MW)

# Annual costs: MW × hours × $/MWh
grid_cost = (
    data["import_MW"] * time_step_hours * data["DA_LMP_$/MWh"]
).sum()

shortfall_cost = (
    data["shed_compute_MW"].sum()
    * time_step_hours
    * shortfall_cost_per_MWh
)

# Count hours with shedding, ignoring tiny numerical noise
tolerance = 1e-9
hours_shed = (data["shed_facility_MW"] > tolerance).sum()
hours_base_shed = (data["shed_base_MW"] > tolerance).sum()

# Locate the first occurrence of maximum demand
peak = data.loc[data["demand_MW"].idxmax()]

# Annual energy totals: MW × hours / 1000 = GWh
to_GWh = time_step_hours / 1000

print("\nSTEP 1: GRID ONLY")
print(f"Facility demand: {data['demand_MW'].sum() * to_GWh:,.3f} GWh")
print(f"Compute demand: {data['compute_MW'].sum() * to_GWh:,.3f} GWh")
print(f"Grid imports: {data['import_MW'].sum() * to_GWh:,.3f} GWh")

print(f"\nFacility shed: {data['shed_facility_MW'].sum() * to_GWh:,.3f} GWh")
print(f"Batch shed: {data['shed_batch_MW'].sum() * to_GWh:,.3f} GWh")
print(f"Base shed: {data['shed_base_MW'].sum() * to_GWh:,.3f} GWh")
print(f"Compute shed: {data['shed_compute_MW'].sum() * to_GWh:,.3f} GWh")

print(f"\nHours with any shed: {hours_shed:,}")
print(f"Hours with base shed: {hours_base_shed:,}")

print(f"\nGrid purchase cost: ${grid_cost:,.2f}")
print(f"Compute shortfall cost: ${shortfall_cost:,.2f}")
print(f"Mean PUE: {data['PUE'].mean():.4f}")

print(f"\nMaximum demand: {peak['demand_MW']:.2f} MW")
print(f"Date: {int(peak['month'])}/{int(peak['day'])}")
print(f"Hour: {int(peak['hour_of_day']):02d}:00")

# Save hourly results to a separate CSV
data.to_csv(output_path, index=False, float_format="%.6f")
print(f"\nResults saved to: {output_path}")

# --------------------------------------------------
# Part B: grid and gas turbines, no battery
# --------------------------------------------------
# Gas turbine settings
turbine_unit_MW = 35
turbine_count = 4
turbine_rating_MW = turbine_unit_MW * turbine_count

full_efficiency = 0.38
variable_OM_per_MWh = 6
MMBtu_per_MWh = 3.412
CO2_tonnes_per_MMBtu = 0.0531

# Keep Step 1 results separate from the gas turbine results
gas_data = data.copy()
gas_data["turbine_regime"] = ""

# Step 2: use the dispatch rules with no battery
for h in range(hour_year):
    demand = gas_data.at[h, "demand_MW"]
    pue = gas_data.at[h, "PUE"]
    gas_price = gas_data.at[h, "Gas_$/MMBtu"]
    grid_price = gas_data.at[h, "DA_LMP_$/MWh"]

    # Equation (8) at full output determines the dispatch regime
    full_cost = MMBtu_per_MWh * gas_price / full_efficiency + variable_OM_per_MWh

    if grid_price > full_cost:
        regime = "economic"
        generation = min(turbine_rating_MW, demand + grid_limit_MW)
        grid_import = min(max(demand - generation, 0), grid_limit_MW)
        grid_export = min(max(generation - demand, 0), grid_limit_MW)
    else:
        regime = "necessity"
        grid_import = min(demand, grid_limit_MW)
        generation = min(max(demand - grid_import, 0), turbine_rating_MW)
        grid_export = 0

    # Section 5.1: efficiency depends on the fraction of fleet capacity used
    load_fraction = generation / turbine_rating_MW

    if generation > 0:
        if load_fraction >= 0.5:
            efficiency = full_efficiency
        else:
            efficiency = full_efficiency - 0.24 * (0.5 - load_fraction)

        # Section 5.2: calculate fuel first, then fuel cost and emissions
        fuel = MMBtu_per_MWh * generation * time_step_hours / efficiency
        generation_cost = MMBtu_per_MWh * gas_price / efficiency + variable_OM_per_MWh
    else:
        # Efficiency and cost per generated MWh are undefined when off
        efficiency = float("nan")
        generation_cost = float("nan")
        fuel = 0

    # Shed batch first, including cooling, if supply cannot meet demand
    total_shed = max(demand - generation - grid_import, 0)
    batch_demand = 20 * gas_data.at[h, "batch_on"] * pue
    batch_shed = min(total_shed, batch_demand)

    gas_data.at[h, "turbine_regime"] = regime
    gas_data.at[h, "generation_MW"] = generation
    gas_data.at[h, "load_fraction"] = load_fraction
    gas_data.at[h, "efficiency"] = efficiency
    gas_data.at[h, "turbine_full_cost_per_MWh"] = full_cost
    gas_data.at[h, "turbine_cost_per_MWh"] = generation_cost
    gas_data.at[h, "fuel_MMBtu"] = fuel
    gas_data.at[h, "fuel_cost"] = gas_price * fuel
    gas_data.at[h, "variable_OM_cost"] = generation * time_step_hours * variable_OM_per_MWh
    gas_data.at[h, "CO2_tonnes"] = CO2_tonnes_per_MMBtu * fuel
    gas_data.at[h, "import_MW"] = grid_import
    gas_data.at[h, "export_MW"] = grid_export
    gas_data.at[h, "curtailed_generation_MW"] = max(generation + grid_import - (demand - total_shed) - grid_export, 0)
    gas_data.at[h, "shed_facility_MW"] = total_shed
    gas_data.at[h, "shed_batch_MW"] = batch_shed
    gas_data.at[h, "shed_base_MW"] = total_shed - batch_shed
    gas_data.at[h, "shed_compute_MW"] = total_shed / pue

# Annual totals for the gas turbine case
gas_grid_cost = (
    gas_data["import_MW"] * time_step_hours * gas_data["DA_LMP_$/MWh"]
).sum()
export_revenue = (
    gas_data["export_MW"] * time_step_hours * gas_data["DA_LMP_$/MWh"]
).sum()
gas_shortfall_cost = (
    gas_data["shed_compute_MW"].sum() * time_step_hours * shortfall_cost_per_MWh
)
capacity_factor = gas_data["generation_MW"].sum() / (turbine_rating_MW * hour_year)
weighted_efficiency = (
    (gas_data["generation_MW"] * gas_data["efficiency"]).sum()
    / gas_data["generation_MW"].sum()
)

# Assume the fleet was off before the first hour
previous_generation = gas_data["generation_MW"].shift(1, fill_value=0)
starts = ((gas_data["generation_MW"] > tolerance) & (previous_generation <= tolerance)).sum()
start_cost = starts * 50 * turbine_rating_MW

print("\nSTEP 2: GAS TURBINES, NO BATTERY")
print(f"Facility demand: {gas_data['demand_MW'].sum() * to_GWh:,.3f} GWh")
print(f"Compute demand: {gas_data['compute_MW'].sum() * to_GWh:,.3f} GWh")
print(f"Grid imports: {gas_data['import_MW'].sum() * to_GWh:,.3f} GWh")
print(f"Grid exports: {gas_data['export_MW'].sum() * to_GWh:,.3f} GWh")
print(f"On-site generation: {gas_data['generation_MW'].sum() * to_GWh:,.3f} GWh")

print(f"\nFacility shed: {gas_data['shed_facility_MW'].sum() * to_GWh:,.3f} GWh")
print(f"Batch shed: {gas_data['shed_batch_MW'].sum() * to_GWh:,.3f} GWh")
print(f"Base shed: {gas_data['shed_base_MW'].sum() * to_GWh:,.3f} GWh")
print(f"Compute shed: {gas_data['shed_compute_MW'].sum() * to_GWh:,.3f} GWh")
print(f"Hours with any shed: {(gas_data['shed_facility_MW'] > tolerance).sum():,}")
print(f"Hours with base shed: {(gas_data['shed_base_MW'] > tolerance).sum():,}")

print(f"\nFuel consumed: {gas_data['fuel_MMBtu'].sum():,.2f} MMBtu")
print(f"Fuel cost: ${gas_data['fuel_cost'].sum():,.2f}")
print(f"Variable O&M: ${gas_data['variable_OM_cost'].sum():,.2f}")
print(f"CO2 emissions: {gas_data['CO2_tonnes'].sum():,.2f} tonnes")
print(f"Grid purchase cost: ${gas_grid_cost:,.2f}")
print(f"Export revenue: ${export_revenue:,.2f}")
print(f"Compute shortfall cost: ${gas_shortfall_cost:,.2f}")

print(f"\nEconomic hours: {(gas_data['turbine_regime'] == 'economic').sum():,}")
print(f"Necessity hours: {(gas_data['turbine_regime'] == 'necessity').sum():,}")
print(f"Capacity factor: {capacity_factor:.2%}")
print(f"Generation-weighted efficiency: {weighted_efficiency:.2%}")
print(f"Turbine starts: {starts:,}")
print(f"Start cost (discussion only): ${start_cost:,.2f}")
print(f"Mean PUE: {gas_data['PUE'].mean():.4f}")
print(f"Maximum demand: {peak['demand_MW']:.2f} MW")
print(f"Date: {int(peak['month'])}/{int(peak['day'])}")
print(f"Hour: {int(peak['hour_of_day']):02d}:00")

# Save gas turbine results separately so the grid-only results remain available
gas_output_path = project_folder / "part_b.csv"
gas_data.to_csv(gas_output_path, index=False, float_format="%.6f")
print(f"\nGas turbine results saved to: {gas_output_path}")

# --------------------------------------------------
# Part C: grid, gas turbines, and battery
# --------------------------------------------------
# Section 6: battery settings
battery_capacity_MWh = 100
battery_rating_MW = 25
battery_round_trip_efficiency = 0.88
battery_charge_efficiency = battery_round_trip_efficiency ** 0.5
battery_discharge_efficiency = battery_round_trip_efficiency ** 0.5
battery_min_energy_MWh = 0.10 * battery_capacity_MWh
battery_max_energy_MWh = 0.90 * battery_capacity_MWh

# Initialize ONCE before the hourly loop, never at the start of each day
battery_initial_energy_MWh = 0.50 * battery_capacity_MWh
battery_energy_MWh = battery_initial_energy_MWh
battery_SOC = battery_energy_MWh / battery_capacity_MWh

# Start a separate case using the same demand and batch schedule
battery_data = data.copy()
battery_data["turbine_regime"] = ""

# Run one hour at a time; stored energy carries into the next hour and day
for h in range(hour_year):
    demand = battery_data.at[h, "demand_MW"]
    pue = battery_data.at[h, "PUE"]
    gas_price = battery_data.at[h, "Gas_$/MMBtu"]
    grid_price = battery_data.at[h, "DA_LMP_$/MWh"]

    # Record stored energy BEFORE this hour's charging or discharging
    energy_start_MWh = battery_energy_MWh

    # Battery controls: available power based on stored energy and the 25 MW rating
    charge_limit_MW = min(
        battery_rating_MW,
        (battery_max_energy_MWh - battery_energy_MWh)
        / (battery_charge_efficiency * time_step_hours),
    )
    discharge_limit_MW = min(
        battery_rating_MW,
        (battery_energy_MWh - battery_min_energy_MWh)
        * battery_discharge_efficiency / time_step_hours,
    )

    # Reset power each hour, but do NOT reset stored energy
    charge_MW = 0
    discharge_MW = 0

    # Equation (8) at full output determines the dispatch regime
    full_cost = MMBtu_per_MWh * gas_price / full_efficiency + variable_OM_per_MWh

    # ECONOMIC: generate -> serve demand -> charge -> export
    if grid_price > full_cost:
        regime = "economic"
        generation = min(
            turbine_rating_MW,
            demand + charge_limit_MW + grid_limit_MW,
        )
        surplus_MW = max(generation - demand, 0)
        charge_MW = min(charge_limit_MW, surplus_MW)
        grid_export = min(surplus_MW - charge_MW, grid_limit_MW)
        grid_import = min(max(demand - generation, 0), grid_limit_MW)

    # NECESSITY: import -> discharge -> generate for the remaining deficit
    else:
        regime = "necessity"
        grid_import = min(demand, grid_limit_MW)
        deficit_MW = max(demand - grid_import, 0)
        discharge_MW = min(discharge_limit_MW, deficit_MW)
        generation = min(deficit_MW - discharge_MW, turbine_rating_MW)
        grid_export = 0

    # Equation (9): charging stores less energy than enters the terminals;
    # discharging removes more stored energy than reaches the facility.
    battery_energy_MWh = energy_start_MWh + time_step_hours * (
        battery_charge_efficiency * charge_MW
        - discharge_MW / battery_discharge_efficiency
    )

    if not (
        battery_min_energy_MWh - tolerance
        <= battery_energy_MWh
        <= battery_max_energy_MWh + tolerance
    ):
        raise ValueError(f"Battery energy outside its limits in hour {h + 1}.")

    # Correct only tiny rounding errors at the energy limits
    battery_energy_MWh = min(
        max(battery_energy_MWh, battery_min_energy_MWh),
        battery_max_energy_MWh,
    )
    battery_SOC = battery_energy_MWh / battery_capacity_MWh
    battery_loss_MWh = time_step_hours * (
        (1 - battery_charge_efficiency) * charge_MW
        + (1 / battery_discharge_efficiency - 1) * discharge_MW
    )

    # Section 5.1: efficiency depends on the fraction of fleet capacity used
    load_fraction = generation / turbine_rating_MW

    if generation > 0:
        if load_fraction >= 0.5:
            efficiency = full_efficiency
        else:
            efficiency = full_efficiency - 0.24 * (0.5 - load_fraction)

        # Section 5.2: calculate fuel first, then fuel cost and emissions
        fuel = MMBtu_per_MWh * generation * time_step_hours / efficiency
        generation_cost = MMBtu_per_MWh * gas_price / efficiency + variable_OM_per_MWh
    else:
        # Efficiency and cost per generated MWh are undefined when off
        efficiency = float("nan")
        generation_cost = float("nan")
        fuel = 0

    # Shed batch first, including cooling, if supply cannot meet demand
    total_shed = max(demand - generation - grid_import - discharge_MW, 0)
    batch_demand = 20 * battery_data.at[h, "batch_on"] * pue
    batch_shed = min(total_shed, batch_demand)

    battery_data.at[h, "turbine_regime"] = regime
    battery_data.at[h, "generation_MW"] = generation
    battery_data.at[h, "load_fraction"] = load_fraction
    battery_data.at[h, "efficiency"] = efficiency
    battery_data.at[h, "turbine_full_cost_per_MWh"] = full_cost
    battery_data.at[h, "turbine_cost_per_MWh"] = generation_cost
    battery_data.at[h, "fuel_MMBtu"] = fuel
    battery_data.at[h, "fuel_cost"] = gas_price * fuel
    battery_data.at[h, "variable_OM_cost"] = generation * time_step_hours * variable_OM_per_MWh
    battery_data.at[h, "CO2_tonnes"] = CO2_tonnes_per_MMBtu * fuel
    battery_data.at[h, "import_MW"] = grid_import
    battery_data.at[h, "export_MW"] = grid_export
    battery_data.at[h, "curtailed_generation_MW"] = max(generation + grid_import + discharge_MW - (demand - total_shed) - charge_MW - grid_export, 0)
    battery_data.at[h, "shed_facility_MW"] = total_shed
    battery_data.at[h, "shed_batch_MW"] = batch_shed
    battery_data.at[h, "shed_base_MW"] = total_shed - batch_shed
    battery_data.at[h, "shed_compute_MW"] = total_shed / pue

    battery_data.at[h, "battery_charge_MW"] = charge_MW
    battery_data.at[h, "battery_discharge_MW"] = discharge_MW
    battery_data.at[h, "battery_energy_start_MWh"] = energy_start_MWh
    battery_data.at[h, "battery_energy_end_MWh"] = battery_energy_MWh
    battery_data.at[h, "battery_SOC"] = battery_SOC
    battery_data.at[h, "battery_loss_MWh"] = battery_loss_MWh

# Annual totals for the gas turbine and battery case
gas_grid_cost = (
    battery_data["import_MW"] * time_step_hours * battery_data["DA_LMP_$/MWh"]
).sum()
export_revenue = (
    battery_data["export_MW"] * time_step_hours * battery_data["DA_LMP_$/MWh"]
).sum()
gas_shortfall_cost = (
    battery_data["shed_compute_MW"].sum() * time_step_hours * shortfall_cost_per_MWh
)
capacity_factor = battery_data["generation_MW"].sum() / (turbine_rating_MW * hour_year)
weighted_efficiency = (
    (battery_data["generation_MW"] * battery_data["efficiency"]).sum()
    / battery_data["generation_MW"].sum()
)

# Assume the fleet was off before the first hour
previous_generation = battery_data["generation_MW"].shift(1, fill_value=0)
starts = ((battery_data["generation_MW"] > tolerance) & (previous_generation <= tolerance)).sum()
start_cost = starts * 50 * turbine_rating_MW

print("\nSTEP 3: GAS TURBINES AND BATTERY")
print(f"Facility demand: {battery_data['demand_MW'].sum() * to_GWh:,.3f} GWh")
print(f"Compute demand: {battery_data['compute_MW'].sum() * to_GWh:,.3f} GWh")
print(f"Grid imports: {battery_data['import_MW'].sum() * to_GWh:,.3f} GWh")
print(f"Grid exports: {battery_data['export_MW'].sum() * to_GWh:,.3f} GWh")
print(f"On-site generation: {battery_data['generation_MW'].sum() * to_GWh:,.3f} GWh")

print(f"\nFacility shed: {battery_data['shed_facility_MW'].sum() * to_GWh:,.3f} GWh")
print(f"Batch shed: {battery_data['shed_batch_MW'].sum() * to_GWh:,.3f} GWh")
print(f"Base shed: {battery_data['shed_base_MW'].sum() * to_GWh:,.3f} GWh")
print(f"Compute shed: {battery_data['shed_compute_MW'].sum() * to_GWh:,.3f} GWh")
print(f"Hours with any shed: {(battery_data['shed_facility_MW'] > tolerance).sum():,}")
print(f"Hours with base shed: {(battery_data['shed_base_MW'] > tolerance).sum():,}")

print(f"\nFuel consumed: {battery_data['fuel_MMBtu'].sum():,.2f} MMBtu")
print(f"Fuel cost: ${battery_data['fuel_cost'].sum():,.2f}")
print(f"Variable O&M: ${battery_data['variable_OM_cost'].sum():,.2f}")
print(f"CO2 emissions: {battery_data['CO2_tonnes'].sum():,.2f} tonnes")
print(f"Grid purchase cost: ${gas_grid_cost:,.2f}")
print(f"Export revenue: ${export_revenue:,.2f}")
print(f"Compute shortfall cost: ${gas_shortfall_cost:,.2f}")

print(f"\nEconomic hours: {(battery_data['turbine_regime'] == 'economic').sum():,}")
print(f"Necessity hours: {(battery_data['turbine_regime'] == 'necessity').sum():,}")
print(f"Capacity factor: {capacity_factor:.2%}")
print(f"Generation-weighted efficiency: {weighted_efficiency:.2%}")
print(f"Turbine starts: {starts:,}")
print(f"Start cost (discussion only): ${start_cost:,.2f}")
print(f"Mean PUE: {battery_data['PUE'].mean():.4f}")
print(f"Maximum demand: {peak['demand_MW']:.2f} MW")
print(f"Date: {int(peak['month'])}/{int(peak['day'])}")
print(f"Hour: {int(peak['hour_of_day']):02d}:00")


# Battery energy totals and actual losses, including the year-end energy change
charge_energy_MWh = battery_data["battery_charge_MW"].sum() * time_step_hours
discharge_energy_MWh = battery_data["battery_discharge_MW"].sum() * time_step_hours
battery_losses_MWh = battery_data["battery_loss_MWh"].sum()

# Equivalent full cycles: stored energy discharged / 80 MWh usable capacity
usable_energy_MWh = battery_max_energy_MWh - battery_min_energy_MWh
full_cycles = discharge_energy_MWh / battery_discharge_efficiency / usable_energy_MWh
minimum_energy_MWh = min(battery_initial_energy_MWh, battery_data["battery_energy_end_MWh"].min())
maximum_energy_MWh = max(battery_initial_energy_MWh, battery_data["battery_energy_end_MWh"].max())

print(f"\nBattery charge energy: {charge_energy_MWh:,.2f} MWh")
print(f"Battery discharge energy: {discharge_energy_MWh:,.2f} MWh")
print(f"Battery losses: {battery_losses_MWh:,.2f} MWh")
print(f"Equivalent full cycles (80 MWh usable basis): {full_cycles:,.2f}")
print(f"Minimum stored energy: {minimum_energy_MWh:.2f} MWh")
print(f"Maximum stored energy: {maximum_energy_MWh:.2f} MWh")
print(f"Final stored energy: {battery_energy_MWh:.2f} MWh")


# --------------------------------------------------
# Save Part C and a simplified combined file
# --------------------------------------------------
battery_output_path = project_folder / "part_c.csv"
battery_data.to_csv(battery_output_path, index=False, float_format="%.6f")

# Shared time, prices, and facility demand
shared_columns = [
    "hour", "month", "day", "hour_of_day",
    "DA_LMP_$/MWh", "Gas_$/MMBtu", "demand_MW",
]

# Part A: grid only
part_a_columns = [
    "import_MW",
    "shed_facility_MW",
]

# Part B: grid and turbines
part_b_columns = [
    "turbine_regime",
    "generation_MW",
    "import_MW",
    "export_MW",
    "shed_facility_MW",
    "fuel_MMBtu",
    "fuel_cost",
]

# Part C: same information, plus battery operation
part_c_columns = part_b_columns + [
    "battery_charge_MW",
    "battery_discharge_MW",
    "battery_energy_end_MWh",
]

# Place all three cases side by side for each hour
combined_data = pd.concat(
    [
        data[shared_columns],
        data[part_a_columns].add_prefix("A_"),
        gas_data[part_b_columns].add_prefix("B_"),
        battery_data[part_c_columns].add_prefix("C_"),
    ],
    axis=1,
)

combined_output_path = project_folder / "all_combined.csv"
combined_data.to_csv(
    combined_output_path, index=False, float_format="%.6f"
)

print(f"\nPart C results saved to: {battery_output_path}")
print(f"Combined results saved to: {combined_output_path}")
