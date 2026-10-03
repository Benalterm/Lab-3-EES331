# EES 3315 lab 3 part A

# Global variables & imports
from pathlib import Path
import pandas as pd

hour_year = 8760
day_year = 365
month_year = 12

# Load the original CSV
project_folder = Path(__file__).resolve().parent
csv_path = project_folder / "Lab_Data.csv"
data = pd.read_csv(csv_path)

# Remove completely empty columns that create extra commas
data = data.dropna(axis=1, how="all")

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

    # Mark the selected eight hours as batch_on = 1
    batch_indices = daily_rows.index[
        daily_rows["hour_of_day"].between(
            lowest_start_hour, lowest_start_hour + 7
        )
    ]
    data.loc[batch_indices, "batch_on"] = 1

    # Optional: display the selected hours using 1–24 numbering
    # first_hour = lowest_start_hour + 1
    # last_hour = first_hour + 7
    # print(
    #     f"{int(month)}/{int(day)}: lowest 8-hour average = "
    #     f"${lowest_average:.2f}/MWh (hours {first_hour}-{last_hour})"
    # )

# Calculate PUE using EACH ROW'S temperature
# Calculate PUE separately for every hour
data["PUE"] = 0.0

for h in range(hour_year):
    temperature = data.loc[h, "Temp_F"]

    pue = min(
        1.10 + 0.005 * max(temperature - 55, 0),
        1.30
    )

    data.loc[h, "PUE"] = pue
# Compute power and total facility demand for each hour
    data["compute_MW"] = 80 + 20 * data["batch_on"]
    data["demand_MW"] = data["compute_MW"] * data["PUE"]


# Print functions for section 8 
print((data["demand_MW"].sum())/1000, "GWh")  # Total energy demand in GWh
print((data["compute_MW"].sum())/1000, "GWh")  # Compute energy demand in GWh

# locate the max hour and demand
peak = data.loc[data["demand_MW"].idxmax()]
print(f"Maximum demand: {peak['demand_MW']:.2f} MW")
print(f"Date: {int(peak['month'])}/{int(peak['day'])}")
print(f"Hour: {int(peak['hour_of_day'])}:00")
######################### Sheding time 










