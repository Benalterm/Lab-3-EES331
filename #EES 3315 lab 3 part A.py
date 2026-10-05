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
output_path = project_folder / "lab_3_output.csv"

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
    temperature = data.loc[h, "Temp_F"]

    pue = min(
        1.10 + 0.005 * max(temperature - 55, 0),
        1.30,
    )

    data.loc[h, "PUE"] = pue

# Compute power and facility demand, including cooling
data["compute_MW"] = 80 + 20 * data["batch_on"]
data["demand_MW"] = data["compute_MW"] * data["PUE"]

# Step 1: grid only, no turbines or battery
for h in range(hour_year):
    demand = data.loc[h, "demand_MW"]
    pue = data.loc[h, "PUE"]

    # Facility power that cannot be supplied by the grid
    total_shed = max(demand - grid_limit_MW, 0)

    # Shed batch first, including its cooling
    batch_demand = 20 * data.loc[h, "batch_on"] * pue
    batch_shed = min(total_shed, batch_demand)
    base_shed = total_shed - batch_shed

    data.loc[h, "shed_facility_MW"] = total_shed
    data.loc[h, "shed_batch_MW"] = batch_shed
    data.loc[h, "shed_base_MW"] = base_shed
    data.loc[h, "shed_compute_MW"] = total_shed / pue
    data.loc[h, "import_MW"] = min(demand, grid_limit_MW)

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
data.to_csv(output_path, index=False, float_format="%.4f")
print(f"\nResults saved to: {output_path}")